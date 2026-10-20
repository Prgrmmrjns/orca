"""Cursor SDK agent that picks paper weights from ML scores and news."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
from cursor_sdk import Agent, ModelParameterValue, ModelSelection
from cursor_sdk import Cursor
from cursor_sdk import CursorAgentError

from orca.news import mentioned_bases
from orca.logging_setup import setup_logging

logger = setup_logging()

SYSTEM_RULES = """You are Orca's paper crypto portfolio agent. Grow a €1000 book.
You decide once a day, at the open. A round trip costs 1% (0.5% in, 0.5% out).

Hindsight from a perfect-foresight book on this same window: the profit was one coin, the strongest trend, held while it led. An equal basket of several "pretty good" names stayed flat. The last live run did that — it bought four top-quarter names and then refused to change — and the account went nowhere.

How to choose:
- One coin, or two if the second is also a clear leader. Never more. About 70% in the leader (highest ret_7d with rank_7d >= 0.85). Leave the rest in cash.
- Read memory and since_entry. If a holding is no longer a top-5 ret_7d name, sell it today. If your leader is still the leader, keep it. Do not refresh weights that are already right.
- You may leave resting orders for the hours until the next day. below is a USD price, the same unit as marks_usd. A buy order adds that weight if price trades at or under below. A sell order exits that coin if price trades at or under below. Use a buy for a dip and a sell as a stop. Do not set a level more than about 15% from the current mark.
- headlines are events that were read and checked. Each one has a signal sentence. A coin with no item had no checked event. Silence is not a buy and not a reason to keep a coin that has stopped leading. price_recap items describe a move that already happened.
- Long only. Weights >= 0 and sum <= 0.95.
- Use only this snapshot. Do not browse, do not use tools, do not open files.
- Reply with JSON only. No markdown.
{"reasoning": "a few sentences on the leader, your memory, and why this book", "weights": {"SOL/USD": 0.7}, "orders": [{"pair": "SOL/USD", "side": "buy", "below": 140.0, "weight": 0.15}, {"pair": "DASH/USD", "side": "sell", "below": 28.0}], "reason": "one line"}
"""


def resolve_grok_medium(api_key: str) -> ModelSelection:
    """Pick Grok 4.7 Medium from the account catalog."""
    models = Cursor.models.list(api_key=api_key)
    grok = None
    for model in models:
        mid = getattr(model, "id", None) or (model.get("id") if isinstance(model, dict) else None)
        if mid == "grok-4.7":
            grok = model
            break
    if grok is None:
        available = []
        for model in models:
            mid = getattr(model, "id", "") or ""
            if "grok" in str(mid).lower():
                available.append(str(mid))
        raise SystemExit(
            "Grok 4.7 not in Cursor.models.list(). Available Grok ids: "
            + (", ".join(available) if available else "(none)")
        )
    variants = list(getattr(grok, "variants", ()) or [])
    medium = None
    for variant in variants:
        params = {p.id: p.value for p in getattr(variant, "params", ())}
        name = (getattr(variant, "display_name", "") or "").lower()
        if params.get("reasoning_effort") == "medium" and params.get("fast") != "true":
            medium = variant
            if "medium" in name and "fast" not in name:
                break
    if medium is None:
        raise SystemExit("Grok 4.7 Medium preset missing from the catalog")
    return ModelSelection(id="grok-4.7", params=tuple(medium.params))


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON root is not an object")
    return data


def parse_orders(payload: dict[str, Any], pairs: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    allowed = set(pairs)
    raw = payload.get("orders") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pair = str(item.get("pair") or "").strip()
        side = str(item.get("side") or "").strip().lower()
        if pair not in allowed or side not in {"buy", "sell"}:
            continue
        try:
            below = float(item.get("below"))
        except (TypeError, ValueError):
            continue
        if below <= 0:
            continue
        try:
            weight = float(item.get("weight") or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        out.append(
            {
                "pair": pair,
                "side": side,
                "below": round(below, 6),
                "weight": round(max(weight, 0.0), 4),
            }
        )
        if len(out) >= 4:
            break
    return out


def parse_decision(
    payload: dict[str, Any], pairs: list[str] | tuple[str, ...]
) -> tuple[dict[str, float], list[dict[str, Any]], str, str]:
    weights, reason = parse_weights(payload, pairs)
    reasoning = str(payload.get("reasoning") or "").strip() or reason
    return weights, parse_orders(payload, pairs), reason, reasoning


def parse_weights(payload: dict[str, Any], pairs: list[str] | tuple[str, ...]) -> tuple[dict[str, float], str]:
    allowed = set(pairs)
    raw = payload.get("weights") or {}
    if not isinstance(raw, dict):
        raise ValueError("weights must be an object")
    out: dict[str, float] = {}
    for key, value in raw.items():
        pair = str(key).strip()
        if pair not in allowed:
            raise ValueError(f"unknown pair {pair}")
        weight = float(value)
        if weight < 0:
            raise ValueError(f"negative weight for {pair}")
        if weight > 0:
            out[pair] = weight
    total = sum(out.values())
    if total > 1.0 + 1e-6:
        raise ValueError(f"weights sum to {total:.4f} > 1")
    reason = str(payload.get("reason") or "").strip() or "no reason"
    return out, reason


_RECAP = re.compile(
    r"price prediction|surges|soars|jumps|rallies|leads (?:way|increases)|tumbles|plunges",
    re.I,
)


def _as_utc(value: object) -> pd.Timestamp:
    published = pd.Timestamp(value)
    if published.tzinfo is None:
        return published.tz_localize("UTC")
    return published.tz_convert("UTC")


def curated_headlines(before: pd.Timestamp, *, limit: int = 8) -> list[dict[str, str]] | None:
    """Read signals for this window. None only when the file is absent."""
    path = Path(__file__).resolve().parents[1] / "data" / "news" / "september_2026_signals.json"
    if not path.exists():
        return None
    before = _as_utc(before)
    rows: list[tuple[pd.Timestamp, dict[str, str]]] = []
    for article in json.loads(path.read_text(encoding="utf-8")):
        published = _as_utc(article["published_at"])
        if published >= before or published < before - pd.Timedelta(days=10):
            continue
        rows.append(
            (
                published,
                {
                    "published_at": published.strftime("%Y-%m-%d %H:%M"),
                    "coins": str(article.get("coins") or ""),
                    "kind": str(article.get("kind") or "event"),
                    "title": str(article.get("title") or "")[:160],
                    "signal": str(article.get("signal") or "")[:700],
                },
            )
        )
    rows.sort(key=lambda item: -item[0].timestamp())
    return [row for _, row in rows[:limit]]


def headlines_before(
    articles: list[dict[str, Any]],
    pairs: list[str] | tuple[str, ...],
    *,
    before: pd.Timestamp,
    limit: int = 8,
) -> list[dict[str, str]]:
    """Checked September signals when present. Otherwise coin-matched headlines."""
    curated = curated_headlines(before, limit=limit)
    if curated is not None:
        return curated
    rows: list[tuple[pd.Timestamp, dict[str, str]]] = []
    for article in articles:
        published = pd.Timestamp(article["published_at"])
        if published.tzinfo is None:
            published = published.tz_localize("UTC")
        else:
            published = published.tz_convert("UTC")
        if published >= before or published < before - pd.Timedelta(days=3):
            continue
        title = str(article.get("title") or "")
        text = f"{title}. {article.get('summary', '')}".lower()
        coins = mentioned_bases(text, pairs)
        if not coins:
            continue
        kind = "price_recap" if _RECAP.search(title) else "event"
        rows.append(
            (
                published,
                {
                    "published_at": published.strftime("%Y-%m-%d %H:%M"),
                    "coins": ",".join(coins[:3]),
                    "kind": kind,
                    "title": title[:160],
                },
            )
        )
    rows.sort(key=lambda item: (item[1]["kind"] != "event", -item[0].timestamp()))
    return [row for _, row in rows[:limit]]


def ml_snapshot(
    panel: pd.DataFrame,
    pairs: list[str] | tuple[str, ...],
    prev: pd.Timestamp,
    state: dict[str, float] | None = None,
    *,
    top_n: int = 6,
) -> list[dict[str, Any]]:
    """Point-in-time momentum board. No model scores: those gaps were noise."""
    held = state or {}
    need = ("ret_1d", "ret_7d", "rank_7d", "dist_sma_7d", "rsi_7d")
    by_pair = {pair: frame.drop(columns=["pair"]) for pair, frame in panel.groupby("pair")}
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        book = by_pair.get(pair)
        if book is None or prev not in book.index:
            continue
        row = book.loc[prev]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        if any(pd.isna(row[col]) for col in need):
            continue
        rows.append(
            {
                "pair": pair,
                "held": 1 if held.get(pair, 0.0) > 0 else 0,
                "ret_1d": round(float(row["ret_1d"]), 4),
                "ret_7d": round(float(row["ret_7d"]), 4),
                "rank_7d": round(float(row["rank_7d"]), 3),
                "dist_sma_7d": round(float(row["dist_sma_7d"]), 4),
                "rsi_7d": round(float(row["rsi_7d"]), 1),
            }
        )
    rows.sort(key=lambda item: (item["ret_7d"], item["rank_7d"]), reverse=True)
    if rows:
        rows[0]["leader"] = True
    top = {row["pair"] for row in rows[:top_n]}
    kept = [row for row in rows if row["pair"] in top or row["held"]]
    return kept


def stabilize_weights(
    proposed: dict[str, float],
    held: dict[str, float],
    opened_at: dict[str, pd.Timestamp],
    now: pd.Timestamp,
    *,
    max_names: int = 2,
    min_hold: pd.Timedelta = pd.Timedelta(hours=24),
) -> dict[str, float]:
    """Cap the book at 2 names and block sells inside 24 hours."""
    locked = {
        pair
        for pair, since in opened_at.items()
        if held.get(pair, 0) > 0 and now - since < min_hold
    }
    out = {pair: weight for pair, weight in proposed.items() if weight > 0.02}
    for pair in locked:
        out[pair] = max(out.get(pair, 0.0), held.get(pair, 0.0))
    ranked = sorted(out, key=lambda pair: out[pair], reverse=True)
    kept: dict[str, float] = {pair: out[pair] for pair in ranked if pair in locked}
    for pair in ranked:
        if pair in kept:
            continue
        if len(kept) >= max_names:
            break
        kept[pair] = out[pair]
    total = sum(kept.values())
    if total > 0.95:
        scale = 0.95 / total
        kept = {pair: weight * scale for pair, weight in kept.items()}
    return {pair: round(weight, 4) for pair, weight in kept.items() if weight > 0.02}


def stamp_held(rows: list[dict[str, Any]], held: dict[str, float]) -> list[dict[str, Any]]:
    """Copy a precomputed board and mark what the book holds now."""
    out = []
    for row in rows:
        item = dict(row)
        item["held"] = 1 if held.get(item["pair"], 0.0) > 0 else 0
        out.append(item)
    return out


def build_prompt(
    *,
    hour: pd.Timestamp,
    cash: float,
    equity: float,
    weights: dict[str, float],
    closes: dict[str, float],
    ml_rows: list[dict[str, Any]],
    headlines: list[dict[str, str]],
    start_eur: float,
    eur_usd: float,
    pairs: list[str] | tuple[str, ...],
    memory: list[dict[str, Any]] | None = None,
    since_entry: dict[str, Any] | None = None,
) -> str:
    held = {pair: round(w, 4) for pair, w in weights.items() if w > 0}
    top_pairs = [r["pair"] for r in ml_rows[:8]]
    marks = {
        pair: round(px, 6)
        for pair, px in closes.items()
        if pair in held or pair in top_pairs
    }
    payload = {
        "day_utc": hour.strftime("%Y-%m-%d %H:%M"),
        "start_eur": start_eur,
        "equity_eur": round(equity / eur_usd, 2),
        "eur_usd": round(eur_usd, 4),
        "cash_usd": round(cash, 2),
        "current_weights": held,
        "since_entry": since_entry or {},
        "memory": (memory or [])[-6:],
        "marks_usd": marks,
        "ml": ml_rows,
        "headlines": headlines,
        "spread": 0.005,
        "tradeable_pairs": list(pairs),
    }
    return (
        SYSTEM_RULES
        + "\nSnapshot:\n"
        + json.dumps(payload, ensure_ascii=True)
        + "\nReturn the JSON decision now."
    )


def decide_weights(
    *,
    api_key: str,
    model: ModelSelection,
    prompt: str,
    pairs: list[str] | tuple[str, ...],
    cwd: Path | None = None,
) -> dict[str, Any]:
    """One-shot Cursor agent call. Weights, resting orders, reasoning, raw text."""
    work = Path(cwd) if cwd else Path(tempfile.mkdtemp(prefix="orca-llm-"))
    work.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    raw = ""
    for attempt in range(2):
        try:
            result = Agent.prompt(
                prompt if attempt == 0 else prompt + "\nPrevious reply was invalid. JSON only.",
                {
                    "api_key": api_key,
                    "model": model,
                    "local": {"cwd": str(work), "setting_sources": []},
                },
            )
            if result.status != "finished":
                raise RuntimeError(f"run status {result.status}")
            raw = str(result.result or "")
            if "tool_call" in raw.lower() and "weights" not in raw:
                raise ValueError("tool call in reply")
            weights, orders, reason, reasoning = parse_decision(_extract_json(raw), pairs)
            return {
                "weights": weights,
                "orders": orders,
                "reason": reason,
                "reasoning": reasoning,
                "raw": raw,
            }
        except CursorAgentError as exc:
            last_err = exc
            logger.warning("Cursor agent startup failed (attempt %d): %s", attempt + 1, exc)
        except Exception as exc:  # noqa: BLE001 — retry once on bad JSON
            last_err = exc
            logger.warning("Bad agent reply (attempt %d): %s", attempt + 1, exc)
    raise RuntimeError(f"agent decision failed: {last_err}")


def load_api_key() -> str:
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        try:
            from dotenv import dotenv_values

            key = (dotenv_values(env_path).get("CURSOR_API_KEY") or "").strip()
        except Exception:  # noqa: BLE001
            key = ""
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("CURSOR_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if key:
            return key
    raise SystemExit("CURSOR_API_KEY missing. Add it to the environment or .env")

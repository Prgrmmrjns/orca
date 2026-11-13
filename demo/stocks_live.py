"""Stocks paper viewer — multi-book comparison. No broker API."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

START = "2026-11-10"
BOOK_ORDER = [
    "agent",
    "bigbeat_gap",
    "guidance_beat",
    "beat5_raise",
    "miss_short",
    "news_event",
    # Tuned news books (paper, from 2026-11-10 box winners)
    "guidance_raise_tuned",
    "dilution_shorts_tuned",
    "quality_longs_tuned",
    "guidance_raise_score",
    "gap_filtered_guidance",
    "wide_capacity",
    # Extended-hours gap-fade book (paper, from 2026-11-13)
    "bigmove_unified_v1",
]
BOOK_TITLES = {
    "agent": "Agent (Chewbacca)",
    "bigbeat_gap": "Big beat + gap",
    "guidance_beat": "Guidance beat",
    "beat5_raise": "Beat 5% + raise",
    "miss_short": "Miss short",
    "news_event": "News event",
    "guidance_raise_tuned": "Guidance raise tuned",
    "dilution_shorts_tuned": "Dilution shorts tuned",
    "quality_longs_tuned": "Quality longs tuned",
    "guidance_raise_score": "Guidance raise score",
    "gap_filtered_guidance": "Gap-filtered guidance",
    "wide_capacity": "Wide capacity",
    "bigmove_unified_v1": "Bigmove unified v1",
}
CONFIG_BOOK_IDS = {
    "news_event",
    "guidance_raise_tuned",
    "dilution_shorts_tuned",
    "quality_longs_tuned",
    "guidance_raise_score",
    "gap_filtered_guidance",
    "wide_capacity",
    "bigmove_unified_v1",
}


def require_paper() -> None:
    if os.environ.get("ORCA_MODE", "paper") != "paper":
        raise RuntimeError("Orca demo is paper-only")


def json_safe(obj):
    """NaN/Inf → None so Starlette JSONResponse (allow_nan=False) never 500s."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def _finite(v) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _state_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "state" / "stocks", here / "state" / "stocks"):
        if cand.exists():
            return cand
    return here / "state" / "stocks"


def _load(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def _win_rate(closed: list[dict]) -> float | None:
    if not closed:
        return None
    wins = sum(1 for t in closed if (t.get("pnl_eur") or 0) > 0 or (t.get("pnl_pct_net") or 0) > 0)
    return round(100.0 * wins / len(closed), 1)


def _spy_return(led: dict, start: float) -> float | None:
    """Return % of SPY (EUR-normalized) since forward-test start, vs start capital."""
    if not start:
        return None
    hist = led.get("equity_history") or []
    spy_hist = led.get("spy_history") or []
    spy_now = None
    if hist:
        spy_now = _finite(hist[-1].get("spy_eur"))
    if spy_now is None and spy_hist:
        # Prefer last finite SPY print (skip NaN days left in old ledgers)
        for row in reversed(spy_hist):
            spy_now = _finite(row.get("spy_eur"))
            if spy_now is not None:
                break
    if spy_now is None:
        return None
    return round((spy_now / start - 1) * 100, 2)


def _strategy_configs() -> dict[str, dict]:
    """Active YAML snapshots for config-driven books. Soft-fail per book."""
    out: dict[str, dict] = {}
    try:
        repo = Path(__file__).resolve().parents[1]
        if str(repo) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(repo))
        from stocks.strategy_config import (
            BIGMOVE_BOOK_ID,
            TUNED_NEWS_BOOK_IDS,
            load_strategy_config,
        )

        for bid in ["news_event", *TUNED_NEWS_BOOK_IDS, BIGMOVE_BOOK_ID]:
            try:
                out[bid] = load_strategy_config(bid).to_public_dict()
            except Exception:
                continue
    except Exception:
        return out
    return out


def _book_summary(book_id: str, led: dict, decs: dict, strategy_config: dict | None = None) -> dict:
    start = _finite(led.get("start_capital_eur")) or 1000.0
    eq = _finite(led.get("equity_eur")) or start
    closed = led.get("closed_trades") or []
    positions = []
    for t, p in (led.get("positions") or {}).items():
        if not isinstance(p, dict):
            continue
        positions.append(
            {
                "ticker": t,
                "side": p.get("side"),
                "size": p.get("size"),
                "entry_usd": p.get("entry_usd"),
                "mark_usd": p.get("mark_usd"),
                "entry_day": p.get("entry_day"),
                "exit_day": p.get("exit_day"),
                "unrealized_pnl_pct": p.get("unrealized_pnl_pct"),
                "reason": p.get("reason"),
                "rule_tags": p.get("rule_tags") or [],
                "session": p.get("entry_session") or p.get("session"),
                "entry_session": p.get("entry_session") or p.get("session"),
            }
        )
    # Sanitize closed trades (session + finite numerics)
    closed_out = []
    for t in reversed(closed[-50:]):
        if not isinstance(t, dict):
            continue
        closed_out.append(
            {
                **t,
                "pnl_eur": _finite(t.get("pnl_eur")),
                "pnl_pct_net": _finite(t.get("pnl_pct_net")),
                "entry_usd": _finite(t.get("entry_usd")),
                "exit_usd": _finite(t.get("exit_usd")),
                "session": t.get("exit_session") or t.get("session") or t.get("entry_session"),
                "entry_session": t.get("entry_session"),
                "exit_session": t.get("exit_session") or t.get("session"),
            }
        )
    ret = round((eq / start - 1) * 100, 2)
    spy_ret = _spy_return(led, start)
    vs_spy = None if spy_ret is None else round(ret - spy_ret, 2)
    out = {
        "id": book_id,
        "title": led.get("strategy_title") or BOOK_TITLES.get(book_id, book_id),
        "kind": led.get("kind") or ("agent" if book_id == "agent" else "rule"),
        "rules": led.get("rules") or led.get("note") or "",
        "equity_eur": round(eq, 2),
        "cash_eur": round(_finite(led.get("cash_eur")) or 0.0, 2),
        "return_pct": ret,
        "spy_return_pct": spy_ret,
        "vs_spy_pct": vs_spy,
        "n_open": len(led.get("positions") or {}),
        "n_pending": len(led.get("pending") or []),
        "n_closed": len(closed),
        "win_rate_pct": _win_rate(closed),
        "eur_usd": led.get("eur_usd"),
        "cost_pct": led.get("cost_pct", 0.005),
        "extended_hours_extra_slippage_pct": led.get("extended_hours_extra_slippage_pct"),
        "allow_extended_hours": led.get("allow_extended_hours"),
        "max_open": led.get("max_open"),
        "default_size": led.get("default_size"),
        "equity_history": led.get("equity_history") or [],
        "spy_history": led.get("spy_history") or [],
        "positions": positions,
        "pending": led.get("pending") or [],
        "closed_trades": closed_out,
        "n_decisions": len(decs.get("decisions") or []),
        "last_run": led.get("last_run"),
        "last_reason": led.get("last_reason"),
        "start_date": led.get("start_date"),
    }
    if strategy_config is not None:
        out["strategy_config"] = strategy_config
        out["config_path"] = strategy_config.get("path") or f"config/strategies/{book_id}.yaml"
    return out


def _discover_books(state: Path) -> list[str]:
    books_dir = state / "books"
    found = []
    if books_dir.exists():
        for p in sorted(books_dir.iterdir()):
            if p.is_dir() and (p / "ledger.json").exists():
                found.append(p.name)
    # preserve preferred order, then any extras
    ordered = [b for b in BOOK_ORDER if b in found]
    ordered += [b for b in found if b not in ordered]
    if not ordered and (state / "ledger.json").exists():
        ordered = ["agent"]
    return ordered or list(BOOK_ORDER)


def snapshot() -> dict:
    require_paper()
    state = _state_dir()
    cands = _load(state / "candidates.json", {})
    news = _load(state / "news.json", {})
    book_ids = _discover_books(state)
    cfg_map = _strategy_configs()
    news_cfg = cfg_map.get("news_event")
    books = []
    book_errors: list[dict] = []
    for bid in book_ids:
        try:
            led_path = state / "books" / bid / "ledger.json"
            dec_path = state / "books" / bid / "decisions.json"
            if not led_path.exists() and bid == "agent":
                led_path = state / "ledger.json"
                dec_path = state / "decisions.json"
            led = _load(led_path, {})
            if not led:
                led = {
                    "mode": "paper",
                    "book_id": bid,
                    "strategy_title": BOOK_TITLES.get(bid, bid),
                    "kind": "agent" if bid == "agent" else "rule",
                    "start_capital_eur": 1000,
                    "cash_eur": 1000,
                    "equity_eur": 1000,
                    "positions": {},
                    "pending": [],
                    "closed_trades": [],
                    "equity_history": [{"day": START, "equity_eur": 1000, "spy_eur": 1000}],
                    "cost_pct": 0.005,
                }
            decs = _load(dec_path, {"decisions": []})
            books.append(
                _book_summary(
                    bid,
                    led,
                    decs,
                    strategy_config=cfg_map.get(bid) if bid in CONFIG_BOOK_IDS else None,
                )
            )
        except Exception as exc:
            book_errors.append({"id": bid, "error": f"{type(exc).__name__}: {exc}"})
            books.append(
                {
                    "id": bid,
                    "title": BOOK_TITLES.get(bid, bid),
                    "kind": "agent" if bid == "agent" else "rule",
                    "rules": "",
                    "equity_eur": 1000.0,
                    "cash_eur": 1000.0,
                    "return_pct": 0.0,
                    "spy_return_pct": None,
                    "vs_spy_pct": None,
                    "n_open": 0,
                    "n_pending": 0,
                    "n_closed": 0,
                    "win_rate_pct": None,
                    "positions": [],
                    "pending": [],
                    "closed_trades": [],
                    "equity_history": [],
                    "spy_history": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    # Agent book shorthand for older clients
    agent = next((b for b in books if b["id"] == "agent"), books[0] if books else {})
    decisions = {}
    agent_dec_path = state / "books" / "agent" / "decisions.json"
    if not agent_dec_path.exists():
        agent_dec_path = state / "decisions.json"
    for d in (_load(agent_dec_path, {}).get("decisions") or []):
        if isinstance(d, dict) and d.get("ticker"):
            decisions[d["ticker"]] = d

    today_cands = []
    for c in cands.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        try:
            d = decisions.get(c.get("ticker"))
            today_cands.append(
                {
                    **{
                        k: c.get(k)
                        for k in (
                            "ticker",
                            "name",
                            "surprise_pct",
                            "gap_pct",
                            "guidance",
                            "tone",
                            "headline",
                            "rule_tags",
                            "suggested_side",
                            "suggested_hold",
                            "exhibit_url",
                            "filing_url",
                            "cal_date",
                            "timing",
                            "spy_above_ma50",
                            "source",
                            "news_event_type",
                            "news_direction",
                            "news_side",
                            "news_actionable",
                        )
                    },
                    "decision": None
                    if not d
                    else {
                        "side": d.get("side"),
                        "size": d.get("size"),
                        "hold": d.get("hold"),
                        "reason": d.get("reason"),
                        "confidence": d.get("confidence"),
                        "status": d.get("status"),
                        "book_id": d.get("book_id") or "agent",
                    },
                }
            )
        except Exception:
            continue

    rules = cands.get("rules") or {b["id"]: b.get("rules") or "" for b in books}
    news_items = []
    for it in (news.get("items") or [])[:40]:
        if not isinstance(it, dict):
            continue
        news_items.append(
            {
                "id": it.get("id"),
                "source": it.get("source"),
                "title": it.get("title"),
                "tickers": it.get("tickers") or [],
                "event_type": it.get("event_type"),
                "direction": it.get("direction"),
                "news_side": it.get("news_side"),
                "actionable": it.get("actionable"),
                "published_at": it.get("published_at"),
                "url": it.get("url"),
            }
        )

    alerts_doc = _load(state / "alerts.json", {"alerts": []})
    alerts = []
    for a in (alerts_doc.get("alerts") or [])[-30:]:
        if not isinstance(a, dict):
            continue
        alerts.append(
            {
                "id": a.get("id"),
                "type": a.get("type"),
                "ticker": a.get("ticker"),
                "gap_pct": _finite(a.get("gap_pct")),
                "adv20_usd": _finite(a.get("adv20_usd")),
                "session": a.get("session"),
                "as_of": a.get("as_of"),
                "action": a.get("action"),
                "note": a.get("note"),
                "item_502": a.get("item_502"),
                "neg_analyst_count": a.get("neg_analyst_count"),
            }
        )

    payload = {
        "mode": "paper",
        "product": "stocks-earnings-multi-book",
        "orders": "paper",
        "note": (
            "paper only, multi-book forward test since 2026-11-10 "
            "(12 books + bigmove_unified_v1 from 2026-11-13)"
        ),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "since": START,
        "scanned_at": cands.get("as_of"),
        "news_as_of": news.get("as_of"),
        "spy_above_ma50": cands.get("spy_above_ma50"),
        "news": news_items,
        "news_meta": {
            "n_items": news.get("n_items"),
            "n_actionable": news.get("n_actionable"),
            "sources": news.get("sources") or [],
            "note": news.get("note"),
        },
        "alerts": alerts,
        "alerts_as_of": alerts_doc.get("as_of"),
        "books": books,
        "news_event_config": news_cfg,
        "strategy_configs": cfg_map,
        "book": {  # backward-compat single-book shape = agent
            "equity_eur": agent.get("equity_eur"),
            "cash_eur": agent.get("cash_eur"),
            "return_pct": agent.get("return_pct"),
            "eur_usd": agent.get("eur_usd"),
            "n_open": agent.get("n_open"),
            "n_closed": agent.get("n_closed"),
            "cost_pct": agent.get("cost_pct", 0.005),
        },
        "equity_history": agent.get("equity_history") or [],
        "spy_history": agent.get("spy_history") or [],
        "positions": agent.get("positions") or [],
        "pending": agent.get("pending") or [],
        "closed_trades": agent.get("closed_trades") or [],
        "candidates": today_cands,
        "rules": rules,
    }
    if book_errors:
        payload["book_errors"] = book_errors
    return json_safe(payload)

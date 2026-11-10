"""Stocks paper viewer — multi-book comparison. No broker API."""
from __future__ import annotations

import json
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
}
CONFIG_BOOK_IDS = {
    "news_event",
    "guidance_raise_tuned",
    "dilution_shorts_tuned",
    "quality_longs_tuned",
    "guidance_raise_score",
    "gap_filtered_guidance",
    "wide_capacity",
}


def require_paper() -> None:
    if os.environ.get("ORCA_MODE", "paper") != "paper":
        raise RuntimeError("Orca demo is paper-only")


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
    hist = led.get("equity_history") or []
    spy_hist = led.get("spy_history") or []
    spy_now = None
    if hist:
        spy_now = hist[-1].get("spy_eur")
    if spy_now is None and spy_hist:
        spy_now = spy_hist[-1].get("spy_eur")
    if spy_now is None:
        return None
    return round((float(spy_now) / start - 1) * 100, 2)


def _strategy_configs() -> dict[str, dict]:
    """Active YAML snapshots for config-driven books. Soft-fail per book."""
    out: dict[str, dict] = {}
    try:
        repo = Path(__file__).resolve().parents[1]
        if str(repo) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(repo))
        from stocks.strategy_config import TUNED_NEWS_BOOK_IDS, load_strategy_config

        for bid in ["news_event", *TUNED_NEWS_BOOK_IDS]:
            try:
                out[bid] = load_strategy_config(bid).to_public_dict()
            except Exception:
                continue
    except Exception:
        return out
    return out


def _book_summary(book_id: str, led: dict, decs: dict, strategy_config: dict | None = None) -> dict:
    start = float(led.get("start_capital_eur") or 1000)
    eq = float(led.get("equity_eur") or start)
    closed = led.get("closed_trades") or []
    positions = []
    for t, p in (led.get("positions") or {}).items():
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
        "cash_eur": round(float(led.get("cash_eur") or 0), 2),
        "return_pct": ret,
        "spy_return_pct": spy_ret,
        "vs_spy_pct": vs_spy,
        "n_open": len(led.get("positions") or {}),
        "n_pending": len(led.get("pending") or []),
        "n_closed": len(closed),
        "win_rate_pct": _win_rate(closed),
        "eur_usd": led.get("eur_usd"),
        "cost_pct": led.get("cost_pct", 0.005),
        "max_open": led.get("max_open"),
        "default_size": led.get("default_size"),
        "equity_history": led.get("equity_history") or [],
        "spy_history": led.get("spy_history") or [],
        "positions": positions,
        "pending": led.get("pending") or [],
        "closed_trades": list(reversed(closed))[:50],
        "n_decisions": len(decs.get("decisions") or []),
        "last_run": led.get("last_run"),
        "last_reason": led.get("last_reason"),
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
    for bid in book_ids:
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

    # Agent book shorthand for older clients
    agent = next((b for b in books if b["id"] == "agent"), books[0] if books else {})
    decisions = {}
    agent_dec_path = state / "books" / "agent" / "decisions.json"
    if not agent_dec_path.exists():
        agent_dec_path = state / "decisions.json"
    for d in (_load(agent_dec_path, {}).get("decisions") or []):
        if d.get("ticker"):
            decisions[d["ticker"]] = d

    today_cands = []
    for c in cands.get("candidates") or []:
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

    rules = cands.get("rules") or {b["id"]: b.get("rules") or "" for b in books}
    news_items = []
    for it in (news.get("items") or [])[:40]:
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

    return {
        "mode": "paper",
        "product": "stocks-earnings-multi-book",
        "orders": "paper",
        "note": (
            "paper only, multi-book forward test since 2026-11-10 "
            "(legacy 6 + 6 tuned news books)"
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

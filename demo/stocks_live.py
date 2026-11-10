"""Stocks paper viewer. Reads state/stocks ledger + candidates. No broker API."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

START = "2026-11-10"


def require_paper() -> None:
    if os.environ.get("ORCA_MODE", "paper") != "paper":
        raise RuntimeError("Orca demo is paper-only")


def _state_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "state" / "stocks", here / "state" / "stocks"):
        if cand.exists():
            return cand
    return here / "state" / "stocks"


def _load(name: str, default=None):
    path = _state_dir() / name
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def snapshot() -> dict:
    require_paper()
    led = _load("ledger.json", {})
    cands = _load("candidates.json", {})
    decs = _load("decisions.json", {})
    start = float(led.get("start_capital_eur") or 1000)
    eq = float(led.get("equity_eur") or start)
    decisions = {d.get("ticker"): d for d in (decs.get("decisions") or [])}
    today_cands = []
    for c in cands.get("candidates") or []:
        d = decisions.get(c.get("ticker"))
        today_cands.append(
            {
                **{k: c.get(k) for k in (
                    "ticker", "name", "surprise_pct", "gap_pct", "guidance", "tone",
                    "headline", "rule_tags", "suggested_side", "suggested_hold",
                    "exhibit_url", "filing_url", "cal_date", "timing",
                )},
                "decision": None
                if not d
                else {
                    "side": d.get("side"),
                    "size": d.get("size"),
                    "hold": d.get("hold"),
                    "reason": d.get("reason"),
                    "confidence": d.get("confidence"),
                    "status": d.get("status"),
                },
            }
        )
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
    return {
        "mode": "paper",
        "product": "stocks-earnings",
        "orders": "paper",
        "note": "paper only, forward test since 2026-11-10",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "since": led.get("start_date") or START,
        "scanned_at": cands.get("as_of"),
        "spy_above_ma50": cands.get("spy_above_ma50"),
        "book": {
            "equity_eur": round(eq, 2),
            "cash_eur": round(float(led.get("cash_eur") or 0), 2),
            "return_pct": round((eq / start - 1) * 100, 2),
            "eur_usd": led.get("eur_usd"),
            "n_open": len(led.get("positions") or {}),
            "n_closed": len(led.get("closed_trades") or []),
            "cost_pct": led.get("cost_pct", 0.005),
        },
        "equity_history": led.get("equity_history") or [],
        "spy_history": led.get("spy_history") or [],
        "positions": positions,
        "pending": led.get("pending") or [],
        "closed_trades": list(reversed(led.get("closed_trades") or []))[:50],
        "candidates": today_cands,
        "rules": cands.get("rules") or {
            "best_long_guidance": "EPS beat >5% AND 8-K raises outlook → long, hold 5",
            "safer_long": "SPY>MA50 AND beat >15% AND gap >5% → long, hold 3",
            "gap_and_go_long": "beat >5% AND gap >5% → long, hold 5",
            "short_cut_guidance": "strong miss + cut guidance → short, small size",
        },
    }

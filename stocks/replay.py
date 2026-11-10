#!/usr/bin/env python3
"""Dry-run / unit replay of rule books over sample (or live) candidates.

Does not write state. Evidence that each rule book opens/closes correctly.
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import COST_PCT, START_CAPITAL_EUR, START_DATE
from stocks.books import BOOKS, BookSpec, matches
from stocks.paths import CANDIDATES, read_json


SAMPLE_CANDIDATES = [
    {
        "ticker": "BIGA",
        "surprise_pct": 22.0,
        "gap_pct": 7.5,
        "guidance": "none",
        "spy_above_ma50": True,
        "timing": "BMO",
        "filing_url": "",
        "exhibit_url": "",
        "raise_hits": "",
    },
    {
        "ticker": "GUIDE",
        "surprise_pct": 12.0,
        "gap_pct": 1.0,
        "guidance": "raised",
        "spy_above_ma50": False,
        "timing": "AMC",
        "filing_url": "https://example/8k",
        "exhibit_url": "https://example/ex99",
        "raise_hits": "raises full-year guidance",
    },
    {
        "ticker": "BEAT5",
        "surprise_pct": 6.0,
        "gap_pct": 0.5,
        "guidance": "raised",
        "spy_above_ma50": False,
        "timing": "BMO",
        "filing_url": "https://example/8k",
        "exhibit_url": "",
        "raise_hits": "increased outlook",
    },
    {
        "ticker": "MISS",
        "surprise_pct": -15.0,
        "gap_pct": -8.0,
        "guidance": "none",
        "spy_above_ma50": True,
        "timing": "BMO",
        "filing_url": "",
        "exhibit_url": "",
        "raise_hits": "",
    },
    {
        "ticker": "NOISE",
        "surprise_pct": 3.0,
        "gap_pct": 2.0,
        "guidance": "reaffirm",
        "spy_above_ma50": True,
        "timing": "BMO",
        "filing_url": "",
        "exhibit_url": "",
        "raise_hits": "",
    },
    {
        # bigbeat needs SPY above — this should NOT match bigbeat_gap
        "ticker": "GAPONLY",
        "surprise_pct": 20.0,
        "gap_pct": 6.0,
        "guidance": "none",
        "spy_above_ma50": False,
        "timing": "BMO",
        "filing_url": "",
        "exhibit_url": "",
        "raise_hits": "",
    },
]


def _add_trading_days(start: date, n: int) -> date:
    d = start
    left = n
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def simulate_book(book: BookSpec, candidates: list[dict], entry_day: date | None = None) -> dict:
    """Open matching names, then time-exit (and stop for miss_short) on synthetic prices."""
    if book.kind != "rule":
        return {
            "book_id": book.id,
            "kind": book.kind,
            "opened": [],
            "closed": [],
            "skipped": "agent book — no auto rules",
        }
    entry_day = entry_day or date.fromisoformat(START_DATE)
    equity = START_CAPITAL_EUR
    cash = START_CAPITAL_EUR
    opened = []
    closed = []
    open_n = 0
    for c in candidates:
        if open_n >= book.max_open:
            break
        if not matches(book, c, c.get("spy_above_ma50")):
            continue
        # Synthetic entry at $100
        entry_px = 100.0
        notional_eur = equity * book.size
        cost_eur = notional_eur * COST_PCT
        shares = notional_eur / entry_px  # pretend FX=1 for unit sim simplicity
        if book.side == "long":
            cash -= notional_eur + cost_eur
        else:
            cash += notional_eur - cost_eur
        exit_day = _add_trading_days(entry_day, book.hold_days)
        pos = {
            "ticker": c["ticker"],
            "side": book.side,
            "shares": shares,
            "entry_usd": entry_px,
            "entry_cost_eur": cost_eur,
            "size": book.size,
            "entry_day": entry_day.isoformat(),
            "exit_day": exit_day.isoformat(),
            "hold_days": book.hold_days,
            "stop_loss": book.stop_loss,
            "reason": f"replay match {book.id}",
        }
        opened.append(
            {
                "ticker": c["ticker"],
                "side": book.side,
                "size": book.size,
                "hold_days": book.hold_days,
                "stop_loss": book.stop_loss,
                "exit_day": exit_day.isoformat(),
                "surprise_pct": c.get("surprise_pct"),
                "gap_pct": c.get("gap_pct"),
                "guidance": c.get("guidance"),
            }
        )
        open_n += 1

        # Close: miss_short hits hard 8% stop against shorts when price rises 8%
        if book.side == "short" and book.stop_loss is not None:
            exit_px = entry_px * (1 + float(book.stop_loss))
            exit_why = "stop_loss"
        else:
            # flat time exit for evidence of hold calendar
            exit_px = entry_px
            exit_why = "time_exit"

        notional_eur_exit = shares * exit_px
        cost_exit = notional_eur_exit * COST_PCT
        if book.side == "long":
            cash += notional_eur_exit - cost_exit
            pnl_pct = (exit_px / entry_px - 1) - 2 * COST_PCT
        else:
            cash -= notional_eur_exit + cost_exit
            pnl_pct = (entry_px / exit_px - 1) - 2 * COST_PCT
        closed.append(
            {
                "ticker": c["ticker"],
                "side": book.side,
                "exit_reason": exit_why,
                "exit_day": exit_day.isoformat() if exit_why == "time_exit" else entry_day.isoformat(),
                "pnl_pct_net": round(pnl_pct * 100, 4),
                "entry_cost_pct": COST_PCT * 100,
                "exit_cost_pct": COST_PCT * 100,
            }
        )

    return {
        "book_id": book.id,
        "kind": book.kind,
        "description": book.description,
        "size": book.size,
        "hold_days": book.hold_days,
        "max_open": book.max_open,
        "stop_loss": book.stop_loss,
        "n_opened": len(opened),
        "n_closed": len(closed),
        "opened": opened,
        "closed": closed,
        "final_cash_eur_sim": round(cash, 2),
    }


def expected_tickers() -> dict[str, list[str]]:
    return {
        "bigbeat_gap": ["BIGA"],
        "guidance_beat": ["GUIDE"],  # surprise 12>8 + raised; BEAT5 has only 6% so no
        "beat5_raise": ["GUIDE", "BEAT5"],  # both raised with beat>5
        "miss_short": ["MISS"],
        "agent": [],
    }


def assert_sample_rules() -> dict:
    """Run sample candidates and assert each rule book opens/closes as expected."""
    results = {}
    exp = expected_tickers()
    errors = []
    for book in BOOKS:
        sim = simulate_book(book, SAMPLE_CANDIDATES)
        results[book.id] = sim
        got = [o["ticker"] for o in sim.get("opened") or []]
        want = exp[book.id]
        if got != want:
            errors.append(f"{book.id}: opened {got}, expected {want}")
        if book.kind == "rule":
            if sim["n_opened"] != sim["n_closed"]:
                errors.append(f"{book.id}: opened {sim['n_opened']} but closed {sim['n_closed']}")
            for o in sim["opened"]:
                if o["size"] != book.size:
                    errors.append(f"{book.id}/{o['ticker']}: size {o['size']} != {book.size}")
                if o["hold_days"] != book.hold_days:
                    errors.append(f"{book.id}/{o['ticker']}: hold {o['hold_days']} != {book.hold_days}")
            if book.id == "miss_short":
                for c in sim["closed"]:
                    if c["exit_reason"] != "stop_loss":
                        errors.append(f"miss_short: expected stop_loss, got {c['exit_reason']}")
                    # 8% adverse move on short → ~ -8% - 1% costs
                    if c["pnl_pct_net"] > -8:
                        errors.append(f"miss_short: stop pnl too high: {c['pnl_pct_net']}")
            else:
                for c in sim["closed"]:
                    if c["exit_reason"] != "time_exit":
                        errors.append(f"{book.id}: expected time_exit, got {c['exit_reason']}")
    ok = not errors
    return {"ok": ok, "errors": errors, "results": results, "expected": exp}


def replay_live_candidates(limit: int = 50) -> dict:
    """Match current candidates.json against each rule book (no fills)."""
    cands = read_json(CANDIDATES, default={})
    rows = cands.get("candidates") or []
    spy = cands.get("spy_above_ma50")
    out = {"as_of": cands.get("as_of"), "spy_above_ma50": spy, "books": {}}
    for book in BOOKS:
        if book.kind != "rule":
            out["books"][book.id] = {"kind": "agent", "matches": []}
            continue
        hits = []
        for c in rows:
            if matches(book, c, spy):
                hits.append(
                    {
                        "ticker": c.get("ticker"),
                        "surprise_pct": c.get("surprise_pct"),
                        "gap_pct": c.get("gap_pct"),
                        "guidance": c.get("guidance"),
                        "spy_above_ma50": c.get("spy_above_ma50"),
                    }
                )
            if len(hits) >= limit:
                break
        out["books"][book.id] = {
            "kind": "rule",
            "n_matches": len(hits),
            "size": book.size,
            "hold_days": book.hold_days,
            "max_open": book.max_open,
            "stop_loss": book.stop_loss,
            "matches": hits,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay rule books over sample / live candidates")
    ap.add_argument("--live", action="store_true", help="also match against state/stocks/candidates.json")
    a = ap.parse_args()
    sample = assert_sample_rules()
    out = {"sample": sample}
    if a.live:
        out["live"] = replay_live_candidates()
    print(json.dumps(out, indent=2, default=str))
    return 0 if sample["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

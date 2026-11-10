#!/usr/bin/env python3
"""CLI for the deciding agent to record a paper decision on the `agent` book only.

  python -m stocks.decide --ticker AAPL --side long --size 0.08 --hold 5 --reason "..."
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import MAX_OPEN, MAX_POSITION_PCT, MAX_SHORT_PCT
from stocks.books import AGENT_BOOK_ID
from stocks.market import utc_now_iso
from stocks.paths import (
    CANDIDATES,
    LEGACY_DECISIONS,
    LEGACY_LEDGER,
    decisions_path,
    ensure_dirs,
    ledger_path,
    read_json,
    write_json,
)

BOOK_ID = AGENT_BOOK_ID
DECISIONS = decisions_path(BOOK_ID)
LEDGER = ledger_path(BOOK_ID)


def load_decisions() -> dict:
    ensure_dirs()
    d = read_json(DECISIONS, default=None)
    if not d:
        # migrate legacy once
        legacy = read_json(LEGACY_DECISIONS, default=None)
        d = legacy if legacy else {"mode": "paper", "decisions": []}
    d.setdefault("decisions", [])
    d.setdefault("book_id", BOOK_ID)
    return d


def open_exposure(led: dict) -> tuple[int, set[str]]:
    pos = led.get("positions") or {}
    pending = led.get("pending") or []
    tickers = set(pos.keys()) | {p.get("ticker") for p in pending if p.get("ticker")}
    dec = load_decisions()
    for d in dec.get("decisions") or []:
        if d.get("status") in ("new", "accepted", "pending") and d.get("side") in ("long", "short"):
            tickers.add(d["ticker"])
    return len(tickers), tickers


def decide(
    ticker: str,
    side: str,
    size: float | None = None,
    hold: int = 5,
    reason: str = "",
    confidence: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    agent: str = "chewbacca",
) -> dict:
    """Record a decision for the agent book only. Rule books are engine-managed."""
    ticker = ticker.upper().replace(".", "-")
    side = side.lower()
    if side not in ("long", "short", "skip"):
        raise SystemExit(f"side must be long|short|skip, got {side}")

    cand_file = read_json(CANDIDATES, default={})
    cands = {c["ticker"]: c for c in (cand_file.get("candidates") or [])}
    cand = cands.get(ticker)

    led = read_json(LEDGER, default=None) or read_json(LEGACY_LEDGER, default={"positions": {}, "pending": []})
    n_open, open_tickers = open_exposure(led)

    if side == "skip":
        size = 0.0
    else:
        if size is None:
            size = 0.05 if side == "short" else 0.08
        size = float(size)
        if side == "long":
            if size <= 0 or size > MAX_POSITION_PCT + 1e-12:
                raise SystemExit(f"long size must be in (0, {MAX_POSITION_PCT}] — got {size}")
        if side == "short":
            if size <= 0 or size > MAX_SHORT_PCT + 1e-12:
                raise SystemExit(f"short size must be in (0, {MAX_SHORT_PCT}] — got {size}")
        if ticker in open_tickers:
            raise SystemExit(f"{ticker} already open or pending on agent book")
        if n_open >= MAX_OPEN:
            raise SystemExit(f"max open positions reached ({MAX_OPEN}) on agent book")

    rec = {
        "id": f"{ticker}-{utc_now_iso()}-{uuid.uuid4().hex[:6]}",
        "book_id": BOOK_ID,
        "ticker": ticker,
        "side": side,
        "size": size,
        "hold": int(hold),
        "reason": reason or ("skip" if side == "skip" else "no reason given"),
        "confidence": confidence,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rule_tags": (cand or {}).get("rule_tags") or [],
        "surprise_pct": (cand or {}).get("surprise_pct"),
        "gap_pct": (cand or {}).get("gap_pct"),
        "guidance": (cand or {}).get("guidance"),
        "agent": agent,
        "decided_at": utc_now_iso(),
        "status": "new",
    }

    dec = load_decisions()
    kept = []
    for d in dec["decisions"]:
        if d.get("ticker") == ticker and d.get("status") in ("new", "accepted") and d.get("side") != "skip":
            continue
        kept.append(d)
    kept.append(rec)
    dec["decisions"] = kept
    dec["updated_at"] = utc_now_iso()
    dec["book_id"] = BOOK_ID
    write_json(DECISIONS, dec)
    write_json(LEGACY_DECISIONS, dec)  # legacy mirror
    print(__import__("json").dumps(rec, indent=2))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Record a paper earnings decision on the agent book only"
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short", "skip"])
    ap.add_argument("--size", type=float, default=None, help="fraction of equity (long≤0.10, short≤0.05)")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--reason", default="")
    ap.add_argument("--confidence", type=float, default=None)
    ap.add_argument("--stop-loss", type=float, default=None, dest="stop_loss")
    ap.add_argument("--take-profit", type=float, default=None, dest="take_profit")
    ap.add_argument("--agent", default="chewbacca")
    a = ap.parse_args()
    decide(
        ticker=a.ticker,
        side=a.side,
        size=a.size,
        hold=a.hold,
        reason=a.reason,
        confidence=a.confidence,
        stop_loss=a.stop_loss,
        take_profit=a.take_profit,
        agent=a.agent,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

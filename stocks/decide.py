#!/usr/bin/env python3
"""CLI for the deciding agent to record a paper decision with risk caps enforced.

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
from stocks.market import utc_now_iso
from stocks.paths import CANDIDATES, DECISIONS, LEDGER, ensure_dirs, read_json, write_json


def load_decisions() -> dict:
    ensure_dirs()
    d = read_json(DECISIONS, default=None)
    if not d:
        d = {"mode": "paper", "decisions": []}
    d.setdefault("decisions", [])
    return d


def open_exposure(led: dict) -> tuple[int, set[str]]:
    pos = led.get("positions") or {}
    pending = led.get("pending") or []
    tickers = set(pos.keys()) | {p.get("ticker") for p in pending if p.get("ticker")}
    # also count accepted-but-not-yet-ingested decisions
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
    ticker = ticker.upper().replace(".", "-")
    side = side.lower()
    if side not in ("long", "short", "skip"):
        raise SystemExit(f"side must be long|short|skip, got {side}")

    cand_file = read_json(CANDIDATES, default={})
    cands = {c["ticker"]: c for c in (cand_file.get("candidates") or [])}
    cand = cands.get(ticker)

    led = read_json(LEDGER, default={"positions": {}, "pending": []})
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
            raise SystemExit(f"{ticker} already open or pending")
        if n_open >= MAX_OPEN:
            raise SystemExit(f"max open positions reached ({MAX_OPEN})")

    rec = {
        "id": f"{ticker}-{utc_now_iso()}-{uuid.uuid4().hex[:6]}",
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
    # replace prior undecided for same ticker today
    kept = []
    for d in dec["decisions"]:
        if d.get("ticker") == ticker and d.get("status") in ("new", "accepted") and d.get("side") != "skip":
            continue
        kept.append(d)
    kept.append(rec)
    dec["decisions"] = kept
    dec["updated_at"] = utc_now_iso()
    write_json(DECISIONS, dec)
    print(__import__("json").dumps(rec, indent=2))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a paper earnings decision")
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

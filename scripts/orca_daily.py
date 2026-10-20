#!/usr/bin/env python3
"""Daily Orca paper run (strategy v2). Public Kraken data only. No private/order calls.

  python scripts/orca_daily.py                 # print signal + marked book
  python scripts/orca_daily.py --apply         # rebalance if due, write ledger, append metrics
  python scripts/orca_daily.py --init 1000     # start a NEW ledger with EUR capital (refuses if one exists)
  python scripts/orca_daily.py --apply --force # rebalance today regardless of weekly cadence
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orca import strategy_v2 as S  # noqa: E402

LEDGER = ROOT / "state" / "ledger.json"
DEMO_LEDGER = ROOT / "demo" / "state" / "ledger.json"
METRICS = ROOT / "logs" / "metrics.jsonl"


def save(led: dict) -> None:
    if led.get("mode") != "paper":
        raise RuntimeError("refusing to write non-paper ledger")
    LEDGER.write_text(json.dumps(led, indent=2) + "\n")
    DEMO_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LEDGER, DEMO_LEDGER)  # Vercel root is demo/


def main() -> int:
    if os.environ.get("ORCA_MODE", "paper") != "paper":
        raise SystemExit("paper only")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--init", type=float, metavar="EUR")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    day = now.date()
    fx = S.eur_usd()
    if a.init:
        if LEDGER.exists() and json.loads(LEDGER.read_text()).get("strategy_version", "").startswith("v2"):
            raise SystemExit("v2 ledger already exists; refusing to overwrite")
        led = {"mode": "paper", "strategy_version": S.VERSION, "base_currency": "EUR", "quote_currency": "USD",
               "note": "Equity reported in EUR; trades in USD pairs, converted at Kraken EUR/USD.",
               "start_date": day.isoformat(), "start_capital_eur": a.init, "eur_usd_start": fx,
               "cash_usd": round(a.init * fx, 4), "positions": {}, "fills": [], "last_rebalance": None,
               "last_reason": "New ledger.", "equity_history": []}
        save(led)
    led = json.loads(LEDGER.read_text())
    sig = S.compute_signal(day)
    quotes = {b: S.ticker(p) for b, p in S.UNIVERSE.items()}
    due = a.force or S.is_rebalance_day(led, day)
    fills = []
    reason = (f"v2 weekly rebalance. BTC gate {sig['btc_gate']}; target "
              + ", ".join(f"{b} {w:.0%}" for b, w in sig["weights"].items() if w) + f", cash {sig['cash_weight']:.0%}.")
    if sig["news_notes"]:
        reason += " News: " + "; ".join(sig["news_notes"])
    if a.apply and due:
        fills = S.rebalance(led, sig["weights"], quotes, fx, now.strftime("%Y-%m-%dT%H:%M:%SZ"), reason, force=a.force)
        led["last_rebalance"] = day.isoformat()
        led["last_reason"] = reason
        led["last_target"] = sig["weights"]
    book = S.mark(led, {b: q["last"] for b, q in quotes.items()}, fx)
    out = {"day": day.isoformat(), "rebalance_due": due, "signal": sig, "book": book, "fills": fills}
    if a.apply:
        hist = [h for h in led.get("equity_history", []) if h["day"] != day.isoformat()]
        hist.append({"day": day.isoformat(), "equity_eur": book["equity_eur"]})
        led["equity_history"] = hist
        save(led)
        METRICS.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": now.isoformat(), "day": day.isoformat(), "mode": "paper", "strategy": S.VERSION,
               "rebalanced": bool(due), "n_fills": len(fills), "fees_usd": round(sum(f["fee_usd"] for f in fills), 4),
               "target": sig["weights"], "weights": book["weights"], "equity_eur": book["equity_eur"],
               "equity_usd": book["equity_usd"], "return_pct": book["return_pct"], "eur_usd": fx,
               "btc_gate": sig["btc_gate"], "news_notes": sig["news_notes"]}
        with METRICS.open("a") as f:
            f.write(json.dumps(row) + "\n")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

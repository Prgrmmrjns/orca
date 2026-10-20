#!/usr/bin/env python3
"""Daily paper signal for Orca. Public Kraken prices only. No private/order calls.

Usage:
  python scripts/orca_daily.py              # print today's signal
  python scripts/orca_daily.py --apply      # append paper fill(s) + metrics line, write ledger

Chewbacca runs this daily and commits state/ledger.json (+ logs/metrics.jsonl).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
sys.path.insert(0, str(DEMO))

os.environ.setdefault("ORCA_MODE", "paper")

from live import (  # noqa: E402
    apply_signal,
    compute_signal,
    headlines,
    ledger_path,
    load_ledger,
    mark_book,
    ranks,
    require_paper,
    save_ledger,
    _eur_usd,
)


def metrics_path() -> Path:
    return ROOT / "logs" / "metrics.jsonl"


def append_metrics(row: dict) -> None:
    path = metrics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def main() -> int:
    require_paper()
    parser = argparse.ArgumentParser(description="Orca paper daily signal")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append paper fill(s) to state/ledger.json and a line to logs/metrics.jsonl",
    )
    parser.add_argument(
        "--day",
        default=None,
        help="Override signal day YYYY-MM-DD (default: today UTC)",
    )
    args = parser.parse_args()

    day = date.fromisoformat(args.day) if args.day else datetime.now(timezone.utc).date()
    ledger = load_ledger()
    holding = (ledger.get("holding") or {}).get("base")
    last_check_raw = ledger.get("last_check")
    last_check = date.fromisoformat(last_check_raw) if last_check_raw else None

    books = ranks()
    news = headlines()
    eur_usd = _eur_usd()
    signal = compute_signal(books, news, day=day, holding=holding, last_check=last_check)
    book = mark_book(ledger, eur_usd)

    out = {
        "mode": "paper",
        "day": signal["day"],
        "signal": signal,
        "book": book,
        "ledger_path": str(ledger_path()),
        "apply": bool(args.apply),
    }

    if args.apply:
        result = apply_signal(ledger, signal, books, eur_usd)
        ledger = result["ledger"]
        # Always advance last_check on weekly days even without a trade.
        if signal.get("weekly_check") and signal.get("last_check"):
            ledger["last_check"] = signal["last_check"]
        save_ledger(ledger)
        book = mark_book(ledger, eur_usd)
        metrics = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "day": signal["day"],
            "mode": "paper",
            "target": signal.get("target"),
            "holding": (ledger.get("holding") or {}).get("base"),
            "changed": signal.get("changed"),
            "traded": result["traded"],
            "fills": result["fills"],
            "reason": signal.get("reason"),
            "equity_eur": book["equity_eur"],
            "equity_usd": book["equity_usd"],
            "return_pct": book["return_pct"],
            "cash_usd": book["cash_usd"],
            "eur_usd": book["eur_usd"],
        }
        append_metrics(metrics)
        out["applied"] = metrics
        out["book"] = book

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

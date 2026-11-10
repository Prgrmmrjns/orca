#!/usr/bin/env python3
"""python -m stocks <scan|engine|decide|init> ..."""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("usage: python -m stocks <scan|engine|decide|init> [args...]")
        return 0
    cmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if cmd == "scan":
        from stocks.scanner import main as m
        return m()
    if cmd == "engine":
        from stocks.engine import main as m
        return m()
    if cmd == "decide":
        from stocks.decide import main as m
        return m()
    if cmd == "init":
        from stocks.engine import empty_ledger
        from stocks.paths import DECISIONS, LEDGER, write_json

        write_json(LEDGER, empty_ledger())
        write_json(DECISIONS, {"mode": "paper", "decisions": [], "updated_at": None})
        print(f"initialized {LEDGER}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

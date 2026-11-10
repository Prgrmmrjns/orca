#!/usr/bin/env python3
"""python -m stocks <scan|news|engine|decide|init|replay> ..."""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("usage: python -m stocks <scan|news|engine|decide|init|replay> [args...]")
        return 0
    cmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if cmd == "scan":
        from stocks.scanner import main as m

        return m()
    if cmd == "news":
        from stocks.news_scan import main as m

        return m()
    if cmd == "engine":
        from stocks.engine import main as m

        return m()
    if cmd == "decide":
        from stocks.decide import main as m

        return m()
    if cmd == "init":
        from stocks.engine import init_all_books

        created = init_all_books(force=True)
        print(f"initialized books: {created}")
        return 0
    if cmd == "replay":
        from stocks.replay import main as m

        return m()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

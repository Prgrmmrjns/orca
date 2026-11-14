#!/usr/bin/env python3
"""python -m crypto <engine|train|data> ..."""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("usage: python -m crypto <engine|train|data> [args...]")
        return 0
    cmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if cmd == "engine":
        from crypto.engine import main as m

        return m()
    if cmd == "train":
        from crypto.train import main as m

        return m()
    if cmd == "data":
        from crypto.data_panel import main as m

        return m()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

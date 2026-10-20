"""Re-export paper demo helpers from the FastAPI package (Vercel /api handlers)."""

from __future__ import annotations

import sys
from pathlib import Path

_DEMO = Path(__file__).resolve().parents[1]
if str(_DEMO) not in sys.path:
    sys.path.insert(0, str(_DEMO))

from live import (  # noqa: E402
    BACKTEST,
    BUDGET_EUR,
    EVERY,
    PAIRS,
    PROMPT,
    SPREAD,
    START,
    STICKY,
    SUB_EUR,
    apply_signal,
    compute_signal,
    headlines,
    ledger_path,
    load_ledger,
    mark_book,
    open_price,
    paper_fill,
    ranks,
    require_paper,
    save_ledger,
    snapshot,
    ticker_last,
)

__all__ = [
    "BACKTEST",
    "BUDGET_EUR",
    "EVERY",
    "PAIRS",
    "PROMPT",
    "SPREAD",
    "START",
    "STICKY",
    "SUB_EUR",
    "apply_signal",
    "compute_signal",
    "headlines",
    "ledger_path",
    "load_ledger",
    "mark_book",
    "open_price",
    "paper_fill",
    "ranks",
    "require_paper",
    "save_ledger",
    "snapshot",
    "ticker_last",
]

"""Re-export paper viewer helpers for Vercel /api handlers."""
import sys
from pathlib import Path

_DEMO = Path(__file__).resolve().parents[1]
if str(_DEMO) not in sys.path:
    sys.path.insert(0, str(_DEMO))
from live import PAIRS, STRATEGY, headlines, ledger_path, load_ledger, require_paper, snapshot  # noqa: E402,F401

"""Paths for stocks paper state. Canonical under state/stocks/; mirrored to demo/ for Vercel."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "stocks"
DEMO_STATE = ROOT / "demo" / "state" / "stocks"
CACHE = ROOT / "stocks" / "data" / "cache"

CANDIDATES = STATE / "candidates.json"
DECISIONS = STATE / "decisions.json"
LEDGER = STATE / "ledger.json"


def ensure_dirs() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    DEMO_STATE.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def write_json(path: Path, data) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    # Mirror into demo/ so Vercel (root=demo/) can serve it
    if path.is_relative_to(STATE):
        rel = path.relative_to(STATE)
        dest = DEMO_STATE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)

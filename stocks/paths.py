"""Paths for stocks paper state. Canonical under state/stocks/; mirrored to demo/ for Vercel."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "stocks"
DEMO_STATE = ROOT / "demo" / "state" / "stocks"
CACHE = ROOT / "stocks" / "data" / "cache"
BOOKS_DIR = STATE / "books"
DEMO_BOOKS_DIR = DEMO_STATE / "books"

# Shared across all books
CANDIDATES = STATE / "candidates.json"
NEWS = STATE / "news.json"

# Legacy single-book paths (migrated into books/agent/)
LEGACY_DECISIONS = STATE / "decisions.json"
LEGACY_LEDGER = STATE / "ledger.json"


def ensure_dirs() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    DEMO_STATE.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)


def book_dir(book_id: str) -> Path:
    return BOOKS_DIR / book_id


def demo_book_dir(book_id: str) -> Path:
    return DEMO_BOOKS_DIR / book_id


def ledger_path(book_id: str) -> Path:
    return book_dir(book_id) / "ledger.json"


def decisions_path(book_id: str) -> Path:
    return book_dir(book_id) / "decisions.json"


# Backward-compat aliases for the agent book (decide CLI / old imports)
DECISIONS = decisions_path("agent")
LEDGER = ledger_path("agent")


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

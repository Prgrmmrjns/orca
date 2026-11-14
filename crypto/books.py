"""Paper book specs. Every book is mode=paper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from crypto.spec import ASSETS, START_CAPITAL_EUR

ROOT = Path(__file__).resolve().parents[1]
BOOK_DIR = ROOT / "config" / "crypto_books"


@dataclass(frozen=True)
class CryptoBook:
    id: str
    title: str
    workspace: str
    kind: str
    mode: str
    capital_eur: float
    bar: str
    universe: tuple[str, ...]
    checkpoint: str | None = None

    @property
    def ledger_dir(self) -> Path:
        return ROOT / "state" / "crypto" / "books" / self.id


def load_books(directory: Path | None = None) -> list[CryptoBook]:
    folder = Path(directory) if directory else BOOK_DIR
    books: list[CryptoBook] = []
    for path in sorted(folder.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        universe = tuple(raw.get("universe") or ASSETS)
        books.append(
            CryptoBook(
                id=str(raw["id"]),
                title=str(raw.get("title") or raw["id"]),
                workspace=str(raw["workspace"]),
                kind=str(raw["kind"]),
                mode=str(raw.get("mode") or "paper"),
                capital_eur=float(raw.get("capital_eur") or START_CAPITAL_EUR),
                bar=str(raw.get("bar") or "4h"),
                universe=universe,
                checkpoint=raw.get("checkpoint"),
            )
        )
    return books


def book_by_id(book_id: str, books: list[CryptoBook] | None = None) -> CryptoBook:
    books = books if books is not None else load_books()
    for book in books:
        if book.id == book_id:
            return book
    raise KeyError(book_id)

#!/usr/bin/env python3
"""Unit tests for multi-book rules, engine open/close, and agent-only decide path."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import (
    BOOK_IDS,
    BOOKS,
    match_beat5_raise,
    match_bigbeat_gap,
    match_guidance_beat,
    match_miss_short,
)
from stocks.replay import SAMPLE_CANDIDATES, assert_sample_rules, simulate_book


class RuleMatchTests(unittest.TestCase):
    def test_bigbeat_gap(self):
        self.assertTrue(
            match_bigbeat_gap(
                {"surprise_pct": 16, "gap_pct": 5.1, "spy_above_ma50": True}
            )
        )
        self.assertFalse(
            match_bigbeat_gap(
                {"surprise_pct": 16, "gap_pct": 5.1, "spy_above_ma50": False}
            )
        )
        self.assertFalse(
            match_bigbeat_gap(
                {"surprise_pct": 14, "gap_pct": 6, "spy_above_ma50": True}
            )
        )

    def test_guidance_beat_requires_release(self):
        self.assertTrue(
            match_guidance_beat(
                {
                    "surprise_pct": 9,
                    "guidance": "raised",
                    "timing": "BMO",
                    "filing_url": "http://x",
                    "raise_hits": "raises guidance",
                }
            )
        )
        self.assertFalse(
            match_guidance_beat(
                {"surprise_pct": 9, "guidance": "raised", "timing": "", "filing_url": ""}
            )
        )
        self.assertFalse(
            match_guidance_beat(
                {
                    "surprise_pct": 7,
                    "guidance": "raised",
                    "timing": "BMO",
                    "filing_url": "http://x",
                }
            )
        )

    def test_beat5_raise(self):
        self.assertTrue(match_beat5_raise({"surprise_pct": 5.1, "guidance": "raised"}))
        self.assertFalse(match_beat5_raise({"surprise_pct": 5.1, "guidance": "none"}))

    def test_miss_short(self):
        self.assertTrue(match_miss_short({"surprise_pct": -11, "gap_pct": -5.1}))
        self.assertFalse(match_miss_short({"surprise_pct": -11, "gap_pct": -4}))
        self.assertFalse(match_miss_short({"surprise_pct": -9, "gap_pct": -6}))


class ReplayTests(unittest.TestCase):
    def test_sample_assert(self):
        out = assert_sample_rules()
        self.assertTrue(out["ok"], out["errors"])

    def test_each_rule_book_closes(self):
        for book in BOOKS:
            if book.kind != "rule":
                continue
            if book.id == "news_event" or book.id.endswith("_tuned") or book.id in {
                "guidance_raise_score",
                "gap_filtered_guidance",
                "wide_capacity",
                "bigmove_unified_v1",
                "bigmove_eu_v1",
            }:
                continue  # news/config books — see test_strategy_config / test_tuned_books / test_bigmove
            sim = simulate_book(book, SAMPLE_CANDIDATES)
            self.assertEqual(sim["n_opened"], sim["n_closed"])
            self.assertGreaterEqual(sim["n_opened"], 1, book.id)

    def test_independent_overlap(self):
        """GUIDE matches both guidance_beat and beat5_raise independently."""
        guide = [c for c in SAMPLE_CANDIDATES if c["ticker"] == "GUIDE"][0]
        self.assertTrue(match_guidance_beat(guide))
        self.assertTrue(match_beat5_raise(guide))


class EngineBookIsolationTests(unittest.TestCase):
    def test_init_creates_five_books(self):
        from stocks import engine
        from stocks import paths

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state" / "stocks"
            demo = root / "demo" / "state" / "stocks"
            with mock.patch.object(paths, "ROOT", root), mock.patch.object(
                paths, "STATE", state
            ), mock.patch.object(paths, "DEMO_STATE", demo), mock.patch.object(
                paths, "BOOKS_DIR", state / "books"
            ), mock.patch.object(paths, "DEMO_BOOKS_DIR", demo / "books"), mock.patch.object(
                paths, "CANDIDATES", state / "candidates.json"
            ), mock.patch.object(paths, "LEGACY_LEDGER", state / "ledger.json"), mock.patch.object(
                paths, "LEGACY_DECISIONS", state / "decisions.json"
            ), mock.patch.object(engine, "eurusd", return_value=1.1):
                # rebind helpers that closed over old paths
                paths.ensure_dirs()
                created = engine.init_all_books(force=True)
                self.assertTrue(set(BOOK_IDS).issubset(set(created) | set(BOOK_IDS)))
                self.assertEqual(set(created), set(BOOK_IDS))
                for bid in BOOK_IDS:
                    led = json.loads((state / "books" / bid / "ledger.json").read_text())
                    self.assertEqual(led["start_capital_eur"], 1000.0)
                    self.assertEqual(led["cash_eur"], 1000.0)
                    self.assertEqual(led["book_id"], bid)
                    self.assertEqual(led["positions"], {})
                    self.assertEqual(led["closed_trades"], [])


if __name__ == "__main__":
    unittest.main()

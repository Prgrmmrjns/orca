#!/usr/bin/env python3
"""Tests for tuned news paper books (box-backtest configs → live paper)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import BOOK_BY_ID, BOOK_IDS, LEGACY_FORWARD_BOOK_IDS
from stocks.strategy_config import (
    TUNED_NEWS_BOOK_IDS,
    load_strategy_config,
    match_candidate_to_news_config,
    normalize_strategy_raw,
    parse_news_event_dict,
    score_size_multiplier,
    strategy_path,
)


UPLOADS = Path("/home/ubuntu/.cursor/projects/agent-repos-orca/uploads")
UPLOAD_MAP = {
    "guidance_raise_tuned": "guidance_raise_tuned_9cb6.yaml",
    "dilution_shorts_tuned": "dilution_shorts_tuned_47fa.yaml",
    "quality_longs_tuned": "quality_longs_tuned_d92a.yaml",
    "guidance_raise_score": "guidance_raise_score_bbcf.yaml",
    "gap_filtered_guidance": "gap_filtered_guidance_9cf2.yaml",
    "wide_capacity": "wide_capacity_0e52.yaml",
}


class TunedBooksRegistryTests(unittest.TestCase):
    def test_all_tuned_registered(self):
        for bid in TUNED_NEWS_BOOK_IDS:
            self.assertIn(bid, BOOK_IDS)
            self.assertTrue(strategy_path(bid).exists(), bid)
            book = BOOK_BY_ID[bid]
            self.assertEqual(book.kind, "rule")
            self.assertEqual(book.started, "2026-11-10")
            self.assertIsNotNone(book.matcher)

    def test_legacy_six_untouched_ids(self):
        for bid in LEGACY_FORWARD_BOOK_IDS:
            self.assertIn(bid, BOOK_IDS)


class TunedConfigLoadTests(unittest.TestCase):
    def test_loads_and_knobs(self):
        expectations = {
            "guidance_raise_tuned": dict(max_positions=15, hold=5, side="long", stop=None, cats=["guidance_raise"], sizing="fixed", gross=0.75),
            "dilution_shorts_tuned": dict(max_positions=8, hold=3, side="short", stop=None, cats=["dilution"], sizing="fixed", gross=0.40),
            "quality_longs_tuned": dict(max_positions=15, hold=5, side="long", stop=None, cats=["guidance_raise", "acquisition_target", "big_contract"], sizing="fixed", gross=0.75),
            "guidance_raise_score": dict(max_positions=8, hold=2, side="long", stop=None, cats=["guidance_raise"], sizing="score_scaled", gross=0.40),
            "gap_filtered_guidance": dict(max_positions=8, hold=5, side="long", stop=None, cats=["guidance_raise"], sizing="fixed", gross=0.40, min_gap=5.0),
            "wide_capacity": dict(max_positions=15, hold=2, side="long", stop=None, cats=["guidance_raise", "acquisition_target", "big_contract"], sizing="fixed", gross=0.75),
        }
        for bid, exp in expectations.items():
            cfg = load_strategy_config(bid)
            self.assertEqual(cfg.book_id, bid)
            self.assertEqual(cfg.max_positions, exp["max_positions"])
            self.assertAlmostEqual(cfg.cost_per_fill_pct, 0.005)
            self.assertAlmostEqual(cfg.max_gross_exposure_pct, exp["gross"])
            self.assertEqual(cfg.sizing, exp["sizing"])
            self.assertEqual(len(cfg.rules), 1)
            r = cfg.rules[0]
            self.assertEqual(r.side, exp["side"])
            self.assertEqual(r.hold.trading_days(), exp["hold"])
            self.assertIsNone(r.exits.stop_pct)
            self.assertEqual(r.entry, "next_open")
            self.assertAlmostEqual(r.size_pct, 0.05)
            self.assertEqual(r.exclude_tags, [])
            self.assertEqual(set(r.categories), set(exp["cats"]))
            if "min_gap" in exp:
                self.assertEqual(r.min_gap_pct, exp["min_gap"])

    def test_backtest_upload_normalizes(self):
        """Attached box YAMLs parse to the same knobs as committed Orca configs."""
        if not UPLOADS.exists():
            self.skipTest("upload fixtures not present")
        for bid, fname in UPLOAD_MAP.items():
            raw = yaml.safe_load((UPLOADS / fname).read_text())
            from_box = parse_news_event_dict(raw, default_book_id=bid)
            from_repo = load_strategy_config(bid)
            self.assertEqual(from_box.max_positions, from_repo.max_positions, bid)
            self.assertAlmostEqual(from_box.cost_per_fill_pct, from_repo.cost_per_fill_pct, places=6, msg=bid)
            self.assertAlmostEqual(
                from_box.max_gross_exposure_pct, from_repo.max_gross_exposure_pct, places=6, msg=bid
            )
            self.assertEqual(from_box.sizing, from_repo.sizing, bid)
            self.assertEqual(from_box.allow_shorts, from_repo.allow_shorts, bid)
            br, rr = from_box.rules[0], from_repo.rules[0]
            self.assertEqual(br.side, rr.side, bid)
            self.assertEqual(set(br.categories), set(rr.categories), bid)
            self.assertEqual(br.hold.trading_days(), rr.hold.trading_days(), bid)
            self.assertAlmostEqual(br.size_pct, rr.size_pct, places=6, msg=bid)
            self.assertEqual(br.entry, rr.entry, bid)
            self.assertEqual(br.exits.stop_pct, rr.exits.stop_pct, bid)
            self.assertEqual(br.min_gap_pct, rr.min_gap_pct, bid)


class MatchAndSizingTests(unittest.TestCase):
    def test_guidance_from_classifier(self):
        cfg = load_strategy_config("guidance_raise_tuned")
        m = match_candidate_to_news_config(
            {"ticker": "ACME", "guidance": "raised", "news_event_type": "earnings"},
            cfg,
        )
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "long")
        self.assertTrue(m.prefer_open)
        self.assertEqual(m.hold_days, 5)
        self.assertIsNone(m.stop_loss)

    def test_dilution_short(self):
        cfg = load_strategy_config("dilution_shorts_tuned")
        m = match_candidate_to_news_config(
            {"ticker": "DILU", "news_event_type": "dilution"},
            cfg,
        )
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "short")
        self.assertEqual(m.hold_days, 3)

    def test_gap_filter(self):
        cfg = load_strategy_config("gap_filtered_guidance")
        low = match_candidate_to_news_config(
            {
                "ticker": "X",
                "news_event_type": "guidance_raise",
                "gap_pct": 4.0,
            },
            cfg,
        )
        hi = match_candidate_to_news_config(
            {
                "ticker": "X",
                "news_event_type": "guidance_raise",
                "gap_pct": 5.5,
            },
            cfg,
        )
        self.assertIsNone(low)
        self.assertIsNotNone(hi)

    def test_score_scaled_size(self):
        self.assertAlmostEqual(score_size_multiplier(10.0, None, 10.0), 1.0)
        self.assertAlmostEqual(score_size_multiplier(20.0, None, 10.0), 2.0)
        self.assertAlmostEqual(score_size_multiplier(1.0, None, 10.0), 0.25)
        self.assertAlmostEqual(score_size_multiplier(None, 15.0, 10.0), 1.5)
        cfg = load_strategy_config("guidance_raise_score")
        m = match_candidate_to_news_config(
            {
                "ticker": "S",
                "news_event_type": "guidance_raise",
                "gap_pct": 20.0,
                "surprise_pct": 5.0,
            },
            cfg,
        )
        self.assertIsNotNone(m)
        # base 5% * 2.0 cap = 10%
        self.assertAlmostEqual(m.size_pct, 0.10, places=4)

    def test_normalize_backtest_percents(self):
        raw = normalize_strategy_raw(
            {
                "name": "demo",
                "book": {
                    "max_positions": 8,
                    "max_gross_exposure_pct": 40.0,
                    "cost_per_fill_pct": 0.5,
                    "sizing": "fixed",
                },
                "rules": [
                    {
                        "name": "r",
                        "categories": ["dilution"],
                        "side": "short",
                        "thresholds": {"exclude_tags": []},
                        "entry": {"style": "next_open"},
                        "size_pct": 5.0,
                        "hold": {"unit": "days", "value": 3},
                        "exits": {"stop_pct": None},
                    }
                ],
            }
        )
        cfg = parse_news_event_dict(raw, default_book_id="demo")
        self.assertAlmostEqual(cfg.cost_per_fill_pct, 0.005)
        self.assertAlmostEqual(cfg.max_gross_exposure_pct, 0.40)
        self.assertAlmostEqual(cfg.rules[0].size_pct, 0.05)
        self.assertEqual(cfg.rules[0].entry, "next_open")
        self.assertEqual(cfg.rules[0].exclude_tags, [])


if __name__ == "__main__":
    unittest.main()

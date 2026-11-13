#!/usr/bin/env python3
"""Tests for bigmove_unified_v1 config, matching, extended-hours cost, classification."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import BOOK_BY_ID, BOOK_IDS
from stocks.bigmove import classify_bigmove_category
from stocks.engine import _fill_cost_pct
from stocks.strategy_config import (
    BIGMOVE_BOOK_ID,
    allows_extended_hours,
    load_strategy_config,
    match_candidate_to_news_config,
    strategy_path,
)


class BigmoveRegistryTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn(BIGMOVE_BOOK_ID, BOOK_IDS)
        self.assertTrue(strategy_path(BIGMOVE_BOOK_ID).exists())
        book = BOOK_BY_ID[BIGMOVE_BOOK_ID]
        self.assertEqual(book.kind, "rule")
        self.assertEqual(book.started, "2026-11-13")
        self.assertFalse(book.prefer_open)
        self.assertEqual(book.max_open, 20)

    def test_existing_twelve_untouched(self):
        legacy = [
            "agent",
            "bigbeat_gap",
            "guidance_beat",
            "beat5_raise",
            "miss_short",
            "news_event",
            "guidance_raise_tuned",
            "dilution_shorts_tuned",
            "quality_longs_tuned",
            "guidance_raise_score",
            "gap_filtered_guidance",
            "wide_capacity",
        ]
        for bid in legacy:
            self.assertIn(bid, BOOK_IDS)
        self.assertEqual(len(legacy), 12)


class BigmoveConfigTests(unittest.TestCase):
    def test_load_knobs(self):
        cfg = load_strategy_config(BIGMOVE_BOOK_ID)
        self.assertEqual(cfg.book_id, BIGMOVE_BOOK_ID)
        self.assertEqual(cfg.started, "2026-11-13")
        self.assertEqual(cfg.max_positions, 20)
        self.assertAlmostEqual(cfg.cost_per_fill_pct, 0.005)
        self.assertAlmostEqual(cfg.extended_hours_extra_slippage_pct, 0.005)
        self.assertTrue(cfg.allow_extended_hours)
        self.assertTrue(cfg.allow_shorts)
        self.assertAlmostEqual(cfg.max_gross_exposure_pct, 1.0)
        self.assertEqual(cfg.universe.min_prev_close, 5)
        self.assertEqual(cfg.universe.min_adv20_usd, 10_000_000)
        self.assertTrue(cfg.universe.exclude_etfs)
        self.assertIsNotNone(cfg.mega_cap_shock)
        self.assertEqual(cfg.mega_cap_shock.action, "alert_only")
        self.assertEqual(len(cfg.rules), 5)
        names = {r.name for r in cfg.rules}
        self.assertEqual(
            names,
            {
                "offering_gapdown_short",
                "pr_pop_fade_short",
                "mna_gapdown_short",
                "analyst_gapdown_fade_long",
                "capitulation_fade_long",
            },
        )

    def test_allows_extended_hours(self):
        self.assertTrue(allows_extended_hours(BIGMOVE_BOOK_ID))
        self.assertFalse(allows_extended_hours("news_event", {"allow_extended_hours": False}))


class BigmoveMatchTests(unittest.TestCase):
    def test_offering_gapdown_short(self):
        cfg = load_strategy_config(BIGMOVE_BOOK_ID)
        c = {
            "ticker": "ABCD",
            "news_event_type": "offering",
            "gap_pct": -5.5,
            "last": 12.0,
            "prev_close": 12.7,
            "adv20_usd": 50_000_000,
            "news_source": "edgar_8k",
            "spy_above_ma50": True,
            "rule_tags": ["bigmove", "offering"],
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.rule.name, "offering_gapdown_short")
        self.assertEqual(m.side, "short")
        self.assertEqual(m.hold_days, 5)
        self.assertAlmostEqual(m.size_pct, 0.05)
        self.assertFalse(m.prefer_open)

    def test_pr_pop_requires_spy_above(self):
        cfg = load_strategy_config(BIGMOVE_BOOK_ID)
        base = {
            "ticker": "EFGH",
            "news_event_type": "other_8k",
            "gap_pct": 6.0,
            "last": 20.0,
            "prev_close": 18.8,
            "adv20_usd": 50_000_000,
            "news_source": "edgar_8k",
            "rule_tags": ["bigmove", "other_8k"],
        }
        miss = match_candidate_to_news_config({**base, "spy_above_ma50": False}, cfg)
        self.assertIsNone(miss)
        hit = match_candidate_to_news_config({**base, "spy_above_ma50": True}, cfg)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.rule.name, "pr_pop_fade_short")
        self.assertEqual(hit.side, "short")

    def test_capitulation_fade_long(self):
        cfg = load_strategy_config(BIGMOVE_BOOK_ID)
        c = {
            "ticker": "IJKL",
            "news_event_type": "no_news_found",
            "gap_pct": -12.0,
            "last": 8.0,
            "prev_close": 9.1,
            "adv20_usd": 20_000_000,
            "news_source": "move_trigger",
            "spy_above_ma50": False,
            "rule_tags": ["bigmove", "no_news_found"],
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.rule.name, "capitulation_fade_long")
        self.assertEqual(m.side, "long")

    def test_no_earnings_chase(self):
        cfg = load_strategy_config(BIGMOVE_BOOK_ID)
        c = {
            "ticker": "MNOP",
            "news_event_type": "guidance_raise",
            "gap_pct": 8.0,
            "last": 50.0,
            "prev_close": 46.0,
            "adv20_usd": 100_000_000,
            "news_source": "edgar_8k",
            "spy_above_ma50": True,
            "rule_tags": ["guidance_raise"],
        }
        self.assertIsNone(match_candidate_to_news_config(c, cfg))


class ClassifyTests(unittest.TestCase):
    def test_offering_and_no_news(self):
        self.assertEqual(
            classify_bigmove_category(event_type="dilution", title="prices public offering"),
            "offering",
        )
        self.assertEqual(
            classify_bigmove_category(event_type=None, has_news=False),
            "no_news_found",
        )
        self.assertEqual(
            classify_bigmove_category(event_type="guidance_raise", title="raises guidance"),
            "earnings_chase",
        )


class ExtendedCostTests(unittest.TestCase):
    def test_extra_slippage_outside_rth(self):
        led = {"cost_pct": 0.005, "extended_hours_extra_slippage_pct": 0.005}
        self.assertAlmostEqual(_fill_cost_pct(led, "open"), 0.005)
        self.assertAlmostEqual(_fill_cost_pct(led, "pre"), 0.01)
        self.assertAlmostEqual(_fill_cost_pct(led, "after"), 0.01)


class ExtendedFillTests(unittest.TestCase):
    def test_fill_pending_pre_session_for_bigmove(self):
        from stocks import engine as eng

        led = {
            "cash_eur": 1000.0,
            "equity_eur": 1000.0,
            "cost_pct": 0.005,
            "extended_hours_extra_slippage_pct": 0.005,
            "allow_extended_hours": True,
            "positions": {},
            "pending": [
                {
                    "id": "t1",
                    "ticker": "TEST",
                    "side": "short",
                    "size": 0.05,
                    "hold_days": 5,
                    "prefer_open": False,
                    "decided_at": "2026-11-13T12:00:00Z",
                    "reason": "test",
                }
            ],
        }
        with mock.patch("stocks.engine.market_session", return_value="pre"), mock.patch(
            "stocks.engine.allows_extended_hours", return_value=True
        ), mock.patch(
            "stocks.engine.fillable_quote",
            return_value={"last": 10.0, "session": "pre"},
        ), mock.patch(
            "stocks.engine.daily_bar", return_value={}
        ), mock.patch(
            "stocks.engine.mark_book",
            side_effect=lambda l, **kw: (l, {"TEST": 10.0}, 1.1),
        ), mock.patch(
            "stocks.engine._update_decision_status"
        ), mock.patch(
            "stocks.engine.add_trading_days", return_value=__import__("datetime").date(2026, 11, 20)
        ):
            fills = eng.fill_pending(led, {"TEST": 10.0}, 1.1, BIGMOVE_BOOK_ID)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["session"], "pre")
        self.assertAlmostEqual(fills[0]["cost_pct"], 0.01)
        self.assertIn("TEST", led["positions"])
        self.assertEqual(led["positions"]["TEST"]["entry_session"], "pre")


if __name__ == "__main__":
    unittest.main()

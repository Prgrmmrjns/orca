#!/usr/bin/env python3
"""Tests for bigmove_eu_v1 config, matching, Berlin session, classification."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import BOOK_BY_ID, BOOK_IDS
from stocks.bigmove_eu import classify_bigmove_eu_category
from stocks.eu_news import classify_eu_news
from stocks.market import berlin_market_session, is_eu_holiday, quote_to_eur_divisor
from stocks.strategy_config import (
    BIGMOVE_EU_BOOK_ID,
    allows_extended_hours,
    load_strategy_config,
    match_candidate_to_news_config,
    strategy_path,
    uses_eu_session,
)

BERLIN = ZoneInfo("Europe/Berlin")


class BigmoveEuRegistryTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn(BIGMOVE_EU_BOOK_ID, BOOK_IDS)
        self.assertTrue(strategy_path(BIGMOVE_EU_BOOK_ID).exists())
        book = BOOK_BY_ID[BIGMOVE_EU_BOOK_ID]
        self.assertEqual(book.kind, "rule")
        self.assertEqual(book.started, "2026-11-13")
        self.assertFalse(book.prefer_open)
        self.assertEqual(book.max_open, 20)

    def test_existing_books_untouched(self):
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
            "bigmove_unified_v1",
        ]
        for bid in legacy:
            self.assertIn(bid, BOOK_IDS)
        self.assertEqual(len(legacy), 13)
        self.assertEqual(len(BOOK_IDS), 14)


class BigmoveEuConfigTests(unittest.TestCase):
    def test_load_knobs(self):
        cfg = load_strategy_config(BIGMOVE_EU_BOOK_ID)
        self.assertEqual(cfg.book_id, BIGMOVE_EU_BOOK_ID)
        self.assertEqual(cfg.started, "2026-11-13")
        self.assertEqual(cfg.max_positions, 20)
        self.assertAlmostEqual(cfg.cost_per_fill_pct, 0.005)
        self.assertAlmostEqual(cfg.extended_hours_extra_slippage_pct, 0.005)
        self.assertFalse(cfg.allow_extended_hours)
        self.assertTrue(cfg.allow_shorts)
        self.assertEqual(cfg.universe.min_prev_close, 5)
        self.assertEqual(cfg.universe.min_adv20_usd, 10_000_000)
        enabled = [r.name for r in cfg.rules if r.enabled]
        self.assertEqual(
            set(enabled),
            {
                "earnings_gapdown_fade_long",
                "other_gapdown_fade_long",
                "capitulation_fade_long",
            },
        )
        disabled = [r.name for r in cfg.rules if not r.enabled]
        self.assertIn("pr_pop_fade_short", disabled)
        self.assertIn("offering_gapdown_short", disabled)

    def test_session_helpers(self):
        self.assertTrue(uses_eu_session(BIGMOVE_EU_BOOK_ID))
        self.assertFalse(allows_extended_hours(BIGMOVE_EU_BOOK_ID))
        open_t = datetime(2026, 11, 13, 10, 0, tzinfo=BERLIN)
        closed_t = datetime(2026, 11, 13, 18, 0, tzinfo=BERLIN)
        self.assertEqual(berlin_market_session(open_t), "open")
        self.assertEqual(berlin_market_session(closed_t), "closed")
        self.assertTrue(is_eu_holiday(datetime(2026, 12, 25, tzinfo=BERLIN).date()))


class BigmoveEuMatchTests(unittest.TestCase):
    def test_earnings_gapdown_long(self):
        cfg = load_strategy_config(BIGMOVE_EU_BOOK_ID)
        c = {
            "ticker": "SAP.DE",
            "news_event_type": "earnings_nosurprise",
            "gap_pct": -12.0,
            "last": 180.0,
            "prev_close": 205.0,
            "adv20_usd": 50_000_000,
            "news_source": "eqs_google",
            "spy_above_ma50": True,
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "long")
        self.assertEqual(m.rule.name, "earnings_gapdown_fade_long")

    def test_other_gapdown_long(self):
        cfg = load_strategy_config(BIGMOVE_EU_BOOK_ID)
        c = {
            "ticker": "BMW.DE",
            "news_event_type": "other_8k",
            "gap_pct": -11.0,
            "last": 70.0,
            "prev_close": 79.0,
            "adv20_usd": 40_000_000,
            "news_source": "wso_nachrichten",
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.rule.name, "other_gapdown_fade_long")

    def test_pr_pop_short_disabled(self):
        cfg = load_strategy_config(BIGMOVE_EU_BOOK_ID)
        c = {
            "ticker": "AIR.PA",
            "news_event_type": "other_8k",
            "gap_pct": 8.0,
            "last": 160.0,
            "prev_close": 148.0,
            "adv20_usd": 80_000_000,
            "news_source": "eqs_google",
            "spy_above_ma50": True,
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNone(m)

    def test_no_earnings_chase(self):
        cfg = load_strategy_config(BIGMOVE_EU_BOOK_ID)
        c = {
            "ticker": "SIE.DE",
            "news_event_type": "earnings_chase",
            "gap_pct": -15.0,
            "last": 150.0,
            "prev_close": 176.0,
            "adv20_usd": 60_000_000,
            "news_source": "eqs_google",
            "rule_tags": ["guidance_raise"],
        }
        m = match_candidate_to_news_config(c, cfg)
        self.assertIsNone(m)


class ClassifyEuTests(unittest.TestCase):
    def test_kapitalerhoehung(self):
        tags = classify_eu_news("Ad-hoc: Kapitalerhöhung und accelerated bookbuild", "")
        self.assertEqual(tags["eu_category"], "offering")

    def test_wpug(self):
        tags = classify_eu_news("WpÜG Pflichtangebot für Zielgesellschaft", "")
        self.assertEqual(tags["eu_category"], "mna")

    def test_gewinnwarnung(self):
        tags = classify_eu_news("Ad-hoc Gewinnwarnung senkt Ausblick", "")
        # guidance keywords may mark earnings_chase; Gewinnwarnung alone → other_8k
        self.assertIn(tags["eu_category"], {"other_8k", "earnings_chase", "earnings_nosurprise"})

    def test_classify_helper(self):
        self.assertEqual(
            classify_bigmove_eu_category(eu_category="offering", has_news=True),
            "offering",
        )
        self.assertEqual(
            classify_bigmove_eu_category(has_news=False),
            "no_news_found",
        )


class EuFxTests(unittest.TestCase):
    def test_eur_divisor(self):
        self.assertEqual(quote_to_eur_divisor("SAP.DE"), 1.0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for news_event YAML loader, validation, and default-behaviour parity."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import match_news_event
from stocks.strategy_config import (
    NEWS_EVENT_PATH,
    default_news_event_config,
    load_news_event_config,
    match_candidate_to_news_config,
    parse_news_event_dict,
)


# Categories the live 2026-11-10 book traded (pre-config hardcoding).
OLD_LONG = {"acquisition_target", "big_contract", "fda_approval", "guidance_raise"}
OLD_SHORT = {"fraud_probe", "guidance_cut", "short_seller", "dilution"}


class ParseValidationTests(unittest.TestCase):
    def test_default_file_loads(self):
        cfg = load_news_event_config()
        self.assertEqual(cfg.book_id, "news_event")
        self.assertEqual(cfg.max_positions, 6)
        self.assertEqual(cfg.cost_per_fill_pct, 0.005)
        self.assertTrue(cfg.allow_shorts)
        self.assertIsNone(cfg.cooldown_minutes)
        self.assertEqual(len(cfg.rules), 2)
        self.assertTrue(NEWS_EVENT_PATH.exists())

    def test_file_matches_baked_defaults(self):
        from_file = load_news_event_config().to_public_dict()
        baked = default_news_event_config().to_public_dict()
        # path is only on the loaded snapshot
        from_file.pop("path", None)
        baked.pop("path", None)
        self.assertEqual(from_file, baked)

    def test_rejects_empty_rules(self):
        with self.assertRaises(ValueError):
            parse_news_event_dict({"rules": []})

    def test_rejects_bad_side(self):
        with self.assertRaises(ValueError):
            parse_news_event_dict(
                {
                    "rules": [
                        {
                            "name": "x",
                            "categories": ["fda_approval"],
                            "side": "flat",
                        }
                    ]
                }
            )

    def test_rejects_bad_entry(self):
        with self.assertRaises(ValueError):
            parse_news_event_dict(
                {
                    "rules": [
                        {
                            "name": "x",
                            "categories": ["fda_approval"],
                            "side": "long",
                            "entry": "market_on_close",
                        }
                    ]
                }
            )

    def test_delay_minutes_entry_ok(self):
        cfg = parse_news_event_dict(
            {
                "rules": [
                    {
                        "name": "delayed",
                        "categories": ["fda_approval"],
                        "side": "long",
                        "entry": "delay_minutes:15",
                        "size_pct": 0.05,
                    }
                ]
            }
        )
        self.assertEqual(cfg.rules[0].entry, "delay_minutes:15")

    def test_missing_file_falls_back(self):
        missing = Path(tempfile.mkdtemp()) / "nope.yaml"
        cfg = load_news_event_config(missing)
        self.assertEqual(cfg.max_positions, 6)
        self.assertEqual(len(cfg.rules), 2)

    def test_invalid_yaml_falls_back(self):
        td = Path(tempfile.mkdtemp())
        bad = td / "bad.yaml"
        bad.write_text("rules: not-a-list\n")
        cfg = load_news_event_config(bad)
        self.assertEqual(cfg.book_id, "news_event")
        self.assertEqual(len(cfg.rules), 2)

    def test_custom_rule_roundtrip(self):
        raw = {
            "max_positions": 3,
            "max_per_ticker": 1,
            "max_gross_exposure_pct": 0.15,
            "cost_per_fill_pct": 0.005,
            "allow_shorts": False,
            "cooldown_minutes": 60,
            "rules": [
                {
                    "name": "fda_only",
                    "enabled": True,
                    "categories": ["fda_approval"],
                    "sources": ["8-K", "PRN"],
                    "side": "long",
                    "exclude_tags": ["shareholder_alert"],
                    "min_gap_pct": 2.0,
                    "entry": "next_open",
                    "size_pct": 0.08,
                    "hold": {"days": 3},
                    "exits": {
                        "stop_pct": 0.05,
                        "take_profit_pct": 0.12,
                        "trailing_stop_pct": 0.04,
                        "exit_at_close": False,
                    },
                }
            ],
        }
        td = Path(tempfile.mkdtemp())
        path = td / "news_event.yaml"
        path.write_text(yaml.safe_dump(raw))
        cfg = load_news_event_config(path)
        self.assertEqual(cfg.max_positions, 3)
        self.assertFalse(cfg.allow_shorts)
        self.assertEqual(cfg.cooldown_minutes, 60)
        r = cfg.rules[0]
        self.assertEqual(r.name, "fda_only")
        self.assertEqual(r.entry, "next_open")
        self.assertEqual(r.exits.take_profit_pct, 0.12)
        self.assertEqual(r.exits.trailing_stop_pct, 0.04)
        self.assertEqual(r.hold.trading_days(), 3)


class DefaultParityTests(unittest.TestCase):
    """Defaults must reproduce the pre-config hardcoded news_event behaviour."""

    def setUp(self):
        self.cfg = load_news_event_config()

    def test_book_level_knobs(self):
        self.assertEqual(self.cfg.max_positions, 6)
        self.assertEqual(self.cfg.max_per_ticker, 1)
        self.assertAlmostEqual(self.cfg.max_gross_exposure_pct, 0.30)
        self.assertAlmostEqual(self.cfg.cost_per_fill_pct, 0.005)
        self.assertTrue(self.cfg.allow_shorts)
        self.assertIsNone(self.cfg.cooldown_minutes)

    def test_long_categories(self):
        long_rules = [r for r in self.cfg.rules if r.side == "long" and r.enabled]
        cats = set()
        for r in long_rules:
            cats |= set(r.categories)
        self.assertEqual(cats, OLD_LONG)
        for r in long_rules:
            self.assertAlmostEqual(r.size_pct, 0.05)
            self.assertEqual(r.hold.trading_days(), 2)
            self.assertAlmostEqual(r.exits.stop_pct, 0.08)
            self.assertIsNone(r.exits.take_profit_pct)
            self.assertEqual(r.entry, "next_bar")
            self.assertIn("shareholder_alert", r.exclude_tags)

    def test_short_categories_include_short_seller_alias(self):
        short_rules = [r for r in self.cfg.rules if r.side == "short" and r.enabled]
        cats = set()
        for r in short_rules:
            cats |= r.category_set()
        self.assertTrue(OLD_SHORT.issubset(cats))
        self.assertIn("short_report", cats)
        self.assertIn("short_seller", cats)

    def test_match_long_fda(self):
        c = {
            "ticker": "ACME",
            "news_event_type": "fda_approval",
            "news_source": "prnewswire",
        }
        m = match_candidate_to_news_config(c, self.cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "long")
        self.assertAlmostEqual(m.size_pct, 0.05)
        self.assertEqual(m.hold_days, 2)
        self.assertAlmostEqual(m.stop_loss, 0.08)
        self.assertFalse(m.prefer_open)  # next_bar

    def test_match_short_fraud(self):
        c = {
            "ticker": "BAD",
            "news_event_type": "fraud_probe",
            "news_source": "edgar_8k",
        }
        m = match_candidate_to_news_config(c, self.cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "short")

    def test_short_seller_alias(self):
        c = {"ticker": "SHRT", "news_event_type": "short_seller"}
        m = match_candidate_to_news_config(c, self.cfg)
        self.assertIsNotNone(m)
        self.assertEqual(m.side, "short")

    def test_shareholder_alert_excluded(self):
        c = {
            "ticker": "NVDA",
            "news_event_type": "shareholder_alert",
            "rule_tags": ["shareholder_alert"],
        }
        self.assertIsNone(match_candidate_to_news_config(c, self.cfg))
        self.assertFalse(match_news_event(c))

    def test_earnings_not_traded_by_default(self):
        c = {
            "ticker": "EARN",
            "news_event_type": "earnings",
            "news_actionable": False,
            "news_side": "long",
        }
        self.assertIsNone(match_candidate_to_news_config(c, self.cfg))
        self.assertFalse(match_news_event(c))

    def test_books_matcher_parity(self):
        for et in OLD_LONG:
            c = {"ticker": "T", "news_event_type": et, "news_actionable": True, "news_side": "long"}
            self.assertTrue(match_news_event(c), et)
        for et in OLD_SHORT:
            c = {"ticker": "T", "news_event_type": et, "news_actionable": True, "news_side": "short"}
            self.assertTrue(match_news_event(c), et)

    def test_disabled_rule_skipped(self):
        raw = default_news_event_config().to_public_dict()
        for r in raw["rules"]:
            r["enabled"] = False
        cfg = parse_news_event_dict(raw)
        self.assertIsNone(
            match_candidate_to_news_config(
                {"ticker": "X", "news_event_type": "fda_approval"}, cfg
            )
        )

    def test_source_filter(self):
        raw = default_news_event_config().to_public_dict()
        for r in raw["rules"]:
            if r["side"] == "long":
                r["sources"] = ["8-K"]
        cfg = parse_news_event_dict(raw)
        ok = match_candidate_to_news_config(
            {
                "ticker": "X",
                "news_event_type": "fda_approval",
                "news_source": "edgar_8k",
            },
            cfg,
        )
        blocked = match_candidate_to_news_config(
            {
                "ticker": "X",
                "news_event_type": "fda_approval",
                "news_source": "cnbc_business",
            },
            cfg,
        )
        self.assertIsNotNone(ok)
        self.assertIsNone(blocked)


if __name__ == "__main__":
    unittest.main()

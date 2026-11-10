#!/usr/bin/env python3
"""Tests for news tagging + news_event matcher."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.books import match_news_event
from stocks.news_tagger import extract_tickers, tag_text


class NewsTaggerTests(unittest.TestCase):
    def test_fda_bullish(self):
        t = tag_text("Acme Pharma (ACME) Receives FDA Approval for Novel Therapy")
        self.assertEqual(t["event_type"], "fda_approval")
        self.assertEqual(t["news_side"], "long")
        self.assertTrue(t["actionable"])

    def test_fraud_bearish(self):
        t = tag_text("SEC Probe Into Accounting Fraud at Widgets Inc (WIDG)")
        self.assertEqual(t["event_type"], "fraud_probe")
        self.assertEqual(t["news_side"], "short")

    def test_dilution(self):
        t = tag_text("Company Announces $200 Million Public Offering of Common Stock")
        self.assertEqual(t["event_type"], "dilution")
        self.assertEqual(t["news_side"], "short")

    def test_guidance_raise(self):
        t = tag_text("Corp Raises Full-Year Guidance After Strong Quarter")
        self.assertEqual(t["event_type"], "guidance_raise")
        self.assertEqual(t["news_side"], "long")

    def test_ticker_extract(self):
        self.assertIn("AAPL", extract_tickers("Apple Inc (AAPL) beats estimates"))


class NewsEventMatchTests(unittest.TestCase):
    def test_long_actionable(self):
        c = {
            "ticker": "ACME",
            "news_actionable": True,
            "news_side": "long",
            "news_event_type": "fda_approval",
        }
        self.assertTrue(match_news_event(c))

    def test_rejects_non_actionable(self):
        c = {
            "ticker": "X",
            "news_actionable": False,
            "news_side": "long",
            "news_event_type": "earnings",
        }
        self.assertFalse(match_news_event(c))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for earnings calendar fetch + Yahoo/EDGAR fallback path."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.market import earnings_calendar, yahoo_earnings


class EarningsCalendarFallbackTests(unittest.TestCase):
    def test_falls_back_to_yahoo_when_nasdaq_fails(self):
        day = date(2026, 11, 11)
        yahoo_rows = [
            {
                "cal_date": day.isoformat(),
                "ticker": "COST",
                "name": "Costco",
                "time": "time-after-hours",
                "eps_reported": 4.0,
                "eps_estimate": 3.8,
                "surprise_pct": 5.26,
                "fiscal_quarter_ending": None,
                "market_cap": 1.0,
                "source": "yahoo",
            }
        ]

        with patch("stocks.market.nasdaq_earnings", side_effect=RuntimeError("blocked")):
            with patch("stocks.market.yahoo_earnings", return_value=yahoo_rows) as yf:
                with patch("stocks.market.edgar_8k_earnings") as edgar:
                    rows = earnings_calendar(day)
                    yf.assert_called_once_with(day)
                    edgar.assert_not_called()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["ticker"], "COST")
                    self.assertEqual(rows[0]["source"], "yahoo")

    def test_falls_back_to_edgar_when_nasdaq_and_yahoo_fail(self):
        day = date(2026, 11, 11)
        edgar_rows = [
            {
                "cal_date": day.isoformat(),
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "time": "",
                "eps_reported": None,
                "eps_estimate": None,
                "surprise_pct": None,
                "fiscal_quarter_ending": None,
                "market_cap": None,
                "source": "edgar_8k_2.02",
            }
        ]

        with patch("stocks.market.nasdaq_earnings", side_effect=RuntimeError("blocked")):
            with patch("stocks.market.yahoo_earnings", side_effect=RuntimeError("down")):
                with patch("stocks.market.edgar_8k_earnings", return_value=edgar_rows):
                    rows = earnings_calendar(day)
                    self.assertEqual(rows[0]["source"], "edgar_8k_2.02")
                    self.assertEqual(rows[0]["ticker"], "AAPL")

    def test_all_sources_fail_raises(self):
        day = date(2026, 11, 11)
        with patch("stocks.market.nasdaq_earnings", side_effect=RuntimeError("n")):
            with patch("stocks.market.yahoo_earnings", side_effect=RuntimeError("y")):
                with patch("stocks.market.edgar_8k_earnings", side_effect=RuntimeError("e")):
                    with self.assertRaises(RuntimeError) as ctx:
                        earnings_calendar(day)
                    msg = str(ctx.exception)
                    self.assertIn("all sources exhausted", msg)
                    self.assertIn("nasdaq", msg)
                    self.assertIn("yahoo", msg)

    def test_yahoo_parser_maps_rows(self):
        day = date(2026, 11, 11)
        raw_page = (
            None,
            [
                {
                    "ticker": "COST",
                    "companyshortname": "Costco Wholesale Corporation",
                    "startdatetimetype": "AMC",
                    "epsestimate": 3.8,
                    "epsactual": 4.0,
                    "epssurprisepct": 5.26,
                    "intradaymarketcap": 1e11,
                }
            ],
        )

        with patch("stocks.market._cache_load", return_value=None):
            with patch("stocks.market._cache_save"):
                with patch("stocks.market._yahoo_earnings_page", return_value=raw_page):
                    rows = yahoo_earnings(day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "COST")
        self.assertEqual(rows[0]["source"], "yahoo")
        self.assertEqual(rows[0]["time"], "time-after-hours")
        self.assertEqual(rows[0]["eps_estimate"], 3.8)
        self.assertEqual(rows[0]["eps_reported"], 4.0)


if __name__ == "__main__":
    raise SystemExit(unittest.main())

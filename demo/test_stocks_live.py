#!/usr/bin/env python3
"""API snapshot must never emit NaN/Inf (Starlette allow_nan=False → HTTP 500)."""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks_live import _book_summary, json_safe, snapshot  # noqa: E402


class JsonSafeTests(unittest.TestCase):
    def test_nan_inf_to_none(self):
        payload = {
            "ok": 1.5,
            "bad": float("nan"),
            "inf": float("inf"),
            "nested": [{"x": float("nan")}, {"y": 2.0}],
        }
        cleaned = json_safe(payload)
        self.assertEqual(cleaned["ok"], 1.5)
        self.assertIsNone(cleaned["bad"])
        self.assertIsNone(cleaned["inf"])
        self.assertIsNone(cleaned["nested"][0]["x"])
        self.assertEqual(cleaned["nested"][1]["y"], 2.0)
        # Strict JSON (same as Starlette JSONResponse)
        json.dumps(cleaned, allow_nan=False)


class SnapshotNanIsolationTests(unittest.TestCase):
    def test_snapshot_with_nan_ledger_is_strict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            books = state / "books" / "agent"
            books.mkdir(parents=True)
            (state / "candidates.json").write_text(
                json.dumps({"candidates": [], "rules": {}, "spy_above_ma50": True})
            )
            (state / "news.json").write_text(json.dumps({"items": [], "n_items": 0}))
            ledger = {
                "mode": "paper",
                "book_id": "agent",
                "strategy_title": "Agent",
                "kind": "agent",
                "start_capital_eur": 1000,
                "cash_eur": 1000,
                "equity_eur": 1000.2,
                "positions": {},
                "pending": [],
                "closed_trades": [],
                "equity_history": [
                    {"day": "2026-11-10", "equity_eur": 1000, "spy_eur": 1000.0},
                    {"day": "2026-11-11", "equity_eur": 1000.2, "spy_eur": float("nan")},
                ],
                "spy_history": [
                    {"day": "2026-11-10", "spy_eur": 1000.0, "spy_usd": 750.0, "eur_usd": 1.12},
                    {
                        "day": "2026-11-11",
                        "spy_eur": float("nan"),
                        "spy_usd": float("nan"),
                        "eur_usd": 1.11,
                    },
                ],
                "cost_pct": 0.005,
            }
            # Persist with Python's permissive dumps (mirrors polluted production files)
            (books / "ledger.json").write_text(json.dumps(ledger))
            (books / "decisions.json").write_text(json.dumps({"decisions": []}))

            # Second book raises inside summary → must not 500 whole snapshot
            bad_books = state / "books" / "miss_short"
            bad_books.mkdir(parents=True)
            (bad_books / "ledger.json").write_text(
                json.dumps(
                    {
                        "start_capital_eur": 1000,
                        "equity_eur": 1010,
                        "cash_eur": 900,
                        "positions": {"BROKEN": "not-a-dict"},
                        "pending": [],
                        "closed_trades": [],
                        "equity_history": [],
                    }
                )
            )
            (bad_books / "decisions.json").write_text(json.dumps({"decisions": []}))

            with mock.patch("stocks_live._state_dir", return_value=state):
                with mock.patch("stocks_live._strategy_configs", return_value={}):
                    data = snapshot()

            self.assertEqual(data["mode"], "paper")
            ids = {b["id"] for b in data["books"]}
            self.assertIn("agent", ids)
            self.assertIn("miss_short", ids)
            agent = next(b for b in data["books"] if b["id"] == "agent")
            # NaN scrubbed from histories
            for row in agent["equity_history"]:
                spy = row.get("spy_eur")
                if spy is not None:
                    self.assertTrue(math.isfinite(spy))
            for row in agent["spy_history"]:
                for k in ("spy_eur", "spy_usd"):
                    v = row.get(k)
                    if v is not None:
                        self.assertTrue(math.isfinite(v))
            # Strict encode must succeed (this is the live 500 root cause)
            body = json.dumps(data, allow_nan=False)
            self.assertNotIn("NaN", body)
            self.assertGreaterEqual(len(data["books"]), 2)

    def test_book_summary_skips_nan_spy_return(self):
        led = {
            "start_capital_eur": 1000,
            "equity_eur": 1000,
            "cash_eur": 1000,
            "positions": {},
            "pending": [],
            "closed_trades": [],
            "equity_history": [{"day": "2026-11-11", "equity_eur": 1000, "spy_eur": float("nan")}],
            "spy_history": [
                {"day": "2026-11-10", "spy_eur": 1000.0},
                {"day": "2026-11-11", "spy_eur": float("nan")},
            ],
        }
        out = _book_summary("agent", led, {"decisions": []})
        self.assertEqual(out["spy_return_pct"], 0.0)  # falls back to last finite 1000
        json.dumps(json_safe(out), allow_nan=False)


class WriteJsonSafeTests(unittest.TestCase):
    def test_paths_write_json_rejects_nan(self):
        from stocks.paths import json_safe as paths_safe, write_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            # Patch STATE relativity check by writing outside STATE — still sanitizes
            write_json(path, {"spy_eur": float("nan"), "ok": 1})
            loaded = json.loads(path.read_text())
            self.assertIsNone(loaded["spy_eur"])
            self.assertEqual(loaded["ok"], 1)
            self.assertEqual(paths_safe(float("nan")), None)


if __name__ == "__main__":
    unittest.main()

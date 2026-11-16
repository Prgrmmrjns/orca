"""Paper-only refusal, downside caps, and ask/bid fills."""

from __future__ import annotations

import json

import pytest

from crypto.books import load_books
from crypto.engine import run
from crypto.risk import PaperOnlyError, apply_downside_caps
from orca.strategy_v2 import BAND, CORR_HAIRCUT, MAX_WEIGHT, TARGET_VOL, rebalance


def test_books_are_paper_and_match_v2_limits():
    books = load_books()
    assert [b.id for b in books] == [
        "dl_patchtst_4h",
        "dl_tcn_regime_4h",
        "mcp_hybrid_agent",
        "v2_trend_core",
    ]
    assert {b.mode for b in books} == {"paper"}
    assert {b.workspace for b in books} == {
        "orca-dl-patchtst",
        "orca-dl-tcn",
        "orca-mcp-hybrid",
        "orca-v2-trend-core",
    }
    assert TARGET_VOL == 0.40
    assert MAX_WEIGHT == 0.50
    assert BAND == 0.05
    assert CORR_HAIRCUT == 0.85


def test_mcp_config_is_paper_services():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    args = raw["mcpServers"]["kraken"]["args"]
    assert args == ["mcp", "-s", "market,paper,feedback"]
    assert "env" not in raw["mcpServers"]["kraken"]


def test_engine_refuses_non_paper(monkeypatch):
    with pytest.raises(PaperOnlyError):
        run(apply=False, mode="live")
    monkeypatch.setenv("ORCA_MODE", "live")
    with pytest.raises(PaperOnlyError):
        run(apply=False, mode="paper")


def test_downside_caps_never_increase():
    weights = {"BTC": 0.40, "ETH": 0.20, "SOL": 0.10, "XRP": 0.05}
    capped = apply_downside_caps(weights, {"BTC": {"max_weight": 0.10, "until": "2099-01-01"}})
    assert capped["BTC"] == pytest.approx(0.10)
    assert capped["ETH"] == pytest.approx(0.20)
    assert capped["SOL"] == pytest.approx(0.10)
    assert capped["XRP"] == pytest.approx(0.05)
    assert sum(capped.values()) <= sum(weights.values()) + 1e-9


def test_paper_fills_use_ask_and_bid():
    ledger = {"cash_usd": 5000.0, "positions": {"ETH": {"units": 2.0, "avg_cost_usd": 100.0}}, "start_capital_eur": 1000.0}
    quotes = {
        "BTC": {"ask": 110.0, "bid": 100.0, "last": 105.0},
        "ETH": {"ask": 55.0, "bid": 50.0, "last": 52.0},
    }
    fills = rebalance(
        ledger,
        {"BTC": 0.5, "ETH": 0.0},
        quotes,
        fx=1.1,
        stamp="2026-01-01T00:00:00Z",
        reason="test",
        force=True,
    )
    by_side = {f["pair"]: f for f in fills}
    assert by_side["BTC/USD"]["side"] == "buy"
    assert by_side["BTC/USD"]["price"] == 110.0
    assert by_side["ETH/USD"]["side"] == "sell"
    assert by_side["ETH/USD"]["price"] == 50.0

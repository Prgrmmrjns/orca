"""Offline tests: python -m pytest newsperp/test_newsperp.py -q"""
import datetime as dt
import json

import pytest

from newsperp import collector as C
from newsperp.book import Book, new_ledger
from newsperp.classify import rule_classify
from newsperp.sources import match_tickers

CFG = json.loads((C.CFG / "book.json").read_text())
UNI = json.loads((C.CFG / "sources.json").read_text())["universe"]
T0 = dt.datetime(2026, 11, 23, 13, 55, tzinfo=dt.timezone.utc)

def tick(bid, ask, rate=0.0):
    return {"PF_NVDAXUSD": dict(bid=bid, ask=ask, mark=(bid + ask) / 2, funding_rel_per_hour=rate, suspended=False)}

def ev(): return dict(key="nvda_newsroom|x", event_type="leadership", lag_s=3.0, stale=False)
def imp(side="long", conf=0.8): return dict(symbol="NVDA", direction=side, confidence=conf, expected_move_pct=5, horizon_minutes=60)

def test_open_sizes_by_risk_and_pays_spread_fee_slippage():
    b = Book(new_ledger(CFG, T0), CFG, UNI)
    r = b.try_open(ev(), imp(), tick(136.0, 136.1), fx=1.0, t=T0)["opened"]
    # 1.5% risk / 3% stop = 50% of equity notional
    assert 495 < r["notional_eur"] <= 500.5
    assert r["price"] == pytest.approx(136.1 * 1.001)
    eq = b.equity_eur(tick(136.0, 136.1), 1.0)
    assert 997 < eq < 999.5                     # spread + slippage + 0.05% fee, nothing else

def test_stop_and_time_exit_and_pnl_sign():
    b = Book(new_ledger(CFG, T0), CFG, UNI)
    b.try_open(ev(), imp("short"), tick(136.0, 136.1), 1.0, T0)
    closed = b.update(tick(120.0, 120.1), 1.0, T0 + dt.timedelta(minutes=5))   # still inside hold, price fell -> no stop for a short
    assert not closed
    closed = b.update(tick(120.0, 120.1), 1.0, T0 + dt.timedelta(minutes=61))  # hold expired
    assert closed[0]["why"] == "time" and closed[0]["pnl_eur"] > 0
    b.try_open(dict(ev(), key="k2"), imp("long"), tick(136.0, 136.1), 1.0, T0)
    closed = b.update(tick(131.0, 131.1), 1.0, T0 + dt.timedelta(minutes=2))   # -3.7% -> stop
    assert closed[0]["why"] == "stop" and closed[0]["pnl_eur"] < 0
    assert not b.L["positions"]

def test_filters_confidence_stale_and_duplicates():
    b = Book(new_ledger(CFG, T0), CFG, UNI)
    assert "confidence" in b.try_open(ev(), imp(conf=0.5), tick(136, 136.1), 1.0, T0)["skipped"]
    assert "older" in b.try_open(dict(ev(), stale=True), imp(), tick(136, 136.1), 1.0, T0)["skipped"]
    b.try_open(ev(), imp(), tick(136, 136.1), 1.0, T0)
    assert b.try_open(ev(), imp(), tick(136, 136.1), 1.0, T0)["skipped"] == "already positioned"

def test_funding_long_pays_positive_rate():
    b = Book(new_ledger(CFG, T0), CFG, UNI)
    b.try_open(ev(), imp(), tick(100, 100), 1.0, T0)
    b.update(tick(100, 100, rate=0.001), 1.0, T0 + dt.timedelta(minutes=30))
    p = b.L["positions"]["NVDA"]
    assert p["funding_usd"] == pytest.approx(p["qty"] * 100 * 0.001 * 0.5, rel=1e-6)

def test_daily_loss_limit_halts_and_flattens():
    b = Book(new_ledger(CFG, T0), CFG, UNI)
    b.update(tick(100, 100), 1.0, T0)                       # sets day start equity
    cfg = dict(CFG, stop_pct={"default": 50.0}, max_position_frac=3.0, risk_per_trade=1.0)
    b.cfg = cfg
    b.try_open(ev(), dict(imp(), expected_move_pct=5), tick(100, 100), 1.0, T0)
    closed = b.update(tick(80, 80), 1.0, T0 + dt.timedelta(minutes=1))
    assert any(c["why"] == "daily loss limit" for c in closed) and T0.strftime("%Y-%m-%d") in b.L["halted_days"]

def test_rules_and_name_matching():
    r = rule_classify(dict(title="NVIDIA Board Reinstates Jensen Huang as President, CEO and Board Director",
                           summary="", tickers=["NVDA"]), "", ["NVDA"])
    assert r["impacts"][0]["direction"] == "long"
    r = rule_classify(dict(title="Acme raises full-year guidance", summary="", tickers=["NVDA"]), "", ["NVDA"])
    assert r["impacts"][0]["direction"] == "long"
    assert match_tickers("Trump says Jensen Huang should be back", UNI) == ["NVDA"]
    assert "AAPL" not in match_tickers("pineapple sales", UNI)

class FakePoller:
    def __init__(self, items): self.items, self.src, self.errors, self.stats, self.next_due = items, {"id": "fake", "llm": "never", "interval": 1}, 0, {}, 0
    def poll(self): return self.items
    def backoff(self): return 1

def test_baseline_then_only_new_items(tmp_path, monkeypatch):
    monkeypatch.setattr(C.market, "tickers", lambda perps: {})
    monkeypatch.setattr(C.market, "eurusd", lambda: 1.1)
    col = C.Collector(state_dir=tmp_path)
    old = [dict(id=f"a{i}", source="fake", title=f"old {i}", tickers=[], published=None) for i in range(3)]
    p = FakePoller(old)
    col.handle_items(p, old)
    assert not list((tmp_path / "events").glob("*.jsonl"))             # baseline: nothing emitted
    new = old + [dict(id="b1", source="fake", title="Bitcoin jumps", tickers=[], published=None)]
    col.handle_items(p, new)
    lines = next((tmp_path / "events").glob("*.jsonl")).read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["tickers"] == ["BTC"]
    col.handle_items(p, new)                                             # repeat poll: no duplicate
    assert len(next((tmp_path / "events").glob("*.jsonl")).read_text().splitlines()) == 1

def test_follow_signal_direction_and_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(C.market, "candles", lambda perp, a, b: [dict(time=(b - 120) * 1000, close="100")])
    col = C.Collector(state_dir=tmp_path)
    pub = dt.datetime(2026, 11, 23, 13, 55, tzinfo=dt.timezone.utc)
    it = dict(source="nvda_newsroom", tickers=["NVDA"])
    up = col.follow_impacts(it, pub, tick(105.0, 105.1), set())
    assert up and up[0]["direction"] == "long" and up[0]["confidence"] >= CFG["min_confidence"]
    assert col.follow_impacts(it, pub, tick(100.5, 100.6), set()) == []          # < 1% move
    assert col.follow_impacts(it, pub, tick(90.0, 90.1), {"NVDA"}) == []          # classifier already decided
    assert col.follow_impacts(dict(it, source="benzinga"), pub, tick(105, 105.1), set()) == []  # not official

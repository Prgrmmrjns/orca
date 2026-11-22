"""Offline tests: python -m pytest crypto/test_crypto.py"""
import numpy as np, pandas as pd
from crypto import live as L, strategy as S
from crypto.model import JsonModel

def _tick(**px): return {c: dict(bid=p * 0.999, ask=p * 1.001, last=p, vol_eur_24h=1e6) for c, p in px.items()}
PAIRS = {c: dict(pair=c + "EUR", ordermin=1e-5, costmin=0.5, lot_decimals=8) for c in ("BTC", "ETH", "SOL")}

def _led(): return dict(start_equity=1000.0, cash=1000.0, positions={}, trades=[], history=[], last_signal_date=None)

def test_buy_at_ask_sell_at_bid_and_exit():
    led = _led(); tk = _tick(BTC=50000, ETH=2000)
    L.rebalance(led, pd.Series({"BTC": 0.2, "ETH": 0.1}), tk, PAIRS, "d1")
    assert abs(led["cash"] - 700) < 0.01 and led["trades"][0]["price"] in (tk["BTC"]["ask"], tk["ETH"]["ask"])
    eq = L.equity(led, tk); assert 999 < eq < 1000          # paid the spread only
    L.rebalance(led, pd.Series({"BTC": 0.2}), tk, PAIRS, "d2")
    assert "ETH" not in led["positions"] and led["trades"][-1]["price"] == tk["ETH"]["bid"]
    assert led["cash"] >= 0

def test_band_blocks_small_changes():
    led = _led(); tk = _tick(BTC=50000)
    L.rebalance(led, pd.Series({"BTC": 0.2}), tk, PAIRS, "d1"); n = len(led["trades"])
    L.rebalance(led, pd.Series({"BTC": 0.22}), tk, PAIRS, "d2")
    assert len(led["trades"]) == n
    L.rebalance(led, pd.Series({"BTC": 0.3}), tk, PAIRS, "d3")
    assert len(led["trades"]) == n + 1

def test_never_spends_more_than_cash():
    led = _led(); tk = _tick(BTC=50000, ETH=2000, SOL=100)
    L.rebalance(led, pd.Series({"BTC": 0.5, "ETH": 0.5, "SOL": 0.5}), tk, PAIRS, "d1")
    assert led["cash"] >= -1e-9

def _panels(n=400, seed=0):
    rng = np.random.default_rng(seed); idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    cols = ["BTC"] + [f"C{i}" for i in range(24)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.04, (n, len(cols))), axis=0)), idx, cols)
    return close, close * 1.02, close * 0.98, pd.DataFrame(rng.uniform(1e5, 1e7, close.shape), idx, cols)

def test_weights_long_only_capped_and_gated():
    close, hi, lo, qv = _panels()
    un = S.universe(close, qv)
    X = S.feature_frame(close, hi, lo, qv, un)
    pred = pd.Series(JsonModel().predict(X), index=X.index).unstack().reindex_like(close)
    w, gate = S.target_weights(close, qv, pred)
    assert (w >= 0).all().all() and w.sum(axis=1).max() <= 1 + 1e-9 and w.max().max() <= 0.2 + 1e-9
    assert (w.sum(axis=1)[gate == 0] == 0).all()

def test_no_lookahead():
    close, hi, lo, qv = _panels()
    un = S.universe(close, qv); X = S.feature_frame(close, hi, lo, qv, un)
    k = 300; c2, h2, l2, q2 = (d.iloc[:k] for d in (close, hi, lo, qv))
    X2 = S.feature_frame(c2, h2, l2, q2, S.universe(c2, q2))
    d = c2.index[-1]
    pd.testing.assert_frame_equal(X.loc[[d]], X2.loc[[d]])

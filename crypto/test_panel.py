"""Panel alignment, causal features, and the daily cross-check. No network."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from crypto.data_panel import (
    align_assets,
    cross_check_closes,
    drop_unfinished,
    resample_ohlcv,
    to_grid,
)
from crypto.features import build_features


def _hour_frame(start: str, n: int, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = price + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.arange(1, n + 1, dtype=float),
        },
        index=idx,
    )


def test_resample_4h_ohlcv():
    df = _hour_frame("2024-01-01", 8, price=10.0)
    out = resample_ohlcv(df, 240)
    assert list(out.index) == [
        pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 04:00", tz="UTC"),
    ]
    first = out.iloc[0]
    assert first["open"] == df.iloc[0]["open"]
    assert first["high"] == df.iloc[:4]["high"].max()
    assert first["low"] == df.iloc[:4]["low"].min()
    assert first["close"] == df.iloc[3]["close"]
    assert first["volume"] == df.iloc[:4]["volume"].sum()


def test_drop_unfinished_current_bar():
    idx = pd.date_range("2024-06-01", periods=3, freq="4h", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0, 2.0, 3.0], "volume": 1.0}, index=idx)
    # 06:30 UTC is inside the 04:00 bar, so 04:00 is unfinished. 00:00 has closed.
    now = datetime(2024, 6, 1, 6, 30, tzinfo=timezone.utc)
    kept = drop_unfinished(df, 240, now=now)
    assert list(kept.index) == [pd.Timestamp("2024-06-01 00:00", tz="UTC")]


def test_align_inner_join_and_start():
    a = _hour_frame("2017-12-01", 24 * 40)
    b = _hour_frame("2018-01-05", 24 * 20, price=50.0)
    a4 = to_grid(resample_ohlcv(a, 240), 240, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    b4 = to_grid(resample_ohlcv(b, 240), 240, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    aligned = align_assets({"BTC": a4, "ETH": b4}, start="2018-01-01")
    assert aligned["BTC"].index.equals(aligned["ETH"].index)
    assert aligned["BTC"].index[0] >= pd.Timestamp("2018-01-05", tz="UTC")
    assert aligned["BTC"].index[0] == aligned["ETH"].index[0]


def test_features_are_causal():
    idx = pd.date_range("2020-01-01", periods=260, freq="4h", tz="UTC")
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {a: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))) for a in ("BTC", "ETH", "SOL", "XRP")},
        index=idx,
    )
    volume = pd.DataFrame({a: rng.uniform(1, 10, len(idx)) for a in close.columns}, index=idx)
    base = build_features(close, volume, interval="4h", sma_windows=(3, 5, 8))
    bumped = close.copy()
    bumped.iloc[-1, 0] *= 1.5
    other = build_features(bumped, volume, interval="4h", sma_windows=(3, 5, 8))
    # The shared history, five bars before the last kept row, does not see the last close.
    assert base["features"].shape[-1] == 11
    assert np.allclose(base["features"][-6], other["features"][-6])
    assert not np.allclose(base["features"][-1], other["features"][-1])


def test_cross_check_against_closes_csv(tmp_path):
    idx = pd.date_range("2024-01-01", periods=12, freq="4h", tz="UTC")
    close = np.linspace(100, 110, len(idx))
    frame = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)
    ref = pd.DataFrame({"BTC": [close[5], close[-1]]}, index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    path = tmp_path / "closes.csv"
    ref.to_csv(path)
    report = cross_check_closes({"BTC": frame}, path)
    assert report["present"] is True
    assert report["pairs"]["BTC"]["overlap"] == 2
    assert report["pairs"]["BTC"]["median_abs_pct"] == 0.0

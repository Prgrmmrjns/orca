"""Causal features. Every value at t uses bars at or before t only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto.spec import (
    ASSETS,
    FEATURE_NAMES,
    SMA_WINDOWS,
    periods_per_year,
    vol_window_bars,
)


def _log_return(close: pd.Series, span: int) -> pd.Series:
    return np.log(close / close.shift(span))


def _rolling_z(series: pd.Series, window: int) -> pd.Series:
    mu = series.rolling(window, min_periods=window).mean()
    sd = series.rolling(window, min_periods=window).std(ddof=1)
    return (series - mu) / sd.replace(0.0, np.nan)


def annualized_close_vol(close: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Trailing realised vol of one-bar log returns, annualised. Causal."""
    window = vol_window_bars(interval)
    log_r = np.log(close / close.shift(1))
    return log_r.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(periods_per_year(interval))


def build_features(
    closes: pd.DataFrame,
    volume: pd.DataFrame,
    *,
    interval: str = "4h",
    sma_windows: tuple[int, ...] = SMA_WINDOWS,
    assets: tuple[str, ...] = ASSETS,
    sma_asset: str = "BTC",
) -> dict:
    """Build the model tensor.

    ``closes`` and ``volume`` are aligned frames with one column per asset,
    oldest row first. Rows that still contain a warmup NaN are dropped, which
    also drops the unfinished-bar problem if the caller already removed it.

    Returns dict with ``features`` (T, A, F), ``vol`` (T, A), ``simple`` (T, A),
    ``index``, ``assets``, ``names``.
    """
    assets = tuple(assets)
    close = closes.loc[:, list(assets)].astype(float).sort_index()
    vol_px = volume.loc[:, list(assets)].astype(float).reindex(close.index).sort_index()
    log1 = _log_return(close, 1)
    spans = (1, 6, 42, 180)
    parts: dict[str, pd.DataFrame] = {}
    for span in spans:
        parts[f"log_ret_{span}"] = _log_return(close, span)
    rv_s = log1.rolling(6, min_periods=6).std(ddof=1)
    rv_l = log1.rolling(42, min_periods=42).std(ddof=1)
    parts["rv_ratio"] = rv_s / rv_l.replace(0.0, np.nan)
    parts["volume_z"] = vol_px.apply(lambda s: _rolling_z(s, 42))
    parts["xs_rank"] = parts["log_ret_6"].rank(axis=1, pct=True)
    if sma_asset not in close.columns:
        raise KeyError(f"{sma_asset} column is required for SMA distance features")
    btc = close[sma_asset]
    for n in sma_windows:
        dist = btc / btc.rolling(n, min_periods=n).mean() - 1.0
        parts[f"btc_dist_sma{n}"] = pd.DataFrame({a: dist for a in assets}, index=close.index)
    parts["ret_z"] = log1.apply(lambda s: _rolling_z(s, 42))

    # Stack in FEATURE_NAMES order. SMA names in the spec are the 50/100/200
    # defaults; custom windows still travel in sma_windows order under those
    # three slots when the caller passes exactly three windows.
    names = list(FEATURE_NAMES)
    if tuple(sma_windows) != tuple(SMA_WINDOWS):
        names = [
            f"btc_dist_sma{sma_windows[0]}" if n == "btc_dist_sma50" else
            f"btc_dist_sma{sma_windows[1]}" if n == "btc_dist_sma100" else
            f"btc_dist_sma{sma_windows[2]}" if n == "btc_dist_sma200" else n
            for n in FEATURE_NAMES
        ]
    mats = []
    for name in names:
        frame = parts[name]
        if isinstance(frame, pd.Series):
            frame = pd.DataFrame({a: frame for a in assets}, index=close.index)
        mats.append(frame.loc[:, list(assets)].to_numpy(dtype=np.float64))
    feats = np.stack(mats, axis=-1)  # (T, A, F)
    vol = annualized_close_vol(close, interval).to_numpy(dtype=np.float64)
    simple = close.pct_change().to_numpy(dtype=np.float64)
    valid = np.isfinite(feats).all(axis=(1, 2)) & np.isfinite(vol).all(axis=1) & np.isfinite(simple).all(axis=1)
    idx = close.index[valid]
    return {
        "features": feats[valid],
        "vol": vol[valid],
        "simple": simple[valid],
        "index": idx,
        "assets": assets,
        "names": tuple(names),
        "interval": interval,
        "sma_asset": sma_asset,
    }

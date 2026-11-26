"""Signal sleeves. Every function returns target weights (fraction of equity) using data up to each row's close."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent.costs import TRAD

CRYPTO_CAP, TRAD_CAP, GROSS_CAP = 0.5, 1.0, 2.0


def ann_vol(close: pd.DataFrame, span: int = 60) -> pd.DataFrame:
    return close.pct_change(fill_method=None).ewm(span=span, min_periods=span // 2).std() * np.sqrt(365)


def cap(w: pd.DataFrame, gross: float = GROSS_CAP) -> pd.DataFrame:
    caps = pd.Series({c: TRAD_CAP if c in TRAD else CRYPTO_CAP for c in w.columns})
    w = w.clip(lower=-caps, upper=caps, axis=1)
    g = w.abs().sum(axis=1)
    return w.mul((gross / g).clip(upper=1).fillna(0), axis=0)


def vol_scale(close: pd.DataFrame, w: pd.DataFrame, target: float, lookback: int = 90, max_lev: float = 3.0) -> pd.DataFrame:
    """Scale weights so ex-ante portfolio vol (rolling covariance) ~= target. Recomputed weekly to save time."""
    R = close[w.columns].pct_change(fill_method=None).fillna(0)
    scale = pd.Series(np.nan, index=w.index)
    for i in range(lookback, len(w), 7):
        cov = R.iloc[i - lookback:i].cov().values * 365
        x = w.iloc[i].values
        v = float(np.sqrt(max(x @ cov @ x, 0)))
        scale.iloc[i] = min(target / v, max_lev) if v > 0 else 0
    return w.mul(scale.ffill().fillna(0), axis=0)


def trend(close: pd.DataFrame, assets: list[str], lookbacks=(30, 90, 180), long_only=False,
          short_crypto_mult=1.0, target_vol=0.20) -> pd.DataFrame:
    """Time-series momentum: signal = mean sign of trailing returns; inverse-vol weights; portfolio vol target."""
    c = close[assets]
    sig = sum(np.sign(c.pct_change(L, fill_method=None)) for L in lookbacks) / len(lookbacks)
    if long_only:
        sig = sig.clip(lower=0)
    else:
        mult = pd.Series({a: 1.0 if a in TRAD else short_crypto_mult for a in assets})
        sig = sig.where(sig >= 0, sig.mul(mult, axis=1))
    vol = ann_vol(c)
    raw = (sig / vol).where(c.notna()).fillna(0)
    n = c.notna().sum(axis=1).clip(lower=1)
    raw = raw.div(n, axis=0)
    return cap(vol_scale(close, raw, target_vol))


def v2_long_only(close: pd.DataFrame, uni=("BTC", "ETH", "SOL", "XRP"), tv=0.40) -> pd.DataFrame:
    """Existing Orca v2 rule (SMA 50/100/200 ensemble, BTC gate, inverse-vol, 40% vol target, 50% cap)."""
    P = close[list(uni)]
    vol = P.pct_change(fill_method=None).rolling(30).std() * np.sqrt(365)
    subs = []
    for N in (50, 100, 200):
        sma = P.rolling(N).mean()
        on = (P > sma).mul(P.BTC > sma.BTC, axis=0)
        iv = (1 / vol).where(on)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
        pv = (w * vol).sum(axis=1) * 0.85
        subs.append(w.mul((tv / pv).clip(upper=1).fillna(0), axis=0).clip(upper=0.5))
    return (sum(subs) / 3).fillna(0)


def weekly(w: pd.DataFrame, start_dow: int = 0) -> pd.DataFrame:
    """Only change targets once a week (Mondays) to cut turnover."""
    keep = w.index.dayofweek == start_dow
    return w.where(pd.Series(keep, index=w.index), np.nan).ffill().fillna(0)


def dd_brake(eq: pd.Series, w: pd.DataFrame, levels=((0.15, 0.5), (0.25, 0.25))) -> pd.DataFrame:
    """Cut exposure when the strategy's own (shadow) equity is in drawdown. Known at close t -> applied to t's target."""
    dd = 1 - eq / eq.cummax()
    m = pd.Series(1.0, index=eq.index)
    for lvl, mult in levels:
        m[dd >= lvl] = mult
    return w.mul(m.reindex(w.index).fillna(1), axis=0)


def regime_trend(close: pd.DataFrame, uni, windows=(50, 100, 200), tv=0.40, gate=True, short_bear=0.0,
                 coin_cap=0.5, vol_win=30) -> pd.DataFrame:
    """Generalised v2: per SMA window, hold coins above their SMA while BTC is above its SMA (regime gate).
    short_bear > 0 additionally shorts coins below their SMA while BTC is below its SMA, at that fraction of size."""
    P = close[list(uni)]
    vol = P.pct_change(fill_method=None).rolling(vol_win).std() * np.sqrt(365)
    subs = []
    for N in windows:
        sma = P.rolling(N).mean()
        btc_on = close.BTC > close.BTC.rolling(N).mean()
        on = (P > sma)
        if gate:
            on = on.mul(btc_on, axis=0)
        iv = (1 / vol).where(on)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
        pv = (w * vol).sum(axis=1) * 0.85
        w = w.mul((tv / pv).clip(upper=1).fillna(0), axis=0).clip(upper=coin_cap)
        if short_bear > 0:
            off = (P < sma).mul(~btc_on, axis=0)
            iv2 = (1 / vol).where(off)
            s = iv2.div(iv2.sum(axis=1), axis=0).fillna(0)
            pv2 = (s * vol).sum(axis=1) * 0.85
            s = s.mul((tv / pv2).clip(upper=1).fillna(0), axis=0).clip(upper=coin_cap) * short_bear
            w = w - s
        subs.append(w)
    return (sum(subs) / len(subs)).fillna(0)


def regime_trend2(close: pd.DataFrame, uni, windows=(20, 50, 100, 200), tv=0.30, buffer=0.0, slope=0,
                  coin_cap=0.5, vol_win=30, btc_only=False) -> pd.DataFrame:
    """regime_trend with false-breakout filters: price must exceed SMA by `buffer` and, if slope>0,
    the SMA must be higher than `slope` days ago. Applied to both the coin and the BTC gate.
    btc_only=True trades BTC alone (with the same gate)."""
    uni = ["BTC"] if btc_only else list(uni)
    P = close[uni]
    vol = P.pct_change(fill_method=None).rolling(vol_win).std() * np.sqrt(365)
    subs = []
    for N in windows:
        sma = P.rolling(N).mean()
        on = P > sma * (1 + buffer)
        if slope:
            on &= sma > sma.shift(slope)
        b = close.BTC.rolling(N).mean()
        gate = close.BTC > b * (1 + buffer)
        if slope:
            gate &= b > b.shift(slope)
        on = on.mul(gate, axis=0)
        iv = (1 / vol).where(on)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
        pv = (w * vol).sum(axis=1) * 0.85
        subs.append(w.mul((tv / pv).clip(upper=1).fillna(0), axis=0).clip(upper=coin_cap))
    return (sum(subs) / len(subs)).fillna(0)

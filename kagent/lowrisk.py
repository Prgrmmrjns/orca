"""Weekly beta-neutral low-risk / anti-lottery long-short sleeve on 20 Kraken perps (signal discovery, 2026-11-26).

Score = -(rank vol30 + rank max daily return 30d + rank run-up from 30d low) / 3. Every 7 days: long the 4 lowest-risk
coins, short the 4 highest, inverse-vol weights, 50% gross per side, shorts scaled so BTC beta nets to ~0.
Weights are set with data to close t and earn from t+1. Costs: fees + slippage per side, real Kraken hourly funding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

K, EVERY, WARMUP = 4, 7, 60
MAJORS = ("BTC", "ETH", "SOL", "XRP")


def score(F: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rk = lambda X: X.rank(axis=1, pct=True)
    return -(rk(F["px_vol30"]) + rk(F["px_maxret30"]) + rk(F["px_up30"])) / 3


def price_score(C: pd.DataFrame) -> pd.DataFrame:
    """Same composite from prices only (vol30, max daily return 30d, run-up from 30d low); works for stocks."""
    R = C.pct_change(fill_method=None)
    rk = lambda X: X.rank(axis=1, pct=True)
    return -(rk(R.rolling(30).std()) + rk(R.rolling(30).max()) + rk(C / C.rolling(30).min() - 1)) / 3


def targets(C: pd.DataFrame, sc: pd.DataFrame, k: int = K, every: int = EVERY, bench: str = "BTC") -> pd.DataFrame:
    R = C.pct_change()
    W = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    w = pd.Series(0.0, index=C.columns)
    for i, d in enumerate(C.index):
        if i >= WARMUP and i % every == 0:
            s = sc.loc[d].dropna()
            if len(s) >= 10:
                lo, hi = s.nsmallest(k).index, s.nlargest(k).index
                vol = R.iloc[i - 30:i].std()
                w = pd.Series(0.0, index=C.columns)
                w[hi] = (1 / vol[hi]) / (1 / vol[hi]).sum() * 0.5
                w[lo] = -(1 / vol[lo]) / (1 / vol[lo]).sum() * 0.5
                b = R.iloc[i - 60:i].cov()[bench] / R[bench].iloc[i - 60:i].var()
                bl, bs = (w.clip(lower=0) * b).sum(), (-w.clip(upper=0) * b).sum()
                if bl > 0 and bs > 0:
                    w[w < 0] *= bl / bs
        W.iloc[i] = w
    return W


def net_returns(C: pd.DataFrame, W: pd.DataFrame, fund: pd.DataFrame, lag: int = 1, cost: pd.Series | None = None) -> pd.Series:
    """Daily net return of held weights W (decided at close t, held from t+lag)."""
    R = C.pct_change().fillna(0)
    H = W.shift(lag).fillna(0)
    if cost is None:
        cost = pd.Series({c: 0.0005 + (0.0005 if c in MAJORS else 0.0015) for c in C.columns})
    tc = (H.diff().abs() * cost).sum(axis=1)
    fd = (H * fund.reindex(C.index).reindex(columns=C.columns).fillna(0)).sum(axis=1)  # longs pay positive funding
    return (H * R).sum(axis=1) - tc - fd


def kraken_funding_daily(index) -> pd.DataFrame:
    from kagent.data import load
    f = load("kraken_funding_hourly")
    if f.index.tz is not None:
        f.index = f.index.tz_localize(None)
    return f.resample("1D").sum().reindex(index)

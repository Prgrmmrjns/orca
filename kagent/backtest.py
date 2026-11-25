"""Daily portfolio simulator with Kraken costs. Weights are fractions of equity (negative = short perp).

Timing: target weights computed from data up to close of day t are executed at close of day t+1
(one full day of lag - conservative), then earn returns from t+1 onward.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent import costs


def simulate(close: pd.DataFrame, target: pd.DataFrame, venue: str = "perp", band: float = 0.02,
             funding: bool = True, lag: int = 1) -> pd.DataFrame:
    target = target.reindex(close.index).fillna(0.0)
    cols = list(target.columns)
    R = close[cols].pct_change().fillna(0.0).values
    T = target.shift(lag).fillna(0.0).values
    cost_side = np.array([costs.per_side_cost(c, venue) for c in cols])
    is_trad = np.array([c in costs.TRAD for c in cols])
    eq = 1.0
    w = np.zeros(len(cols))
    rows = []
    for i in range(len(R)):
        # drift
        gross_ret = float((w * R[i]).sum())
        eq_new = eq * (1 + gross_ret)
        if eq_new <= 0:
            eq_new = 1e-9
        w = w * (1 + R[i]) * eq / eq_new
        eq = eq_new
        # daily carry
        carry = 0.0
        if funding:
            if venue == "perp":
                lc = np.where(is_trad, costs.FUND_TRAD, costs.FUND_LONG_CRYPTO)
                sc = np.where(is_trad, costs.FUND_TRAD, -costs.FUND_SHORT_CRYPTO)
                carry = float((np.clip(w, 0, None) * lc + np.clip(-w, 0, None) * sc).sum()) / 365
            else:  # spot margin: rollover on borrowed part (gross>1 or shorts)
                borrowed = max(np.clip(w, 0, None).sum() - 1, 0) + np.clip(-w, 0, None).sum()
                carry = borrowed * costs.FEES["margin_rollover_per_4h"] * 6
        # rebalance
        tgt = T[i]
        d = tgt - w
        trade = np.abs(d) >= band
        trade |= (tgt == 0) & (w != 0)  # always fully close
        d = np.where(trade, d, 0.0)
        fee = float((np.abs(d) * cost_side).sum())
        w = w + d
        eq *= (1 - fee - carry)
        rows.append((eq, gross_ret, fee, carry, float(np.abs(d).sum()), float(np.abs(w).sum()), float(w.sum())))
    out = pd.DataFrame(rows, index=close.index, columns=["equity", "gross_ret", "fee", "carry", "turnover", "gross", "net"])
    return out


def stats(eq: pd.Series, res: pd.DataFrame | None = None) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = len(r) / 365
    dd = eq / eq.cummax() - 1
    down = r[r < 0].std() * np.sqrt(365)
    out = dict(
        cagr=round(((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 1),
        vol=round(r.std() * np.sqrt(365) * 100, 1),
        sharpe=round(r.mean() / r.std() * np.sqrt(365), 2) if r.std() > 0 else 0,
        sortino=round(r.mean() * 365 / down, 2) if down > 0 else 0,
        mdd=round(dd.min() * 100, 1),
        calmar=round((((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) / -dd.min()), 2) if dd.min() < 0 else 0,
        mult=round(eq.iloc[-1] / eq.iloc[0], 2),
    )
    if res is not None:
        res = res.loc[eq.index]
        out["turn_yr"] = round(res.turnover.sum() / yrs, 1)
        out["fees_yr%"] = round(res.fee.sum() / yrs * 100, 2)
        out["carry_yr%"] = round(res.carry.sum() / yrs * 100, 2)
        out["avg_gross"] = round(res.gross.mean(), 2)
    return out


def yearly(eq: pd.Series) -> pd.Series:
    y = eq.resample("YE").last()
    first = eq.iloc[0]
    return (y / y.shift(1).fillna(first) - 1).mul(100).round(1).set_axis(y.index.year)


def boot_sharpe(eq: pd.Series, n: int = 2000, block: int = 20, seed: int = 0) -> tuple[float, float]:
    r = eq.pct_change().dropna().values
    rng = np.random.default_rng(seed)
    k = len(r) // block
    sh = []
    for _ in range(n):
        idx = rng.integers(0, len(r) - block, k)
        s = np.concatenate([r[j:j + block] for j in idx])
        sh.append(s.mean() / s.std() * np.sqrt(365))
    return round(float(np.percentile(sh, 5)), 2), round(float(np.percentile(sh, 95)), 2)

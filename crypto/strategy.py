"""Orca crypto strategy v3 — gated momentum + ML blend (long-only, daily).

One code path for research and live: every function takes daily panels
(index = UTC day, columns = coins) and returns per-day target weights.
Nothing here looks past the row it is computing.

Rules (frozen 2026-11-22, see crypto/STRATEGY.md):
  universe   top-20 coins by trailing 30d median quote volume, >=120d history
  gate       invested only while BTC close > its 100d SMA
  sleeve A   momentum: top-5 by mean cross-sectional rank of 7/14/30d return,
             and only coins whose 14d return is > 0
  sleeve B   ML: top-5 by LightGBM forecast of next-7d return, forecast > 0
  blend      50% A + 50% B, 10% per pick per sleeve (max 20% in one coin)
  risk       scale down so est. portfolio vol (sum w*vol30*0.85) <= 50%/yr
  trading    daily, only when a coin's target moves > 3% of equity (exits always)
"""
from __future__ import annotations
import numpy as np, pandas as pd

PARAMS = dict(universe_n=20, min_history=120, gate_sma=100, top_k=5,
              mom_lookbacks=(7, 14, 30), mom_abs=14, horizon=7,
              vol_target=0.50, band=0.03, w_mom=0.5, w_ml=0.5)

def universe(close, qvol, n=20, min_history=120):
    hist = close.notna().rolling(min_history).sum() >= min_history
    v30 = qvol.rolling(30).median().where(hist)
    return v30.rank(axis=1, ascending=False) <= n

def features(close, high, low, qvol, un):
    """Per-coin features, their cross-sectional ranks, and market features."""
    ret = close.pct_change()
    btc = close["BTC"]
    mkt = ret.where(un).mean(axis=1)
    F = {}
    for l in (1, 3, 7, 14, 30, 60, 90): F[f"r{l}"] = close / close.shift(l) - 1
    for l in (7, 30): F[f"vol{l}"] = ret.rolling(l).std()
    F["volchg"] = qvol.rolling(7).mean() / qvol.rolling(30).mean()
    F["dist_hi90"] = close / high.rolling(90).max() - 1
    F["dist_lo90"] = close / low.rolling(90).min() - 1
    F["sma50"] = close / close.rolling(50).mean() - 1
    F["sma200"] = close / close.rolling(200).mean() - 1
    F["range14"] = ((high - low) / close).rolling(14).mean()
    F["beta60"] = ret.rolling(60).cov(mkt) / mkt.rolling(60).var()
    F["skew30"] = ret.rolling(30).skew()
    XS = {k + "_xs": v.where(un).rank(axis=1, pct=True) for k, v in F.items()}
    M = {"btc_r7": btc / btc.shift(7) - 1, "btc_r30": btc / btc.shift(30) - 1,
         "btc_sma100": btc / btc.rolling(100).mean() - 1,
         "mkt_r7": mkt.rolling(7).sum(), "mkt_vol30": mkt.rolling(30).std(),
         "breadth": (close > close.rolling(50).mean()).astype(float).where(un).mean(axis=1)}
    return F, XS, M

def feature_frame(close, high, low, qvol, un, rows=None):
    """Long (date, coin) feature table restricted to universe members.
    rows: optional index slice (e.g. last day only) to keep live inference cheap."""
    F, XS, M = features(close, high, low, qvol, un)
    sel = (lambda d: d if rows is None else d.loc[rows])
    m = sel(un)
    X = pd.concat({k: sel(v).where(m).stack(future_stack=True) for k, v in {**F, **XS}.items()}, axis=1)
    X = X[m.stack(future_stack=True).reindex(X.index).fillna(False).values]
    for k, v in M.items(): X[k] = v.reindex(X.index.get_level_values(0)).values
    return X.astype(float)

def momentum_sleeve(close, un, p=PARAMS):
    score = sum((close / close.shift(l) - 1).where(un).rank(axis=1, pct=True) for l in p["mom_lookbacks"])
    absm = close / close.shift(p["mom_abs"]) - 1
    pick = (score.rank(axis=1, ascending=False) <= p["top_k"]) & (absm > 0)
    return pick.astype(float) / p["top_k"]

def ml_sleeve(pred, p=PARAMS):
    pick = (pred.rank(axis=1, ascending=False) <= p["top_k"]) & (pred > 0)
    return pick.astype(float) / p["top_k"]

def target_weights(close, qvol, pred, p=PARAMS):
    """pred: date x coin forecast of next-7d return (NaN outside universe)."""
    un = universe(close, qvol, p["universe_n"], p["min_history"])
    btc = close["BTC"]
    gate = (btc > btc.rolling(p["gate_sma"]).mean()).astype(float)
    w = p["w_mom"] * momentum_sleeve(close, un, p) + p["w_ml"] * ml_sleeve(pred.reindex_like(close), p)
    w = w.mul(gate, axis=0)
    rv = close.pct_change().rolling(30).std() * np.sqrt(365)
    est = (w * rv).sum(axis=1) * 0.85
    scale = (p["vol_target"] / est).clip(upper=1.0).replace([np.inf, -np.inf], 0).fillna(0)
    return w.mul(scale, axis=0).fillna(0.0), gate

def apply_band(w, band):
    """Backtest helper: keep the previous weight unless the target moved > band."""
    cur = np.zeros(w.shape[1]); out = []
    for row in w.values:
        ch = (np.abs(row - cur) > band) | ((row == 0) & (cur != 0))
        cur = np.where(ch, row, cur); out.append(cur.copy())
    return pd.DataFrame(out, index=w.index, columns=w.columns)

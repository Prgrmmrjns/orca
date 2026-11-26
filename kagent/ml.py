"""Walk-forward ML sleeve: cross-sectional crypto ranker (LightGBM, shallow + heavily regularised).

Target: next-7-day return minus the cross-sectional mean (relative performance).
Retrain every 28 days on all history ending 8 days before the train date (purge/embargo for the 7d label).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent.strategies import ann_vol, cap, vol_scale

HORIZON = 7


def features(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, qvol: pd.DataFrame, assets: list[str]) -> pd.DataFrame:
    c = close[assets]
    f = {}
    for L in (1, 3, 7, 14, 30, 60, 90, 180):
        f[f"ret{L}"] = c.pct_change(L, fill_method=None)
    for L in (20, 50, 100, 200):
        f[f"sma{L}"] = c / c.rolling(L).mean() - 1
    f["vol7"] = c.pct_change(fill_method=None).rolling(7).std()
    f["vol30"] = c.pct_change(fill_method=None).rolling(30).std()
    f["volratio"] = f["vol7"] / f["vol30"]
    f["dd30"] = c / c.rolling(30).max() - 1
    f["up30"] = c / c.rolling(30).min() - 1
    rng = (high[assets] - low[assets]) / c
    f["range7"] = rng.rolling(7).mean()
    lv = np.log1p(qvol[assets])
    f["vlm_z"] = (lv.rolling(7).mean() - lv.rolling(90).mean()) / lv.rolling(90).std()
    btc = close["BTC"]
    mkt = {"btc_ret30": btc.pct_change(30, fill_method=None), "btc_sma200": btc / btc.rolling(200).mean() - 1,
           "btc_vol30": btc.pct_change(fill_method=None).rolling(30).std()}
    # cross-sectional ranks for asset-specific features (scale-free, robust to regime)
    parts = []
    for k, v in f.items():
        parts.append(v.rank(axis=1, pct=True).stack().rename(k + "_r"))
    for k in ("ret30", "sma200", "vol30"):
        parts.append(f[k].stack().rename(k))
    X = pd.concat(parts, axis=1)
    for k, v in mkt.items():
        X[k] = v.reindex(X.index.get_level_values(0)).values
    fwd = c.shift(-HORIZON) / c - 1
    y = fwd.sub(fwd.mean(axis=1), axis=0).stack(future_stack=True).rename("y")
    X = X.join(y, how="left")
    X.index.names = ["date", "asset"]
    return X


def walk_forward(X: pd.DataFrame, start="2020-01-01", retrain=28, model="lgbm", min_assets=8) -> pd.DataFrame:
    import lightgbm as lgb
    from sklearn.linear_model import Ridge

    feats = [c for c in X.columns if c != "y"]
    dates = X.index.get_level_values(0).unique().sort_values()
    preds = []
    mdl = None
    last_train = None
    for d in dates[dates >= pd.Timestamp(start)]:
        if last_train is None or (d - last_train).days >= retrain:
            tr = X.loc[X.index.get_level_values(0) <= d - pd.Timedelta(days=HORIZON + 1)].dropna()
            if model == "lgbm":
                mdl = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.02, num_leaves=7, min_child_samples=300,
                                        subsample=0.7, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0,
                                        verbose=-1, random_state=0)
            else:
                mdl = Ridge(alpha=10.0)
            yt = tr["y"].clip(tr["y"].quantile(0.01), tr["y"].quantile(0.99))
            mdl.fit(tr[feats].fillna(0), yt)
            last_train = d
        today = X.loc[d]
        today = today[today[feats[:5]].notna().all(axis=1)]
        if len(today) < min_assets:
            continue
        p = pd.Series(mdl.predict(today[feats].fillna(0)), index=today.index, name=d)
        preds.append(p)
    return pd.DataFrame(preds)


def xs_portfolio(close: pd.DataFrame, pred: pd.DataFrame, k: int = 4, target_vol=0.20, long_only=False) -> pd.DataFrame:
    """Long top-k, short bottom-k predicted assets (inverse-vol, dollar-neutral), vol targeted."""
    assets = list(pred.columns)
    vol = ann_vol(close[assets]).reindex(pred.index)
    rk = pred.rank(axis=1, ascending=False)
    n = pred.notna().sum(axis=1)
    longm = rk.le(k)
    shortm = rk.gt(n.values[:, None] - k) & pred.notna()
    iv = 1 / vol
    lw = iv.where(longm)
    lw = lw.div(lw.sum(axis=1), axis=0).fillna(0) * 0.5
    sw = iv.where(shortm)
    sw = sw.div(sw.sum(axis=1), axis=0).fillna(0) * 0.5
    w = lw if long_only else lw - sw
    w = w.reindex(close.index).fillna(0)
    return cap(vol_scale(close, w, target_vol))

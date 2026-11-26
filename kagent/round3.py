"""Round 3: alt-data features + walk-forward models for weekly long/short perp positions.

All alt inputs are lagged to when they would have been known: daily sources +1 day, FRED weekly +7 days.
Models are refit every 28 days on data ending 8 days before (7d label + 1d embargo).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent.data import DATA, load

ASSETS = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX"]
H = 7


def load_alt(index) -> pd.DataFrame:
    a = pd.read_csv(DATA / "altdata.csv.gz", index_col=0, parse_dates=True)
    a.index = a.index.normalize()
    a = a.groupby(level=0).last()
    fred_cols = [c for c in ("WALCL", "RRPONTSYD", "WTREGEN", "DTWEXBGS", "DFII10") if c in a]
    f = a[fred_cols].ffill()
    f.index = f.index + pd.Timedelta(days=7)
    rest = a.drop(columns=fred_cols).shift(1, freq="D")
    try:
        g = pd.read_csv(DATA / "gdelt_crypto.csv.gz", index_col=0, parse_dates=True).shift(1, freq="D")
    except FileNotFoundError:
        g = pd.DataFrame()
    full = pd.concat([rest, f, g], axis=1).sort_index()
    full = full.groupby(level=0).last().reindex(pd.date_range(full.index.min(), index.max(), freq="D")).ffill(limit=10)
    return full.reindex(index)


def market_features(close: pd.DataFrame) -> pd.DataFrame:
    a = load_alt(close.index)
    m = pd.DataFrame(index=close.index)
    for k in ("BTC", "ETH"):
        if f"fund_{k}" in a:
            fd = a[f"fund_{k}"] * 365  # annualised
            m[f"fund_{k}_7"] = fd.rolling(7).mean()
            m[f"fund_{k}_30"] = fd.rolling(30).mean()
    if "btc_CapMVRVCur" in a:
        mv = np.log(a["btc_CapMVRVCur"])
        m["mvrv"] = mv
        m["mvrv_z"] = (mv - mv.rolling(365, min_periods=200).mean()) / mv.rolling(365, min_periods=200).std()
        m["mvrv_chg30"] = mv.diff(30)
    for k in ("btc_AdrActCnt", "eth_AdrActCnt"):
        if k in a:
            x = np.log(a[k])
            m[k + "_z"] = (x.rolling(7).mean() - x.rolling(90).mean()) / x.rolling(90).std()
    if "btc_FlowInExUSD" in a and "btc_FlowOutExUSD" in a:
        nf = (a["btc_FlowInExUSD"] - a["btc_FlowOutExUSD"]) / a["btc_CapMrktCurUSD"]
        m["btc_exflow_7"] = nf.rolling(7).sum()
    if "fng" in a:
        m["fng"] = a["fng"]
        m["fng_7"] = a["fng"].rolling(7).mean()
    if "stables" in a:
        m["stables_30"] = np.log(a["stables"]).diff(30)
        m["stables_90"] = np.log(a["stables"]).diff(90)
    if {"WALCL", "RRPONTSYD", "WTREGEN"} <= set(a.columns):
        nl = a["WALCL"] - a["RRPONTSYD"].fillna(0) * 1000 - a["WTREGEN"]  # all in $M
        m["netliq_28"] = np.log(nl).diff(28)
        m["netliq_91"] = np.log(nl).diff(91)
    if "DTWEXBGS" in a:
        m["dxy_28"] = np.log(a["DTWEXBGS"]).diff(28)
    if "DFII10" in a:
        m["ry_28"] = a["DFII10"].diff(28)
    if "timelinetone" in a:
        t = a["timelinetone"]
        m["tone_7"] = t.rolling(7).mean()
        m["tone_z"] = (t.rolling(7).mean() - t.rolling(90).mean()) / t.rolling(90).std()
    if "timelinevolraw" in a:
        v = np.log1p(a["timelinevolraw"])
        m["newsvol_z"] = (v.rolling(7).mean() - v.rolling(90).mean()) / v.rolling(90).std()
    btc = close.BTC
    for N in (20, 50, 100, 200):
        m[f"btc_sma{N}"] = btc / btc.rolling(N).mean() - 1
    m["btc_r30"] = btc.pct_change(30)
    m["btc_vol30"] = btc.pct_change().rolling(30).std() * np.sqrt(365)
    return m


def panel(close: pd.DataFrame, assets=ASSETS) -> pd.DataFrame:
    c = close[assets]
    f = {}
    for N in (20, 50, 100, 200):
        f[f"sma{N}"] = c / c.rolling(N).mean() - 1
    for L in (7, 30, 90):
        f[f"r{L}"] = c.pct_change(L, fill_method=None)
    f["vol30"] = c.pct_change(fill_method=None).rolling(30).std() * np.sqrt(365)
    X = pd.concat([v.stack().rename(k) for k, v in f.items()], axis=1)
    M = market_features(close)
    X = X.join(M, on=X.index.get_level_values(0)) if False else pd.concat(
        [X, M.reindex(X.index.get_level_values(0)).set_axis(X.index)], axis=1)
    X["is_btc"] = (X.index.get_level_values(1) == "BTC").astype(float)
    y = (c.shift(-H) / c - 1).stack(future_stack=True)
    X["y"] = y
    X.index.names = ["date", "asset"]
    return X


def walk_forward(X, feats, start="2020-01-01", retrain=28, kind="logit", min_train_days=365):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import lightgbm as lgb

    d = X.index.get_level_values(0)
    out = []
    for t0 in pd.date_range(start, d.max(), freq=f"{retrain}D"):
        tr = X[(d <= t0 - pd.Timedelta(days=H + 1))].dropna(subset=["y"])
        tr = tr[tr[feats].notna().mean(axis=1) > 0.7]
        if tr.index.get_level_values(0).nunique() < min_train_days:
            continue
        te = X[(d > t0) & (d <= t0 + pd.Timedelta(days=retrain))]
        if te.empty:
            continue
        med = tr[feats].median()
        if kind == "logit":
            mdl = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=500))
        else:
            mdl = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.02, num_leaves=5, min_child_samples=400,
                                     subsample=0.7, subsample_freq=1, colsample_bytree=0.6, reg_lambda=10, verbose=-1)
        mdl.fit(tr[feats].fillna(med), (tr["y"] > 0).astype(int))
        out.append(pd.Series(mdl.predict_proba(te[feats].fillna(med))[:, 1], index=te.index))
    return pd.concat(out)


CANDIDATES = ["fund_BTC_30", "fund_ETH_30", "mvrv_z", "mvrv_chg30", "btc_AdrActCnt_z", "eth_AdrActCnt_z",
              "btc_exflow_7", "fng", "stables_30", "stables_90", "netliq_28", "netliq_91", "dxy_28", "ry_28",
              "btc_sma20", "btc_sma50", "btc_sma100", "btc_sma200", "btc_r30", "tone_z", "newsvol_z"]


def composite_score(close: pd.DataFrame, horizon=28, min_ic=0.03, refit="MS", start="2020-01-01", extra=None):
    """Walk-forward composite: each month, keep features whose past IC with BTC's forward return
    (data ending `horizon`+1 days ago) has |IC| >= min_ic with the same sign in both halves of the past.
    Score = mean of sign * rolling 365d z-score. Returns (score series, selection log)."""
    M = market_features(close)
    if extra is not None:
        M = M.join(extra)
    cands = [c for c in CANDIDATES if c in M]
    Z = (M[cands] - M[cands].rolling(365, min_periods=180).mean()) / M[cands].rolling(365, min_periods=180).std()
    Z = Z.clip(-3, 3)
    fwd = close.BTC.shift(-horizon) / close.BTC - 1
    score = pd.Series(np.nan, index=close.index)
    log = {}
    for t0 in pd.date_range(start, close.index[-1], freq=refit):
        past = M.loc[:t0 - pd.Timedelta(days=horizon + 1)]
        f = fwd.loc[past.index]
        signs = {}
        for c in cands:
            j = pd.concat([past[c], f], axis=1).dropna()
            if len(j) < 365:
                continue
            mid = len(j) // 2
            a = j.iloc[:mid].corr("spearman").iloc[0, 1]
            b = j.iloc[mid:].corr("spearman").iloc[0, 1]
            ic = j.corr("spearman").iloc[0, 1]
            if abs(ic) >= min_ic and np.sign(a) == np.sign(b) == np.sign(ic):
                signs[c] = np.sign(ic)
        log[t0.date()] = signs
        t1 = t0 + pd.offsets.MonthBegin(1)
        seg = Z.loc[t0:t1 - pd.Timedelta(days=1), list(signs)]
        if len(signs):
            score.loc[seg.index] = (seg * pd.Series(signs)).mean(axis=1)
    return score, log


def combined_targets(close: pd.DataFrame, comp_weight: float = 1.0, thr: float = 0.25, comp_vol: float = 0.25):
    """Trend sleeve (frozen agent rule) + alt-data composite sleeve (long/short perps on the same 8 coins).
    Composite score = average of the 7-day and 28-day walk-forward scores; trade only when |score| > thr."""
    from kagent import agent, strategies as S
    s7, _ = composite_score(close, horizon=7)
    s28, _ = composite_score(close, horizon=28)
    sc = ((s7 + s28) / 2).ewm(span=5).mean()
    pos = pd.Series(np.where(sc > thr, 1.0, np.where(sc < -thr, -1.0, 0.0)), index=close.index)
    uni = agent.UNIVERSE
    vol = close[uni].pct_change().rolling(30).std() * np.sqrt(365)
    w = (1 / vol).div((1 / vol).sum(axis=1), axis=0).mul(pos, axis=0).fillna(0)
    comp = S.weekly(S.cap(S.vol_scale(close, w, comp_vol), 1.5))
    trend = agent.targets(close).reindex(columns=uni).fillna(0)
    return S.cap(trend + comp_weight * comp, gross=1.5), trend, comp, sc

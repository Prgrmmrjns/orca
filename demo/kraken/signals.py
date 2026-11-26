"""Signal sleeves, ported 1:1 from kagent/ (agent.py, strategies.py, round3.py, lowrisk.py, policy.py, zoo.py).
All functions take data known at the latest daily close and return target weights (fraction of the book's capital)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db
from .config import SYMS, TREND_UNI

H = 7


# ------------------------------------------------------------------ data
def load_panel(start: str = "2017-01-01"):
    def piv(tbl, cols):
        rows = db.rpc("panel", {"tbl": tbl, "start": start})
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=cols).pivot_table(index=cols[0], columns=cols[1], values=cols[2], aggfunc="last")
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    C = piv("prices", ["d", "sym", "close"]).reindex(columns=SYMS)
    C = C.reindex(pd.date_range(C.index[0], C.index[-1], freq="D")).ffill(limit=3)
    F = piv("funding", ["d", "sym", "rate"])
    fund = F.reindex(index=C.index, columns=SYMS).fillna(0.10 / 365) if len(F) else \
        pd.DataFrame(0.10 / 365, index=C.index, columns=SYMS)
    A = piv("alt", ["d", "k", "v"])
    return C, fund, A


# ------------------------------------------------------------------ helpers (kagent/strategies.py)
def cap(w: pd.DataFrame, gross: float, coin_cap: float = 0.5) -> pd.DataFrame:
    w = w.clip(lower=-coin_cap, upper=coin_cap)
    g = w.abs().sum(axis=1)
    return w.mul((gross / g).clip(upper=1).fillna(0), axis=0)


def weekly(w: pd.DataFrame, start_dow: int = 0) -> pd.DataFrame:
    keep = w.index.dayofweek == start_dow
    return w.where(pd.Series(keep, index=w.index), np.nan).ffill().fillna(0)


def vol_scale(close, w, target, lookback=90, max_lev=3.0):
    R = close[w.columns].pct_change(fill_method=None).fillna(0)
    scale = pd.Series(np.nan, index=w.index)
    for i in range(lookback, len(w), 7):
        cov = R.iloc[i - lookback:i].cov().values * 365
        x = w.iloc[i].values
        v = float(np.sqrt(max(x @ cov @ x, 0)))
        scale.iloc[i] = min(target / v, max_lev) if v > 0 else 0
    return w.mul(scale.ffill().fillna(0), axis=0)


def regime_trend(close, uni, windows=(20, 50, 100, 200), tv=0.30, coin_cap=0.5, vol_win=30):
    P = close[list(uni)]
    vol = P.pct_change(fill_method=None).rolling(vol_win).std() * np.sqrt(365)
    subs = []
    for N in windows:
        sma = P.rolling(N).mean()
        btc_on = close.BTC > close.BTC.rolling(N).mean()
        on = (P > sma).mul(btc_on, axis=0)
        iv = (1 / vol).where(on)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
        pv = (w * vol).sum(axis=1) * 0.85
        subs.append(w.mul((tv / pv).clip(upper=1).fillna(0), axis=0).clip(upper=coin_cap))
    return (sum(subs) / len(subs)).fillna(0)


# ------------------------------------------------------------------ trend + alt-data composite (kagent/agent.py + round3.py)
CANDIDATES = ["fund_BTC_7", "fund_BTC_30", "fund_ETH_7", "fund_ETH_30", "mvrv", "mvrv_z", "mvrv_chg30",
              "btc_AdrActCnt_z", "eth_AdrActCnt_z", "btc_exflow_7", "fng", "stables_30", "stables_90", "netliq_28",
              "netliq_91", "dxy_28", "ry_28", "btc_sma20", "btc_sma50", "btc_sma100", "btc_sma200", "btc_r30"]


def load_alt(A: pd.DataFrame, index) -> pd.DataFrame:
    a = A.copy()
    fred_cols = [c for c in ("WALCL", "RRPONTSYD", "WTREGEN", "DTWEXBGS", "DFII10") if c in a]
    f = a[fred_cols].ffill()
    f.index = f.index + pd.Timedelta(days=7)
    rest = a.drop(columns=fred_cols).shift(1, freq="D")
    full = pd.concat([rest, f], axis=1).sort_index()
    full = full.groupby(level=0).last()
    full = full.reindex(pd.date_range(full.index.min(), max(index.max(), full.index.max()), freq="D")).ffill(limit=10)
    return full.reindex(index)


def market_features(close, A):
    a = load_alt(A, close.index)
    m = pd.DataFrame(index=close.index)
    for k in ("BTC", "ETH"):
        if f"fund_{k}" in a:
            fd = a[f"fund_{k}"] * 365
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
    if {"btc_FlowInExUSD", "btc_FlowOutExUSD", "btc_CapMrktCurUSD"} <= set(a.columns):
        nf = (a["btc_FlowInExUSD"] - a["btc_FlowOutExUSD"]) / a["btc_CapMrktCurUSD"]
        m["btc_exflow_7"] = nf.rolling(7).sum()
    if "fng" in a:
        m["fng"] = a["fng"]
    if "stables" in a:
        m["stables_30"] = np.log(a["stables"]).diff(30)
        m["stables_90"] = np.log(a["stables"]).diff(90)
    if {"WALCL", "RRPONTSYD", "WTREGEN"} <= set(a.columns):
        nl = a["WALCL"] - a["RRPONTSYD"].fillna(0) * 1000 - a["WTREGEN"]
        m["netliq_28"] = np.log(nl).diff(28)
        m["netliq_91"] = np.log(nl).diff(91)
    if "DTWEXBGS" in a:
        m["dxy_28"] = np.log(a["DTWEXBGS"]).diff(28)
    if "DFII10" in a:
        m["ry_28"] = a["DFII10"].diff(28)
    btc = close.BTC
    for N in (20, 50, 100, 200):
        m[f"btc_sma{N}"] = btc / btc.rolling(N).mean() - 1
    m["btc_r30"] = btc.pct_change(30)
    return m


def composite_score(close, A, horizon=28, min_ic=0.03, start="2020-01-01"):
    M = market_features(close, A)
    cands = [c for c in CANDIDATES if c in M]
    Z = (M[cands] - M[cands].rolling(365, min_periods=180).mean()) / M[cands].rolling(365, min_periods=180).std()
    Z = Z.clip(-3, 3)
    fwd = close.BTC.shift(-horizon) / close.BTC - 1
    score = pd.Series(np.nan, index=close.index)
    log = {}
    for t0 in pd.date_range(start, close.index[-1], freq="MS"):
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
                signs[c] = float(np.sign(ic))
        log[str(t0.date())] = signs
        t1 = t0 + pd.offsets.MonthBegin(1)
        seg = Z.loc[t0:t1 - pd.Timedelta(days=1), list(signs)]
        if len(signs):
            score.loc[seg.index] = (seg * pd.Series(signs)).mean(axis=1)
    return score, log, Z


def trend_alt(close, A, thr=0.25, comp_vol=0.25):
    tr = weekly(cap(regime_trend(close, TREND_UNI), gross=1.0))
    info = {}
    if len(A):
        s7, log7, Z = composite_score(close, A, horizon=7)
        s28, log28, _ = composite_score(close, A, horizon=28)
        sc = ((s7 + s28) / 2).ewm(span=5).mean()
        pos = pd.Series(np.where(sc > thr, 1.0, np.where(sc < -thr, -1.0, 0.0)), index=close.index)
        vol = close[TREND_UNI].pct_change().rolling(30).std() * np.sqrt(365)
        w = (1 / vol).div((1 / vol).sum(axis=1), axis=0).mul(pos, axis=0).fillna(0)
        comp = weekly(cap(vol_scale(close, w, comp_vol), 1.5))
        last7 = log7[max(log7)] if log7 else {}
        last28 = log28[max(log28)] if log28 else {}
        zl = Z.iloc[-1].dropna().round(2).to_dict()
        info = dict(composite=float(sc.iloc[-1]) if pd.notna(sc.iloc[-1]) else None, threshold=thr,
                    composite_position=float(pos.iloc[-1]), features_7d=last7, features_28d=last28, feature_z=zl)
    else:
        comp = tr * 0
    W = cap(tr.reindex(columns=TREND_UNI).fillna(0) + comp.reindex(columns=TREND_UNI).fillna(0), gross=1.5)
    last = W.iloc[-1]
    btc = close.BTC
    info["btc_above_sma"] = {N: bool(btc.iloc[-1] > btc.rolling(N).mean().iloc[-1]) for N in (20, 50, 100, 200)}
    info["trend_weights"] = tr.iloc[-1][tr.iloc[-1].abs() > 1e-4].round(4).to_dict()
    info["composite_weights"] = comp.iloc[-1][comp.iloc[-1].abs() > 1e-4].round(4).to_dict()
    return {k: float(v) for k, v in last.items() if abs(v) > 1e-4}, info


# ------------------------------------------------------------------ low-risk long/short (kagent/lowrisk.py)
def lowrisk(close, k=4):
    C = close[SYMS]
    R = C.pct_change(fill_method=None)
    rk = lambda X: X.rank(axis=1, pct=True)
    sc = -(rk(R.rolling(30).std()) + rk(R.rolling(30).max()) + rk(C / C.rolling(30).min() - 1)) / 3
    s = sc.iloc[-1].dropna()
    if len(s) < 10:
        return {}, {"error": "fewer than 10 coins with data"}
    lo, hi = s.nsmallest(k).index, s.nlargest(k).index
    vol = R.iloc[-30:].std()
    w = pd.Series(0.0, index=C.columns)
    w[hi] = (1 / vol[hi]) / (1 / vol[hi]).sum() * 0.5
    w[lo] = -(1 / vol[lo]) / (1 / vol[lo]).sum() * 0.5
    b = R.iloc[-60:].cov()["BTC"] / R["BTC"].iloc[-60:].var()
    bl, bs = (w.clip(lower=0) * b).sum(), (-w.clip(upper=0) * b).sum()
    if bl > 0 and bs > 0:
        w[w < 0] *= bl / bs
    info = dict(score=s.round(3).sort_values().to_dict(), longs=list(hi), shorts=list(lo),
                beta=b.round(2).to_dict(), net_beta=float((w * b).sum()))
    return {kk: float(v) for kk, v in w.items() if abs(v) > 1e-6}, info


# ------------------------------------------------------------------ objective-max linear policy (kagent/policy.py)
def features(C, fund):
    R = C.pct_change(fill_method=None)
    rk = lambda X: X.sub(X.mean(axis=1), axis=0).div(X.std(axis=1), axis=0)
    beta = R.rolling(60).cov(R["BTC"]).div(R["BTC"].rolling(60).var(), axis=0)
    F = {"mom7": C.pct_change(7), "mom30": C.pct_change(30), "mom90": C.pct_change(90), "rev1": -R, "rev3": -C.pct_change(3),
         "vol30": R.rolling(30).std(), "max30": R.rolling(30).max(), "runup30": C / C.rolling(30).min() - 1,
         "sma50": C / C.rolling(50).mean() - 1, "sma200": C / C.rolling(200).mean() - 1,
         "fund7": fund.rolling(7).mean(), "beta60": beta}
    X = {k: rk(v).clip(-3, 3) for k, v in F.items()}
    btc = C.BTC
    T = pd.DataFrame({"btc_sma50": np.sign(btc / btc.rolling(50).mean() - 1), "btc_sma200": np.sign(btc / btc.rolling(200).mean() - 1),
                      "breadth50": (C > C.rolling(50).mean()).mean(axis=1) * 2 - 1, "btc_mom30": np.sign(btc.pct_change(30))})
    return X, T, R


def fit_policy(C, fund, start="2019-09-01", gamma=1.0, rho=1e-4):
    """theta = (Cov(g) + ridge)^-1 mean(g) on non-overlapping weekly samples; g = feature-weighted 7d returns."""
    C = C[SYMS]
    X, T, R = features(C, fund[SYMS])
    valid = C.notna() & C.shift(200).notna()
    R7 = (C.shift(-H) / C - 1).where(valid)
    n = valid.sum(axis=1)
    G = pd.DataFrame({k: (X[k].fillna(0) * R7).where(valid).sum(axis=1) / n for k in X})
    Gt = pd.DataFrame({k: T[k] * R7.sum(axis=1) / n for k in T})
    Gall = pd.concat([G, Gt], axis=1).loc[start:].dropna()
    Gall = Gall.iloc[::H]
    mu, S = Gall.mean().values, np.cov(Gall.values.T)
    th = np.linalg.solve(S + rho * np.trace(S) / len(mu) * np.eye(len(mu)) * 10, mu) / gamma
    th = pd.Series(th, index=Gall.columns)
    r = Gall.values @ th.values  # in-sample weekly portfolio return of the unscaled policy
    metrics = dict(n_weeks=int(len(Gall)), insample_sharpe=float(r.mean() / r.std() * np.sqrt(52)) if r.std() > 0 else 0.0,
                   start=str(Gall.index[0].date()), end=str(Gall.index[-1].date()))
    return {k: float(v) for k, v in th.items()}, metrics


def policy(C, fund, theta, tv=0.15, lev_cap=3.0, top_k=6):
    C = C[SYMS]
    X, T, R = features(C, fund[SYMS])
    valid = C.notna() & C.shift(200).notna()
    keys_x = [k for k in X]
    W = sum(theta[k] * X[k].fillna(0) for k in keys_x).where(valid, 0)
    W = W.div(valid.sum(axis=1), axis=0)
    net = (T.fillna(0) @ pd.Series({k: theta[k] for k in T})) / valid.sum(axis=1)
    W = W.add(net, axis=0).where(valid, 0)
    w = W.iloc[-1].fillna(0)
    Rf = R.fillna(0).iloc[-60:]
    S = np.cov(Rf.values.T) * 365
    pv = float(np.sqrt(max(w.values @ S @ w.values, 0)))
    k = min(tv / pv, lev_cap) if pv > 1e-6 else 0.0
    w = (w * k).clip(-0.5, 0.5)
    if top_k and (w.abs() > 0).sum() > top_k:  # small account: keep the strongest positions, same gross exposure
        g = w.abs().sum()
        w = w.where(w.abs().rank(ascending=False) <= top_k, 0.0)
        w = (w * g / w.abs().sum()).clip(-0.5, 0.5) if w.abs().sum() > 0 else w
    contrib = {f: float(theta[f] * X[f].iloc[-1].fillna(0).abs().mean()) for f in keys_x}
    info = dict(scale=k, top_k=top_k, exante_vol_unscaled=pv, net_exposure=float(w.sum()), gross=float(w.abs().sum()),
                timing={c: float(T[c].iloc[-1]) for c in T}, feature_strength=contrib)
    return {kk: float(v) for kk, v in w.items() if abs(v) > 1e-4}, info

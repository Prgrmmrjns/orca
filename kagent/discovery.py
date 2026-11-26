"""Signal discovery on the last 2 years (Jonas's rule), with an honest split:
year A (2024-11-26 .. 2025-11-25) finds signals, year B (2025-11-26 .. now) checks them. Nothing is tuned on B.

Each feature is scored by rank-IC against forward 1d / 3d / 7d returns, two ways:
  ts  = pooled time-series IC (does a high value predict this coin going up?)  -> per-date IC across the panel of
        (feature z-scored within its own coin), averaged over dates
  xs  = cross-sectional IC (does a high value predict this coin beating the other coins?)
t-stats use the daily IC series, deflated for overlapping horizons: t = mean / sd * sqrt(n / h).
Daily bars are labelled by their start (UTC); a bar's values are known at the next midnight, when the
forward return starts. Results also re-checked with one extra day of delay.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent.data import CRYPTO, DATA, load

A = ("2024-11-26", "2025-11-25")
B = ("2025-11-26", "2026-12-31")
HORIZONS = (1, 3, 7)


def _panel(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df[col].unstack(0)


def build_features() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    an = pd.read_csv(DATA / "kraken_analytics_daily.csv.gz", index_col=[0, 1], parse_dates=["t"])
    cd = pd.read_csv(DATA / "kraken_candles_daily.csv.gz", index_col=[0, 1], parse_dates=["t"])
    syms = list(CRYPTO.values())
    inv = {v: k for k, v in CRYPTO.items()}
    an = an[an.index.get_level_values(0).isin(syms)]
    cd = cd[cd.index.get_level_values(0).isin(syms)]
    C = _panel(cd, "close").rename(columns=inv)
    H = _panel(cd, "high").rename(columns=inv)
    L = _panel(cd, "low").rename(columns=inv)
    V = (_panel(cd, "volume").rename(columns=inv) * C)
    g = lambda c: _panel(an, c).rename(columns=inv).reindex(C.index)
    oi, ls, liq, agg = g("open-interest.close"), g("long-short-ratio"), g("liquidation-volume"), g("aggressor-differential")
    top_long, tv, tc = g("top-traders.top20Percent.longPercent"), g("trade-volume"), g("trade-count")
    rvol, basis, buy, sell = g("rolling-volatility"), g("future-basis.basis"), g("cvd.buy_volume"), g("cvd.sell_volume")
    r = C.pct_change(fill_method=None)
    F = {}
    # --- price / volume
    for n in (1, 3, 7, 14, 30):
        F[f"px_ret{n}"] = C.pct_change(n, fill_method=None)
    for n in (20, 50, 100):
        F[f"px_sma{n}"] = C / C.rolling(n).mean() - 1
    F["px_vol7"] = r.rolling(7).std()
    F["px_vol30"] = r.rolling(30).std()
    F["px_volratio"] = F["px_vol7"] / F["px_vol30"]
    F["px_range1"] = (H - L) / C
    F["px_dd30"] = C / C.rolling(30).max() - 1
    F["px_up30"] = C / C.rolling(30).min() - 1
    up, dn = r.clip(lower=0).rolling(14).mean(), (-r.clip(upper=0)).rolling(14).mean()
    F["px_rsi14"] = up / (up + dn)
    lv = np.log1p(V)
    F["px_volume_z"] = (lv - lv.rolling(30).mean()) / lv.rolling(30).std()
    F["px_skew30"] = r.rolling(30).skew()
    F["px_maxret30"] = r.rolling(30).max()
    # --- Kraken positioning / flow
    loi = np.log(oi)
    F["oi_chg1"] = loi.diff(1)
    F["oi_chg7"] = loi.diff(7)
    F["oi_z30"] = (loi - loi.rolling(30).mean()) / loi.rolling(30).std()
    F["oi_vs_price7"] = loi.diff(7) - np.log(C).diff(7)
    F["oi_turnover"] = tv / oi
    F["ls_ratio"] = ls
    F["ls_chg7"] = ls.diff(7)
    F["ls_z30"] = (ls - ls.rolling(30).mean()) / ls.rolling(30).std()
    F["top_long"] = top_long
    F["top_long_chg7"] = top_long.diff(7)
    F["top_vs_crowd"] = top_long / 100 - ls / (1 + ls)
    F["liq_1"] = liq / oi
    F["liq_3"] = liq.rolling(3).sum() / oi
    F["liq_z30"] = (liq - liq.rolling(30).mean()) / liq.rolling(30).std()
    F["taker_imb1"] = agg / tv
    F["taker_imb3"] = agg.rolling(3).sum() / tv.rolling(3).sum()
    F["taker_imb7"] = agg.rolling(7).sum() / tv.rolling(7).sum()
    F["cvd_imb7"] = (buy - sell).rolling(7).sum() / (buy + sell).rolling(7).sum()
    F["tradecount_z"] = (np.log1p(tc) - np.log1p(tc).rolling(30).mean()) / np.log1p(tc).rolling(30).std()
    F["avg_trade_size_z"] = np.log(tv / tc) - np.log(tv / tc).rolling(30).mean()
    F["rvol_kraken"] = rvol
    F["basis"] = basis
    # --- market-wide (same value for all coins)
    btc = C["BTC"]
    mk = {
        "mkt_btc_ret1": btc.pct_change(), "mkt_btc_ret7": btc.pct_change(7),
        "mkt_btc_sma50": btc / btc.rolling(50).mean() - 1,
        "mkt_breadth20": (C > C.rolling(20).mean()).mean(axis=1),
        "mkt_oi_chg7": np.log(oi.sum(axis=1, min_count=5)).diff(7) if False else loi.diff(7).mean(axis=1),
        "mkt_taker_imb3": F["taker_imb3"].mean(axis=1),
        "mkt_ls": ls.mean(axis=1),
        "mkt_liq3": F["liq_3"].mean(axis=1),
        "mkt_dispersion7": C.pct_change(7).std(axis=1),
        "mkt_ethbtc_7": (C["ETH"] / btc).pct_change(7),
        "cal_dow": pd.Series(C.index.dayofweek, index=C.index).astype(float),
        "cal_weekend": pd.Series((C.index.dayofweek >= 5).astype(float), index=C.index),
    }
    try:
        from kagent.round3 import load_alt
        alt = load_alt(C.index)
        mk["alt_fng"] = alt["fng"]
        mk["alt_fng_chg7"] = alt["fng"].diff(7)
        mk["alt_stables_30"] = np.log(alt["stables"]).diff(30)
        nl = alt["WALCL"] - alt["RRPONTSYD"].fillna(0) * 1000 - alt["WTREGEN"]
        mk["alt_netliq_28"] = np.log(nl).diff(28)
        mk["alt_dxy_28"] = np.log(alt["DTWEXBGS"]).diff(28)
        mk["alt_ry_28"] = alt["DFII10"].diff(28)
        mk["alt_mvrv_chg30"] = np.log(alt["btc_CapMVRVCur"]).diff(30)
        exf = (alt["btc_FlowInExUSD"] - alt["btc_FlowOutExUSD"]) / alt["btc_CapMrktCurUSD"]
        mk["alt_btc_exflow7"] = exf.rolling(7).sum()
        mk["alt_deribit_fund30"] = alt["fund_BTC"].rolling(30).mean() * 365
    except Exception as e:  # noqa
        print("alt data skipped:", e)
    try:
        y = load("close")
        for k in ("NDX", "SPX", "GOLD", "EUR", "WTI"):
            s = y[k].reindex(C.index, method="ffill")
            mk[f"x_{k}_ret1"] = s.pct_change()
            mk[f"x_{k}_ret5"] = s.pct_change(5)
    except Exception as e:  # noqa
        print("cross-asset skipped:", e)
    for k, s in mk.items():
        F[k] = pd.DataFrame({c: s for c in C.columns}, index=C.index)
    return F, C


def ic_tables(F: dict[str, pd.DataFrame], C: pd.DataFrame, lag: int = 0) -> pd.DataFrame:
    rows = []
    fwd = {h: C.shift(-h - lag) / C.shift(-lag) - 1 for h in HORIZONS}
    for name, X in F.items():
        X = X.reindex(C.index)
        Xz = (X - X.rolling(90, min_periods=30).mean()) / X.rolling(90, min_periods=30).std()  # per-coin z for ts
        is_mkt = name.startswith(("mkt_", "alt_", "x_", "cal_"))
        for h in HORIZONS:
            Y = fwd[h]
            for kind in (("ts",) if is_mkt else ("ts", "xs")):
                if kind == "xs":
                    ic = X.rank(axis=1).corrwith(Y.rank(axis=1), axis=1)
                elif is_mkt:
                    # market feature vs equal-weight market forward return: one IC per rolling window -> use daily product
                    m = Y.mean(axis=1)
                    s = X.iloc[:, 0]
                    ic = None
                else:
                    ic = Xz.rank(axis=1, pct=True).sub(0.5).mul(Y.sub(Y.mean(axis=1), axis=0).add(Y.mean(axis=1), axis=0).rank(axis=1, pct=True).sub(0.5)) if False else None
                    # pooled TS: correlation of within-coin z-score with own forward return, per date across coins
                    ic = Xz.corrwith(Y, axis=1, method="spearman")
                for lab, (a, b) in (("A", A), ("B", B)):
                    if ic is None:  # market-level: rolling-free spearman on the period, n_eff = n / h
                        d = pd.concat([s, m], axis=1).loc[a:b].dropna()
                        if len(d) < 60:
                            continue
                        rho = d.rank().corr().iloc[0, 1]
                        t = rho * np.sqrt((len(d) / h - 2) / max(1e-9, 1 - rho ** 2))
                        rows.append(dict(feature=name, kind="mkt", h=h, period=lab, ic=rho, t=t, n=len(d)))
                    else:
                        s2 = ic.loc[a:b].dropna()
                        if len(s2) < 60:
                            continue
                        t = s2.mean() / s2.std() * np.sqrt(len(s2) / h)
                        rows.append(dict(feature=name, kind=kind, h=h, period=lab, ic=s2.mean(), t=t, n=len(s2)))
    T = pd.DataFrame(rows).pivot_table(index=["feature", "kind", "h"], columns="period", values=["ic", "t"])
    T.columns = [f"{a}_{b}" for a, b in T.columns]
    return T.reset_index()


if __name__ == "__main__":
    F, C = build_features()
    T = ic_tables(F, C)
    T.to_csv(DATA.parent / "kagent_report" / "signal_discovery.csv", index=False)
    print(T.sort_values("t_A", key=abs, ascending=False).head(40).round(3).to_string())

"""Hourly signal discovery for BTC/ETH/SOL perps on Kraken analytics + candles (last 2 years, A/B split as daily)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kagent.data import DATA
from kagent.discovery import A, B

HZ = (1, 4, 24)


def run() -> pd.DataFrame:
    an = pd.read_csv(DATA / "kraken_analytics_hourly.csv.gz", index_col=[0, 1], parse_dates=["t"])
    cd = pd.read_csv(DATA / "kraken_candles_hourly.csv.gz", index_col=[0, 1], parse_dates=["t"])
    rows = []
    for sym in cd.index.get_level_values(0).unique():
        c = cd.loc[sym]; c = c[~c.index.duplicated()]
        a = an.loc[sym]; a = a[~a.index.duplicated()].reindex(c.index)
        px = c.close
        r = np.log(px).diff()
        f = {}
        for n in (1, 4, 24, 72):
            f[f"ret{n}h"] = np.log(px).diff(n)
        f["vol24"] = r.rolling(24).std()
        f["vol_ratio"] = r.rolling(24).std() / r.rolling(24 * 7).std()
        f["range1h"] = (c.high - c.low) / px
        f["volume_z"] = (np.log1p(c.volume) - np.log1p(c.volume).rolling(168).mean()) / np.log1p(c.volume).rolling(168).std()
        oi = np.log(a["open-interest.close"])
        for n in (1, 4, 24):
            f[f"oi_chg{n}h"] = oi.diff(n)
        f["oi_minus_px24"] = oi.diff(24) - np.log(px).diff(24)
        ls = a["long-short-ratio"]
        f["ls"] = ls
        f["ls_chg24"] = ls.diff(24)
        tv = a["trade-volume"]
        agg = a["aggressor-differential"]
        for n in (1, 4, 24):
            f[f"taker_imb{n}h"] = agg.rolling(n).sum() / tv.rolling(n).sum()
        liq = a["liquidation-volume"] / a["open-interest.close"]
        for n in (1, 4, 24):
            f[f"liq{n}h"] = liq.rolling(n).sum()
        f["top_long"] = a["top-traders.top20Percent.longPercent"]
        f["top_long_chg24"] = f["top_long"].diff(24)
        f["tradecount_z"] = (np.log1p(a["trade-count"]) - np.log1p(a["trade-count"]).rolling(168).mean()) / np.log1p(a["trade-count"]).rolling(168).std()
        f["hour"] = pd.Series(px.index.hour, index=px.index).astype(float)
        f["us_open"] = pd.Series(((px.index.hour >= 13) & (px.index.hour <= 15)).astype(float), index=px.index)
        # hourly bar labelled by start; its close is known at label+1h, so fwd return starts from that close
        for h in HZ:
            y = np.log(px).shift(-h) - np.log(px)
            for k, x in f.items():
                for lab, (s, e) in (("A", A), ("B", B)):
                    d = pd.concat([x, y], axis=1).loc[s:e].dropna()
                    if len(d) < 500:
                        continue
                    rho = d.rank().corr().iloc[0, 1]
                    t = rho * np.sqrt(len(d) / h)
                    rows.append(dict(sym=sym, feature=k, h=h, period=lab, ic=rho, t=t))
    T = pd.DataFrame(rows).groupby(["feature", "h", "period"])[["ic", "t"]].mean().unstack("period")
    T.columns = [f"{a}_{b}" for a, b in T.columns]
    return T.reset_index()


if __name__ == "__main__":
    T = run()
    T.to_csv(DATA.parent / "kagent_report" / "signal_discovery_hourly.csv", index=False)
    rob = T[(T.t_A.abs() > 2) & (T.t_B.abs() > 1.5) & (T.t_A * T.t_B > 0)]
    print("tests", len(T), "robust", len(rob))
    print(T.reindex(T.t_A.abs().sort_values(ascending=False).index).head(25).round(3).to_string())

"""Minute-level macro data surprises from FinancialJuice's Telegram squawk ("X ACTUAL a (FORECAST f, PREVIOUS p) $MACRO").
1. Latency: releases come out on the minute (mostly :00 / :30); the post's second-stamp minus that mark = squawk delay.
2. Trading: per release minute, signal = sum_k sign_k * z_k (z = surprise / std of that indicator's surprises,
   sign_k = learned direction of BTC's 5-min reaction to indicator k, fitted on training folds only).
   Trade BTC and ETH perps (Binance 1m prices as proxy for Kraken), long or short, enter L minutes after the post,
   exit after X minutes. Cost 0.20% round trip (0.05% fee + 0.05% slippage per side). Purged CPCV over time blocks.
python -m kagent.events_macro"""
from __future__ import annotations

import itertools, re

import numpy as np
import pandas as pd

from kagent.data import DATA

RT = 0.0020
LAT, EXITS = (1, 2, 5), (5, 15, 60, 240)
NUM = r"(-?[\d.]+)\s*([%KMBT]?)"


def _num(s):
    m = re.match(NUM, s.strip())
    if not m:
        return np.nan
    v = float(m.group(1)) if m.group(1) not in ("-", ".") else np.nan
    return v * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2), 1)


def parse():
    df = pd.read_csv(DATA / "news/tg_financialjuice.csv.gz")
    df["ts"] = pd.to_datetime(df.ts, utc=True, format="mixed")
    x = df[df.text.str.contains("ACTUAL", na=False) & df.text.str.contains("FORECAST", na=False)].copy()
    pat = re.compile(r"^\W*(.*?)\s+ACTUAL\s+(.+?)\s*\(FORECAST\s+(.+?),\s*PREVIOUS\s+(.+?)\)", re.S)
    rows = []
    for _, r in x.iterrows():
        m = pat.search(r.text)
        if m:
            rows.append(dict(ts=r.ts, ind=m.group(1).strip().upper(), actual=_num(m.group(2)), forecast=_num(m.group(3))))
    e = pd.DataFrame(rows).dropna()
    e["surprise"] = e.actual - e.forecast
    sd = e.groupby("ind").surprise.transform(lambda s: s.std() if s.std() > 0 else np.nan)
    e["z"] = (e.surprise / sd).clip(-4, 4)
    e["n_ind"] = e.groupby("ind").ind.transform("size")
    e["mark"] = e.ts.dt.floor("1min")
    e["delay_s"] = e.ts.dt.second + 60 * (e.ts.dt.minute % 15)
    return e


def prices(coin):
    p = pd.read_parquet(DATA / f"m1/{coin}.parquet").close
    p.index = p.index.tz_localize("UTC")
    return p


def reactions(e, px):
    out = []
    for t in e.mark.unique():
        r = {"mark": t}
        try:
            p0 = px.asof(t)
            r["ret5_from_mark"] = px.asof(t + pd.Timedelta("5min")) / p0 - 1
            for L in LAT:
                pin = px.asof(t + pd.Timedelta(minutes=L))
                for X in EXITS:
                    r[f"r_L{L}_X{X}"] = px.asof(t + pd.Timedelta(minutes=L + X)) / pin - 1
        except Exception:
            continue
        out.append(r)
    return pd.DataFrame(out).set_index("mark")


def cpcv(e, R, nb=10, kt=3, min_n=8):
    e = e[(e.n_ind >= min_n) & e.mark.isin(R.index)]
    marks = np.array(sorted(e.mark.unique()))
    edges = np.linspace(0, len(marks), nb + 1).astype(int)
    res = []
    for combo in itertools.combinations(range(nb), kt):
        te = np.concatenate([np.arange(edges[b], edges[b + 1]) for b in combo])
        ban = np.zeros(len(marks), bool)
        for b in combo:
            ban[max(0, edges[b] - 2):min(len(marks), edges[b + 1] + 2)] = True
        tr_m, te_m = set(marks[~ban]), set(marks[te])
        etr = e[e.mark.isin(tr_m)].join(R["ret5_from_mark"], on="mark")
        sign = etr.groupby("ind").apply(lambda g: np.sign((g.z * g.ret5_from_mark).mean()))
        ete = e[e.mark.isin(te_m)].copy()
        ete["s"] = ete.ind.map(sign).fillna(0) * ete.z
        sig = ete.groupby("mark").s.sum()
        sig = sig[sig.abs() > 0.5]
        for c in R.columns:
            if not c.startswith("r_"):
                continue
            pnl = np.sign(sig) * R.loc[sig.index, c] - RT
            res.append(dict(combo=combo, col=c, n=len(pnl), mean_bp=pnl.mean() * 1e4, hit=(pnl > 0).mean()))
    return pd.DataFrame(res)


if __name__ == "__main__":
    e = parse()
    e = e[e.ts >= "2024-11-26"]
    print("releases parsed:", len(e), "release minutes:", e.mark.nunique())
    print("squawk delay after the minute mark (s): median", e.ts.dt.second.median(), " p10/p90", e.ts.dt.second.quantile([.1, .9]).tolist())
    print(e.ind.value_counts().head(25).to_string())
    e.to_csv("research/kagent_report/macro_releases.csv.gz", index=False)
    for coin in ("BTC", "ETH"):
        R = reactions(e, prices(coin))
        res = cpcv(e, R)
        s = res.groupby("col").agg(n=("n", "median"), mean_bp=("mean_bp", "median"), p10=("mean_bp", lambda x: x.quantile(.1)),
                                    p_pos=("mean_bp", lambda x: (x > 0).mean()), hit=("hit", "median")).round(1)
        print(coin); print(s.to_string())
        res.to_csv(f"research/kagent_report/macro_cpcv_{coin}.csv.gz", index=False)

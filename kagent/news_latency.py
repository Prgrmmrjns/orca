"""Which free source reports first? Measured from history (second-stamped Telegram posts vs official times).
A) Binance listings/delistings: official CMS release time vs first post naming Binance + the ticker per channel.
B) US macro releases (CPI, PPI, PCE, jobless claims, NFP, ISM, retail sales): official minute vs first post per channel.
C) How much of BTC's 15-minute reaction has happened after 15 s / 30 s / 60 s (1m bars, so 60 s is the floor we can see).
python -m kagent.news_latency"""
import re
import numpy as np
import pandas as pd
from kagent.data import DATA
from kagent.events_binance import classify
from kagent.events_macro import parse, prices

CH = ["financialjuice", "WatcherGuru", "unfolded", "wublockchainenglish"]


def tg():
    out = {}
    for c in CH:
        d = pd.read_csv(DATA / f"news/tg_{c}.csv.gz")
        d["ts"] = pd.to_datetime(d.ts, utc=True, format="mixed")
        out[c] = d[d.ts >= "2024-11-26"].dropna(subset=["text"]).sort_values("ts")
    return out


def first_after(d, t0, pat, window="60min"):
    x = d[(d.ts >= t0 - pd.Timedelta("2min")) & (d.ts <= t0 + pd.Timedelta(window))]
    x = x[x.text.str.contains(pat, case=False, regex=True)]
    return (x.ts.iloc[0] - t0).total_seconds() if len(x) else np.nan


def binance_lat(T):
    ev = pd.concat([pd.read_csv(DATA / f"news/binance_{f}.csv.gz") for f in ("listing", "delisting")])
    ev["ts"] = pd.to_datetime(ev.ts, utc=True, format="mixed")
    ev = ev[ev.ts >= "2024-11-26"]
    rows = []
    for _, e in ev.iterrows():
        kind, tks = classify(e.text)
        if not kind or not tks or kind == "futures_launch":
            continue
        pat = r"binance.*\b(" + "|".join(map(re.escape, tks[:5])) + r")\b|\b(" + "|".join(map(re.escape, tks[:5])) + r")\b.*binance"
        rows.append({"kind": kind, **{c: first_after(T[c], e.ts, pat) for c in CH}})
    return pd.DataFrame(rows)


def macro_lat(T):
    e = parse()
    e = e[(e.ts >= "2024-11-26") & e.ind.str.startswith("US")]
    keys = {"CPI": r"\bCPI\b", "PPI": r"\bPPI\b", "PCE": r"\bPCE\b", "JOBLESS": r"jobless", "NONFARM": r"nonfarm|non-farm|payrolls",
            "ISM": r"\bISM\b", "RETAIL": r"retail sales"}
    rows = []
    for k, pat in keys.items():
        for m in e[e.ind.str.contains(k)].mark.unique():
            rows.append({"ind": k, **{c: first_after(T[c], m, pat, "30min") for c in CH}})
    return pd.DataFrame(rows)


def reaction_profile():
    e = parse()
    e = e[(e.ts >= "2024-11-26") & e.ind.str.contains("US CPI|US CORE CPI|NONFARM|US PPI|PCE PRICE", regex=True)]
    px = prices("BTC")
    rows = []
    for m in e.mark.unique():
        p0 = px.asof(m - pd.Timedelta("1min"))
        path = [px.asof(m + pd.Timedelta(minutes=i)) / p0 - 1 for i in range(0, 16)]
        if np.isfinite(path).all() and abs(path[-1]) > 0:
            rows.append(np.array(path) / path[-1])
    P = pd.DataFrame(rows, columns=[f"{i}m" for i in range(16)])
    return P.median()


if __name__ == "__main__":
    T = tg()
    b = binance_lat(T)
    print("Binance announcements: seconds after the official post (median, n found)")
    print(pd.DataFrame({c: [b[c].median(), b[c].notna().sum()] for c in CH}, index=["median_s", "n"]).round(0).to_string())
    m = macro_lat(T)
    print("US macro releases: seconds after the release minute (median, n found)")
    print(m.groupby("ind")[CH].median().round(0).to_string())
    print(pd.DataFrame({c: [m[c].median(), m[c].quantile(.1), m[c].quantile(.9), m[c].notna().sum()] for c in CH},
                       index=["median_s", "p10", "p90", "n"]).round(0).to_string())
    print("BTC: share of the 15-min move done by minute i (minute 0 = the release minute's bar):")
    print(reaction_profile().round(2).to_string())
    b.to_csv("research/kagent_report/latency_binance.csv", index=False); m.to_csv("research/kagent_report/latency_macro.csv", index=False)

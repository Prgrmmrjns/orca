"""Minute-level event study: Binance listing / delisting announcements -> trade the coin on Kraken perps.
Long on listings, short on delistings (profit from falling prices). Entry after a latency of 1, 2 or 5 minutes
(we see the post within seconds via the CMS API / Telegram), exit after 5m..24h. Costs: 0.05% fee per side plus
slippage 0.30% per side (thin books during news spikes) -> 0.70% round trip. Shorts pay no funding over <1 day.
python -m kagent.events_binance"""
from __future__ import annotations

import json, re, time, urllib.request

import numpy as np
import pandas as pd

from kagent.data import DATA

RT_COST = 2 * (0.0005 + 0.0030)
LAT = (1, 2, 5)
EXITS = (5, 15, 60, 240, 1440)


def classify(title):
    t = title
    if re.search(r"Will Delist|Will Remove", t):
        kind = "delist"
        if "Futures Will Delist" in t:
            tk = re.findall(r"USDⓈ-M ([A-Z0-9]+?)USDT", t)
        else:
            m = re.search(r"Delist ([A-Z0-9, &]+?) on", t)
            tk = re.split(r",\s*|\s*&\s*", m.group(1)) if m else re.findall(r"\(([A-Z0-9]+)\)", t)
    elif re.search(r"Futures Will Launch", t):
        kind, tk = "futures_launch", re.findall(r"USDⓈ-M ([A-Z0-9]{2,12}?)(?:USDT)? Perpetual", t) or re.findall(r"\b([A-Z0-9]{2,10})USDT\b", t)
    elif re.search(r"Will List|Will Add .* on Earn|Will Be Available on Binance", t):
        kind, tk = "spot_list", re.findall(r"\(([A-Z0-9]+)\)", t)
    else:
        return None, []
    return kind, [x.strip() for x in tk if x.strip()]


def kraken_perps():
    d = json.loads(urllib.request.urlopen("https://futures.kraken.com/derivatives/api/v3/instruments", timeout=30).read())
    return {i["base"].upper(): i["symbol"] for i in d["instruments"] if i.get("type") == "flexible_futures" and i["symbol"].startswith("PF_")}


def m1(sym, t):
    a, b = int((t - pd.Timedelta("30min")).timestamp()), int((t + pd.Timedelta("1450min")).timestamp())
    rows = []
    for t0 in range(a, b, 2000 * 60):
        try:
            d = json.loads(urllib.request.urlopen(f"https://futures.kraken.com/api/charts/v1/trade/{sym}/1m?from={t0}&to={min(b, t0 + 2000 * 60)}", timeout=30).read())
            rows += d.get("candles", [])
        except Exception:
            time.sleep(1)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("time"), unit="ms", utc=True)
    return df.close.astype(float).sort_index().groupby(level=0).last()


def study(since="2024-11-26"):
    ev = pd.concat([pd.read_csv(DATA / f"news/binance_{f}.csv.gz") for f in ("listing", "delisting")])
    ev["ts"] = pd.to_datetime(ev.ts, utc=True, format="mixed")
    ev = ev[ev.ts >= since]
    perps = kraken_perps()
    rows = []
    for _, e in ev.iterrows():
        kind, tks = classify(e.text)
        for tk in tks:
            sym = perps.get(tk)
            if not kind or not sym:
                continue
            px = m1(sym, e.ts)
            if px is None or len(px) < 100:
                continue
            px = px.reindex(pd.date_range(px.index[0], px.index[-1], freq="1min")).ffill()
            t0 = e.ts.floor("1min")
            try:
                p_ann = px.asof(t0)
            except Exception:
                continue
            r = dict(ts=e.ts, kind=kind, coin=tk, title=e.text[:120], pre30=px.asof(t0) / px.asof(t0 - pd.Timedelta("30min")) - 1)
            side = -1 if kind == "delist" else 1
            for L in LAT:
                pin = px.asof(t0 + pd.Timedelta(minutes=L))
                r[f"move_{L}m"] = pin / p_ann - 1
                for X in EXITS:
                    pout = px.asof(t0 + pd.Timedelta(minutes=L + X))
                    r[f"net_L{L}_X{X}"] = side * (pout / pin - 1) - RT_COST
            rows.append(r)
            time.sleep(0.2)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = study()
    df.to_csv("research/kagent_report/events_binance.csv", index=False)
    print(df.kind.value_counts())
    cols = [c for c in df if c.startswith("net_")]
    g = df.groupby("kind")[cols + ["move_1m", "move_5m"]]
    print((g.mean() * 100).round(2).T.to_string())
    print((g.apply(lambda x: (x[cols] > 0).mean())).round(2).T.to_string())

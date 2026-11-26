"""Kraken Futures public analytics (positioning / flow) + perp candles, for signal research.

Per perp, daily (and hourly for a few majors):
open interest (OHLC), long/short ratio, liquidation volume, aggressor differential (taker buy-sell),
top-20% traders' open interest / long share, trade volume & count, rolling volatility, perp basis, buy/sell volume.
Endpoint: futures.kraken.com/api/charts/v1/analytics/{symbol}/{type}?since=..&interval=..  (public)
"""
from __future__ import annotations

import json
import time
import urllib.request

import pandas as pd

from kagent.data import DATA

TYPES = ["open-interest", "long-short-ratio", "liquidation-volume", "aggressor-differential", "top-traders",
         "trade-volume", "trade-count", "rolling-volatility", "future-basis", "cvd"]


def _get(url):
    for i in range(5):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "orca-research"}), timeout=60).read())
        except Exception:
            time.sleep(2 * (i + 1))
    return {}


def _flatten(name, data):
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    out[f"{name}.{k}.{k2}"] = v2
            else:
                out[f"{name}.{k}"] = v
        return out
    if data and isinstance(data[0], list):  # OHLC
        return {f"{name}.close": [x[3] for x in data], f"{name}.high": [x[1] for x in data], f"{name}.low": [x[2] for x in data]}
    return {name: data}


def analytics(symbol: str, interval: int = 86400, since: int | None = None) -> pd.DataFrame:
    since = since or int(time.time()) - 2 * 365 * 86400 - 30 * 86400
    frames = []
    for t in TYPES:
        cols, ts, s = {}, [], since
        while True:
            d = _get(f"https://futures.kraken.com/api/charts/v1/analytics/{symbol}/{t}?since={s}&interval={interval}")
            r = d.get("result")
            if not r or not r.get("timestamp"):
                break
            flat = _flatten(t, r["data"])
            for k, v in flat.items():
                cols.setdefault(k, []).extend(v)
            ts.extend(r["timestamp"])
            if str(r.get("more")) != "True":
                break
            s = r["timestamp"][-1] + interval
        if ts:
            n = len(ts)
            df = pd.DataFrame({k: v[:n] for k, v in cols.items() if len(v) >= n}, index=pd.to_datetime(ts, unit="s"))
            frames.append(df.apply(pd.to_numeric, errors="coerce"))
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def candles(symbol: str, res: str = "1d", since: int | None = None) -> pd.DataFrame:
    since = since or int(time.time()) - 2 * 365 * 86400 - 30 * 86400
    out, s = [], since
    while True:
        d = _get(f"https://futures.kraken.com/api/charts/v1/trade/{symbol}/{res}?from={s}&to={int(time.time())}")
        c = d.get("candles", [])
        if not c:
            break
        out += c
        if not d.get("more_candles"):
            break
        s = c[-1]["time"] // 1000 + 1
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.pop("time"), unit="ms")
    return df.astype(float)[~df.index.duplicated()]


def build(symbols=None, hourly=("PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD")):
    from kagent.data import CRYPTO
    symbols = symbols or list(CRYPTO.values()) + ["PF_NVDAXUSD", "PF_TSLAXUSD", "PF_SPYXUSD", "PF_QQQXUSD", "PF_MSTRXUSD",
                                                  "PF_COINXUSD", "PF_XAUUSD", "PF_US100USD", "PF_HYPEUSD", "PF_ZECUSD"]
    A, P = {}, {}
    for s in symbols:
        a = analytics(s)
        c = candles(s)
        if len(a):
            A[s] = a
        if len(c):
            P[s] = c
        print(s, a.shape, len(c), flush=True)
    pd.concat(A, names=["symbol", "t"]).to_csv(DATA / "kraken_analytics_daily.csv.gz")
    pd.concat(P, names=["symbol", "t"]).to_csv(DATA / "kraken_candles_daily.csv.gz")
    HA, HP = {}, {}
    for s in hourly:
        HA[s] = analytics(s, 3600)
        HP[s] = candles(s, "1h")
        print("hourly", s, HA[s].shape, len(HP[s]), flush=True)
    pd.concat(HA, names=["symbol", "t"]).to_csv(DATA / "kraken_analytics_hourly.csv.gz")
    pd.concat(HP, names=["symbol", "t"]).to_csv(DATA / "kraken_candles_hourly.csv.gz")


if __name__ == "__main__":
    build()

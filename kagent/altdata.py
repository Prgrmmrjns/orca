"""Alternative daily inputs with multi-year free history (public endpoints, no keys).

- Deribit perpetual funding (BTC, ETH) 2019+: hourly interest_1h -> daily sum
- Coin Metrics community: BTC/ETH MVRV, active addresses, tx count, exchange net flows where free
- alternative.me Fear & Greed index 2018+
- DefiLlama total stablecoin supply 2017+
- FRED: Fed balance sheet (WALCL), reverse repo (RRPONTSYD), TGA (WTREGEN), broad dollar (DTWEXBGS), 10y real yield (DFII10)
- GDELT news tone/volume timelines for crypto keywords 2017+
"""
from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from kagent.data import DATA


def _get(url, raw=False, tries=5, wait=3):
    req = urllib.request.Request(url, headers={"User-Agent": "orca-research"})
    for i in range(tries):
        try:
            b = urllib.request.urlopen(req, timeout=60).read()
            return b if raw else json.loads(b)
        except Exception as e:  # noqa
            err = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def deribit_funding(inst="BTC-PERPETUAL") -> pd.Series:
    out = {}
    start = int(pd.Timestamp("2019-05-01").timestamp() * 1000)
    end_all = int(time.time() * 1000)
    while start < end_all:
        end = min(start + 30 * 86400000, end_all)
        r = _get(f"https://www.deribit.com/api/v2/public/get_funding_rate_history?instrument_name={inst}&start_timestamp={start}&end_timestamp={end}")["result"]
        for x in r:
            out[pd.Timestamp(x["timestamp"], unit="ms")] = x["interest_1h"]
        start = end
    s = pd.Series(out).sort_index()
    return s.resample("1D").sum()  # daily funding (fraction)


def coinmetrics(asset="btc", metrics=("CapMVRVCur", "AdrActCnt", "TxCnt", "CapMrktCurUSD", "FlowInExUSD", "FlowOutExUSD", "SplyExNtv")) -> pd.DataFrame:
    rows, url = [], ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=" + asset +
                     "&metrics=" + ",".join(metrics) + "&start_time=2017-01-01&page_size=10000&ignore_unsupported_errors=true&ignore_forbidden_errors=true")
    while url:
        d = _get(url)
        rows += d.get("data", [])
        url = d.get("next_page_url")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("time")).dt.tz_localize(None).dt.normalize()
    df = df.drop(columns=["asset"]).apply(pd.to_numeric, errors="coerce")
    return df.add_prefix(asset + "_")


def fear_greed() -> pd.Series:
    d = _get("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    return pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s"): float(x["value"]) for x in d}).sort_index().rename("fng")


def stablecoins() -> pd.Series:
    d = _get("https://stablecoins.llama.fi/stablecoincharts/all")
    return pd.Series({pd.Timestamp(int(x["date"]), unit="s"): x["totalCirculatingUSD"].get("peggedUSD", 0) for x in d}).sort_index().rename("stables")


def fred(ids=("WALCL", "RRPONTSYD", "WTREGEN", "DTWEXBGS", "DFII10")) -> pd.DataFrame:
    out = {}
    for i in ids:
        import subprocess
        b = subprocess.run(["curl", "-s", "-m", "60", "--retry", "4", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={i}"],
                           capture_output=True, check=True).stdout
        s = pd.read_csv(io.BytesIO(b), index_col=0, parse_dates=True).iloc[:, 0]
        out[i] = pd.to_numeric(s, errors="coerce")
    return pd.DataFrame(out).loc["2016":]


def gdelt_tone(query='(bitcoin OR crypto OR cryptocurrency)') -> pd.DataFrame:
    """Daily average tone and article-volume share for the query, fetched in 1-year chunks (GDELT allows 1 req / 5 s)."""
    parts = []
    for y in range(2017, pd.Timestamp.now().year + 1):
        for mode in ("timelinetone", "timelinevolraw"):
            q = urllib.parse.quote(query)
            u = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode={mode}&format=json"
                 f"&startdatetime={y}0101000000&enddatetime={y}1231235959&timelinesmooth=0")
            time.sleep(6)
            d = _get(u, tries=6, wait=8)
            tl = d["timeline"][0]["data"]
            s = pd.Series({pd.Timestamp(x["date"][:8]): x["value"] for x in tl}, name=mode)
            parts.append(s)
    df = pd.concat(parts, axis=1)
    return df.groupby(level=0).first()


def _cached(name, fn):
    p = DATA / f"_raw_{name}.pkl"
    if p.exists():
        return pd.read_pickle(p)
    x = fn()
    x.to_pickle(p)
    return x


def build():
    out = {}
    for inst in ("BTC", "ETH"):
        out[f"fund_{inst}"] = _cached(f"fund_{inst}", lambda i=inst: deribit_funding(f"{i}-PERPETUAL")); print("deribit", inst, len(out[f"fund_{inst}"]))
    cm = _cached("cm", lambda: pd.concat([coinmetrics("btc"), coinmetrics("eth")], axis=1))
    cm = cm[[c for c in cm.columns if "status" not in c]]; print("coinmetrics", cm.shape, cm.notna().sum().to_dict())
    df = pd.concat([pd.DataFrame(out), cm, fear_greed(), stablecoins(), fred()], axis=1).sort_index()
    df.to_csv(DATA / "altdata.csv.gz")
    print(df.loc["2018":].notna().mean().round(2).to_dict())
    # GDELT DOC API only serves a rolling ~3-month window, so it cannot provide multi-year news history.


if __name__ == "__main__":
    build()

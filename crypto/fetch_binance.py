"""Download Binance public spot klines (data.binance.vision) for research.

Kraken's public OHLC endpoint only returns the last 720 bars, so long-history
research uses Binance USDT pairs; live trading uses Kraken prices.
"""
from __future__ import annotations
import io, sys, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd, requests

BASE = "https://data.binance.vision/data/spot/monthly/klines"
CACHE = Path(__file__).parent / "data_cache"
COLS = ["open_time","open","high","low","close","volume","close_time","qvol","trades","tbv","tbq","ig"]

def month_list(start="2019-01"):
    y, m = map(int, start.split("-")); out = []
    today = dt.date.today()
    while (y, m) < (today.year, today.month):
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out

def fetch_month(sym, itv, ym):
    url = f"{BASE}/{sym}/{itv}/{sym}-{itv}-{ym}.zip"
    for _ in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404: return None
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS)
            if str(df.iloc[0, 0]).startswith("open"): df = df.iloc[1:]
            df = df.astype(float)
            ot = df["open_time"]
            # 2025+ files use microseconds
            df["open_time"] = ot.where(ot < 1e14, ot / 1000).astype("int64")
            return df[["open_time","open","high","low","close","volume","qvol"]]
        except Exception as e:  # noqa
            err = e
    print("fail", url, err, file=sys.stderr); return None

def fetch_symbol(sym, itv="1h", start="2019-01"):
    out = CACHE / f"{sym}_{itv}.parquet"
    if out.exists(): return out
    with ThreadPoolExecutor(8) as ex:
        parts = [p for p in ex.map(lambda ym: fetch_month(sym, itv, ym), month_list(start)) if p is not None]
    if not parts: return None
    df = pd.concat(parts).drop_duplicates("open_time").sort_values("open_time")
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.drop(columns="open_time").set_index("ts").to_parquet(out)
    return out

DEFAULT = ("BTC ETH SOL XRP ADA DOGE LTC LINK AVAX DOT BCH TRX XLM ATOM UNI AAVE NEAR ETC FIL ALGO XTZ HBAR ICP INJ APT "
           "ARB SUI SEI TON BNB XMR ZEC SHIB PEPE CRV LDO GRT SAND MANA AXS COMP DASH MATIC POL TAO ONDO JUP KSM FLOW "
           "CHZ BAT KAVA MINA").split()   # coins with a Kraken EUR margin pair and Binance history

if __name__ == "__main__":
    CACHE.mkdir(exist_ok=True)
    for s in sys.argv[1:] or DEFAULT:
        p = fetch_symbol(s + "USDT")
        print(s, "ok" if p else "MISSING", flush=True)

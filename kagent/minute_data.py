"""1-minute bars for the 20 coins (Binance USD-M perps, data.binance.vision monthly zips) for the last ~2 years,
plus Kraken Futures 1m for the stock/commodity perps since their launch.
python -m kagent.minute_data  -> research/kagent_data/m1/<coin>.parquet (close, volume, taker_buy_volume)"""
from __future__ import annotations

import io, json, sys, time, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from kagent.data import CRYPTO, DATA

OUT = DATA / "m1"
KRAKEN_PERPS = ["PF_SPYXUSD", "PF_QQQXUSD", "PF_GLDXUSD", "PF_NVDAXUSD", "PF_TSLAXUSD", "PF_MSTRXUSD", "PF_COINXUSD",
                "PF_AAPLXUSD", "PF_GOOGLXUSD", "PF_HOODXUSD", "PF_AMZNXUSD", "PF_METAXUSD", "PF_XAUUSD", "PF_US100USD", "PF_WTIOILUSD"]


def _month(coin, m):
    u = f"https://data.binance.vision/data/futures/um/monthly/klines/{coin}USDT/1m/{coin}USDT-1m-{m}.zip"
    for i in range(3):
        try:
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read()))
            df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 4, 5, 9])
            if not str(df.iloc[0, 0]).isdigit():
                df = df.iloc[1:]
            df.columns = ["t", "close", "volume", "taker_buy"]
            df = df.astype(float)
            df.index = pd.to_datetime(df.pop("t").astype("int64"), unit="ms")
            return df.astype("float32")
        except Exception:
            time.sleep(3)
    return None


def binance_coin(coin, start="2024-09"):
    months = [p.strftime("%Y-%m") for p in pd.period_range(start, pd.Timestamp.today() - pd.offsets.MonthBegin(1), freq="M")]
    with ThreadPoolExecutor(3) as ex:
        parts = [p for p in ex.map(lambda m: _month(coin, m), months) if p is not None]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated()]
    df.to_parquet(OUT / f"{coin}.parquet")
    print(coin, len(df), df.index[0], df.index[-1], flush=True)


def kraken_perp(sym, start="2024-09-01"):
    t0, end, rows = int(pd.Timestamp(start).timestamp()), int(time.time()), []
    while t0 < end:
        t1 = t0 + 2000 * 60
        try:
            d = json.loads(urllib.request.urlopen(f"https://futures.kraken.com/api/charts/v1/trade/{sym}/1m?from={t0}&to={t1}", timeout=30).read())
            rows += d.get("candles", [])
        except Exception:
            time.sleep(2); continue
        t0 = t1
        time.sleep(0.15)
    if not rows:
        print(sym, 0); return
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("time"), unit="ms")
    df = df[["close", "volume"]].astype("float32")
    df = df[~df.index.duplicated()].sort_index()
    df.to_parquet(OUT / f"kraken_{sym}.parquet")
    print(sym, len(df), df.index[0], flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "coins"):
        for c in CRYPTO:
            binance_coin(c)
    if what in ("all", "kraken"):
        for s in KRAKEN_PERPS:
            kraken_perp(s, "2025-12-01")

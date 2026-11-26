"""Market data for the Kraken agent.

- Crypto daily OHLCV 2017+ from Binance's public data mirror (deeper history than Kraken offers);
  used as a price proxy for Kraken perps (BTC/ETH/... trade within a few bps across venues).
- Kraken perp daily candles (2022-03+) and hourly funding (last ~12 months) from Kraken Futures public API.
- Traditional assets (gold, silver, oil, Nasdaq-100, S&P 500, EURUSD) from Yahoo as proxies for Kraken's
  RWA perps (PF_XAUUSD, PF_XAGUSD, PF_WTIOILUSD, PF_US100USD, PF_SPYXUSD, PF_EURUSD).
Public endpoints only; no keys.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "research" / "kagent_data"

# Liquid Kraken perps with long histories (symbol -> Kraken perp)
CRYPTO = {
    "BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD", "XRP": "PF_XRPUSD",
    "ADA": "PF_ADAUSD", "DOGE": "PF_DOGEUSD", "LINK": "PF_LINKUSD", "AVAX": "PF_AVAXUSD",
    "LTC": "PF_LTCUSD", "DOT": "PF_DOTUSD", "BCH": "PF_BCHUSD", "XLM": "PF_XLMUSD",
    "UNI": "PF_UNIUSD", "ATOM": "PF_ATOMUSD", "NEAR": "PF_NEARUSD", "AAVE": "PF_AAVEUSD",
    "FIL": "PF_FILUSD", "ETC": "PF_ETCUSD", "TRX": "PF_TRXUSD", "INJ": "PF_INJUSD",
}
TRAD = {  # yahoo ticker -> (name, kraken perp)
    "GC=F": ("GOLD", "PF_XAUUSD"), "SI=F": ("SILVER", "PF_XAGUSD"), "CL=F": ("WTI", "PF_WTIOILUSD"),
    "^NDX": ("NDX", "PF_US100USD"), "^GSPC": ("SPX", "PF_SPYXUSD"), "EURUSD=X": ("EUR", "PF_EURUSD"),
}


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "orca-research"})
    for i in range(4):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError(url)


def fetch_binance_daily(sym: str) -> pd.DataFrame:
    rows, start = [], 1500000000000
    while True:
        r = _get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval=1d&startTime={start}&limit=1000")
        if not r:
            break
        rows += r
        start = r[-1][0] + 86400000
        if len(r) < 1000:
            break
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 7]]
    df.columns = ["t", "open", "high", "low", "close", "qvol"]
    df.index = pd.to_datetime(df.pop("t"), unit="ms")
    return df.astype(float)


def fetch_kraken_perp_daily(symbol: str) -> pd.DataFrame:
    d = _get(f"https://futures.kraken.com/api/charts/v1/trade/{symbol}/1d?from=1600000000&to={int(time.time())}")
    df = pd.DataFrame(d["candles"])
    if df.empty:
        return df
    df.index = pd.to_datetime(df.pop("time"), unit="ms")
    return df.astype(float)


def fetch_kraken_funding(symbol: str) -> pd.Series:
    d = _get(f"https://futures.kraken.com/derivatives/api/v4/historicalfundingrates?symbol={symbol}")
    s = pd.Series({pd.Timestamp(r["timestamp"]): r["relativeFundingRate"] for r in d.get("rates", [])})
    return s.sort_index()


def build(refresh: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    closes, highs, lows, vols = {}, {}, {}, {}
    for s in CRYPTO:
        df = fetch_binance_daily(s)
        closes[s], highs[s], lows[s], vols[s] = df.close, df.high, df.low, df.qvol
        print("binance", s, len(df), df.index[0].date())
    import yfinance as yf
    for y, (name, _) in TRAD.items():
        df = yf.download(y, start="2017-01-01", progress=False, auto_adjust=True)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        closes[name], highs[name], lows[name] = df.Close, df.High, df.Low
        vols[name] = df.Close * df.Volume
        print("yahoo", name, len(df))
    for nm, d in [("close", closes), ("high", highs), ("low", lows), ("qvol", vols)]:
        pd.DataFrame(d).sort_index().to_csv(DATA / f"{nm}.csv.gz")
    fund, kc = {}, {}
    for s, k in CRYPTO.items():
        f = fetch_kraken_funding(k)
        if len(f):
            fund[s] = f
        c = fetch_kraken_perp_daily(k)
        if len(c):
            kc[s] = c.close
    pd.DataFrame(fund).to_csv(DATA / "kraken_funding_hourly.csv.gz")
    pd.DataFrame(kc).to_csv(DATA / "kraken_perp_close.csv.gz")
    print("funding", {k: len(v) for k, v in fund.items()})


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"{name}.csv.gz", index_col=0, parse_dates=True)


if __name__ == "__main__":
    build()

"""Kraken public market data (no keys): futures tickers, 1-minute candles, EUR/USD."""
from __future__ import annotations

import time

import requests

FUT = "https://futures.kraken.com"
_S = requests.Session()

def _get(url, params=None, tries=3):
    err = None
    for i in range(tries):
        try:
            r = _S.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            err = e
            time.sleep(1 + i)
    raise RuntimeError(f"{url}: {err}")

def _tick(t: dict) -> dict:
    mark = float(t.get("markPrice") or t.get("last") or 0)
    rate = float(t.get("fundingRate") or 0)
    return dict(bid=float(t.get("bid") or mark), ask=float(t.get("ask") or mark), mark=mark,
                funding_rel_per_hour=(rate / mark) if mark else 0.0, suspended=bool(t.get("suspended")))

def ticker(perp: str) -> dict:
    return _tick(_get(f"{FUT}/derivatives/api/v3/tickers/{perp}")["ticker"])

def tickers(perps: list[str]) -> dict[str, dict]:
    want = set(perps)
    return {t["symbol"]: _tick(t) for t in _get(f"{FUT}/derivatives/api/v3/tickers")["tickers"] if t["symbol"] in want}

def eurusd() -> float:
    r = _get("https://api.kraken.com/0/public/Ticker", params={"pair": "EURUSD"})["result"]
    x = next(iter(r.values()))
    return (float(x["a"][0]) + float(x["b"][0])) / 2

def candles(perp: str, t0: int, t1: int, res: str = "1m") -> list[dict]:
    """Trade candles; 'time' is the bar open in ms."""
    return _get(f"{FUT}/api/charts/v1/trade/{perp}/{res}", params={"from": t0, "to": t1}).get("candles", [])

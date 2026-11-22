"""Kraken public REST client (no keys, read-only market data)."""
from __future__ import annotations
import time
import pandas as pd, requests

API = "https://api.kraken.com/0/public"
ALIAS = {"XBT": "BTC", "XDG": "DOGE"}
# fiat, stablecoins, gold and wrapped/staked duplicates are not trading candidates
EXCLUDE = {"USDT", "USDC", "DAI", "EURC", "EURT", "EURR", "EURQ", "EUROP", "PAXG", "XAUT", "TUSD", "PYUSD",
           "USDG", "USDE", "USDS", "USDD", "USD1", "RLUSD", "FDUSD", "USDQ", "USDR", "UST", "GBP", "USD",
           "AUD", "CHF", "CAD", "JPY", "WBTC", "TBTC", "CBBTC", "KBTC", "MSOL", "JITOSOL", "BSOL",
           "STETH", "WSTETH", "CBETH", "RETH", "LSETH", "WETH"}

def _get(path, tries=4, **params):
    err = None
    for i in range(tries):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=30)
            j = r.json()
            if j.get("error"): raise RuntimeError(str(j["error"]))
            return j["result"]
        except Exception as e:  # rate limit / transient network
            err = e; time.sleep(2 * (i + 1))
    raise RuntimeError(f"kraken {path} {params}: {err}")

def eur_pairs() -> dict[str, dict]:
    """coin -> pair info for online EUR spot pairs."""
    out = {}
    for key, v in _get("AssetPairs").items():
        if v.get("quote") not in ("ZEUR", "EUR") or v.get("status") != "online" or "/" not in v.get("wsname", ""):
            continue
        coin = v["wsname"].split("/")[0]; coin = ALIAS.get(coin, coin)
        if coin in EXCLUDE: continue
        out[coin] = dict(pair=key, ordermin=float(v["ordermin"]), costmin=float(v.get("costmin") or 0.5),
                         lot_decimals=int(v["lot_decimals"]), shortable=bool(v.get("leverage_sell")))
    return out

def tickers(pairs: dict[str, dict]) -> dict[str, dict]:
    """coin -> bid/ask/last/24h EUR volume (one call for the whole exchange)."""
    t = _get("Ticker"); out = {}
    for coin, info in pairs.items():
        x = t.get(info["pair"])
        if not x: continue
        bid, ask = float(x["b"][0]), float(x["a"][0])
        if bid <= 0 or ask <= 0: continue
        out[coin] = dict(bid=bid, ask=ask, last=float(x["c"][0]), vol_eur_24h=float(x["v"][1]) * float(x["p"][1]))
    return out

def daily_ohlc(pair: str) -> pd.DataFrame:
    """Completed daily bars (UTC), up to 720. The in-progress bar is dropped."""
    res = _get("OHLC", pair=pair, interval=1440)
    rows = next(v for k, v in res.items() if k != "last")
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "vwap", "volume", "n"]).astype(float)
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    today = pd.Timestamp.now(tz="UTC").floor("D")
    df = df[df.index < today]
    df["qvol"] = df["vwap"] * df["volume"]
    return df[["close", "high", "low", "qvol"]]

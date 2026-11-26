"""Public market data: Kraken Futures tickers/candles/funding and the daily alt-data inputs. No keys."""
from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta, timezone

import requests

from .config import PERPS, UA

S = requests.Session()
S.headers["User-Agent"] = UA
KF = "https://futures.kraken.com"


def tickers() -> dict[str, dict]:
    """base -> {px (mark), bid, ask, funding_rel (per hour), change24h} for every Kraken USD perp."""
    d = S.get(KF + "/derivatives/api/v3/tickers", timeout=10).json()["tickers"]
    out = {}
    for t in d:
        sym = t["symbol"]
        if not (sym.startswith("PF_") and sym.endswith("USD")) or t.get("suspended") or t.get("tag") != "perpetual":
            continue
        base = sym[3:-3].replace("XBT", "BTC")
        px = t.get("markPrice") or t.get("last")
        if px:
            out[base] = dict(px=float(px), bid=t.get("bid"), ask=t.get("ask"), funding_rel=t.get("relativeFundingRate"),
                             change24h=t.get("change24h"), vol24h_usd=t.get("volumeQuote"))
    return out


def all_perp_bases() -> set[str]:
    """Every base asset with a Kraken perp (for event trades on coins outside the 20-coin core)."""
    d = S.get(KF + "/derivatives/api/v3/tickers", timeout=10).json()["tickers"]
    out = set()
    for t in d:
        sym = t["symbol"]
        if sym.startswith("PF_") and sym.endswith("USD") and t.get("tag") == "perpetual":
            out.add(sym[3:-3].replace("XBT", "BTC"))
    return out


def perp_symbol(base: str) -> str:
    return PERPS.get(base) or f"PF_{'XBT' if base == 'BTC' else base}USD"


def ticker_for(base: str) -> float | None:
    sym = perp_symbol(base)
    d = S.get(KF + "/derivatives/api/v3/tickers/" + sym, timeout=10).json()
    t = d.get("ticker") or {}
    px = t.get("markPrice") or t.get("last")
    return float(px) if px else None


def daily_closes(days: int = 10) -> list[dict]:
    """Completed UTC daily candles for the core perps."""
    since = int(time.time()) - days * 86400
    today = datetime.now(timezone.utc).date()
    rows = []
    for s, k in PERPS.items():
        try:
            c = S.get(f"{KF}/api/charts/v1/trade/{k}/1d?from={since}", timeout=15).json().get("candles", [])
        except Exception:
            continue
        for x in c:
            d = datetime.fromtimestamp(x["time"] / 1000, timezone.utc).date()
            if d < today:  # only closed candles
                rows.append(dict(sym=s, d=d.isoformat(), close=float(x["close"])))
    return rows


def daily_funding(days: int = 10) -> list[dict]:
    """Sum of hourly relative funding per UTC day (fraction of notional; longs pay positive)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    today = datetime.now(timezone.utc).date()
    rows = []
    for s, k in PERPS.items():
        try:
            r = S.get(f"{KF}/derivatives/api/v4/historicalfundingrates?symbol={k}", timeout=15).json().get("rates", [])
        except Exception:
            continue
        agg: dict[date, float] = {}
        for x in r:
            ts = datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00"))
            if ts >= cutoff and ts.date() < today:
                agg[ts.date()] = agg.get(ts.date(), 0.0) + float(x["relativeFundingRate"])
        rows += [dict(sym=s, d=d.isoformat(), rate=v) for d, v in agg.items()]
    return rows


# ---------------------------------------------------------------- alt data (same series as kagent/altdata.py)
def _j(url, **kw):
    return S.get(url, timeout=20, **kw).json()


def alt_recent(days: int = 20) -> list[dict]:
    rows: list[dict] = []
    now = int(time.time() * 1000)
    start_d = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    def add(d, k, v):
        if v is not None and d >= start_d:
            rows.append(dict(d=d.isoformat(), k=k, v=float(v)))
    errs = []
    for inst in ("BTC", "ETH"):
        try:
            r = _j(f"https://www.deribit.com/api/v2/public/get_funding_rate_history?instrument_name={inst}-PERPETUAL"
                   f"&start_timestamp={now - days * 86400000}&end_timestamp={now}")["result"]
            agg: dict = {}
            for x in r:
                d = datetime.fromtimestamp(x["timestamp"] / 1000, timezone.utc).date()
                agg[d] = agg.get(d, 0.0) + x["interest_1h"]
            for d, v in agg.items():
                add(d, f"fund_{inst}", v)
        except Exception as e:
            errs.append(f"deribit {inst}: {e}")
    try:
        mets = "CapMVRVCur,AdrActCnt,TxCnt,CapMrktCurUSD,FlowInExUSD,FlowOutExUSD,SplyExNtv"
        st = start_d.isoformat()
        d = _j("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc,eth&metrics=" + mets +
               f"&start_time={st}&page_size=1000&ignore_unsupported_errors=true&ignore_forbidden_errors=true")
        for x in d.get("data", []):
            dd = datetime.fromisoformat(x["time"].replace("Z", "+00:00")).date()
            for m in mets.split(","):
                if x.get(m) is not None:
                    add(dd, f"{x['asset']}_{m}", x[m])
    except Exception as e:
        errs.append(f"coinmetrics: {e}")
    try:
        for x in _j(f"https://api.alternative.me/fng/?limit={days}&format=json")["data"]:
            add(datetime.fromtimestamp(int(x["timestamp"]), timezone.utc).date(), "fng", x["value"])
    except Exception as e:
        errs.append(f"fng: {e}")
    try:
        for x in _j("https://stablecoins.llama.fi/stablecoincharts/all")[-days:]:
            add(datetime.fromtimestamp(int(x["date"]), timezone.utc).date(), "stables",
                x["totalCirculatingUSD"].get("peggedUSD", 0))
    except Exception as e:
        errs.append(f"stablecoins: {e}")
    for sid in ("WALCL", "RRPONTSYD", "WTREGEN", "DTWEXBGS", "DFII10"):
        try:
            txt = S.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start_d - timedelta(days=30)}",
                        timeout=20).text
            for rec in csv.reader(io.StringIO(txt)):
                if len(rec) == 2 and rec[0][:2] in ("19", "20"):
                    try:
                        rows.append(dict(d=rec[0], k=sid, v=float(rec[1])))
                    except ValueError:
                        pass
        except Exception as e:
            errs.append(f"fred {sid}: {e}")
    return rows, errs

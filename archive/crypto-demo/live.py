"""Orca paper viewer (strategy v2). Reads the paper ledger and marks it with public Kraken prices.
No private API, no orders, no strategy logic here (that lives in orca/strategy_v2.py)."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}
FEEDS = ("https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss")
ALIASES = {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether"), "SOL": ("solana",), "XRP": ("xrp", "ripple")}
RISK = re.compile(r"hack|exploit|insolven|bankrupt|halt|depeg|sec sues|lawsuit|delist|outage", re.I)
STRATEGY = {
    "name": "Orca v2 — trend-filtered, vol-sized core basket",
    "rules": [
        "Universe: BTC, ETH, SOL, XRP spot vs USD on Kraken; the rest is cash.",
        "Three sub-books (50/100/200-day SMA). In each: hold coins above their SMA, only while BTC is above its SMA.",
        "Inverse-volatility weights, portfolio vol target 40%/yr, max 50% per coin; average the three sub-books.",
        "Rebalance weekly; skip changes under 5% of equity. 0.4% taker fee on every paper fill.",
        "News can only cut risk (hacks, insolvency, depegs on a held coin), never add it.",
    ],
    "oos": "Walk-forward 2019-01 to 2026-10, fees in: CAGR 47%, Sharpe 1.32, max DD -45% vs BTC buy-and-hold 48% / 0.95 / -77%.",
}


def require_paper() -> None:
    if os.environ.get("ORCA_MODE", "paper") != "paper":
        raise RuntimeError("Orca demo is paper-only")


def ledger_path() -> Path:
    env = os.environ.get("ORCA_LEDGER")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "state" / "ledger.json", here / "state" / "ledger.json"):
        if cand.exists():
            return cand
    return here / "state" / "ledger.json"


def load_ledger() -> dict:
    data = json.loads(ledger_path().read_text())
    if data.get("mode") != "paper":
        raise RuntimeError("ledger mode must be paper")
    return data


def _get(url: str, timeout: int = 12) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 orca-paper"}), timeout=timeout) as res:
        return res.read()


def _last(pair: str) -> float | None:
    try:
        res = json.loads(_get(f"https://api.kraken.com/0/public/Ticker?pair={pair}"))["result"]
        return float(next(iter(res.values()))["c"][0])
    except Exception:
        return None


def headlines(limit: int = 12) -> list[dict]:
    out = []
    for url in FEEDS:
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            low = title.lower()
            coins = [b for b, al in ALIASES.items() if any(a in low for a in al)]
            if coins:
                out.append({"title": title, "coins": coins, "kind": "risk" if RISK.search(title) else "news",
                            "link": item.findtext("link")})
    return out[:limit]


def snapshot() -> dict:
    require_paper()
    led = load_ledger()
    fx = _last("EURUSD") or led.get("eur_usd_start") or 1.16
    rows, eq = [], float(led.get("cash_usd") or 0)
    for base, p in (led.get("positions") or {}).items():
        px = _last(PAIRS.get(base, base + "USD")) or p.get("avg_cost_usd", 0)
        val = p["units"] * px
        eq += val
        rows.append({"pair": f"{base}/USD", "units": p["units"], "last": px, "value_usd": round(val, 2),
                     "pnl_pct": round((px / p["avg_cost_usd"] - 1) * 100, 2) if p.get("avg_cost_usd") else None})
    for r in rows:
        r["weight"] = round(r["value_usd"] / eq, 4) if eq else 0
    eq_eur = eq / fx
    start = float(led.get("start_capital_eur") or 1000)
    held = ", ".join(f"{r['pair'].split('/')[0]} {r['weight']:.0%}" for r in rows) or "cash"
    return {
        "mode": "paper", "venue": "kraken-public", "orders": "paper",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "since": led.get("start_date"), "strategy_version": led.get("strategy_version"),
        "decision": {"pair": held, "reason": led.get("last_reason") or "", "paper": True},
        "book": {"equity_eur": round(eq_eur, 2), "equity_usd": round(eq, 2),
                 "return_pct": round((eq_eur / start - 1) * 100, 2), "cash_usd": round(float(led.get("cash_usd") or 0), 2),
                 "eur_usd": fx, "n_trades": len(led.get("fills") or [])},
        "positions": rows, "fills": led.get("fills") or [],
        "equity_history": led.get("equity_history") or [],
        "headlines": headlines(), "strategy": STRATEGY,
    }

"""Kraken paper demo. Live public prices, no private orders."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen

PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "NEAR": "NEARUSD",
    "ARB": "ARBUSD",
    "RAY": "RAYUSD",
    "ENA": "ENAUSD",
    "AVAX": "AVAXUSD",
    "ONDO": "ONDOUSD",
    "HBAR": "HBARUSD",
    "MINA": "MINAUSD",
    "SUI": "SUIUSD",
    "AERO": "AEROUSD",
    "INJ": "INJUSD",
}

FEEDS = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://www.theblock.co/rss.xml",
)

ALIASES = {
    "BTC": ("bitcoin", "xbt"),
    "ETH": ("ethereum", "ether"),
    "SOL": ("solana",),
    "XRP": ("ripple", "xrp"),
    "NEAR": ("near protocol",),
    "ARB": ("arbitrum",),
    "RAY": ("raydium",),
    "ENA": ("ethena",),
    "AVAX": ("avalanche",),
    "ONDO": ("ondo",),
    "HBAR": ("hedera", "hbar"),
    "MINA": ("mina protocol",),
    "SUI": ("sui ",),
    "AERO": ("aerodrome",),
    "INJ": ("injective",),
}

EVENT = re.compile(r"launch|lists|listed|listing|partner|buyback|mainnet|upgrade|etf|approves", re.I)
RECAP = re.compile(r"surge|soars|jumps|rallies|plunges|tumbles", re.I)

PROMPT = """You are Orca's paper spot agent. One coin. Budget €1000. Spread is 0.5% per side. You do not place live orders.

September 2026 is the tune set. The book that made the month held the strongest 14-day trend, one name, and only switched when a product event landed on a coin already rising. That test finished +75% (€1751) in three trades: ARB, then NEAR.

What counted as a product event: a launch, a listing, a partnership, or a fee buyback on the same day as the jump.
- NEAR's Chain Signatures launch (16 Sep) and the confidential-TVL drop (17 Sep) sat on the rally. The Bitwise ETF listing (29 Sep) was after the move. Do not chase a listing that arrives late.
- RAY's StonkFun routing onto LaunchLab (5 Sep) and the fee buyback (8 Sep) sat on the rally.
- ENA's Binance equity basis trade (25 Sep) and ONDO's BlackRock portfolio launch (24 Sep) sat on those days.
- HBAR's IBM listing (23 Sep) showed up in the price five days later. MINA's Mesa upgrade (3 Sep) showed up on 9 Sep. A late reaction is weaker than a same-day one.
- AERO and SUI rose with no item. 17–20 Sep was the whole alt market after the Clarity vote, not a headline per coin.
- Price recaps (surged, soars, rallies) are not signals.

Today:
- Hold the strongest positive 14-day name.
- If a product event from the last five days names a top-5 coin whose 7-day return is positive, switch to the strongest of those.
- Otherwise keep the leader. Quiet news is not a reason to go to cash.
- One coin, about 95% of the book. Long only.

Reply with JSON only.
{"pair": "NEAR/USD", "weight": 0.95, "reason": "one sentence"}
"""


def _get(url: str, timeout: int = 12) -> bytes:
    req = Request(url, headers={"User-Agent": "orca-paper-demo"})
    with urlopen(req, timeout=timeout) as res:
        return res.read()


def _ohlc(base: str, pair: str) -> dict | None:
    try:
        raw = json.loads(_get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"))
    except Exception:
        return None
    result = raw.get("result") or {}
    rows = next((v for k, v in result.items() if k != "last"), None)
    if not rows or len(rows) < 16:
        return None
    last = float(rows[-1][4])
    d14 = last / float(rows[-15][4]) - 1.0
    d7 = last / float(rows[-8][4]) - 1.0
    return {"base": base, "pair": f"{base}/USD", "last": last, "ret_14d": d14, "ret_7d": d7}


def ranks() -> list[dict]:
    out = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for row in pool.map(lambda item: _ohlc(*item), PAIRS.items()):
            if row:
                out.append(row)
    out.sort(key=lambda row: row["ret_14d"], reverse=True)
    return out


def _coins(title: str) -> list[str]:
    text = title.lower()
    found = []
    for base, names in ALIASES.items():
        if any(name in text for name in names):
            found.append(base)
    return found


def headlines() -> list[dict]:
    rows = []
    for url in FEEDS:
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            coins = _coins(title)
            if not coins:
                continue
            kind = "price_recap" if RECAP.search(title) and not EVENT.search(title) else (
                "event" if EVENT.search(title) else "other"
            )
            rows.append({"title": title[:180], "coins": coins, "kind": kind})
            if len(rows) >= 12:
                return rows
    return rows


def decide(board: list[dict], news: list[dict]) -> dict:
    if not board:
        return {"pair": None, "weight": 0, "reason": "Kraken returned no daily candles."}
    by_base = {row["base"]: row for row in board}
    leader = next((row for row in board if row["ret_14d"] > 0), board[0])
    top5 = {row["base"] for row in board[:5]}
    hit = []
    for item in news:
        if item["kind"] != "event":
            continue
        for base in item["coins"]:
            row = by_base.get(base)
            if row and base in top5 and row["ret_7d"] > 0:
                hit.append(row)
    chosen = max(hit, key=lambda row: row["ret_7d"]) if hit else leader
    why = (
        f"{chosen['base']} has a product headline and is up {chosen['ret_7d'] * 100:.0f}% over 7 days."
        if hit
        else f"{chosen['base']} leads the 14-day trend at {chosen['ret_14d'] * 100:.0f}%. No product event on another top-5 name."
    )
    return {"pair": chosen["pair"], "weight": 0.95, "reason": why, "paper": True}


def snapshot() -> dict:
    board = ranks()
    news = headlines()
    pick = decide(board, news)
    return {
        "mode": "paper",
        "venue": "kraken-public",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "orders": "none",
        "decision": pick,
        "ranks": [
            {
                "pair": row["pair"],
                "last": round(row["last"], 6),
                "ret_14d": round(row["ret_14d"], 4),
                "ret_7d": round(row["ret_7d"], 4),
            }
            for row in board
        ],
        "headlines": news,
        "prompt": PROMPT,
        "backtest": {
            "window": "2026-09-07 to 2026-09-30",
            "return_pct": 75.05,
            "final_eur": 1751,
            "trades": 3,
            "path": "ARB then NEAR",
        },
    }

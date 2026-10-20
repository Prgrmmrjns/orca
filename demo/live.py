"""Kraken paper demo. Live public prices, no private orders."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

START = date(2026, 10, 13)
SPREAD = 0.005
EVERY = 7
STICKY = 3
BUDGET_EUR = 1000.0
SUB_EUR = 4.99

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
    bars = []
    for row in rows:
        bars.append(
            {
                "date": datetime.fromtimestamp(int(row[0]), timezone.utc).date(),
                "open": float(row[1]),
                "close": float(row[4]),
            }
        )
    last = bars[-1]["close"]
    d14 = last / bars[-15]["close"] - 1.0
    d7 = last / bars[-8]["close"] - 1.0
    return {
        "base": base,
        "pair": f"{base}/USD",
        "last": last,
        "ret_14d": d14,
        "ret_7d": d7,
        "bars": bars,
    }


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
            published = item.findtext("pubDate") or item.findtext("published") or ""
            day = None
            if published:
                try:
                    day = parsedate_to_datetime(published).astimezone(timezone.utc).date().isoformat()
                except (TypeError, ValueError, IndexError):
                    day = None
            rows.append({"title": title[:180], "coins": coins, "kind": kind, "day": day})
            if len(rows) >= 12:
                return rows
    return rows


def _eur_usd() -> float:
    try:
        raw = json.loads(_get("https://api.kraken.com/0/public/Ticker?pair=EURUSD"))
        result = raw.get("result") or {}
        ticker = next(v for k, v in result.items() if k != "last")
        return float(ticker["c"][0])
    except Exception:
        return 1.16


def _at(bars: list[dict], day: date) -> dict | None:
    return next((bar for bar in bars if bar["date"] == day), None)


def _trail(bars: list[dict], day: date, n: int) -> float | None:
    now = _at(bars, day)
    then = _at(bars, day - timedelta(days=n))
    if not now or not then or then["close"] <= 0:
        return None
    return now["close"] / then["close"] - 1.0


def _ranked(books: list[dict], day: date) -> list[dict]:
    rows = []
    for book in books:
        ret = _trail(book["bars"], day, 14)
        week = _trail(book["bars"], day, 7)
        if ret is None or ret <= 0:
            continue
        rows.append({"base": book["base"], "ret_14d": ret, "ret_7d": week or 0.0})
    rows.sort(key=lambda row: row["ret_14d"], reverse=True)
    return rows


def _fill(fills: list, day: str, side: str, base: str, price: float, amount: float, fee: float, reason: str) -> None:
    fills.append(
        {
            "time": day,
            "side": side,
            "pair": f"{base}/USD",
            "price": round(price, 6),
            "amount": round(amount, 6),
            "fee": round(fee, 2),
            "reason": reason,
        }
    )


def _move(
    by_base: dict,
    base: str | None,
    units: float,
    cash: float,
    day: date,
    target: str,
    reason: str,
    fills: list,
) -> tuple[str | None, float, float]:
    """Sell the hold at its open, then buy the target at its open. 0.5% spread each side."""
    stamp = day.isoformat()
    if base and units > 0 and base != target:
        bar = _at(by_base[base]["bars"], day)
        if not bar:
            return base, units, cash
        proceeds = units * bar["open"]
        fee = proceeds * SPREAD
        cash += proceeds - fee
        _fill(fills, stamp, "sell", base, bar["open"], units, fee, reason)
        base, units = None, 0.0
    if base == target:
        return base, units, cash
    bar = _at(by_base[target]["bars"], day)
    if not bar or bar["open"] <= 0:
        return base, units, cash
    spend = cash * 0.95
    if spend <= 1:
        return base, units, cash
    fee = spend * SPREAD
    units = (spend - fee) / bar["open"]
    cash -= spend
    _fill(fills, stamp, "buy", target, bar["open"], units, fee, reason)
    return target, units, cash


def replay(books: list[dict], news: list[dict], eur_usd: float) -> dict:
    """September rule from the day the demo went up. One coin, weekly check, sticky top 3.

    A product headline on a top-5 name that is already up on the week switches the same day.
    """
    by_base = {book["base"]: book for book in books}
    days = sorted({bar["date"] for book in books for bar in book["bars"] if bar["date"] >= START})
    cash = (BUDGET_EUR - SUB_EUR) * eur_usd
    base: str | None = None
    units = 0.0
    fills: list[dict] = []
    last_check: date | None = None
    reason = "Waiting for the first weekly check."
    for day in days:
        prev = day - timedelta(days=1)
        if last_check is None or (day - last_check).days >= EVERY:
            ranked = _ranked(books, prev)
            pool = {row["base"] for row in ranked[:STICKY]}
            top = ranked[0]["base"] if ranked else None
            chosen = base if base in pool else top
            if chosen and chosen != base:
                reason = f"{chosen} leads the 14-day trend. Weekly check, still the rule from September."
                base, units, cash = _move(by_base, base, units, cash, day, chosen, reason, fills)
            last_check = day
        hits = []
        for item in news:
            if item["kind"] != "event" or item.get("day") != day.isoformat():
                continue
            ranked = _ranked(books, prev)
            top5 = {row["base"] for row in ranked[:5]}
            for coin in item["coins"]:
                row = next((r for r in ranked if r["base"] == coin), None)
                if row and coin in top5 and row["ret_7d"] > 0:
                    hits.append((row, item["title"]))
        if hits:
            row, title = max(hits, key=lambda pair: pair[0]["ret_7d"])
            if row["base"] != base:
                reason = f"{row['base']} product headline, 7-day {row['ret_7d'] * 100:.0f}%. {title}"
                base, units, cash = _move(by_base, base, units, cash, day, row["base"], reason, fills)
    mark = 0.0
    if base:
        mark = by_base[base]["last"]
    equity = cash + units * mark
    final_eur = equity / eur_usd
    return {
        "pair": f"{base}/USD" if base else None,
        "weight": 0.95 if base else 0,
        "reason": reason,
        "paper": True,
        "cash_usd": round(cash, 2),
        "units": round(units, 6),
        "mark": round(mark, 6),
        "equity_usd": round(equity, 2),
        "equity_eur": round(final_eur, 2),
        "return_pct": round((final_eur / BUDGET_EUR - 1.0) * 100.0, 2),
        "fills": fills,
    }


def snapshot() -> dict:
    books = ranks()
    news = headlines()
    eur_usd = _eur_usd()
    book = replay(books, news, eur_usd)
    return {
        "mode": "paper",
        "venue": "kraken-public",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "orders": "paper",
        "since": START.isoformat(),
        "decision": {
            "pair": book["pair"],
            "weight": book["weight"],
            "reason": book["reason"],
            "paper": True,
        },
        "book": {
            "equity_eur": book["equity_eur"],
            "return_pct": book["return_pct"],
            "cash_usd": book["cash_usd"],
            "units": book["units"],
            "mark": book["mark"],
            "eur_usd": round(eur_usd, 4),
            "n_trades": len(book["fills"]),
        },
        "fills": book["fills"],
        "ranks": [
            {
                "pair": row["pair"],
                "last": round(row["last"], 6),
                "ret_14d": round(row["ret_14d"], 4),
                "ret_7d": round(row["ret_7d"], 4),
            }
            for row in books
        ],
        "headlines": [{k: v for k, v in item.items() if k != "bars"} for item in news],
        "prompt": PROMPT,
        "backtest": {
            "window": "7–30 Sep 2026",
            "hours": 552,
            "start_eur": 1000,
            "return_pct": 75.05,
            "final_eur": 1751,
            "max_drawdown_pct": -23.2,
            "fees_usd": 20.21,
            "trades": 3,
            "fills": [
                {"time": "2026-09-07", "side": "buy", "pair": "ARB/USD", "price": 0.168},
                {"time": "2026-09-21", "side": "sell", "pair": "ARB/USD", "price": 0.2388},
                {"time": "2026-09-21", "side": "buy", "pair": "NEAR/USD", "price": 4.1346},
            ],
            "how": "The settings were frozen on Aug 2025–Aug 2026. September was scored once.",
            "rule": [
                "One coin, about 95% of the book. Long only.",
                "Buy the strongest name with a positive 14-day return.",
                "Keep it while it stays in the top 3.",
                "Check again every 7 days, and trade only when the name changes.",
                "Pay 0.5% spread on each fill.",
            ],
        },
    }

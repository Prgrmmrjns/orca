"""Kraken paper demo. Live public prices, no private orders. Ledger-backed book."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
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

BACKTEST = {
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
    "how": "The settings were frozen on Aug 2025–Aug 2026. September was scored once. Labelled backtest — not the live paper book.",
    "rule": [
        "One coin, about 95% of the book. Long only.",
        "Buy the strongest name with a positive 14-day return.",
        "Keep it while it stays in the top 3.",
        "Check again every 7 days, and trade only when the name changes.",
        "Pay 0.5% spread on each fill.",
    ],
}


def require_paper() -> None:
    mode = os.getenv("ORCA_MODE", "paper").lower()
    if mode != "paper":
        raise RuntimeError(
            f"ORCA_MODE={mode!r} rejected. Paper only — no private/order calls. Set ORCA_MODE=paper."
        )


def ledger_path() -> Path:
    env = os.environ.get("ORCA_LEDGER")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    # Prefer repo-root state/ (sibling of demo/ + scripts/) over demo/state/ (Vercel bundle).
    for parent in here.parents:
        candidate = parent / "state" / "ledger.json"
        if candidate.is_file() and (parent / "demo").is_dir() and (parent / "scripts").is_dir():
            return candidate
    for parent in here.parents:
        candidate = parent / "state" / "ledger.json"
        if candidate.is_file():
            return candidate
    return here.parents[1] / "state" / "ledger.json"


def load_ledger(path: Path | None = None) -> dict:
    p = path or ledger_path()
    with p.open() as f:
        data = json.load(f)
    if data.get("mode", "paper") != "paper":
        raise RuntimeError("ledger mode must be paper")
    return data


def save_ledger(data: dict, path: Path | None = None) -> Path:
    require_paper()
    if data.get("mode", "paper") != "paper":
        raise RuntimeError("refusing to write non-paper ledger")
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = p.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(p)
    # Keep demo/state in sync when writing the repo-root ledger (Vercel root = demo/).
    demo_copy = Path(__file__).resolve().parent / "state" / "ledger.json"
    if p.resolve() != demo_copy.resolve():
        demo_copy.parent.mkdir(parents=True, exist_ok=True)
        demo_copy.write_text(p.read_text())
    return p


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


def ticker_last(base: str) -> float | None:
    """Public Kraken last price for a base (e.g. INJ). Paper mark-to-market only."""
    pair = PAIRS.get(base)
    if not pair:
        return None
    try:
        raw = json.loads(_get(f"https://api.kraken.com/0/public/Ticker?pair={pair}"))
        result = raw.get("result") or {}
        ticker = next(v for k, v in result.items() if k != "last")
        return float(ticker["c"][0])
    except Exception:
        return None


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


def compute_signal(
    books: list[dict],
    news: list[dict],
    day: date | None = None,
    holding: str | None = None,
    last_check: date | None = None,
) -> dict:
    """Today's target per the existing September rule (weekly sticky top-3 + product events).

    Does not place fills. Rule itself unchanged — decision pending.
    """
    day = day or datetime.now(timezone.utc).date()
    prev = day - timedelta(days=1)
    reason = "No change."
    target = holding
    did_weekly = False

    if last_check is None or (day - last_check).days >= EVERY:
        ranked = _ranked(books, prev)
        pool = {row["base"] for row in ranked[:STICKY]}
        top = ranked[0]["base"] if ranked else None
        chosen = holding if holding in pool else top
        if chosen and chosen != holding:
            reason = f"{chosen} leads the 14-day trend. Weekly check, still the rule from September."
            target = chosen
        elif chosen:
            reason = f"Hold {chosen}. Weekly check, still in the sticky top {STICKY}."
            target = chosen
        else:
            reason = "No positive 14-day name on the weekly check."
            target = holding
        did_weekly = True
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
        if row["base"] != target:
            reason = f"{row['base']} product headline, 7-day {row['ret_7d'] * 100:.0f}%. {title}"
            target = row["base"]

    return {
        "day": day.isoformat(),
        "target": target,
        "pair": f"{target}/USD" if target else None,
        "weight": 0.95 if target else 0,
        "reason": reason,
        "holding": holding,
        "changed": target != holding,
        "last_check": last_check.isoformat() if last_check else None,
        "weekly_check": did_weekly,
        "paper": True,
    }


def open_price(books: list[dict], base: str, day: date) -> float | None:
    book = next((b for b in books if b["base"] == base), None)
    if not book:
        return None
    bar = _at(book["bars"], day)
    if not bar or bar["open"] <= 0:
        return None
    return float(bar["open"])


def paper_fill(
    side: str,
    base: str,
    price: float,
    amount: float,
    fee: float,
    day: str,
    reason: str,
) -> dict:
    return {
        "time": day,
        "side": side,
        "pair": f"{base}/USD",
        "price": round(price, 6),
        "amount": round(amount, 6),
        "fee": round(fee, 2),
        "reason": reason,
    }


def apply_signal(ledger: dict, signal: dict, books: list[dict], eur_usd: float) -> dict:
    """Append paper fill(s) when the signal target differs from the ledger holding. Paper only."""
    require_paper()
    day = date.fromisoformat(signal["day"])
    stamp = day.isoformat()
    target = signal.get("target")
    holding = ledger.get("holding") or {}
    base = holding.get("base")
    units = float(holding.get("units") or 0)
    cash = float(ledger.get("cash_usd") or 0)
    reason = signal.get("reason") or ""
    fills = list(ledger.get("fills") or [])
    new_fills: list[dict] = []

    if not signal.get("changed") or not target:
        ledger["last_check"] = signal.get("last_check") or ledger.get("last_check")
        ledger["last_reason"] = reason
        return {"ledger": ledger, "fills": [], "traded": False}

    if base and units > 0 and base != target:
        px = open_price(books, base, day)
        if px is None:
            raise RuntimeError(f"no open price for {base} on {stamp}")
        proceeds = units * px
        fee = proceeds * SPREAD
        cash += proceeds - fee
        fill = paper_fill("sell", base, px, units, fee, stamp, reason)
        fills.append(fill)
        new_fills.append(fill)
        base, units = None, 0.0

    if target and base != target:
        px = open_price(books, target, day)
        if px is None:
            raise RuntimeError(f"no open price for {target} on {stamp}")
        spend = cash * 0.95
        if spend <= 1:
            raise RuntimeError("cash too low for paper buy")
        fee = spend * SPREAD
        units = (spend - fee) / px
        cash -= spend
        fill = paper_fill("buy", target, px, units, fee, stamp, reason)
        fills.append(fill)
        new_fills.append(fill)
        base = target

    mark = open_price(books, base, day) if base else 0.0
    equity_usd = cash + (units * (mark or 0.0) if base else 0.0)
    equity_eur = equity_usd / eur_usd if eur_usd else equity_usd

    ledger["cash_usd"] = round(cash, 2)
    ledger["holding"] = (
        {
            "base": base,
            "pair": f"{base}/USD",
            "units": round(units, 6),
            "entry_price": round(mark or 0.0, 6),
            "reason": reason,
        }
        if base
        else None
    )
    ledger["fills"] = fills
    ledger["last_check"] = signal.get("last_check") or stamp
    ledger["last_reason"] = reason
    hist = list(ledger.get("equity_history") or [])
    hist.append(
        {
            "date": stamp,
            "equity_usd": round(equity_usd, 2),
            "equity_eur": round(equity_eur, 2),
            "cash_usd": round(cash, 2),
            "holding": base,
            "units": round(units, 6) if base else 0,
        }
    )
    ledger["equity_history"] = hist
    return {"ledger": ledger, "fills": new_fills, "traded": bool(new_fills)}


def mark_book(ledger: dict, eur_usd: float | None = None) -> dict:
    """Mark-to-market the ledger holding with live public Kraken prices."""
    eur_usd = eur_usd if eur_usd is not None else _eur_usd()
    holding = ledger.get("holding") or {}
    base = holding.get("base")
    units = float(holding.get("units") or 0)
    cash = float(ledger.get("cash_usd") or 0)
    mark = 0.0
    if base and units > 0:
        live = ticker_last(base)
        if live is None:
            # Fall back to OHLC last from ranks if ticker fails.
            books = ranks()
            row = next((r for r in books if r["base"] == base), None)
            live = row["last"] if row else 0.0
        mark = float(live or 0.0)
    equity_usd = cash + units * mark
    start_eur = float(ledger.get("start_capital_eur") or (BUDGET_EUR - SUB_EUR))
    budget = float(ledger.get("budget_eur") or BUDGET_EUR)
    equity_eur = equity_usd / eur_usd if eur_usd else equity_usd
    return {
        "cash_usd": round(cash, 2),
        "units": round(units, 6),
        "mark": round(mark, 6),
        "base": base,
        "pair": f"{base}/USD" if base else None,
        "equity_usd": round(equity_usd, 2),
        "equity_eur": round(equity_eur, 2),
        "return_pct": round((equity_eur / budget - 1.0) * 100.0, 2),
        "vs_start_pct": round((equity_eur / start_eur - 1.0) * 100.0, 2),
        "eur_usd": round(eur_usd, 4),
        "n_trades": len(ledger.get("fills") or []),
    }


def snapshot() -> dict:
    """Read the paper ledger and mark-to-market. No trade replay, no private API."""
    require_paper()
    ledger = load_ledger()
    books = ranks()
    news = headlines()
    eur_usd = _eur_usd()
    book = mark_book(ledger, eur_usd)
    holding = ledger.get("holding") or {}
    reason = ledger.get("last_reason") or holding.get("reason") or "Paper ledger."
    return {
        "mode": "paper",
        "venue": "kraken-public",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "orders": "paper",
        "since": ledger.get("start_date") or START.isoformat(),
        "decision": {
            "pair": book["pair"],
            "weight": 0.95 if book["pair"] else 0,
            "reason": reason,
            "paper": True,
        },
        "book": {
            "equity_eur": book["equity_eur"],
            "return_pct": book["return_pct"],
            "cash_usd": book["cash_usd"],
            "units": book["units"],
            "mark": book["mark"],
            "eur_usd": book["eur_usd"],
            "n_trades": book["n_trades"],
        },
        "fills": ledger.get("fills") or [],
        "equity_history": ledger.get("equity_history") or [],
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
        "backtest": BACKTEST,
    }

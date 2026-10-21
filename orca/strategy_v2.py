"""Orca strategy v2: trend-filtered, vol-sized core basket (BTC/ETH/SOL/XRP) with cash.

PAPER ONLY. Uses public Kraken endpoints exclusively. No private/order calls.
See strategy/STRATEGY.md for the rules and the research behind them.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

VERSION = "v2.1"
UNIVERSE = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}
SMA_WINDOWS = (50, 100, 200)   # ensemble: weight = share of windows where close > SMA
VOL_WINDOW = 30                # days, realised vol for inverse-vol sizing
TARGET_VOL = 0.40              # annualised portfolio vol target
CORR_HAIRCUT = 0.85            # crude correlation adjustment used in research
MAX_WEIGHT = 0.50              # per asset cap
REBALANCE_DAYS = 7             # weekly
BAND = 0.05                    # skip trades smaller than 5% of equity
TAKER_FEE = 0.0               # Jonas has a 0%-fee Kraken plan; cost = bid/ask spread only (paper)
ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "strategy" / "news_overrides.json"


def _get(url: str, timeout: int = 15) -> dict:
    req = Request(url, headers={"User-Agent": "orca-paper-v2"})
    with urlopen(req, timeout=timeout) as res:
        raw = json.loads(res.read())
    if raw.get("error"):
        raise RuntimeError(raw["error"])
    return raw["result"]


def closes(pair: str) -> list[tuple[date, float]]:
    """Up to 720 public daily closes; drops today's unfinished bar."""
    res = _get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440")
    rows = next(v for k, v in res.items() if k != "last")
    today = datetime.now(timezone.utc).date()
    out = [(datetime.fromtimestamp(int(r[0]), timezone.utc).date(), float(r[4])) for r in rows]
    return [x for x in out if x[0] < today]


def ticker(pair: str) -> dict:
    res = _get(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
    t = next(iter(res.values()))
    return {"ask": float(t["a"][0]), "bid": float(t["b"][0]), "last": float(t["c"][0])}


def eur_usd() -> float:
    return ticker("EURUSD")["last"]


def load_overrides(day: date) -> dict[str, dict]:
    """News risk overrides: {"SOL": {"max_weight": 0, "until": "2026-11-01", "why": "..."}}.
    Overrides can only REDUCE exposure, never add it."""
    if not OVERRIDES.exists():
        return {}
    data = json.loads(OVERRIDES.read_text() or "{}")
    return {k: v for k, v in data.items() if date.fromisoformat(v["until"]) >= day}


def signal_from_closes(series: dict[str, list[float]], overrides: dict | None = None) -> dict:
    """Pure function (testable): series = {base: closes oldest->newest}."""
    overrides = overrides or {}
    info, raw = {}, {}
    for base, c in series.items():
        if len(c) < max(SMA_WINDOWS) + 1:
            continue
        last = c[-1]
        above = {n: last > sum(c[-n:]) / n for n in SMA_WINDOWS}
        rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - VOL_WINDOW, len(c))]
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(365)
        info[base] = {"close": last, "vol30": round(vol, 4), "above_sma": above,
                      "trend_score": sum(above.values()) / len(SMA_WINDOWS)}
    btc = info.get("BTC")
    weights = {b: 0.0 for b in info}
    for n in SMA_WINDOWS:  # each window is an independent sub-portfolio; average them
        gate = btc is not None and btc["above_sma"][n]
        on = [b for b in info if gate and info[b]["above_sma"][n]]
        if not on:
            continue
        iv = {b: 1 / info[b]["vol30"] for b in on}
        tot = sum(iv.values())
        w = {b: iv[b] / tot for b in on}
        pv = sum(w[b] * info[b]["vol30"] for b in on) * CORR_HAIRCUT
        scale = min(1.0, TARGET_VOL / pv) if pv > 0 else 0.0
        for b in on:
            weights[b] += min(MAX_WEIGHT, w[b] * scale) / len(SMA_WINDOWS)
    notes = []
    for b, o in overrides.items():
        if b in weights and weights[b] > o.get("max_weight", 0):
            weights[b] = float(o.get("max_weight", 0))
            notes.append(f"{b} capped at {o.get('max_weight', 0):.0%} until {o['until']}: {o.get('why', '')}")
    weights = {b: round(w, 4) for b, w in weights.items()}
    return {"version": VERSION, "weights": weights, "cash_weight": round(1 - sum(weights.values()), 4),
            "btc_gate": {n: bool(btc and btc["above_sma"][n]) for n in SMA_WINDOWS}, "assets": info,
            "news_notes": notes}


def compute_signal(day: date | None = None) -> dict:
    day = day or datetime.now(timezone.utc).date()
    data = {b: closes(p) for b, p in UNIVERSE.items()}
    sig = signal_from_closes({b: [c for _, c in v] for b, v in data.items()}, load_overrides(day))
    sig["day"] = day.isoformat()
    sig["bar_date"] = max(v[-1][0] for v in data.values()).isoformat()
    return sig


def is_rebalance_day(ledger: dict, day: date) -> bool:
    last = ledger.get("last_rebalance")
    return last is None or (day - date.fromisoformat(last)).days >= REBALANCE_DAYS


def mark(ledger: dict, prices: dict[str, float], fx: float) -> dict:
    pos = ledger.get("positions") or {}
    val = {b: p["units"] * prices[b] for b, p in pos.items() if p["units"] > 0}
    eq_usd = ledger["cash_usd"] + sum(val.values())
    eq_eur = eq_usd / fx
    return {"equity_usd": round(eq_usd, 2), "equity_eur": round(eq_eur, 2), "eur_usd": fx,
            "return_pct": round((eq_eur / ledger["start_capital_eur"] - 1) * 100, 2),
            "cash_usd": round(ledger["cash_usd"], 2),
            "weights": {b: round(v / eq_usd, 4) for b, v in val.items()}}


def rebalance(ledger: dict, weights: dict[str, float], quotes: dict[str, dict], fx: float,
              stamp: str, reason: str, force: bool = False) -> list[dict]:
    """Paper fills: buys at ask, sells at bid, plus taker fee in USD. Mutates ledger."""
    pos = ledger.setdefault("positions", {})
    px = {b: q["last"] for b, q in quotes.items()}
    eq = ledger["cash_usd"] + sum(p["units"] * px[b] for b, p in pos.items())
    fills = []
    order = sorted(quotes, key=lambda b: weights.get(b, 0) * eq - pos.get(b, {}).get("units", 0) * px[b])
    for b in order:  # sells first
        cur = pos.get(b, {}).get("units", 0.0) * px[b]
        tgt = weights.get(b, 0.0) * eq
        diff = tgt - cur
        if abs(diff) < BAND * eq and not (tgt == 0 and cur > 0) and not force:
            continue
        if abs(diff) < 1:
            continue
        side = "buy" if diff > 0 else "sell"
        price = quotes[b]["ask"] if side == "buy" else quotes[b]["bid"]
        units = abs(diff) / price
        if side == "buy":
            units = min(units, ledger["cash_usd"] / (price * (1 + TAKER_FEE)))
        notional = units * price
        fee = notional * TAKER_FEE
        p = pos.setdefault(b, {"units": 0.0, "avg_cost_usd": 0.0})
        if side == "buy":
            p["avg_cost_usd"] = (p["avg_cost_usd"] * p["units"] + notional + fee) / (p["units"] + units)
            p["units"] += units
            ledger["cash_usd"] -= notional + fee
        else:
            p["units"] -= units
            ledger["cash_usd"] += notional - fee
        fills.append({"time": stamp, "side": side, "pair": f"{b}/USD", "price": price,
                      "units": round(units, 8), "notional_usd": round(notional, 2),
                      "fee_usd": round(fee, 4), "notional_eur": round(notional / fx, 2),
                      "eur_usd": fx, "reason": reason})
    for b in [b for b, p in pos.items() if p["units"] <= 1e-12]:
        del pos[b]
    for p in pos.values():
        p["units"] = round(p["units"], 8)
        p["avg_cost_usd"] = round(p["avg_cost_usd"], 6)
    ledger["cash_usd"] = round(ledger["cash_usd"], 4)
    ledger.setdefault("fills", []).extend(fills)
    return fills

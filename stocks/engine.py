#!/usr/bin/env python3
"""Paper engine: fill pending decisions, time/stop exits, mark to market."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import (
    COST_PCT,
    MAX_OPEN,
    MAX_POSITION_PCT,
    MAX_SHORT_PCT,
    START_CAPITAL_EUR,
    START_DATE,
)
from stocks.market import (
    add_trading_days,
    daily_bar,
    eurusd,
    market_session,
    now_et,
    price_snapshot,
    spy_history_eur,
    utc_now_iso,
)
from stocks.paths import DECISIONS, LEDGER, ensure_dirs, read_json, write_json


def empty_ledger() -> dict:
    fx = eurusd()
    return {
        "mode": "paper",
        "strategy": "earnings-agent-v1",
        "base_currency": "EUR",
        "note": "Paper only. Forward test of agentic US earnings strategy. No broker API.",
        "start_date": START_DATE,
        "start_capital_eur": START_CAPITAL_EUR,
        "eur_usd_start": fx,
        "cash_eur": START_CAPITAL_EUR,
        "positions": {},
        "pending": [],
        "closed_trades": [],
        "equity_history": [
            {"day": START_DATE, "equity_eur": START_CAPITAL_EUR, "spy_eur": START_CAPITAL_EUR}
        ],
        "cost_pct": COST_PCT,
        "max_position_pct": MAX_POSITION_PCT,
        "max_short_pct": MAX_SHORT_PCT,
        "max_open": MAX_OPEN,
        "last_run": None,
        "last_reason": "Initialized paper book.",
    }


def load_ledger() -> dict:
    ensure_dirs()
    led = read_json(LEDGER, default=None)
    if not led or led.get("mode") != "paper":
        led = empty_ledger()
        write_json(LEDGER, led)
    return led


def equity_eur(led: dict, marks: dict[str, float], fx: float) -> float:
    eq = float(led.get("cash_eur") or 0)
    for t, p in (led.get("positions") or {}).items():
        px = marks.get(t) or p.get("mark_usd") or p.get("entry_usd")
        shares = float(p["shares"])
        side = p.get("side", "long")
        # value in EUR
        notional_usd = abs(shares) * float(px)
        notional_eur = notional_usd / fx
        if side == "long":
            eq += notional_eur
        else:
            # short: cash already includes short proceeds; P&L = entry - current
            entry = float(p["entry_usd"])
            pnl_usd = (entry - float(px)) * abs(shares)
            # accounting: cash_eur held short collateral notionally as cash + unrealized
            # We store shorts with cash credited at entry (net of cost). Mark = cash + unrealized.
            eq += pnl_usd / fx
            # Also the reserved/credited proceeds are already in cash; don't double-count shares.
    return eq


def _position_equity_contrib(p: dict, px: float, fx: float) -> float:
    shares = abs(float(p["shares"]))
    side = p.get("side", "long")
    if side == "long":
        return (shares * px) / fx
    entry = float(p["entry_usd"])
    return ((entry - px) * shares) / fx


def mark_book(led: dict) -> tuple[dict, dict[str, float], float]:
    fx = eurusd()
    marks: dict[str, float] = {}
    for t, p in list((led.get("positions") or {}).items()):
        snap = price_snapshot(t)
        px = snap.get("last") or p.get("mark_usd") or p.get("entry_usd")
        marks[t] = float(px)
        p["mark_usd"] = marks[t]
        p["mark_eur"] = round(marks[t] / fx, 4)
        entry = float(p["entry_usd"])
        if p.get("side") == "short":
            p["unrealized_pnl_pct"] = round((entry / marks[t] - 1) * 100, 4) if marks[t] else None
        else:
            p["unrealized_pnl_pct"] = round((marks[t] / entry - 1) * 100, 4) if entry else None
    # Recompute equity carefully for shorts:
    # cash_eur includes proceeds from short sales (and deducts long buys).
    # For longs: equity = cash + market value.
    # For shorts: at entry we added proceeds to cash and did NOT subtract a liability separately;
    # so mark = cash + sum(long MV) + sum(short unrealized P&L) - wait.
    # Simpler invariant:
    #   long buy: cash -= notional*(1+cost); position holds shares
    #   short sell: cash += notional*(1-cost); position holds negative exposure shares
    #   equity = cash + sum(side_sign * shares * px)/fx
    #   where side_sign = +1 long, -1 short... but short shares stored positive with side=short.
    eq = float(led.get("cash_eur") or 0)
    for t, p in (led.get("positions") or {}).items():
        px = marks[t]
        shares = abs(float(p["shares"]))
        if p.get("side") == "short":
            # liability to buy back
            eq -= (shares * px) / fx
        else:
            eq += (shares * px) / fx
    led["equity_eur"] = round(eq, 2)
    led["eur_usd"] = fx
    return led, marks, fx


def _open_count(led: dict) -> int:
    return len(led.get("positions") or {}) + len(led.get("pending") or [])


def ingest_decisions(led: dict) -> int:
    """Move undecided→pending orders from decisions.json into ledger.pending."""
    dec = read_json(DECISIONS, default={"decisions": []})
    decisions = dec.get("decisions") or []
    pending = led.setdefault("pending", [])
    pending_ids = {p.get("id") for p in pending}
    open_tickers = set((led.get("positions") or {}).keys()) | {p.get("ticker") for p in pending}
    n = 0
    for d in decisions:
        if d.get("status") not in (None, "new", "accepted"):
            continue
        if d.get("side") == "skip":
            d["status"] = "skipped"
            continue
        tid = d.get("id") or f"{d.get('ticker')}-{d.get('decided_at')}"
        d["id"] = tid
        if tid in pending_ids or d.get("ticker") in open_tickers:
            d["status"] = d.get("status") or "duplicate"
            continue
        if d.get("side") not in ("long", "short"):
            d["status"] = "invalid"
            continue
        size = float(d.get("size") or 0)
        side = d["side"]
        if side == "long" and size > MAX_POSITION_PCT + 1e-9:
            size = MAX_POSITION_PCT
            d["size"] = size
            d["size_capped"] = True
        if side == "short" and size > MAX_SHORT_PCT + 1e-9:
            size = MAX_SHORT_PCT
            d["size"] = size
            d["size_capped"] = True
        if _open_count(led) >= MAX_OPEN:
            d["status"] = "rejected_max_open"
            continue
        order = {
            "id": tid,
            "ticker": d["ticker"],
            "side": side,
            "size": size,
            "hold_days": int(d.get("hold") or d.get("hold_days") or 5),
            "stop_loss": d.get("stop_loss"),
            "take_profit": d.get("take_profit"),
            "reason": d.get("reason") or "",
            "confidence": d.get("confidence"),
            "rule_tags": d.get("rule_tags") or [],
            "decided_at": d.get("decided_at"),
            "status": "pending",
        }
        pending.append(order)
        open_tickers.add(d["ticker"])
        d["status"] = "pending"
        n += 1
    write_json(DECISIONS, dec)
    return n


def fill_pending(led: dict, marks: dict[str, float], fx: float) -> list[dict]:
    """Fill pending at next regular open, or current price if market is open at decision time."""
    session = market_session()
    today = now_et().date()
    fills = []
    still = []
    for order in led.get("pending") or []:
        ticker = order["ticker"]
        # determine fill price
        bar = daily_bar(ticker, today)
        px = None
        fill_note = ""
        if session == "open":
            # fill at current/last (or today's open if available)
            snap = price_snapshot(ticker)
            px = snap.get("last") or (bar.get("open") if bar else None) or marks.get(ticker)
            fill_note = "filled at session price (market open)"
        else:
            # wait for next regular-session open
            decided = order.get("decided_at")
            decided_day = None
            if decided:
                try:
                    decided_day = datetime.fromisoformat(decided.replace("Z", "+00:00")).astimezone(now_et().tzinfo).date()
                except Exception:
                    decided_day = None
            if decided_day and decided_day < today and bar.get("open"):
                px = bar["open"]
                fill_note = "filled at regular-session open"
            elif session == "pre" and bar.get("open"):
                # pre-market: don't fill yet unless we have a real open print after 9:30
                still.append(order)
                continue
            else:
                still.append(order)
                continue
        if not px or px <= 0:
            still.append(order)
            continue

        size = float(order["size"])
        # mark book first for sizing equity
        led, marks2, fx = mark_book(led)
        eq = float(led["equity_eur"])
        notional_eur = eq * size
        if notional_eur < 5:
            order["status"] = "rejected_too_small"
            continue
        notional_usd = notional_eur * fx
        shares = notional_usd / px
        cost = notional_usd * COST_PCT
        cost_eur = cost / fx
        side = order["side"]
        ts = utc_now_iso()

        if side == "long":
            led["cash_eur"] = float(led["cash_eur"]) - notional_eur - cost_eur
        else:
            # short: receive proceeds, pay cost
            led["cash_eur"] = float(led["cash_eur"]) + notional_eur - cost_eur

        exit_day = add_trading_days(today, int(order["hold_days"]))
        pos = {
            "ticker": ticker,
            "side": side,
            "shares": round(shares, 6),
            "entry_usd": round(px, 6),
            "entry_eur": round(px / fx, 6),
            "entry_notional_eur": round(notional_eur, 2),
            "entry_cost_eur": round(cost_eur, 4),
            "size": size,
            "entry_day": today.isoformat(),
            "entry_time": ts,
            "exit_day": exit_day.isoformat(),
            "hold_days": int(order["hold_days"]),
            "stop_loss": order.get("stop_loss"),
            "take_profit": order.get("take_profit"),
            "reason": order.get("reason"),
            "confidence": order.get("confidence"),
            "rule_tags": order.get("rule_tags") or [],
            "decision_id": order.get("id"),
            "mark_usd": round(px, 6),
        }
        led.setdefault("positions", {})[ticker] = pos
        fill = {
            "time": ts,
            "ticker": ticker,
            "side": "buy" if side == "long" else "sell_short",
            "price_usd": round(px, 6),
            "shares": pos["shares"],
            "notional_eur": round(notional_eur, 2),
            "cost_eur": round(cost_eur, 4),
            "reason": order.get("reason"),
            "note": fill_note,
            "decision_id": order.get("id"),
        }
        fills.append(fill)
        # mark decision filled
        _update_decision_status(order.get("id"), "filled", fill)
    led["pending"] = still
    return fills


def _update_decision_status(did: str | None, status: str, fill: dict | None = None) -> None:
    if not did:
        return
    dec = read_json(DECISIONS, default={"decisions": []})
    for d in dec.get("decisions") or []:
        if d.get("id") == did:
            d["status"] = status
            if fill:
                d["fill"] = fill
    write_json(DECISIONS, dec)


def check_exits(led: dict, fx: float) -> list[dict]:
    """Time exits at close after hold days; optional stop/target on daily high/low."""
    today = now_et().date()
    session = market_session()
    closed = []
    for ticker, p in list((led.get("positions") or {}).items()):
        bar = daily_bar(ticker, today)
        px_close = bar.get("close")
        px_high = bar.get("high")
        px_low = bar.get("low")
        snap = price_snapshot(ticker)
        last = snap.get("last") or px_close or p.get("mark_usd")
        entry = float(p["entry_usd"])
        side = p.get("side", "long")
        exit_px = None
        exit_why = None

        sl = p.get("stop_loss")
        tp = p.get("take_profit")
        # stop/target checked on daily high/low
        if side == "long":
            if sl is not None and px_low is not None and px_low <= entry * (1 - float(sl)):
                exit_px = entry * (1 - float(sl))
                exit_why = "stop_loss"
            elif tp is not None and px_high is not None and px_high >= entry * (1 + float(tp)):
                exit_px = entry * (1 + float(tp))
                exit_why = "take_profit"
        else:
            if sl is not None and px_high is not None and px_high >= entry * (1 + float(sl)):
                exit_px = entry * (1 + float(sl))
                exit_why = "stop_loss"
            elif tp is not None and px_low is not None and px_low <= entry * (1 - float(tp)):
                exit_px = entry * (1 - float(tp))
                exit_why = "take_profit"

        exit_day = date.fromisoformat(p["exit_day"])
        # time exit at close on/after exit_day
        if exit_px is None and today >= exit_day:
            if session in ("after", "closed") or (session == "open" and now_et().hour >= 15):
                exit_px = px_close or last
                exit_why = "time_exit"
            elif session == "open" and today > exit_day:
                exit_px = last
                exit_why = "time_exit"

        if exit_px is None:
            continue

        shares = abs(float(p["shares"]))
        notional_usd = shares * float(exit_px)
        cost_usd = notional_usd * COST_PCT
        cost_eur = cost_usd / fx
        notional_eur = notional_usd / fx

        if side == "long":
            # sell: receive proceeds - cost
            led["cash_eur"] = float(led["cash_eur"]) + notional_eur - cost_eur
            pnl_eur = (float(exit_px) - entry) / fx * shares - float(p.get("entry_cost_eur") or 0) - cost_eur
            pnl_pct = (float(exit_px) / entry - 1) - 2 * COST_PCT
        else:
            # cover short: pay to buy back + cost
            led["cash_eur"] = float(led["cash_eur"]) - notional_eur - cost_eur
            pnl_eur = (entry - float(exit_px)) / fx * shares - float(p.get("entry_cost_eur") or 0) - cost_eur
            pnl_pct = (entry / float(exit_px) - 1) - 2 * COST_PCT

        trade = {
            "ticker": ticker,
            "side": side,
            "entry_day": p.get("entry_day"),
            "exit_day": today.isoformat(),
            "entry_usd": entry,
            "exit_usd": round(float(exit_px), 6),
            "shares": shares,
            "hold_days": p.get("hold_days"),
            "exit_reason": exit_why,
            "entry_cost_eur": p.get("entry_cost_eur"),
            "exit_cost_eur": round(cost_eur, 4),
            "pnl_eur": round(pnl_eur, 2),
            "pnl_pct_net": round(pnl_pct * 100, 4),
            "reason": p.get("reason"),
            "rule_tags": p.get("rule_tags") or [],
            "decision_id": p.get("decision_id"),
            "closed_at": utc_now_iso(),
        }
        led.setdefault("closed_trades", []).append(trade)
        del led["positions"][ticker]
        closed.append(trade)
        _update_decision_status(p.get("decision_id"), "closed", trade)
    return closed


def append_equity(led: dict) -> None:
    today = now_et().date().isoformat()
    led, _, _ = mark_book(led)
    spy_rows = spy_history_eur(START_DATE)
    spy_today = None
    if spy_rows:
        spy_today = spy_rows[-1].get("spy_eur")
        # align by day if possible
        for r in spy_rows:
            if r["day"] == today:
                spy_today = r["spy_eur"]
                break
    hist = [h for h in led.get("equity_history") or [] if h.get("day") != today]
    hist.append(
        {
            "day": today,
            "equity_eur": led["equity_eur"],
            "spy_eur": spy_today if spy_today is not None else START_CAPITAL_EUR,
            "cash_eur": round(float(led.get("cash_eur") or 0), 2),
            "n_open": len(led.get("positions") or {}),
        }
    )
    led["equity_history"] = hist
    # also store spy series snapshot for dashboard (compact)
    led["spy_history"] = spy_rows[-120:] if spy_rows else led.get("spy_history") or []


def run(apply: bool = True) -> dict:
    ensure_dirs()
    led = load_ledger()
    ingested = ingest_decisions(led)
    led, marks, fx = mark_book(led)
    fills = fill_pending(led, marks, fx) if apply else []
    led, marks, fx = mark_book(led)
    closed = check_exits(led, fx) if apply else []
    append_equity(led)
    led["last_run"] = utc_now_iso()
    led["last_reason"] = (
        f"engine: ingested={ingested} fills={len(fills)} closed={len(closed)} "
        f"open={len(led.get('positions') or {})} session={market_session()}"
    )
    if apply:
        write_json(LEDGER, led)
    out = {
        "as_of": led["last_run"],
        "equity_eur": led.get("equity_eur"),
        "cash_eur": led.get("cash_eur"),
        "ingested": ingested,
        "fills": fills,
        "closed": closed,
        "n_open": len(led.get("positions") or {}),
        "n_pending": len(led.get("pending") or {}),
        "session": market_session(),
    }
    print(__import__("json").dumps(out, indent=2, default=str))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Stocks paper engine")
    ap.add_argument("--apply", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--init", action="store_true", help="reset ledger to starting capital")
    a = ap.parse_args()
    if a.init:
        write_json(LEDGER, empty_ledger())
        print(f"initialized {LEDGER}")
        return 0
    run(apply=not a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

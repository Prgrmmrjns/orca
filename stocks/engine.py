#!/usr/bin/env python3
"""Paper engine: multi-book fills, exits, marks. Rule books auto-decide; agent uses CLI."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import COST_PCT, MAX_POSITION_PCT, MAX_SHORT_PCT, START_CAPITAL_EUR, START_DATE
from stocks.books import AGENT_BOOK_ID, BOOK_BY_ID, BOOKS, BookSpec, matches
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
from stocks.paths import (
    CANDIDATES,
    LEGACY_DECISIONS,
    LEGACY_LEDGER,
    decisions_path,
    ensure_dirs,
    ledger_path,
    read_json,
    write_json,
)


def empty_ledger(book: BookSpec | None = None) -> dict:
    book = book or BOOK_BY_ID[AGENT_BOOK_ID]
    fx = eurusd()
    return {
        "mode": "paper",
        "book_id": book.id,
        "strategy": book.id,
        "strategy_title": book.title,
        "kind": book.kind,
        "base_currency": "EUR",
        "note": (
            f"Paper only. Forward test book `{book.id}` since {START_DATE}. "
            "No broker API. " + book.description
        ),
        "rules": book.description,
        "start_date": START_DATE,
        "start_capital_eur": START_CAPITAL_EUR,
        "eur_usd_start": fx,
        "cash_eur": START_CAPITAL_EUR,
        "equity_eur": START_CAPITAL_EUR,
        "positions": {},
        "pending": [],
        "closed_trades": [],
        "equity_history": [
            {"day": START_DATE, "equity_eur": START_CAPITAL_EUR, "spy_eur": START_CAPITAL_EUR}
        ],
        "spy_history": [],
        "cost_pct": COST_PCT,
        "max_position_pct": MAX_POSITION_PCT if book.side == "long" or book.kind == "agent" else book.size,
        "max_short_pct": MAX_SHORT_PCT,
        "default_size": book.size,
        "default_hold_days": book.hold_days,
        "stop_loss": book.stop_loss,
        "max_open": book.max_open,
        "last_run": None,
        "last_reason": f"Initialized paper book `{book.id}`.",
    }


def empty_decisions() -> dict:
    return {"mode": "paper", "decisions": [], "updated_at": None}


def migrate_legacy_agent_state() -> None:
    """Move top-level ledger/decisions into books/agent/ if agent book is missing."""
    ensure_dirs()
    agent_led = ledger_path(AGENT_BOOK_ID)
    agent_dec = decisions_path(AGENT_BOOK_ID)
    if not agent_led.exists() and LEGACY_LEDGER.exists():
        write_json(agent_led, read_json(LEGACY_LEDGER, default=empty_ledger()))
    if not agent_dec.exists() and LEGACY_DECISIONS.exists():
        write_json(agent_dec, read_json(LEGACY_DECISIONS, default=empty_decisions()))


def init_all_books(force: bool = False) -> list[str]:
    """Ensure every book has a €1,000 ledger. With force=True, reset all books."""
    migrate_legacy_agent_state()
    created = []
    for book in BOOKS:
        lp = ledger_path(book.id)
        dp = decisions_path(book.id)
        if force or not lp.exists():
            # If agent book already exists with meaningful history and not forcing, skip.
            # (force path always resets; !exists always creates.)
            if (
                not force
                and book.id == AGENT_BOOK_ID
                and lp.exists()
            ):
                pass
            else:
                # Reset agent only if empty/tiny when force requested via stocks init;
                # force=True always resets every book.
                if force and book.id == AGENT_BOOK_ID and lp.exists():
                    led = read_json(lp, default={})
                    n_closed = len(led.get("closed_trades") or [])
                    n_open = len(led.get("positions") or {}) + len(led.get("pending") or [])
                    eq = float(led.get("equity_eur") or START_CAPITAL_EUR)
                    tiny = n_closed == 0 and n_open == 0 and abs(eq - START_CAPITAL_EUR) < 1.0
                    if not tiny:
                        # Still reset: user asked for aligned forward test start.
                        pass
                write_json(lp, empty_ledger(book))
                created.append(book.id)
        else:
            led = read_json(lp, default={})
            dirty = False
            if led.get("book_id") != book.id:
                led["book_id"] = book.id
                dirty = True
            if not led.get("strategy_title"):
                led["strategy_title"] = book.title
                dirty = True
            if not led.get("kind"):
                led["kind"] = book.kind
                dirty = True
            if dirty:
                write_json(lp, led)
        if force or not dp.exists():
            write_json(dp, empty_decisions())
    agent_led = read_json(ledger_path(AGENT_BOOK_ID), default=empty_ledger())
    agent_dec = read_json(decisions_path(AGENT_BOOK_ID), default=empty_decisions())
    write_json(
        LEGACY_LEDGER,
        {**agent_led, "note": "DEPRECATED mirror of books/agent/ledger.json — use books/agent/"},
    )
    write_json(LEGACY_DECISIONS, agent_dec)
    return created


def load_ledger(book_id: str) -> dict:
    ensure_dirs()
    book = BOOK_BY_ID[book_id]
    path = ledger_path(book_id)
    led = read_json(path, default=None)
    if not led or led.get("mode") != "paper":
        led = empty_ledger(book)
        write_json(path, led)
    led.setdefault("book_id", book_id)
    led.setdefault("max_open", book.max_open)
    return led


def load_decisions(book_id: str) -> dict:
    ensure_dirs()
    path = decisions_path(book_id)
    d = read_json(path, default=None)
    if not d:
        d = empty_decisions()
        write_json(path, d)
    d.setdefault("decisions", [])
    return d


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
    eq = float(led.get("cash_eur") or 0)
    for t, p in (led.get("positions") or {}).items():
        px = marks[t]
        shares = abs(float(p["shares"]))
        if p.get("side") == "short":
            eq -= (shares * px) / fx
        else:
            eq += (shares * px) / fx
    led["equity_eur"] = round(eq, 2)
    led["eur_usd"] = fx
    return led, marks, fx


def _open_count(led: dict) -> int:
    return len(led.get("positions") or {}) + len(led.get("pending") or [])


def _open_tickers(led: dict, book_id: str) -> set[str]:
    tickers = set((led.get("positions") or {}).keys()) | {
        p.get("ticker") for p in (led.get("pending") or []) if p.get("ticker")
    }
    for d in load_decisions(book_id).get("decisions") or []:
        if d.get("status") in ("new", "accepted", "pending") and d.get("side") in ("long", "short"):
            tickers.add(d["ticker"])
    return tickers


def auto_decide_rule_book(book: BookSpec) -> int:
    """Create decisions for candidates matching a rule book. Returns # new decisions."""
    if book.kind != "rule" or book.matcher is None:
        return 0
    cands = read_json(CANDIDATES, default={})
    spy_above = cands.get("spy_above_ma50")
    led = load_ledger(book.id)
    open_tickers = _open_tickers(led, book.id)
    # One trade per ticker per book for this forward test (no re-entry after close).
    seen = set(open_tickers)
    for t in (led.get("closed_trades") or []):
        if t.get("ticker"):
            seen.add(t["ticker"])
    for d in load_decisions(book.id).get("decisions") or []:
        if d.get("ticker") and d.get("side") in ("long", "short"):
            seen.add(d["ticker"])
    max_open = int(led.get("max_open") or book.max_open)
    dec = load_decisions(book.id)
    n = 0
    for c in cands.get("candidates") or []:
        ticker = c.get("ticker")
        if not ticker or ticker in seen:
            continue
        if not matches(book, c, spy_above):
            continue
        if len(open_tickers) >= max_open:
            break
        reason = (
            f"[{book.id}] surprise={c.get('surprise_pct')} gap={c.get('gap_pct')} "
            f"guidance={c.get('guidance')} spy_above={c.get('spy_above_ma50')} "
            f"→ {book.side} {book.hold_days}d"
        )
        rec = {
            "id": f"{book.id}-{ticker}-{utc_now_iso()}-{uuid.uuid4().hex[:6]}",
            "book_id": book.id,
            "ticker": ticker,
            "side": book.side,
            "size": book.size,
            "hold": book.hold_days,
            "reason": reason,
            "confidence": 1.0,
            "stop_loss": book.stop_loss,
            "take_profit": book.take_profit,
            "rule_tags": [book.id] + list(c.get("rule_tags") or []),
            "surprise_pct": c.get("surprise_pct"),
            "gap_pct": c.get("gap_pct"),
            "guidance": c.get("guidance"),
            "agent": f"rule:{book.id}",
            "prefer_open": True,
            "decided_at": utc_now_iso(),
            "status": "new",
        }
        dec["decisions"].append(rec)
        open_tickers.add(ticker)
        seen.add(ticker)
        n += 1
    if n:
        dec["updated_at"] = utc_now_iso()
        write_json(decisions_path(book.id), dec)
    return n


def ingest_decisions(led: dict, book_id: str) -> int:
    """Move new decisions into ledger.pending for one book."""
    book = BOOK_BY_ID[book_id]
    max_open = int(led.get("max_open") or book.max_open)
    dec = load_decisions(book_id)
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
            d["status"] = d.get("status") if d.get("status") not in (None, "new", "accepted") else "duplicate"
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
        if _open_count(led) >= max_open:
            d["status"] = "rejected_max_open"
            continue
        order = {
            "id": tid,
            "ticker": d["ticker"],
            "side": side,
            "size": size,
            "hold_days": int(d.get("hold") or d.get("hold_days") or book.hold_days),
            "stop_loss": d.get("stop_loss", book.stop_loss),
            "take_profit": d.get("take_profit", book.take_profit),
            "reason": d.get("reason") or "",
            "confidence": d.get("confidence"),
            "rule_tags": d.get("rule_tags") or [],
            "prefer_open": bool(d.get("prefer_open") or book.kind == "rule"),
            "decided_at": d.get("decided_at"),
            "status": "pending",
            "book_id": book_id,
        }
        pending.append(order)
        open_tickers.add(d["ticker"])
        d["status"] = "pending"
        n += 1
    write_json(decisions_path(book_id), dec)
    return n


def fill_pending(led: dict, marks: dict[str, float], fx: float, book_id: str) -> list[dict]:
    """Fill pending at next regular open, or current price if market is open at decision time."""
    session = market_session()
    today = now_et().date()
    fills = []
    still = []
    for order in led.get("pending") or []:
        ticker = order["ticker"]
        bar = daily_bar(ticker, today)
        px = None
        fill_note = ""
        prefer_open = bool(order.get("prefer_open"))
        if session == "open":
            if prefer_open and bar.get("open"):
                px = bar["open"]
                fill_note = "filled at regular-session open (rule book)"
            else:
                snap = price_snapshot(ticker)
                px = snap.get("last") or (bar.get("open") if bar else None) or marks.get(ticker)
                fill_note = "filled at session price (market open)"
        else:
            decided = order.get("decided_at")
            decided_day = None
            if decided:
                try:
                    decided_day = datetime.fromisoformat(decided.replace("Z", "+00:00")).astimezone(
                        now_et().tzinfo
                    ).date()
                except Exception:
                    decided_day = None
            if decided_day and decided_day < today and bar.get("open"):
                px = bar["open"]
                fill_note = "filled at regular-session open"
            elif session == "pre" and bar.get("open"):
                still.append(order)
                continue
            else:
                still.append(order)
                continue
        if not px or px <= 0:
            still.append(order)
            continue

        size = float(order["size"])
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
            "book_id": book_id,
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
            "book_id": book_id,
        }
        fills.append(fill)
        _update_decision_status(book_id, order.get("id"), "filled", fill)
    led["pending"] = still
    return fills


def _update_decision_status(book_id: str, did: str | None, status: str, fill: dict | None = None) -> None:
    if not did:
        return
    path = decisions_path(book_id)
    dec = read_json(path, default={"decisions": []})
    for d in dec.get("decisions") or []:
        if d.get("id") == did:
            d["status"] = status
            if fill:
                d["fill"] = fill
    write_json(path, dec)


def check_exits(led: dict, fx: float, book_id: str) -> list[dict]:
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
            led["cash_eur"] = float(led["cash_eur"]) + notional_eur - cost_eur
            pnl_eur = (float(exit_px) - entry) / fx * shares - float(p.get("entry_cost_eur") or 0) - cost_eur
            pnl_pct = (float(exit_px) / entry - 1) - 2 * COST_PCT
        else:
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
            "book_id": book_id,
            "closed_at": utc_now_iso(),
        }
        led.setdefault("closed_trades", []).append(trade)
        del led["positions"][ticker]
        closed.append(trade)
        _update_decision_status(book_id, p.get("decision_id"), "closed", trade)
    return closed


def append_equity(led: dict) -> None:
    today = now_et().date().isoformat()
    led, _, _ = mark_book(led)
    spy_rows = spy_history_eur(START_DATE)
    spy_today = None
    if spy_rows:
        spy_today = spy_rows[-1].get("spy_eur")
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
    led["spy_history"] = spy_rows[-120:] if spy_rows else led.get("spy_history") or []


def run_book(book_id: str, apply: bool = True) -> dict:
    book = BOOK_BY_ID[book_id]
    ensure_dirs()
    auto_n = 0
    if book.kind == "rule" and apply:
        auto_n = auto_decide_rule_book(book)
    led = load_ledger(book_id)
    ingested = ingest_decisions(led, book_id) if apply else 0
    led, marks, fx = mark_book(led)
    fills = fill_pending(led, marks, fx, book_id) if apply else []
    led, marks, fx = mark_book(led)
    closed = check_exits(led, fx, book_id) if apply else []
    if apply:
        append_equity(led)
        led["last_run"] = utc_now_iso()
        led["last_reason"] = (
            f"engine[{book_id}]: auto={auto_n} ingested={ingested} fills={len(fills)} "
            f"closed={len(closed)} open={len(led.get('positions') or {})} session={market_session()}"
        )
        write_json(ledger_path(book_id), led)
        if book_id == AGENT_BOOK_ID:
            write_json(LEGACY_LEDGER, led)
            write_json(LEGACY_DECISIONS, load_decisions(book_id))
    else:
        led["last_run"] = utc_now_iso()
        led["last_reason"] = f"engine[{book_id}] dry-run (no writes)"
    return {
        "book_id": book_id,
        "as_of": led["last_run"],
        "equity_eur": led.get("equity_eur"),
        "cash_eur": led.get("cash_eur"),
        "auto_decisions": auto_n,
        "ingested": ingested,
        "fills": fills,
        "closed": closed,
        "n_open": len(led.get("positions") or {}),
        "n_pending": len(led.get("pending") or []),
        "session": market_session(),
    }


def run(apply: bool = True, book_id: str | None = None) -> dict:
    init_all_books(force=False)
    ids = [book_id] if book_id else [b.id for b in BOOKS]
    results = []
    for bid in ids:
        results.append(run_book(bid, apply=apply))
    out = {
        "as_of": utc_now_iso(),
        "session": market_session(),
        "books": results,
    }
    print(json.dumps(out, indent=2, default=str))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Stocks paper engine (multi-book)")
    ap.add_argument("--apply", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--init", action="store_true", help="reset all books to €1,000")
    ap.add_argument("--book", default=None, help="run a single book id")
    a = ap.parse_args()
    if a.init:
        created = init_all_books(force=True)
        print(f"initialized books: {created or [b.id for b in BOOKS]}")
        return 0
    if a.book and a.book not in BOOK_BY_ID:
        print(f"unknown book: {a.book}", file=sys.stderr)
        return 2
    run(apply=not a.dry_run, book_id=a.book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

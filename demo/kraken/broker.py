"""Paper broker. Fills at the Kraken Futures mark price plus slippage, charges the taker fee, books realised P&L.
PAPER ONLY: nothing here talks to an order endpoint."""
from __future__ import annotations

from datetime import datetime, timezone

from . import db
import math

from .config import BOOKS, MAX_LEVERAGE, START_CAPITAL_EUR, TAKER_FEE, slippage


def now():
    return datetime.now(timezone.utc).isoformat()


def round_qty(sym: str, q: float) -> float:
    """Round toward zero to the smallest size Kraken accepts for this perp."""
    from .market import instruments
    p = instruments().get(sym, 3)
    f = 10 ** p
    return math.trunc(q * f) / f


def start_capital_usd() -> float:
    v = db.kv_get("start_capital")
    if v:
        return v["usd"]
    from .market import eurusd
    fx = eurusd()
    db.kv_set("start_capital", {"eur": START_CAPITAL_EUR, "eurusd": fx, "usd": START_CAPITAL_EUR * fx, "ts": now()})
    return START_CAPITAL_EUR * fx


def ensure_books():
    have = {b["id"] for b in db.select("books")}
    rows = []
    norm = sum(b["alloc"] for b in BOOKS.values())
    for bid, b in BOOKS.items():
        if bid not in have:
            # starting split only; the agent re-allocates dynamically. A book added to a running account starts empty.
            cap = start_capital_usd() * b["alloc"] / norm if not have else 0.0
            rows.append(dict(id=bid, name=b["name"], description=b["description"], capital=cap, cash=cap, hwm=cap,
                             alloc=b["alloc"] / norm if not have else 0.0, enabled=True, params={}))
    if rows:
        db.insert("books", rows)


def state():
    books = {b["id"]: b for b in db.select("books")}
    pos = db.select("positions")
    by = {bid: [p for p in pos if p["book"] == bid] for bid in books}
    return books, by


def book_equity(book: dict, positions: list[dict], px: dict) -> tuple[float, float]:
    unreal, gross = 0.0, 0.0
    for p in positions:
        x = px.get(p["sym"], p["entry_px"])
        unreal += p["qty"] * (x - p["entry_px"])
        gross += abs(p["qty"] * x)
    return book["cash"] + unreal, gross


def _fill(book_id, sym, cur, dq, px, reason, decision_id, meta=None):
    """Apply a signed quantity change dq to position cur (dict or None). Returns (new_pos or None, cash_delta, trade)."""
    side = "buy" if dq > 0 else "sell"
    fpx = px * (1 + slippage(sym)) if dq > 0 else px * (1 - slippage(sym))
    fee = abs(dq) * fpx * TAKER_FEE
    q0 = cur["qty"] if cur else 0.0
    e0 = cur["entry_px"] if cur else fpx
    q1 = q0 + dq
    pnl = 0.0
    if q0 != 0 and (q0 > 0) != (dq > 0):  # reducing or flipping
        closed = min(abs(dq), abs(q0)) * (1 if q0 > 0 else -1)
        pnl = closed * (fpx - e0)
    if abs(q1) < 1e-12:
        newp = None
    elif q0 == 0 or (q0 > 0) != (q1 > 0):  # opened or flipped
        newp = dict(book=book_id, sym=sym, qty=q1, entry_px=fpx, entry_ts=now(), meta=meta or {})
    elif abs(q1) > abs(q0):  # added
        newp = dict(cur, qty=q1, entry_px=(q0 * e0 + dq * fpx) / q1)
    else:  # reduced
        newp = dict(cur, qty=q1)
    if q0 == 0:
        action = "open long" if dq > 0 else "open short"
    elif abs(q1) < 1e-12:
        action = "close long" if q0 > 0 else "close short"
    elif (q0 > 0) != (q1 > 0):
        action = "flip to long" if q1 > 0 else "flip to short"
    elif abs(q1) > abs(q0):
        action = "add to long" if q0 > 0 else "add to short"
    else:
        action = "reduce long" if q0 > 0 else "reduce short"
    trade = dict(book=book_id, sym=sym, side=side, action=action, venue="perp", qty=abs(dq), px=fpx, notional=abs(dq) * fpx, fee=fee,
                 pnl=pnl - fee if pnl else -fee, reason=reason[:500], decision_id=decision_id, ts=now())
    return newp, pnl - fee, trade


def _write_pos(book_id, sym, newp):
    db.delete("positions", {"book": book_id, "sym": sym})
    if newp:
        keep = {k: newp.get(k) for k in ("book", "sym", "qty", "entry_px", "entry_ts", "stop_px", "take_px", "exit_after", "meta")}
        db.insert("positions", keep)


def rebalance(book_id: str, targets: dict[str, float], px: dict[str, float], reason: str, decision_id: int | None,
              band: float = 0.005) -> list[dict]:
    """Move a book to target weights (fraction of book equity). Skips changes smaller than `band` of equity."""
    books, by = state()
    book, cur = books[book_id], {p["sym"]: p for p in by[book_id]}
    eq, _ = book_equity(book, by[book_id], px)
    trades, cash = [], book["cash"]
    for sym in sorted(set(targets) | set(cur)):
        if sym not in px:
            continue
        tq = round_qty(sym, targets.get(sym, 0.0) * eq / px[sym])
        q0 = cur[sym]["qty"] if sym in cur else 0.0
        dq = tq - q0
        if abs(dq) < 1e-12 or (abs(dq * px[sym]) < band * eq and not (tq == 0 and q0 != 0)):
            continue
        newp, dcash, tr = _fill(book_id, sym, cur.get(sym), dq, px[sym], reason, decision_id)
        cash += dcash
        _write_pos(book_id, sym, newp)
        trades.append(tr)
    if trades:
        db.insert("trades", trades)
        db.update("books", {"id": book_id}, {"cash": cash, "updated": now()})
    return trades


def open_event(book_id: str, sym: str, side: str, frac: float, px: float, stop: float, exit_after: str,
               reason: str, decision_id: int | None, meta: dict) -> dict | None:
    books, by = state()
    book = books[book_id]
    if any(p["sym"] == sym for p in by[book_id]):
        return None
    eq, _ = book_equity(book, by[book_id], {sym: px})
    dq = round_qty(sym, (1 if side == "long" else -1) * frac * eq / px)
    if dq == 0:
        return None  # below Kraken's minimum size for this account size
    if not margin_ok(abs(dq) * px):
        return None
    newp, dcash, tr = _fill(book_id, sym, None, dq, px, reason, decision_id, meta)
    newp["stop_px"] = newp["entry_px"] * (1 - stop) if side == "long" else newp["entry_px"] * (1 + stop)
    newp["exit_after"] = exit_after
    _write_pos(book_id, sym, newp)
    db.insert("trades", tr)
    db.update("books", {"id": book_id}, {"cash": book["cash"] + dcash, "updated": now()})
    return tr


def margin_ok(extra_notional: float, buffer: float = 0.9) -> bool:
    """Total perp notional across all books must stay under MAX_LEVERAGE x total equity (with a buffer)."""
    from .market import tickers
    px = {k: v["px"] for k, v in tickers().items()}
    books, by = state()
    tot = gross = 0.0
    for bid, b in books.items():
        e, g = book_equity(b, by[bid], px)
        tot += e
        gross += g
    return gross + extra_notional <= MAX_LEVERAGE * buffer * tot


def close(book_id: str, pos: dict, px: float, reason: str) -> dict:
    book = db.select("books", {"id": f"eq.{book_id}"})[0]
    _, dcash, tr = _fill(book_id, pos["sym"], pos, -pos["qty"], px, reason, None)
    _write_pos(book_id, pos["sym"], None)
    db.insert("trades", tr)
    db.update("books", {"id": book_id}, {"cash": book["cash"] + dcash, "updated": now()})
    gross_ret = (px / pos["entry_px"] - 1) * (1 if pos["qty"] > 0 else -1)
    tr["ret"] = gross_ret - 2 * (TAKER_FEE + slippage(pos["sym"]))
    return tr


def flatten(book_id: str, px: dict, reason: str):
    return rebalance(book_id, {}, px, reason, None, band=0.0)


def charge_funding(px: dict, rates: dict[str, float], day: str) -> float:
    """Daily funding on open perp positions: longs pay positive rates, shorts receive them."""
    books, by = state()
    total = 0.0
    for bid, plist in by.items():
        c = 0.0
        for p in plist:
            r = rates.get(p["sym"])
            if r is None:
                continue
            c += p["qty"] * px.get(p["sym"], p["entry_px"]) * r
        if c:
            db.update("books", {"id": bid}, {"cash": books[bid]["cash"] - c})
            total += c
    return total

"""HTTP routes: dashboard page, read-only JSON for the UI, and the two scheduler endpoints (secret-protected)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import db

router = APIRouter()
PAGE = Path(__file__).resolve().parent / "static" / "kraken.html"


def _ok(x):
    return JSONResponse(x, headers={"Cache-Control": "no-store"})


def _auth(authorization: str | None):
    sec = os.environ.get("CRON_SECRET")
    if not sec or authorization != f"Bearer {sec}":
        raise HTTPException(401, "unauthorized")


@router.get("/kraken")
def page():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@router.api_route("/api/kraken/cron/tick", methods=["GET", "POST"])
def cron_tick(authorization: str | None = Header(None), all: int = 0):
    _auth(authorization)
    from . import agent
    return _ok(agent.tick(force_all=bool(all)))


@router.api_route("/api/kraken/cron/daily", methods=["GET", "POST"])
def cron_daily(authorization: str | None = Header(None), rebalance: int = 0):
    _auth(authorization)
    from . import agent
    return _ok(agent.daily(force_rebalance=bool(rebalance)))


@router.get("/api/kraken/summary")
def summary():
    from . import broker, market
    books = db.select("books", {"order": "alloc.desc"})
    pos = db.select("positions")
    try:
        tk = market.tickers()
    except Exception:
        tk = {}
    px = {k: v["px"] for k, v in tk.items()}
    out_books, total, gross = [], 0.0, 0.0
    for b in books:
        bp = [p for p in pos if p["book"] == b["id"]]
        eq, g = broker.book_equity(b, bp, px)
        total += eq
        gross += g
        out_books.append(dict(id=b["id"], name=b["name"], description=b["description"], equity=eq, gross=g,
                              alloc=b["alloc"], hwm=b["hwm"], enabled=b["enabled"],
                              dd=1 - eq / b["hwm"] if b["hwm"] else 0))
    positions = []
    for p in pos:
        x = px.get(p["sym"], p["entry_px"])
        positions.append(dict(p, px=x, notional=p["qty"] * x, upnl=p["qty"] * (x - p["entry_px"]),
                              ret=(x / p["entry_px"] - 1) * (1 if p["qty"] > 0 else -1),
                              change24h=tk.get(p["sym"], {}).get("change24h")))
    positions.sort(key=lambda r: -abs(r["notional"]))
    tr = db.select("trades", {"select": "pnl,fee,action"})
    realised = sum((t["pnl"] or 0) + (t["fee"] or 0) for t in tr if t.get("action", "").startswith(("close", "reduce", "flip")))
    fees = sum(t["fee"] or 0 for t in tr)
    first = db.select("equity", {"book": "eq.total", "order": "ts"}, limit=1)
    pol = db.select("model_params", {"model": "eq.policy", "order": "ts.desc"}, limit=1)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return _ok(dict(
        mode="paper", now=datetime.now(timezone.utc).isoformat(), total=total, gross=gross,
        start_equity=first[0]["equity"] if first else None, started=first[0]["ts"] if first else None,
        total_hwm=db.kv_get("total_hwm"), realised_pnl=realised, fees_paid=fees,
        cash=sum(b["cash"] for b in books), capital=db.kv_get("start_capital"),
        eurusd=tk.get("EUR", {}).get("px"), books=out_books, positions=positions,
        last_tick=db.kv_get("last_tick"), daily_done=db.kv_get("daily_done"), llm=db.kv_get("llm_spend"),
        params=db.kv_get("agent_params"), event_stats=db.select("event_stats"),
        policy=pol[0] if pol else None,
        news_24h=len(db.select("news", {"select": "id", "seen": f"gte.{since}"})),
        errors_24h=len(db.select("runs", {"select": "id", "ok": "eq.false", "ts": f"gte.{since}"})),
    ))


@router.get("/api/kraken/equity")
def equity(days: int = Query(30, le=365)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _ok(db.select("equity", {"ts": f"gte.{since}", "order": "ts", "select": "ts,book,equity"}))


@router.post("/api/kraken/lab")
def lab_run(body: dict = Body(...), authorization: str | None = Header(None)):
    _auth(authorization)
    from . import lab
    return lab.run(body["model"], body["system"], body["users"], body["schema"], body.get("max_tokens", 4000),
                   body.get("effort", "low"), body.get("workers", 16))


@router.get("/api/kraken/exposure")
def exposure(days: int = Query(7, le=60)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.select("exposure", {"ts": f"gte.{since}", "order": "ts,sym", "select": "ts,sym,notional"})
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ts"], {})[r["sym"]] = r["notional"]
    return _ok([{"ts": t, "v": v} for t, v in out.items()])


@router.get("/api/kraken/trades")
def trades(limit: int = Query(100, le=500), book: str | None = None):
    p = {"order": "ts.desc"}
    if book:
        p["book"] = f"eq.{book}"
    return _ok(db.select("trades", p, limit=limit))


@router.get("/api/kraken/decisions")
def decisions(limit: int = Query(50, le=200), kind: str | None = None):
    p = {"order": "ts.desc"}
    if kind:
        p["kind"] = f"eq.{kind}"
    return _ok(db.select("decisions", p, limit=limit))


@router.get("/api/kraken/news")
def news(limit: int = Query(150, le=500), min_impact: int = 0, source: str | None = None):
    p = {"order": "seen.desc", "select": "id,source,published,seen,title,url,category,impact,assets,direction,confidence,reason"}
    if min_impact:
        p["impact"] = f"gte.{min_impact}"
    if source:
        p["source"] = f"eq.{source}"
    return _ok(db.select("news", p, limit=limit))


@router.get("/api/kraken/learning")
def learning():
    return _ok(dict(
        lessons=db.select("lessons", {"order": "ts.desc"}, limit=60),
        models=db.select("model_params", {"order": "ts.desc"}, limit=12),
        event_stats=db.select("event_stats"),
        params=db.kv_get("agent_params"),
        runs=db.select("runs", {"order": "ts.desc", "select": "ts,job,ok,ms,detail"}, limit=40),
        llm_usage=db.select("llm_usage", {"order": "ts.desc"}, limit=50),
    ))

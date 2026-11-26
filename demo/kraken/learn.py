"""How the agent adapts.

1. Weekly policy refit: the objective-max linear policy is refit on all data since 2019 (closed form) and blended 50/50
   with last week's coefficients so one noisy week can't flip it. Coefficient drift is logged.
2. Weekly dynamic capital split (no fixed percentages): equal ex-ante risk across the systematic books that currently
   have positions to take (a book with no signal gets nothing), tilted by exp(eta * J_book), J = annualised net log growth
   minus 3x downside variance over the book's last 8 weeks of paper P&L; per-book floor 5%, cap 45%. Events get 5-20%
   depending on their own J.
3. Drawdown brake on total equity: >= 15% below high-water mark -> half size, >= 25% -> quarter size.
4. Event-type track record: every closed event trade updates its type's stats; a losing type is switched off (agent.py).
5. Daily Haiku self-review: reads the day's trades, closed event trades, P&L by book, errors and skipped events, writes
   lessons (fed back into the news triage and event-trader prompts) and may adjust a few bounded knobs."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from . import broker, db, llm, signals
from .config import BOOKS, RISK

ETA = 1.0
MIN_POSITION_USD = 10.0  # with a EUR 1000 account, smaller positions are mostly fees and rounding


def _now():
    return datetime.now(timezone.utc)


def book_returns(days=56) -> dict[str, list[float]]:
    since = (_now() - timedelta(days=days)).isoformat()
    rows = db.select("equity", {"ts": f"gte.{since}", "order": "ts"})
    by: dict[str, dict] = {}
    for r in rows:
        d = r["ts"][:10]
        by.setdefault(r["book"], {})[d] = r["equity"]  # last snapshot per day
    out = {}
    for b, m in by.items():
        v = [m[k] for k in sorted(m)]
        out[b] = [v[i] / v[i - 1] - 1 for i in range(1, len(v)) if v[i - 1] > 0]
    return out


def objective(r: list[float]) -> float:
    x = np.asarray(r)
    if len(x) < 2:
        return 0.0
    return float(365 * np.mean(np.log1p(x)) - 3 * 365 * np.mean(np.minimum(x, 0) ** 2))


def exante_vol(C, w: dict) -> float:
    """Annualised ex-ante vol of a weight vector from the last 60 days of daily returns."""
    cols = [k for k in w if k in C]
    if not cols:
        return 0.0
    R = C[cols].pct_change(fill_method=None).iloc[-60:].fillna(0)
    x = np.array([w[k] for k in cols])
    return float(np.sqrt(max(x @ (R.cov().values * 365) @ x, 0)))


def reallocate(px, tot, plans: dict, C) -> dict:
    """Dynamic capital split (no fixed percentages): equal risk across the systematic books that currently have
    positions to take, tilted by each book's own recent objective J, plus a performance-driven slice for events."""
    books, by = broker.state()
    rets = book_returns()
    tilts = (db.kv_get("agent_params", {}) or {}).get("book_tilts", {})
    info, raw = {}, {}

    def perf(bid):
        r = rets.get(bid, [])
        return (objective(r) if len(r) >= 21 else 0.0), len(r)
    for bid, (w, _) in plans.items():
        if not books.get(bid, {}).get("enabled", False):
            continue
        J, n = perf(bid)
        v = exante_vol(C, w)
        g = sum(abs(x) for x in w.values())
        # risk per unit of capital = v; equal risk -> capital ~ 1/v. A book with no signal gets the floor.
        base = (1.0 / max(v, 0.05)) if g > 0.02 else 0.0
        raw[bid] = base * math.exp(ETA * max(min(J, 1.0), -1.0)) * (1 + float(tilts.get(bid, 0.0)))
        info[bid] = dict(exante_vol_of_targets=round(v, 3), gross=round(g, 3), days=n, J=round(J, 3))
    lo, hi = RISK["events_alloc"]
    ev = books.get("events")
    if ev and ev["enabled"]:
        J, n = perf("events")
        on = [s for s in db.select("event_stats") if s["enabled"]]
        ev_alloc = min(max(0.10 * math.exp(ETA * max(min(J, 1.0), -1.0)), lo), hi)
        info["events"] = dict(days=n, J=round(J, 3), enabled_types=len(on))
    else:
        ev_alloc = 0.0
    s = sum(raw.values())
    alloc = {}
    for bid in books:
        if bid == "events":
            alloc[bid] = ev_alloc
        elif bid in raw and s > 0:
            alloc[bid] = raw[bid] / s * (1 - ev_alloc)
        else:
            alloc[bid] = 0.0
    # floors / caps, then renormalise (two passes are enough here)
    for _ in range(2):
        for bid in alloc:
            if bid != "events" and alloc[bid] > 0:
                alloc[bid] = min(max(alloc[bid], RISK["alloc_floor"]), RISK["alloc_cap"])
        tot_sys = sum(v for k, v in alloc.items() if k != "events")
        if tot_sys > 0:
            for bid in alloc:
                if bid != "events":
                    alloc[bid] *= (1 - ev_alloc) / tot_sys
    # a book with nothing to do keeps its cash as collateral; move capital (paper transfer)
    for bid, b in books.items():
        eq, _ = broker.book_equity(b, by[bid], px)
        target = alloc.get(bid, 0.0) * tot
        db.update("books", {"id": bid}, {"cash": b["cash"] + (target - eq), "alloc": alloc.get(bid, 0.0),
                                          "hwm": b["hwm"] * target / eq if eq > 0 and target > 0 else target})
        info.setdefault(bid, {})["alloc"] = round(alloc.get(bid, 0.0), 4)
    return info


def refit_policy(C, fund) -> dict:
    th, metrics = signals.fit_policy(C, fund)
    prev = db.select("model_params", {"model": "eq.policy", "order": "ts.desc"}, limit=1)
    if prev:
        p = prev[0]["params"]
        blended = {k: 0.5 * th[k] + 0.5 * p.get(k, th[k]) for k in th}
        drift = float(np.linalg.norm([th[k] - p.get(k, 0) for k in th]) / (np.linalg.norm(list(p.values())) + 1e-9))
    else:
        blended, drift = th, None
    metrics.update(drift_vs_last=drift, raw_fit=th)
    db.insert("model_params", dict(model="policy", params=blended, metrics=metrics))
    return dict(theta=blended, metrics=metrics)


EXPLAIN_SYS = """You explain a quantitative PAPER trading book's weekly rebalance to its owner, an ML scientist.
Write 3-5 plain sentences: what the book now holds and why, using the numbers given (signals, features, regime).
No hype, no advice, no Markdown."""


def explain(book, inputs) -> str:
    try:
        out = llm.ask_json("explain", EXPLAIN_SYS, json.dumps(inputs, default=str)[:12000],
                           {"type": "object", "additionalProperties": False, "required": ["text"],
                            "properties": {"text": {"type": "string"}}}, 1500)
        return (out or {}).get("text", "")
    except Exception as e:  # noqa
        return f"(no LLM explanation: {e})"


def weekly(px, tk, tot, hwm) -> dict:
    C, fund, A = signals.load_panel()
    out = {"data_end": str(C.index[-1].date())}
    pol = refit_policy(C, fund)
    out["policy_fit"] = pol["metrics"]
    dd = 1 - tot / hwm if hwm else 0.0
    brake = 1.0
    if dd >= RISK["dd_quarter"]:
        brake = 0.25
    elif dd >= RISK["dd_half"]:
        brake = 0.5
    out["brake"] = brake
    books = {b["id"]: b for b in db.select("books")}
    plans = {}
    w, info = signals.trend_alt(C, A)
    plans["trend_alt"] = (w, info)
    w, info = signals.lowrisk(C)
    plans["lowrisk"] = (w, info)
    w, info = signals.policy(C, fund, pol["theta"])
    info["theta"] = {k: round(v, 3) for k, v in pol["theta"].items()}
    plans["policy"] = (w, info)
    w, info = signals.stocks_trend(C)
    plans["stocks"] = (w, info)
    out["alloc"] = reallocate(px, tot, plans, C)
    books = {b["id"]: b for b in db.select("books")}
    # margin: scale every systematic book down together if the combined notional would exceed the leverage cap
    from .config import MAX_LEVERAGE
    alloc = {b: books[b]["alloc"] for b in books}
    planned = sum(alloc.get(b, 0) * sum(abs(v) for v in w.values()) for b, (w, _) in plans.items()) * brake
    ev_gross = sum(abs(p["qty"]) * px.get(p["sym"], p["entry_px"]) for p in db.select("positions", {"book": "eq.events"})) / max(tot, 1)
    lev_scale = min(1.0, (MAX_LEVERAGE * 0.9 - ev_gross) / planned) if planned > 0 else 1.0
    out["leverage_scale"] = round(lev_scale, 3)
    for bid, (w, info) in plans.items():
        if not books.get(bid, {}).get("enabled", False):
            continue
        tw = {k: v * brake * lev_scale for k, v in w.items() if k in px}
        cap_usd = books[bid]["alloc"] * tot
        tw = {k: v for k, v in tw.items() if abs(v) * cap_usd >= MIN_POSITION_USD}  # skip dust positions
        inputs = dict(book=BOOKS[bid]["name"], rule=BOOKS[bid]["description"], data_through=out["data_end"],
                      book_capital_usd=round(books[bid]["alloc"] * tot, 2),
                      note="Small account: Kraken minimum sizes round some targets to zero; that is expected.",
                      targets={k: round(v, 4) for k, v in tw.items()}, drawdown_brake=brake, total_drawdown=round(dd, 4),
                      signal_info=info)
        reasoning = explain(bid, inputs)
        d = db.insert("decisions", dict(book=bid, kind="rebalance",
                                        summary=f"{BOOKS[bid]['name']}: {len(tw)} positions, gross {sum(abs(v) for v in tw.values()):.2f}, "
                                                f"net {sum(tw.values()):+.2f}",
                                        reasoning=reasoning, inputs=json.loads(json.dumps(info, default=str)), targets=tw),
                      returning=True)[0]
        trades = broker.rebalance(bid, tw, px, f"weekly rebalance ({BOOKS[bid]['name']})", d["id"])
        out[bid] = dict(n_targets=len(tw), trades=len(trades))
    return out


# ------------------------------------------------------------------ daily self-review
REVIEW_SYS = """You are the self-review module of a PAPER crypto trading agent on Kraken perps (four books: trend_alt,
lowrisk, policy = systematic weekly books; events = fast news/event trades). Once a day you read what happened and
(1) write a short daily report for the owner (an ML scientist; plain language, numbers, no hype),
(2) extract lessons from mistakes: concrete, testable rules about event trading or news triage (e.g. "Delisting news
for coins already down >20% in 24h reversed; skip shorts after large drops"). Only state lessons the evidence supports;
small samples are noise, say so. At most 3 new lessons; return [] if nothing new was learned.
(3) optionally adjust bounded knobs for the events book only. Keep current values unless the evidence is clear.
You cannot change the systematic books' rules, the risk limits, or place trades."""

REVIEW_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["report", "lessons", "retire_lessons", "knobs"],
                 "properties": {
                     "report": {"type": "string"},
                     "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                            "required": ["text", "evidence"],
                                                            "properties": {"text": {"type": "string"}, "evidence": {"type": "string"}}}},
                     "retire_lessons": {"type": "array", "items": {"type": "integer"}},
                     "knobs": {"type": "object", "additionalProperties": False,
                               "required": ["event_size_mult", "min_impact", "min_confidence", "why"],
                               "properties": {"event_size_mult": {"type": "number"}, "min_impact": {"type": "integer"},
                                              "min_confidence": {"type": "number"}, "why": {"type": "string"}}}}}


def review() -> dict:
    since = (_now() - timedelta(days=1)).isoformat()
    trades = db.select("trades", {"ts": f"gte.{since}", "order": "ts"}, limit=200)
    decs = db.select("decisions", {"ts": f"gte.{since}", "order": "ts", "select": "ts,book,kind,summary,reasoning"}, limit=60)
    runs = db.select("runs", {"ts": f"gte.{since}", "ok": "eq.false", "select": "ts,job,detail"}, limit=10)
    news = db.select("news", {"seen": f"gte.{since}", "impact": "gte.3", "order": "impact.desc",
                              "select": "title,source,category,impact,direction,assets"}, limit=40)
    eq = db.select("equity", {"ts": f"gte.{(_now() - timedelta(days=8)).isoformat()}", "order": "ts"})
    perf: dict = {}
    for r in eq:
        perf.setdefault(r["book"], []).append(r["equity"])
    perf = {b: dict(start=round(v[0], 2), now=round(v[-1], 2), chg_pct=round((v[-1] / v[0] - 1) * 100, 2)) for b, v in perf.items() if v}
    lessons = db.select("lessons", {"active": "eq.true", "order": "ts.desc"}, limit=20)
    from .agent import params as cur_params
    p = cur_params()
    first = db.select("equity", {"book": "eq.total", "order": "ts", "select": "ts"}, limit=1)
    stats = db.select("event_stats")
    user = json.dumps(dict(date=str(_now().date()), paper_trading_started=first[0]["ts"] if first else None,
                           note="Opening fills show realised P&L = -fee; that is by design.", performance_8d=perf, trades_24h=trades, decisions_24h=decs,
                           failed_jobs_24h=runs, high_impact_news_24h=news, event_type_stats=stats,
                           current_knobs=dict(event_size_mult=p.get("event_size_mult", 1.0), min_impact=p.get("min_impact", 4),
                                              min_confidence=p.get("min_confidence", 0.6)),
                           active_lessons=[dict(id=l["id"], text=l["text"]) for l in lessons]), default=str)[:60000]
    try:
        out = llm.ask_json("review", REVIEW_SYS, user, REVIEW_SCHEMA, 6000, effort="medium")
    except llm.BudgetError as e:
        return {"skipped": str(e)}
    if not out:
        return {"skipped": "refusal"}
    db.insert("decisions", dict(book="agent", kind="daily_report", summary=f"Daily report {_now().date()}",
                                reasoning=out["report"][:6000], inputs=dict(performance_8d=perf)))
    for l in out["lessons"][:3]:
        db.insert("lessons", dict(source="haiku_review", text=l["text"][:500], evidence={"note": l["evidence"][:800]}))
    for lid in out["retire_lessons"][:5]:
        db.update("lessons", {"id": lid}, {"active": False})
    k = out["knobs"]
    new = dict(p, event_size_mult=min(max(float(k["event_size_mult"]), 0.5), 1.5),
               min_impact=min(max(int(k["min_impact"]), 3), 5),
               min_confidence=min(max(float(k["min_confidence"]), 0.5), 0.9))
    changed = {kk: (p.get(kk), new[kk]) for kk in ("event_size_mult", "min_impact", "min_confidence") if p.get(kk) != new[kk]}
    if changed:
        db.insert("lessons", dict(source="haiku_review", text=f"Adjusted event knobs: {changed}. Why: {k['why'][:300]}",
                                  action=changed, applied=True))
    db.kv_set("agent_params", new)
    # keep the active lesson list short
    act = db.select("lessons", {"active": "eq.true", "order": "ts.desc", "select": "id"})
    for r in act[15:]:
        db.update("lessons", {"id": r["id"]}, {"active": False})
    return dict(lessons=len(out["lessons"]), knobs_changed=changed)

"""How the agent adapts.

1. Weekly policy refit: the objective-max linear policy is refit on all data since 2019 (closed form) and blended 50/50
   with last week's coefficients so one noisy week can't flip it. Coefficient drift is logged.
2. Weekly capital re-allocation between books: prior allocation x exp(eta * J_book), J = annualised net log growth minus
   3x downside variance over the last 8 weeks of the book's own paper P&L, clipped to [0.5x, 1.6x] of the prior.
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


def reallocate(px, tot) -> dict:
    books, by = broker.state()
    rets = book_returns()
    tilts = (db.kv_get("agent_params", {}) or {}).get("book_tilts", {})
    raw, info = {}, {}
    for bid, b in books.items():
        prior = (b.get("params") or {}).get("prior_alloc", BOOKS.get(bid, {}).get("alloc", 0.1))
        r = rets.get(bid, [])
        J = objective(r) if len(r) >= 21 else 0.0
        a = prior * math.exp(ETA * J) * (1 + float(tilts.get(bid, 0.0)))
        a = min(max(a, RISK["alloc_min"] * prior), RISK["alloc_max"] * prior)
        if not b["enabled"]:
            a = 0.0
        raw[bid] = a
        info[bid] = dict(prior=prior, days=len(r), J=round(J, 3), tilt=tilts.get(bid, 0.0))
    s = sum(raw.values()) or 1.0
    alloc = {k: v / s for k, v in raw.items()}
    # move cash so each book's equity matches its new allocation (paper transfer)
    for bid, b in books.items():
        eq, _ = broker.book_equity(b, by[bid], px)
        target = alloc[bid] * tot
        db.update("books", {"id": bid}, {"cash": b["cash"] + (target - eq), "alloc": alloc[bid],
                                          "hwm": b["hwm"] * target / eq if eq > 0 else target})
        info[bid]["alloc"] = round(alloc[bid], 4)
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
    out["alloc"] = reallocate(px, tot)
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
    for bid, (w, info) in plans.items():
        if not books.get(bid, {}).get("enabled", False):
            continue
        tw = {k: v * brake for k, v in w.items() if k in px}
        inputs = dict(book=BOOKS[bid]["name"], rule=BOOKS[bid]["description"], data_through=out["data_end"],
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
    p = db.kv_get("agent_params", {}) or {}
    stats = db.select("event_stats")
    user = json.dumps(dict(date=str(_now().date()), performance_8d=perf, trades_24h=trades, decisions_24h=decs,
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

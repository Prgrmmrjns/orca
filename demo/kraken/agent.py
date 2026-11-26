"""The agent's jobs.

tick()   every minute  : scrape due sources, store news, Haiku-classify, open/close event trades, mark to market.
daily()  once a day    : update prices/funding/alt data, charge funding, risk checks, weekly rebalance (Mondays),
                         weekly policy refit + capital re-allocation, Haiku self-review and daily report.
Everything is paper. No order is ever sent to Kraken."""
from __future__ import annotations

import json
import math
import re
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import broker, db, feeds, llm, market
from .config import BOOKS, PERPS, RISK

EVENTS = "events"


def utcnow():
    return datetime.now(timezone.utc)


def log_run(job, ok, t0, detail=""):
    try:
        db.insert("runs", dict(job=job, ok=ok, ms=int((time.time() - t0) * 1000), detail=str(detail)[:4000]))
    except Exception:
        pass


def params() -> dict:
    p = db.kv_get("agent_params", None) or {}
    base = dict(event_size_mult=1.0, min_impact=4, min_confidence=0.6, disabled_event_types=[], book_tilts={})
    base.update(p)
    return base


def active_lessons(n=12) -> list[str]:
    rows = db.select("lessons", {"active": "eq.true", "order": "ts.desc"}, limit=n)
    return [r["text"] for r in rows]


# ============================================================== news classification
CLASSIFY_SYS = """You triage breaking news for a crypto perpetual-futures paper-trading agent on Kraken.
For each item judge the likely price impact over the next 1-24 hours on coins tradable as Kraken perps
(BTC, ETH, SOL, XRP, ADA, DOGE, LINK, AVAX, LTC, DOT, BCH, XLM, UNI, ATOM, NEAR, AAVE, FIL, ETC, TRX, INJ and ~170 others).
impact: 1 = noise/irrelevant, 2 = minor, 3 = notable, 4 = likely to move a coin or the whole market >2%, 5 = major shock.
Most items are 1-2. Macro data (CPI, payrolls, FOMC) moves BTC/ETH only a little (8-11 bp in 5 minutes on average): rate
3 or less unless it is a big surprise. Exchange listings, hacks/exploits, ETF decisions, regulatory actions against a
specific coin, and large unlocks can matter. Old news, opinion, price recaps, ads, and whale-transfer alerts are 1.
assets: tickers of coins clearly affected (use "MARKET" for broad crypto). direction: long, short or none.
Lessons the agent learned from its own past mistakes:
{lessons}"""

CLASSIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "category", "impact", "assets", "direction", "confidence", "reason"],
        "properties": {
            "id": {"type": "string"},
            "category": {"type": "string", "enum": ["listing", "delisting", "hack_exploit", "regulatory", "etf",
                                                     "macro_data", "fed", "partnership_product", "unlock_supply",
                                                     "exchange_ops", "market_move", "corporate_treasury", "other"]},
            "impact": {"type": "integer"},
            "assets": {"type": "array", "items": {"type": "string"}},
            "direction": {"type": "string", "enum": ["long", "short", "none"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"}}}}}}


def classify_pending(max_items=60) -> int:
    rows = db.select("news", {"classified": "eq.false", "order": "seen.desc"}, limit=max_items)
    if not rows:
        return 0
    lessons = "\n".join(f"- {l}" for l in active_lessons(8)) or "- (none yet)"
    done = 0
    for i in range(0, len(rows), 20):
        chunk = rows[i:i + 20]
        lines = [json.dumps({"id": r["id"], "source": r["source"], "published": r["published"],
                             "title": r["title"], "excerpt": (r.get("summary") or "")[:400]}) for r in chunk]
        try:
            out = llm.ask_json("classify", CLASSIFY_SYS.format(lessons=lessons), "\n".join(lines), CLASSIFY_SCHEMA, 4000)
        except llm.BudgetError:
            return done
        got = {x["id"]: x for x in (out or {}).get("items", [])}
        for r in chunk:
            x = got.get(r["id"])
            vals = dict(classified=True)
            if x:
                vals.update(category=x["category"], impact=max(1, min(5, int(x["impact"]))),
                            assets=[a.upper()[:12] for a in x["assets"]][:6], direction=x["direction"],
                            confidence=float(max(0, min(1, x["confidence"]))), reason=x["reason"][:300])
                r.update(vals)
            db.update("news", {"id": r["id"]}, vals)
            done += 1
        events_from_news([r for r in chunk if r.get("impact")])
    return done


# ============================================================== event trades
LIST_RE = re.compile(r"\(([A-Z0-9]{2,12})\)")


def _cooldown_ok(sym, hours=12) -> bool:
    since = (utcnow() - timedelta(hours=hours)).isoformat()
    return not db.select("trades", {"book": f"eq.{EVENTS}", "sym": f"eq.{sym}", "ts": f"gte.{since}"}, limit=1)


def _stats_enabled(etype: str, p: dict) -> bool:
    if etype in p.get("disabled_event_types", []):
        return False
    s = db.select("event_stats", {"etype": f"eq.{etype}"})
    return s[0]["enabled"] if s else True


TRADER_SYS = """You are the event trader of a PAPER crypto perpetual-futures book on Kraken. A news item was rated high impact.
Decide whether to open ONE position. Costs are ~0.2-0.4% round trip. Research by this agent found: Binance listing
announcements gave +4-5% on average over 1-4 h on Kraken perps (15 events, under half won); macro data releases have no
edge after costs; most news is priced within minutes. Skip if the move already happened (see 24h change), if the news is
old, vague, or not specific to the coin. Prefer skip when unsure. Size is a fraction of the events book (max 0.25).
Lessons from the agent's own past mistakes:
{lessons}
Track record per event type (n, win rate, mean net return): {stats}"""

TRADER_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["action", "symbol", "size", "hold_hours", "stop", "reasoning"],
                 "properties": {"action": {"type": "string", "enum": ["long", "short", "skip"]},
                                "symbol": {"type": "string"}, "size": {"type": "number"},
                                "hold_hours": {"type": "number"}, "stop": {"type": "number"},
                                "reasoning": {"type": "string"}}}


def _stats_text():
    rows = db.select("event_stats")
    return json.dumps({r["etype"]: [r["n"], round(r["wins"] / r["n"], 2) if r["n"] else None,
                                    round(r["sum_ret"] / r["n"], 4) if r["n"] else None] for r in rows}) or "{}"


def events_from_news(items: list[dict], tick: dict | None = None):
    p = params()
    try:
        bk = db.select("books", {"id": f"eq.{EVENTS}"})[0]
    except IndexError:
        return
    if not bk["enabled"]:
        return
    tk = tick or market.tickers()
    for it in items:
        src, title = it["source"], it["title"]
        # 1) rule-based Binance listing / delisting trades (from the CMS API, the fastest source)
        if src in ("binance_listing", "binance_delisting") or (src == "tg:binance_announcements" and re.search(r"Will (List|Delist|Add)", title)):
            listing = "delist" not in title.lower() and ("Will List" in title or "Will Add" in title or src == "binance_listing")
            if src == "binance_listing" and not re.search(r"Will (List|Add)", title):
                continue
            etype = "binance_listing" if listing else "binance_delisting"
            if not _stats_enabled(etype, p):
                continue
            for base in set(LIST_RE.findall(title)):
                if base not in tk or not _cooldown_ok(base):
                    continue
                side = "long" if listing else "short"
                frac = (0.15 if listing else 0.08) * p["event_size_mult"]
                hold = 4 if listing else 24
                d = db.insert("decisions", dict(book=EVENTS, kind="event", summary=f"{side} {base}: {title[:150]}",
                                                reasoning=f"Rule: {etype}. Research: Binance listings gave +4-5% avg over 1-4h on "
                                                          f"Kraken perps (n=15, not proven). Size {frac:.0%} of book, hold {hold}h, "
                                                          f"stop {RISK['event_stop']:.0%}.",
                                                inputs=dict(news_id=it["id"], source=src, etype=etype)), returning=True)[0]
                broker.open_event(EVENTS, base, side, min(frac, RISK["event_max_weight"]), tk[base]["px"], RISK["event_stop"],
                                  (utcnow() + timedelta(hours=hold)).isoformat(), f"{etype}: {title[:200]}", d["id"],
                                  dict(etype=etype, news_id=it["id"]))
            continue
        # 2) Haiku-rated high-impact news -> Haiku trader decides
        if (it.get("impact") or 0) < p["min_impact"] or (it.get("confidence") or 0) < p["min_confidence"]:
            continue
        if it.get("direction") not in ("long", "short"):
            continue
        cands = [a for a in (it.get("assets") or []) if a in tk]
        if not cands:
            continue
        etype = f"news:{it.get('category') or 'other'}"
        if not _stats_enabled(etype, p):
            continue
        ctx = {a: dict(px=tk[a]["px"], change24h_pct=tk[a].get("change24h"), funding_per_h=tk[a].get("funding_rel"))
               for a in cands}
        user = json.dumps(dict(news=dict(title=it["title"], source=it["source"], published=it.get("published"),
                                         seen=it.get("seen"), excerpt=(it.get("summary") or "")[:500],
                                         triage=dict(category=it.get("category"), impact=it.get("impact"),
                                                     direction=it.get("direction"), reason=it.get("reason"))),
                               market=ctx, now=utcnow().isoformat()))
        try:
            dec = llm.ask_json("trade", TRADER_SYS.format(lessons="\n".join(f"- {l}" for l in active_lessons(10)) or "- none",
                                                          stats=_stats_text()), user, TRADER_SCHEMA, 2000, effort="medium")
        except llm.BudgetError:
            return
        if not dec:
            continue
        sym = dec["symbol"].upper()
        d = db.insert("decisions", dict(book=EVENTS, kind="event", summary=f"{dec['action']} {sym}: {it['title'][:150]}",
                                        reasoning=dec["reasoning"][:2000], inputs=dict(news_id=it["id"], etype=etype, ctx=ctx),
                                        targets=dec), returning=True)[0]
        if dec["action"] == "skip" or sym not in tk or not _cooldown_ok(sym):
            continue
        frac = max(0.02, min(float(dec["size"]), RISK["event_max_weight"])) * p["event_size_mult"]
        stop = max(0.015, min(float(dec["stop"]), RISK["event_stop"]))
        hold = max(0.5, min(float(dec["hold_hours"]), RISK["event_max_hold_h"]))
        broker.open_event(EVENTS, sym, dec["action"], min(frac, RISK["event_max_weight"]), tk[sym]["px"], stop,
                          (utcnow() + timedelta(hours=hold)).isoformat(), f"{etype}: {it['title'][:200]}", d["id"],
                          dict(etype=etype, news_id=it["id"]))


def manage_events(tk: dict):
    pos = db.select("positions", {"book": f"eq.{EVENTS}"})
    now = utcnow()
    for p in pos:
        px = tk.get(p["sym"], {}).get("px")
        if not px:
            continue
        long = p["qty"] > 0
        hit_stop = p.get("stop_px") and ((long and px <= p["stop_px"]) or (not long and px >= p["stop_px"]))
        expired = p.get("exit_after") and now >= datetime.fromisoformat(p["exit_after"])
        if hit_stop or expired:
            tr = broker.close(EVENTS, p, px, "stop hit" if hit_stop else "planned exit (hold time over)")
            etype = (p.get("meta") or {}).get("etype", "unknown")
            update_event_stats(etype, tr["ret"])


def update_event_stats(etype: str, ret: float):
    s = db.select("event_stats", {"etype": f"eq.{etype}"})
    s = s[0] if s else dict(etype=etype, n=0, wins=0, sum_ret=0.0, sum_ret2=0.0, enabled=True)
    s.update(n=s["n"] + 1, wins=s["wins"] + (ret > 0), sum_ret=s["sum_ret"] + ret, sum_ret2=s["sum_ret2"] + ret * ret,
             updated=utcnow().isoformat())
    n = s["n"]
    if n >= 8 and s["enabled"]:
        m = s["sum_ret"] / n
        sd = math.sqrt(max(s["sum_ret2"] / n - m * m, 1e-12))
        if m < 0 and m / (sd / math.sqrt(n)) < -1.0:
            s["enabled"] = False
            db.insert("lessons", dict(source="stats", text=f"Event type {etype} is switched off: {n} trades, mean net "
                                                          f"return {m:+.2%}, t={m / (sd / math.sqrt(n)):.1f}.",
                                      evidence=dict(n=n, mean=m, sd=sd), action=dict(disable=etype), applied=True))
    db.insert("event_stats", s, upsert=True)


# ============================================================== mark to market
def snapshot(tk: dict, store: bool):
    books, by = broker.state()
    px = {k: v["px"] for k, v in tk.items()}
    tot, gross = 0.0, 0.0
    ts = utcnow().replace(second=0, microsecond=0).isoformat()
    rows = []
    for bid, b in books.items():
        eq, g = broker.book_equity(b, by[bid], px)
        tot += eq
        gross += g
        if eq > b["hwm"]:
            db.update("books", {"id": bid}, {"hwm": eq})
        rows.append(dict(ts=ts, book=bid, equity=eq, gross=g))
    rows.append(dict(ts=ts, book="total", equity=tot, gross=gross))
    if store:
        db.insert("equity", rows, upsert=True)
    hwm = max(db.kv_get("total_hwm", 0) or 0, tot)
    db.kv_set("total_hwm", hwm)
    return tot, hwm


# ============================================================== tick
def tick(force_all=False) -> dict:
    t0 = time.time()
    out = {}
    try:
        broker.ensure_books()
        m = utcnow()
        minute = m.hour * 60 + m.minute
        items, status = feeds.poll(minute, force_all=force_all)
        out["sources"] = status
        if items:
            ids = list({i["id"] for i in items})
            have = set()
            for j in range(0, len(ids), 150):
                have |= {r["id"] for r in db.select("news", {"select": "id", "id": f"in.({','.join(ids[j:j + 150])})"})}
            new, seen_ids = [], set()
            for i in items:
                if i["id"] not in have and i["id"] not in seen_ids:
                    seen_ids.add(i["id"])
                    new.append(dict(i, seen=m.isoformat()))
            db.insert("news", new, ignore_dupes=True)
            out["new_news"] = len(new)
        tk = market.tickers()
        out["classified"] = classify_pending()
        manage_events(tk)
        tot, hwm = snapshot(tk, store=(m.minute % 10 == 0) or force_all)
        out["equity"] = round(tot, 2)
        db.kv_set("last_tick", dict(ts=m.isoformat(), out={k: v for k, v in out.items() if k != "sources"},
                                    source_errors={k: v for k, v in status.items() if isinstance(v, str)}))
        # self-healing: run the daily job if the scheduler missed it
        last_daily = db.kv_get("daily_done")
        last_try = db.kv_get("daily_attempt")
        if last_daily != m.date().isoformat() and m.hour >= 1 and (
                not last_try or (m - datetime.fromisoformat(last_try)).total_seconds() > 1800):
            db.kv_set("daily_attempt", m.isoformat())
            out["daily"] = daily()
        log_run("tick", True, t0, json.dumps(out)[:2000])
    except Exception as e:  # noqa
        log_run("tick", False, t0, traceback.format_exc())
        out["error"] = repr(e)
    return out


# ============================================================== daily
def update_data() -> dict:
    info = {}
    rows = market.daily_closes(10)
    db.insert("prices_daily", rows, upsert=True)
    info["prices"] = len(rows)
    fr = market.daily_funding(10)
    db.insert("funding_daily", fr, upsert=True)
    info["funding"] = len(fr)
    alt, errs = market.alt_recent(20)
    db.insert("altdata", alt, upsert=True)
    info["alt"] = len(alt)
    if errs:
        info["alt_errors"] = errs
    return info


def daily(force_rebalance=False) -> dict:
    t0 = time.time()
    out: dict = {}
    today = utcnow().date()
    try:
        broker.ensure_books()
        out["data"] = update_data()
        tk = market.tickers()
        px = {k: v["px"] for k, v in tk.items()}
        # funding for yesterday on open positions
        yday = (today - timedelta(days=1)).isoformat()
        if db.kv_get("funding_charged") != yday:
            rates = {r["sym"]: r["rate"] for r in db.select("funding_daily", {"d": f"eq.{yday}"})}
            out["funding_paid"] = broker.charge_funding(px, rates, yday)
            db.kv_set("funding_charged", yday)
        tot, hwm = snapshot(tk, store=True)
        # risk: book kill switch
        books, by = broker.state()
        for bid, b in books.items():
            eq, _ = broker.book_equity(b, by[bid], px)
            if b["enabled"] and b["hwm"] > 0 and 1 - eq / b["hwm"] >= RISK["book_kill_dd"]:
                broker.flatten(bid, px, f"kill switch: book drawdown {1 - eq / b['hwm']:.0%}")
                db.update("books", {"id": bid}, {"enabled": False})
                db.insert("lessons", dict(source="risk", text=f"Book {bid} was switched off after a {1 - eq / b['hwm']:.0%} drawdown.",
                                          evidence=dict(equity=eq, hwm=b["hwm"]), applied=True))
        never = not db.kv_get("first_rebalance")
        if today.weekday() == 0 or never or force_rebalance:
            from . import learn
            out["learn"] = learn.weekly(px, tk, tot, hwm)
            db.kv_set("first_rebalance", db.kv_get("first_rebalance") or today.isoformat())
        from . import learn
        out["review"] = learn.review()
        db.kv_set("daily_done", today.isoformat())
        log_run("daily", True, t0, json.dumps(out, default=str)[:4000])
    except Exception as e:  # noqa
        log_run("daily", False, t0, traceback.format_exc())
        out["error"] = repr(e)
    return out

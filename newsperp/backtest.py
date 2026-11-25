"""Historical backtest of the news -> perp idea on Kraken stock perps (Feb 2026 onward).

  python -m newsperp backtest [--latency 45] [--cost-bp 20]

Events (no look-ahead: only what was public at the time):
  * every SEC 8-K of the 10 perp stocks (EDGAR acceptance time to the second; text = exhibit 99.x)
  * company IR press releases from Q4 feeds (minute-precision publish time)
  Releases of the same company within 20 min are merged; the earliest time counts.

We "see" an event `latency` seconds after publication and fill at the close of the
1-minute perp bar containing that moment, paying `cost_bp` per side (half-spread +
slippage + taker fee). Stops are checked on bar highs/lows; time exits at bar close.

Signal variants:
  rules    keyword rules from classify.py (what the live book does without an API key)
  llm      the live Claude classifier (only when ANTHROPIC_API_KEY is set; cached)
  follow   trade in the direction of the move between publication and our entry, if |move| >= 1%
  fade     the opposite of follow
  random   random direction on every event (control, averaged over seeds)
"""
from __future__ import annotations

import datetime as dt
import json
import random
import statistics as st
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import market
from .classify import Classifier, rule_classify
from .collector import CFG, ROOT, load_json
from .sources import Q4_PATH, Q4_TZ, SEC_UA, WEB_UA, _clean, sec_exhibit_text

CACHE = Path(__file__).parent / "data_cache"
OUT = ROOT / "reports" / "newsperp"
UTC = dt.timezone.utc

# ---------------------------------------------------------------- data ----
class Bars:
    """1-minute OHLC per perp, cached on disk per 12h chunk."""
    def __init__(self):
        self.mem = {}
        (CACHE / "bars").mkdir(parents=True, exist_ok=True)

    def chunk(self, perp, c):
        k = (perp, c)
        if k in self.mem: return self.mem[k]
        f = CACHE / "bars" / f"{perp}_{c}.json"
        if f.exists() and (c + 1) * 43200 < time.time() - 3600:
            d = json.loads(f.read_text())
        else:
            cs = market.candles(perp, c * 43200, (c + 1) * 43200)
            d = {str(x["time"] // 1000): [float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])] for x in cs}
            f.write_text(json.dumps(d)); time.sleep(0.15)
        self.mem[k] = {int(t): v for t, v in d.items()}
        return self.mem[k]

    def bar(self, perp, t):
        """Bar containing t; if no trade that minute, carry the last close forward (flat bar)."""
        m = (t // 60) * 60
        for back in range(0, 30):
            mm = m - 60 * back
            b = self.chunk(perp, mm // 43200).get(mm)
            if b: return b if back == 0 else [b[3]] * 4
        return None

def _get_json(url, ua, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=20); r.raise_for_status(); return r.json()
        except Exception:
            time.sleep(1 + i)
    return None

def build_events(since="2026-02-07", refresh=False) -> list[dict]:
    f = CACHE / f"events_{since}.json"
    if f.exists() and not refresh: return json.loads(f.read_text())
    cfg = load_json(CFG / "sources.json", {})
    uni = cfg["universe"]
    raw = []
    for sym, u in uni.items():
        if not (u.get("perp") and u.get("cik")): continue
        j = _get_json(f"https://data.sec.gov/submissions/CIK{u['cik']:010d}.json", SEC_UA)
        rec = j["filings"]["recent"]; time.sleep(0.2)
        for i in range(len(rec["form"])):
            if rec["form"][i] not in ("8-K", "8-K/A") or rec["filingDate"][i] < since: continue
            acc = rec["accessionNumber"][i]
            item = dict(source="sec_8k", url=f"https://www.sec.gov/Archives/edgar/data/{u['cik']}/{acc.replace('-', '')}/{rec['primaryDocument'][i]}")
            body = sec_exhibit_text(item, 4000); time.sleep(0.2)
            raw.append(dict(symbol=sym, t=int(dt.datetime.fromisoformat(rec["acceptanceDateTime"][i].replace("Z", "+00:00")).timestamp()),
                            source="sec_8k", title=f"{sym} 8-K items {rec['items'][i]}", items=rec["items"][i], body=body))
    for src in cfg["sources"]:
        if src["kind"] != "q4_json": continue
        sym = src["tickers"][0]; base = src["url"].rstrip("/")
        j = _get_json(base + Q4_PATH.replace("pageSize=10", "pageSize=100"), WEB_UA)
        if not j: continue
        tz = ZoneInfo(Q4_TZ.get(base.split("//")[-1], "America/New_York"))
        for it in j.get("GetPressReleaseListResult", []):
            try: t = dt.datetime.strptime(it["PressReleaseDate"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=tz)
            except Exception: continue
            if t.astimezone(UTC).strftime("%Y-%m-%d") < since: continue
            raw.append(dict(symbol=sym, t=int(t.timestamp()), source=src["id"], title=_clean(it.get("Headline"), 300), items="",
                            body=_clean(" ".join(str(it.get(k) or "") for k in ("Subheadline", "ShortBody") if it.get(k) not in (None, "None")), 4000)))
    raw.sort(key=lambda e: (e["symbol"], e["t"]))
    events = []
    for e in raw:                                   # merge same-company releases within 20 minutes
        last = events[-1] if events else None
        if last and last["symbol"] == e["symbol"] and e["t"] - last["t"] <= 1200:
            if e["source"] != "sec_8k": last["title"] = e["title"]
            last["body"] = (last["body"] + "\n\n" + e["body"])[:6000]
            last["items"] = last["items"] or e["items"]; last["sources"].append(e["source"])
            continue
        events.append(dict(e, sources=[e["source"]]))
    events.sort(key=lambda e: e["t"])
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(events))
    return events

# ------------------------------------------------------------- signals ----
def signals(events, variant, bars, uni, latency, seed=0, clf_cache=None):
    rng = random.Random(seed); out = []
    for e in events:
        perp = uni[e["symbol"]]["perp"]; t_in = e["t"] + latency
        if variant in ("follow", "fade"):
            b0, b1 = bars.bar(perp, e["t"] - 60), bars.bar(perp, t_in)
            if not b0 or not b1: continue
            mv = b1[3] / b0[3] - 1
            if abs(mv) < 0.01 or abs(mv) > 0.08: continue      # > 8% in a minute on these thin perps is usually a bad print
            side = "long" if (mv > 0) == (variant == "follow") else "short"
            out.append(dict(e, side=side, hold=120, conf=None, why=f"move {mv*100:+.1f}% before entry"))
        elif variant == "random":
            out.append(dict(e, side=rng.choice(["long", "short"]), hold=120, conf=None, why="random"))
        elif variant == "rules":
            r = rule_classify(dict(title=e["title"], summary="", tickers=[e["symbol"]]), e["body"], [e["symbol"]])
            for i in r["impacts"]:                     # same entry filters as the live book
                if i["confidence"] >= 0.65 and abs(i["expected_move_pct"]) >= 1.0:
                    out.append(dict(e, side=i["direction"], hold=i["horizon_minutes"], conf=i["confidence"], why=r["event_type"]))
        elif variant == "llm":
            r = clf_cache.get(f"{e['symbol']}|{e['t']}")
            for i in (r or {}).get("impacts", []):
                if i["confidence"] >= 0.65 and abs(i["expected_move_pct"]) >= 1.0 and uni.get(i["symbol"], {}).get("perp"):
                    out.append(dict(e, symbol=i["symbol"], side=i["direction"], hold=i["horizon_minutes"], conf=i["confidence"], why=r["event_type"]))
    return out

def llm_classify_all(events, uni):
    f = CACHE / "llm_cache.json"
    cache = load_json(f, {})
    clf = Classifier([s for s, u in uni.items() if u.get("perp")])
    if clf.method != "llm": return None
    for e in events:
        k = f"{e['symbol']}|{e['t']}"
        if k in cache: continue
        item = dict(source=e["sources"][0], published=dt.datetime.fromtimestamp(e["t"], UTC).isoformat(),
                    title=e["title"], summary="", tickers=[e["symbol"]])
        cache[k] = clf.classify(item, e["body"])
        f.write_text(json.dumps(cache))
    return cache

# ----------------------------------------------------------- simulator ----
def simulate(sigs, bars, uni, bcfg, latency, cost_bp, start_eq=1000.0):
    cost = cost_bp / 1e4
    eq = start_eq; open_until = {}; trades = []
    for s in sorted(sigs, key=lambda x: x["t"]):
        t_in = s["t"] + latency
        open_until = {k: v for k, v in open_until.items() if v > t_in}
        if s["symbol"] in open_until or len(open_until) >= bcfg["max_open_positions"]: continue
        perp = uni[s["symbol"]]["perp"]
        b = bars.bar(perp, t_in)
        if not b: continue
        stop_pct = float(bcfg["stop_pct"].get(s["symbol"], bcfg["stop_pct"]["default"])) / 100
        notional = min(bcfg["risk_per_trade"] * eq / stop_pct, bcfg["max_position_frac"] * eq)
        sign = 1 if s["side"] == "long" else -1
        entry = b[3] * (1 + sign * cost)
        stop = entry * (1 - sign * stop_pct)
        hold = int(min(max(s["hold"] or 120, 15), bcfg["max_hold_minutes"]))
        m0 = (t_in // 60) * 60; exit_px = None; why = "time"; t_exit = m0 + hold * 60
        last_close = b[3]
        for k in range(1, hold + 1):
            bb = bars.bar(perp, m0 + 60 * k)
            if not bb: continue
            o, h, l, c = bb
            if (sign == 1 and l <= stop) or (sign == -1 and h >= stop):
                fill = min(stop, o) if sign == 1 else max(stop, o)       # gap through the stop fills at the open
                exit_px, why, t_exit = fill * (1 - sign * cost), "stop", m0 + 60 * k; break
            last_close = c
        if exit_px is None:
            exit_px = last_close * (1 - sign * cost)
        ret = sign * (exit_px / entry - 1)
        pnl = notional * ret; eq += pnl
        open_until[s["symbol"]] = t_exit
        trades.append(dict(time=dt.datetime.fromtimestamp(s["t"], UTC).strftime("%Y-%m-%d %H:%M"), symbol=s["symbol"],
                           side=s["side"], title=s["title"][:80], items=s.get("items"), why=s["why"], conf=s.get("conf"),
                           exit=why, hold_min=hold, ret_pct=round(ret * 100, 2), pnl_eur=round(pnl, 2), equity=round(eq, 2)))
    return trades

def stats(trades, start_eq=1000.0):
    if not trades: return dict(trades=0)
    rets = [t["ret_pct"] for t in trades]
    eqs = [start_eq] + [t["equity"] for t in trades]
    peak, mdd = start_eq, 0.0
    for x in eqs: peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    return dict(trades=len(trades), win_rate=round(sum(r > 0 for r in rets) / len(rets), 2),
                avg_ret_pct=round(st.mean(rets), 2), median_ret_pct=round(st.median(rets), 2),
                total_pct=round((eqs[-1] / start_eq - 1) * 100, 2), max_dd_pct=round(mdd * 100, 2),
                stops=sum(t["exit"] == "stop" for t in trades))

def run(latency=45, cost_bp=20, since="2026-02-07"):
    uni = load_json(CFG / "sources.json", {})["universe"]
    bcfg = load_json(CFG / "book.json", {})
    events = build_events(since)
    bars = Bars()
    llm = llm_classify_all(events, uni)
    res = dict(generated=dt.datetime.now(UTC).isoformat(timespec="seconds"), since=since, n_events=len(events),
               latency_s=latency, cost_bp_per_side=cost_bp,
               n_earnings_events=sum("2.02" in (e["items"] or "") or "results" in e["title"].lower() for e in events),
               classifier="llm" if llm else "not available (no ANTHROPIC_API_KEY)", variants={}, trades={})
    for v in ("rules", "follow", "fade", "llm"):
        if v == "llm" and not llm: continue
        sig = signals(events, v, bars, uni, latency, clf_cache=llm)
        out = {}
        for c in (cost_bp, 2 * cost_bp):
            tr = simulate(sig, bars, uni, bcfg, latency, c)
            out[f"cost_{c}bp"] = stats(tr)
            if c == cost_bp: res["trades"][v] = tr
        for lat in (10, 120, 300):
            out[f"latency_{lat}s"] = stats(simulate(signals(events, v, bars, uni, lat, clf_cache=llm), bars, uni, bcfg, lat, cost_bp))
        res["variants"][v] = out
    rnd = [stats(simulate(signals(events, "random", bars, uni, latency, seed=s), bars, uni, bcfg, latency, cost_bp)) for s in range(20)]
    res["variants"]["random_x20"] = {k: round(st.mean(r[k] for r in rnd), 2) for k in ("trades", "win_rate", "avg_ret_pct", "total_pct", "max_dd_pct")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "backtest.json").write_text(json.dumps(res, indent=1))
    return res

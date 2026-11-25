"""Event study on collected events, plus a backfill on historical SEC 8-Ks.

  python -m newsperp study            # logged events -> forward returns after OUR first-seen time
  python -m newsperp study --sec      # 8-K backfill: how much move is left after a 0/1/5-min delay

Forward returns come from Kraken 1-minute perp candles (close of the bar that
contains t). Results go to reports/newsperp/.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
import time
from pathlib import Path

import requests

from . import market
from .collector import CFG, ROOT, STATE, load_json
from .sources import SEC_UA

OUT = ROOT / "reports" / "newsperp"
HORIZONS = (1, 5, 15, 60, 120)

def _ts(s): return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())

class Prices:
    """Caches 1m candles per perp in 12h chunks."""
    def __init__(self): self.c = {}
    def px(self, perp, t):
        chunk = t // 43200
        key = (perp, chunk)
        if key not in self.c:
            cs = market.candles(perp, chunk * 43200 - 60, chunk * 43200 + 43200 + 60)
            self.c[key] = {c["time"] // 1000: float(c["close"]) for c in cs}
            time.sleep(0.2)
        d = self.c[key]
        m = (t // 60) * 60
        for back in range(0, 6):                 # nearest bar at or before t (bars missing when no trades)
            if m - 60 * back in d: return d[m - 60 * back]
        return None

def _summ(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return None
    return dict(n=len(xs), mean=round(st.mean(xs), 3), median=round(st.median(xs), 3),
                hit=round(sum(x > 0 for x in xs) / len(xs), 2))

def study_events():
    uni = load_json(CFG / "sources.json", {})["universe"]
    P = Prices(); rows = []
    now = time.time()
    for f in sorted((STATE / "events").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            ev = json.loads(line); t0 = _ts(ev["first_seen"])
            impacts = (ev.get("signal") or {}).get("impacts") or [
                dict(symbol=s, direction="none") for s in ev.get("tickers", []) if uni.get(s, {}).get("perp")]
            for imp in impacts:
                perp = uni.get(imp["symbol"], {}).get("perp")
                if not perp: continue
                base = P.px(perp, t0)
                if not base: continue
                sign = {"long": 1, "short": -1}.get(imp["direction"], 0)
                r = dict(key=ev["key"], source=ev["source"], event_type=ev.get("event_type"), symbol=imp["symbol"],
                         direction=imp["direction"], confidence=imp.get("confidence"), lag_s=ev.get("lag_s"))
                for h in HORIZONS:
                    if t0 + h * 60 > now: continue
                    p = P.px(perp, t0 + h * 60)
                    if p: r[f"ret_{h}m"] = round((p / base - 1) * 100, 3); r[f"signed_{h}m"] = round(sign * r[f"ret_{h}m"], 3) if sign else None
                if ev.get("published") and ev.get("lag_s") and ev["lag_s"] > 0:
                    p0 = P.px(perp, _ts(ev["published"]))
                    if p0: r["move_before_seen"] = round((base / p0 - 1) * 100, 3)
                rows.append(r)
    groups = {}
    for r in rows:
        for g in (("all",), ("source", r["source"]), ("event_type", r["event_type"]), ("direction", r["direction"])):
            groups.setdefault(":".join(map(str, g)), []).append(r)
    summary = {g: {f"{k}_{h}m": _summ([x.get(f"{k}_{h}m") for x in rs]) for h in HORIZONS for k in ("signed", "ret")}
               | {"abs_60m": _summ([abs(x["ret_60m"]) for x in rs if x.get("ret_60m") is not None])}
               for g, rs in groups.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "events_study.json").write_text(json.dumps(dict(generated=dt.datetime.now(dt.timezone.utc).isoformat(),
                                                           n_rows=len(rows), summary=summary, rows=rows), indent=1))
    return summary, len(rows)

def study_sec(since="2026-02-07"):
    """Historical 8-Ks for every perp stock: absolute move left after a delay."""
    uni = load_json(CFG / "sources.json", {})["universe"]
    P = Prices(); rows = []
    for sym, u in uni.items():
        if not (u.get("perp") and u.get("cik")): continue
        f = requests.get(f"https://data.sec.gov/submissions/CIK{u['cik']:010d}.json", headers={"User-Agent": SEC_UA},
                         timeout=20).json()["filings"]["recent"]
        time.sleep(0.3)
        for i in range(len(f["form"])):
            if f["form"][i] != "8-K" or f["filingDate"][i] < since: continue
            t = _ts(f["acceptanceDateTime"][i].replace(".000Z", "Z"))
            px = {d: P.px(u["perp"], t + d * 60) for d in (-5, 0, 1, 5, 30, 120)}
            if not px[-5] or not px[120]: continue
            mv = lambda a, b: round((px[b] / px[a] - 1) * 100, 3) if px[a] and px[b] else None
            rows.append(dict(symbol=sym, accepted=f["acceptanceDateTime"][i], items=f["items"][i],
                             earnings="2.02" in f["items"][i], pre=mv(-5, 0), d0_120=mv(0, 120), d1_120=mv(1, 120),
                             d5_120=mv(5, 120), d0_30=mv(0, 30), d5_30=mv(5, 30), first5=mv(0, 5)))
    def agg(rs):
        return {k: _summ([abs(r[k]) for r in rs if r.get(k) is not None]) for k in ("pre", "first5", "d0_30", "d5_30", "d0_120", "d1_120", "d5_120")} \
            | {"same_sign_first5_vs_5_120": round(st.mean([(r["first5"] > 0) == (r["d5_120"] > 0) for r in rs
                                                            if r.get("first5") and r.get("d5_120")] or [0]), 2), "n": len(rs)}
    summary = dict(all=agg(rows), earnings=agg([r for r in rows if r["earnings"]]),
                   other=agg([r for r in rows if not r["earnings"]]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sec_backfill.json").write_text(json.dumps(dict(generated=dt.datetime.now(dt.timezone.utc).isoformat(),
                                                           summary=summary, rows=rows), indent=1))
    return summary

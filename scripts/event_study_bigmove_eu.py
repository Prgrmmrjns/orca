#!/usr/bin/env python3
"""EU event study for bigmove rules — last ~2y of >10% single-day moves (pre-Nov 2026).

For each liquid EU name, find |day return| >= 10%, classify news category via
headline heuristics + Yahoo news when available, then measure next 1/3/5-day
returns for the corresponding US-style fade side after 0.5%/fill round-trip.

Only pre-2026-11-01 data is used for rule selection (thresholds may be retuned
on that window only). Writes logs/bigmove_eu_event_study.json + .md.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from stocks.eu_news import classify_eu_news  # noqa: E402
from stocks.eu_universe import EU_UNIVERSE_SEED  # noqa: E402
from stocks.market import quote_to_eur_divisor  # noqa: E402
from stocks.paths import json_safe  # noqa: E402

COST = 0.005  # per fill
RT_COST = 2 * COST
HOLD_DAYS = 5
MIN_MOVE = 0.10
STUDY_START = date(2024, 11, 1)
STUDY_END = date(2026, 10, 31)  # freeze before Nov 2026
MIN_PRICE_EUR = 5.0
MIN_ADV_EUR = 10_000_000.0

# Candidate rules to validate (US frozen → EU). side relative to the event day move.
RULES = [
    {
        "name": "offering_gapdown_short",
        "categories": {"offering"},
        "side": "short",
        "min_gap": 0.04,
        "gap_dir": "down",
        "regime": "any",
    },
    {
        "name": "pr_pop_fade_short",
        "categories": {"other_8k", "contract", "earnings_nosurprise"},
        "side": "short",
        "min_gap": 0.04,
        "gap_dir": "up",
        "regime": "index_above",
    },
    {
        "name": "mna_gapdown_short",
        "categories": {"mna"},
        "side": "short",
        "min_gap": 0.04,
        "gap_dir": "down",
        "regime": "any",
    },
    {
        "name": "analyst_gapdown_fade_long",
        "categories": {"analyst"},
        "side": "long",
        "min_gap": 0.08,
        "gap_dir": "down",
        "regime": "any",
    },
    {
        "name": "capitulation_fade_long",
        "categories": {"no_news_found"},
        "side": "long",
        "min_gap": 0.10,
        "gap_dir": "down",
        "regime": "index_below",
    },
]


def _index_above_series():
    """Daily STOXX/DAX above-50d-MA boolean series keyed by date."""
    for sym in ("^STOXX", "^GDAXI"):
        try:
            h = yf.Ticker(sym).history(start="2024-01-01", end="2026-11-15", auto_adjust=True)
            if h is None or len(h) < 60:
                continue
            ma = h["Close"].rolling(50).mean()
            out = {}
            for ts, px in h["Close"].items():
                d = ts.date() if hasattr(ts, "date") else ts
                m = ma.loc[ts]
                if m == m and px == px:
                    out[d] = bool(px > m)
            if out:
                return out, sym
        except Exception:
            continue
    return {}, None


def _yahoo_title(ticker: str, day: date) -> str:
    """Best-effort headline near event day from Yahoo news."""
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        return ""
    best = ""
    best_dt = None
    for it in items:
        # yfinance news schema varies
        title = it.get("title") or (it.get("content") or {}).get("title") or ""
        pub = it.get("providerPublishTime") or it.get("pubDate")
        if not title:
            continue
        try:
            if isinstance(pub, (int, float)):
                dt = datetime.utcfromtimestamp(int(pub)).date()
            else:
                dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00")).date()
        except Exception:
            dt = day
        if abs((dt - day).days) <= 2:
            if best_dt is None or abs((dt - day).days) < abs((best_dt - day).days):
                best, best_dt = title, dt
    return best


def classify_event(ticker: str, day: date, move: float) -> str:
    title = _yahoo_title(ticker, day)
    if not title:
        return "no_news_found"
    tags = classify_eu_news(title, "")
    cat = tags.get("eu_category") or "other_8k"
    if cat == "earnings_chase":
        return "earnings_chase"
    return cat


def fwd_return(closes, i: int, n: int, side: str) -> float | None:
    if i + n >= len(closes):
        return None
    entry = float(closes.iloc[i])  # enter at event close (conservative vs open)
    exit_px = float(closes.iloc[i + n])
    if entry <= 0 or exit_px <= 0:
        return None
    if side == "long":
        raw = exit_px / entry - 1.0
    else:
        raw = entry / exit_px - 1.0
    return raw - RT_COST


def main() -> int:
    print(f"EU event study {STUDY_START}..{STUDY_END} universe={len(EU_UNIVERSE_SEED)}", flush=True)
    index_above, index_sym = _index_above_series()
    print(f"regime index={index_sym} days={len(index_above)}", flush=True)

    events = []
    for i, ticker in enumerate(EU_UNIVERSE_SEED):
        try:
            h = yf.Ticker(ticker).history(
                start=(STUDY_START - timedelta(days=40)).isoformat(),
                end=(STUDY_END + timedelta(days=20)).isoformat(),
                auto_adjust=True,
            )
        except Exception as e:
            print(f"  skip {ticker}: {e}", flush=True)
            continue
        if h is None or h.empty or len(h) < 30:
            continue
        try:
            div = float(quote_to_eur_divisor(ticker) or 1.0)
        except Exception:
            div = 1.0
        closes = h["Close"].dropna()
        vols = h["Volume"] if "Volume" in h.columns else None
        for j in range(21, len(closes) - HOLD_DAYS - 1):
            ts = closes.index[j]
            d = ts.date() if hasattr(ts, "date") else ts
            if d < STUDY_START or d > STUDY_END:
                continue
            prev = float(closes.iloc[j - 1])
            px = float(closes.iloc[j])
            if prev <= 0:
                continue
            move = px / prev - 1.0
            if abs(move) < MIN_MOVE:
                continue
            prev_eur = prev / div
            if prev_eur < MIN_PRICE_EUR:
                continue
            # ADV filter
            if vols is not None:
                window = h.iloc[max(0, j - 20) : j]
                dv = (window["Close"] * window["Volume"]).dropna()
                adv = float(dv.mean()) / div if len(dv) >= 5 else None
                if adv is None or adv < MIN_ADV_EUR:
                    continue
            else:
                adv = None
            if abs(move) < MIN_MOVE:
                continue
            events.append(
                {
                    "ticker": ticker,
                    "day": d.isoformat(),
                    "move": round(move * 100, 3),
                    "prev": prev,
                    "px": px,
                    "adv_eur": adv,
                    "i": j,
                    "closes_key": ticker,
                }
            )
        if (i + 1) % 20 == 0:
            print(f"  scanned {i+1}/{len(EU_UNIVERSE_SEED)} events_so_far={len(events)}", flush=True)
        time.sleep(0.03)

    print(f"raw >10% events: {len(events)}", flush=True)

    # Attach classification + forward returns (cache histories)
    hist_cache: dict[str, any] = {}
    classified = []
    for k, ev in enumerate(events):
        ticker = ev["ticker"]
        if ticker not in hist_cache:
            hist_cache[ticker] = yf.Ticker(ticker).history(
                start=(STUDY_START - timedelta(days=40)).isoformat(),
                end=(STUDY_END + timedelta(days=20)).isoformat(),
                auto_adjust=True,
            )
        h = hist_cache[ticker]
        closes = h["Close"].dropna()
        # re-find index
        day = date.fromisoformat(ev["day"])
        idx = None
        for j, ts in enumerate(closes.index):
            d = ts.date() if hasattr(ts, "date") else ts
            if d == day:
                idx = j
                break
        if idx is None:
            continue
        cat = classify_event(ticker, day, ev["move"] / 100.0)
        if cat == "earnings_chase":
            continue
        reg = index_above.get(day)
        row = dict(ev)
        row["category"] = cat
        row["index_above"] = reg
        for n in (1, 3, 5):
            for side in ("long", "short"):
                row[f"ret_{side}_{n}d"] = fwd_return(closes, idx, n, side)
        classified.append(row)
        if (k + 1) % 25 == 0:
            print(f"  classified {k+1}/{len(events)}", flush=True)
        time.sleep(0.02)

    print(f"classified events (excl earnings_chase): {len(classified)}", flush=True)

    # Per-category stats (5d fade: short after up-move, long after down-move as baseline)
    by_cat = defaultdict(list)
    for r in classified:
        by_cat[r["category"]].append(r)

    cat_stats = {}
    for cat, rows in sorted(by_cat.items()):
        # Natural fade: fade the move (short if up, long if down)
        rets = []
        for r in rows:
            side = "short" if r["move"] > 0 else "long"
            v = r.get(f"ret_{side}_5d")
            if v is not None:
                rets.append(v)
        cat_stats[cat] = {
            "n": len(rows),
            "n_with_fwd": len(rets),
            "mean_5d_fade_net": round(sum(rets) / len(rets), 5) if rets else None,
            "hit_5d": round(sum(1 for x in rets if x > 0) / len(rets), 3) if rets else None,
            "mean_move": round(sum(r["move"] for r in rows) / len(rows), 3),
        }

    # Rule validation
    rule_results = []
    kept = []
    # Optional threshold retune on pre-Nov data only (try a few gap floors)
    gap_grid = {
        "offering_gapdown_short": [0.04, 0.06, 0.08],
        "pr_pop_fade_short": [0.04, 0.06, 0.08],
        "mna_gapdown_short": [0.04, 0.06],
        "analyst_gapdown_fade_long": [0.08, 0.10],
        "capitulation_fade_long": [0.10, 0.12],
    }
    for rule in RULES:
        best = None
        for min_gap in gap_grid.get(rule["name"], [rule["min_gap"]]):
            matched = []
            for r in classified:
                if r["category"] not in rule["categories"]:
                    continue
                move = r["move"] / 100.0
                if rule["gap_dir"] == "down" and move > -min_gap:
                    continue
                if rule["gap_dir"] == "up" and move < min_gap:
                    continue
                if rule["regime"] == "index_above" and r.get("index_above") is not True:
                    continue
                if rule["regime"] == "index_below" and r.get("index_above") is not False:
                    continue
                v = r.get(f"ret_{rule['side']}_5d")
                if v is None:
                    continue
                matched.append(v)
            if not matched:
                continue
            mean = sum(matched) / len(matched)
            hit = sum(1 for x in matched if x > 0) / len(matched)
            cand = {
                "name": rule["name"],
                "min_gap_pct": round(min_gap * 100, 1),
                "side": rule["side"],
                "categories": sorted(rule["categories"]),
                "regime": rule["regime"],
                "n": len(matched),
                "mean_5d_net": round(mean, 5),
                "hit_rate": round(hit, 3),
                "profitable": mean > 0 and len(matched) >= 3,
            }
            if best is None or (
                cand["profitable"]
                and (
                    not best["profitable"]
                    or cand["mean_5d_net"] > best["mean_5d_net"]
                    or (cand["mean_5d_net"] == best["mean_5d_net"] and cand["n"] > best["n"])
                )
            ):
                best = cand
            elif best is None or (not best["profitable"] and cand["mean_5d_net"] > best["mean_5d_net"]):
                best = cand
        if best is None:
            best = {
                "name": rule["name"],
                "min_gap_pct": round(rule["min_gap"] * 100, 1),
                "side": rule["side"],
                "categories": sorted(rule["categories"]),
                "regime": rule["regime"],
                "n": 0,
                "mean_5d_net": None,
                "hit_rate": None,
                "profitable": False,
            }
        rule_results.append(best)
        if best.get("profitable"):
            kept.append(best)

    out = {
        "study_start": STUDY_START.isoformat(),
        "study_end": STUDY_END.isoformat(),
        "cost_per_fill": COST,
        "hold_days": HOLD_DAYS,
        "min_move": MIN_MOVE,
        "index": index_sym,
        "n_events": len(classified),
        "category_stats": cat_stats,
        "rule_results": rule_results,
        "kept_rules": [k["name"] for k in kept],
        "kept_detail": kept,
        "events_sample": classified[:40],
    }
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "bigmove_eu_event_study.json").write_text(
        json.dumps(json_safe(out), indent=2, default=str)
    )

    lines = [
        "# bigmove_eu_v1 event study (pre-Nov 2026)",
        "",
        f"Window: **{STUDY_START} → {STUDY_END}** · |move|≥10% · cost 0.5%/fill RT · hold 5d",
        f"Index regime: `{index_sym}` vs 50d MA · events classified: **{len(classified)}**",
        "",
        "## Category fade stats (fade the move, 5d net)",
        "",
        "| Category | N | mean 5d net | hit |",
        "|---|---:|---:|---:|",
    ]
    for cat, st in sorted(cat_stats.items()):
        lines.append(
            f"| {cat} | {st['n']} | {st['mean_5d_fade_net']} | {st['hit_5d']} |"
        )
    lines += ["", "## Rule validation (retuned gaps on pre-Nov only)", ""]
    lines += [
        "| Rule | min gap | N | mean 5d net | hit | keep? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in rule_results:
        keep = "YES" if r.get("profitable") else "no"
        lines.append(
            f"| {r['name']} | {r['min_gap_pct']}% | {r['n']} | {r['mean_5d_net']} | {r['hit_rate']} | {keep} |"
        )
    lines += ["", f"**Kept:** {', '.join(k['name'] for k in kept) or '(none)'}", ""]
    (logs / "bigmove_eu_event_study.md").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwrote logs/bigmove_eu_event_study.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

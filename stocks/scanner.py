#!/usr/bin/env python3
"""Scan Nasdaq earnings calendar + yfinance + EDGAR 8-K → state/stocks/candidates.json."""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import START_DATE
from stocks.books import book_rules_public
from stocks.market import (
    fetch_guidance,
    load_cik_map,
    nasdaq_earnings,
    now_et,
    price_snapshot,
    spy_above_ma50,
    utc_now_iso,
    yf_earnings_surprise,
)
from stocks.paths import CANDIDATES, ensure_dirs, read_json, write_json
from stocks.rules import suggested_hold, suggested_side, tag_candidate


def _merge_surprise(nas: dict, yf: dict) -> float | None:
    for src in (nas, yf):
        s = src.get("surprise_pct")
        if s is not None:
            return float(s)
    # compute
    for src in (yf, nas):
        est, act = src.get("eps_estimate"), src.get("eps_reported")
        if est is not None and act is not None and float(est) != 0:
            return (float(act) - float(est)) / abs(float(est)) * 100
    return None


def scan(days_back: int = 1, days_forward: int = 1, max_tickers: int | None = None) -> dict:
    ensure_dirs()
    today = now_et().date()
    spy_above = spy_above_ma50()
    cik_map = load_cik_map()

    calendar_rows: list[dict] = []
    calendar_errors: list[str] = []
    for i in range(-days_back, days_forward + 1):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            rows = nasdaq_earnings(d)
            calendar_rows.extend(rows)
            print(f"nasdaq {d}: {len(rows)} rows", flush=True)
        except Exception as e:
            calendar_errors.append(f"{d}: {e}")
            print(f"nasdaq calendar {d}: {e}", flush=True)

    prev_file = read_json(CANDIDATES, default={})
    prev_by = {c["ticker"]: c for c in (prev_file.get("candidates") or []) if c.get("ticker")}

    # de-dupe by ticker, prefer today's row
    by_ticker: dict[str, dict] = {}
    for row in calendar_rows:
        t = row["ticker"]
        prev = by_ticker.get(t)
        if prev is None or row["cal_date"] == today.isoformat():
            by_ticker[t] = row

    # If every calendar fetch failed, keep prior candidates (do not wipe the book).
    if not by_ticker and prev_by:
        print(
            f"nasdaq empty ({len(calendar_errors)} errors) — retaining "
            f"{len(prev_by)} prior candidates",
            flush=True,
        )
        retained = dict(prev_file)
        retained["as_of"] = utc_now_iso()
        retained["as_of_et"] = now_et().strftime("%Y-%m-%d %H:%M ET")
        retained["spy_above_ma50"] = spy_above
        retained["scan_note"] = (
            "calendar fetch failed; retained prior candidates. errors="
            + "; ".join(calendar_errors[:10])
        )
        write_json(CANDIDATES, retained)
        return retained

    tickers = sorted(by_ticker.keys())
    if max_tickers:
        tickers = tickers[:max_tickers]

    # Pass 1: surprise + gap. Prefer Nasdaq fields; yfinance only when surprise missing.
    prelim: list[dict] = []
    errors = []

    def enrich(ticker: str) -> dict:
        nas = by_ticker[ticker]
        surprise = nas.get("surprise_pct")
        yf = {}
        if surprise is None and nas.get("eps_reported") is None:
            # upcoming / not yet reported — skip heavy yfinance earnings call
            pass
        elif surprise is None:
            yf = yf_earnings_surprise(ticker)
            surprise = _merge_surprise(nas, yf)
        px = price_snapshot(ticker)
        report_date = yf.get("earnings_date") or nas.get("cal_date")
        hour = yf.get("hour_et")
        timing = None
        if hour is not None:
            if hour < 10:
                timing = "BMO"
            elif hour >= 16:
                timing = "AMC"
            else:
                timing = "during"
        tfield = (nas.get("time") or "").lower()
        if timing is None:
            if "pre" in tfield:
                timing = "BMO"
            elif "after" in tfield:
                timing = "AMC"
        return {
            "ticker": ticker,
            "name": nas.get("name") or ticker,
            "cal_date": nas.get("cal_date"),
            "report_date": report_date,
            "timing": timing,
            "eps_estimate": yf.get("eps_estimate") if yf.get("eps_estimate") is not None else nas.get("eps_estimate"),
            "eps_reported": yf.get("eps_reported") if yf.get("eps_reported") is not None else nas.get("eps_reported"),
            "surprise_pct": round(surprise, 4) if surprise is not None else None,
            "gap_pct": px.get("gap_pct"),
            "last": px.get("last"),
            "open": px.get("open"),
            "prev_close": px.get("prev_close"),
            "spy_above_ma50": spy_above,
            "scanned_at": utc_now_iso(),
            "_needs_guidance": (
                surprise is not None
                and (surprise > 5 or surprise < -10 or (px.get("gap_pct") or 0) > 5)
            ),
        }

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(enrich, t): t for t in tickers}
        for fut in as_completed(futs):
            done += 1
            try:
                prelim.append(fut.result())
            except Exception as e:
                errors.append({"ticker": futs[fut], "error": f"{type(e).__name__}: {e}"})
            if done % 25 == 0:
                print(f"prices {done}/{len(tickers)}", flush=True)

    def attach_guidance(cand: dict) -> dict:
        ticker = cand["ticker"]
        report_date = cand.get("report_date") or today.isoformat()
        prior = prev_by.get(ticker) or {}
        needs = cand.pop("_needs_guidance", False)
        try:
            if prior.get("report_date") == report_date and prior.get("guidance") not in (None, "unknown", ""):
                guid = {
                    "guidance": prior.get("guidance"),
                    "tone": prior.get("tone"),
                    "headline": prior.get("headline"),
                    "excerpt": prior.get("excerpt"),
                    "filing_url": prior.get("filing_url"),
                    "exhibit_url": prior.get("exhibit_url"),
                    "raise_hits": prior.get("raise_hits"),
                    "cut_hits": prior.get("cut_hits"),
                }
            elif needs:
                guid = fetch_guidance(ticker, report_date, cik_map)
            else:
                guid = {
                    "guidance": "none",
                    "tone": "unknown",
                    "headline": "",
                    "excerpt": "",
                    "filing_url": "",
                    "exhibit_url": "",
                    "raise_hits": "",
                    "cut_hits": "",
                }
            cand.update(
                {
                    "guidance": guid.get("guidance"),
                    "tone": guid.get("tone"),
                    "headline": guid.get("headline") or "",
                    "excerpt": guid.get("excerpt") or "",
                    "raise_hits": guid.get("raise_hits") or "",
                    "cut_hits": guid.get("cut_hits") or "",
                    "filing_url": guid.get("filing_url") or "",
                    "exhibit_url": guid.get("exhibit_url") or "",
                }
            )
        except Exception as e:
            errors.append({"ticker": ticker, "error": f"guidance {type(e).__name__}: {e}"})
            cand.update(
                {
                    "guidance": "unknown",
                    "tone": "unknown",
                    "headline": "",
                    "excerpt": "",
                    "raise_hits": "",
                    "cut_hits": "",
                    "filing_url": "",
                    "exhibit_url": "",
                }
            )
        tags = tag_candidate(cand, spy_above)
        cand["rule_tags"] = tags
        cand["suggested_side"] = suggested_side(tags)
        cand["suggested_hold"] = suggested_hold(tags) if tags else None
        return cand

    # Cap EDGAR pulls per run (rate limits); prioritize largest |surprise| / |gap|
    needy = [c for c in prelim if c.get("_needs_guidance")]
    needy.sort(key=lambda c: -(abs(c.get("surprise_pct") or 0) + abs(c.get("gap_pct") or 0)))
    allow = {c["ticker"] for c in needy[:40]}
    for c in prelim:
        if c.get("_needs_guidance") and c["ticker"] not in allow:
            c["_needs_guidance"] = False

    candidates = []
    needs_n = sum(1 for c in prelim if c.get("_needs_guidance"))
    print(f"guidance fetch for {needs_n}/{len(prelim)} candidates", flush=True)
    # EDGAR is rate-limited; keep workers low
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(attach_guidance, c) for c in prelim]
        done = 0
        for fut in as_completed(futs):
            candidates.append(fut.result())
            done += 1
            if done % 20 == 0:
                print(f"guidance {done}/{len(prelim)}", flush=True)

    # rank: tagged first, then |surprise|
    def rank_key(c):
        tags = c.get("rule_tags") or []
        surprise = abs(c.get("surprise_pct") or 0)
        return (0 if tags else 1, -surprise)

    candidates.sort(key=rank_key)

    out = {
        "mode": "paper",
        "as_of": utc_now_iso(),
        "as_of_et": now_et().strftime("%Y-%m-%d %H:%M ET"),
        "forward_test_start": START_DATE,
        "spy_above_ma50": spy_above,
        "n_calendar": len(calendar_rows),
        "n_candidates": len(candidates),
        "n_tagged": sum(1 for c in candidates if c.get("rule_tags")),
        "errors": errors[:50],
        "rules": book_rules_public(),
        "candidates": candidates,
    }
    write_json(CANDIDATES, out)
    print(
        f"wrote {CANDIDATES} n={len(candidates)} tagged={out['n_tagged']} "
        f"spy_above={spy_above} errors={len(errors)}",
        flush=True,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan US earnings candidates (paper)")
    ap.add_argument("--days-back", type=int, default=1)
    ap.add_argument("--days-forward", type=int, default=1)
    ap.add_argument("--max", type=int, default=None, help="cap tickers (debug)")
    a = ap.parse_args()
    scan(days_back=a.days_back, days_forward=a.days_forward, max_tickers=a.max)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

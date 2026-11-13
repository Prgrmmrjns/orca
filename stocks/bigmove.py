"""bigmove_unified_v1 — extended-hours gap-fade detection (paper only).

Polls SEC EDGAR current filings (8-K / 424B / SC TO), flags stocks moving
>=4% vs prior regular close on >=2x relative volume, classifies news category,
writes candidates + mega-cap alerts. Does NOT chase earnings/guidance gaps.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import feedparser
import requests

from stocks import EDGAR_UA
from stocks.market import (
    adv20_usd,
    extended_hours_quote,
    is_etf_ticker,
    is_weekday,
    market_session,
    now_et,
    spy_above_ma50,
    utc_now_iso,
)
from stocks.news_tagger import extract_tickers, tag_text
from stocks.paths import CANDIDATES, NEWS, STATE, ensure_dirs, read_json, write_json
from stocks.strategy_config import BIGMOVE_BOOK_ID, load_strategy_config

BOOK_ID = BIGMOVE_BOOK_ID
ALERTS_PATH = STATE / "alerts.json"
MIN_GAP_ABS = 4.0
MIN_REL_VOL = 2.0

EDGAR_FORMS = ("8-K", "424B", "SC TO")
EDGAR_ATOM_TMPL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type={form}&company=&dateb=&owner=include&count=40&output=atom"
)

EDGAR_HEADERS = {
    "User-Agent": EDGAR_UA,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/atom+xml, application/xml, text/xml, */*",
}

# Map tagger / filing signals → bigmove rule categories
_CATEGORY_MAP = {
    "dilution": "offering",
    "offering": "offering",
    "big_contract": "contract",
    "contract": "contract",
    "mna_deal": "mna",
    "acquisition_target": "mna",
    "mna": "mna",
    "exec_departure": "other_8k",
    "fraud_probe": "other_8k",
    "lawsuit": "other_8k",
    "other": "other_8k",
    "earnings": "earnings_nosurprise",
    "earnings_beat": "earnings_nosurprise",
    "earnings_miss": "earnings_nosurprise",
    "analyst": "analyst",
    "no_news_found": "no_news_found",
    "guidance_raise": "earnings_chase",  # excluded by rules
    "guidance_cut": "earnings_chase",
}

ANALYST_RE = re.compile(
    r"\b("
    r"analyst|downgrade[sd]?|upgrade[sd]?|price[- ]target|"
    r"initiates? coverage|reiterate[sd]?|overweight|underweight|"
    r"outperform|underperform|sell[- ]rated|buy[- ]rated"
    r")\b",
    re.I,
)
OFFERING_FORM_RE = re.compile(r"\b(424B\d?|F-1|S-1|S-3)\b", re.I)
SC_TO_RE = re.compile(r"\bSC\s*TO\b|\btender offer\b", re.I)
ITEM_502_RE = re.compile(r"Item\s*5\.02", re.I)
NEGATIVE_ANALYST_RE = re.compile(
    r"\b(downgrade[sd]?|cuts? (?:pt|price[- ]target)|underweight|underperform|sell[- ]rated)\b",
    re.I,
)


def _id_for(*parts: str) -> str:
    raw = "|".join(parts).encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _parse_when(entry: dict) -> str | None:
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
    return None


def _fetch_atom(url: str) -> str | None:
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=25)
        if r.status_code == 200 and ("<feed" in r.text[:800].lower() or "atom" in r.text[:200].lower()):
            return r.text
    except Exception:
        pass
    try:
        proc = subprocess.run(
            [
                "curl", "-sS", "-m", "25", "--http1.1", "--compressed",
                "-A", EDGAR_HEADERS["User-Agent"],
                "-H", "Accept: application/atom+xml",
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout and "<feed" in proc.stdout[:800].lower():
            return proc.stdout
    except Exception:
        return None
    return None


def fetch_edgar_form(form: str) -> list[dict]:
    """Fetch current EDGAR Atom feed for a form type (8-K, 424B, SC TO)."""
    url = EDGAR_ATOM_TMPL.format(form=form.replace(" ", "+"))
    text = _fetch_atom(url)
    if not text:
        return []
    out: list[dict] = []
    try:
        parsed = feedparser.parse(text)
        for e in parsed.entries[:50]:
            title = (e.get("title") or "").strip()
            summary = (e.get("summary") or "")
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:500]
            link = (e.get("link") or "").strip()
            company = ""
            m = re.search(
                rf"{re.escape(form)}\s*[-–]\s*(.+?)(?:\s*\(\d{{10}}\))",
                title,
                re.I,
            )
            if not m:
                m = re.search(r"[-–]\s*(.+?)(?:\s*\(\d{10}\))", title)
            if m:
                company = m.group(1).strip()
            tags = tag_text(title, summary)
            # Form / item boosts
            if OFFERING_FORM_RE.search(title) or form.upper().startswith("424"):
                tags = {
                    "event_type": "offering",
                    "direction": "bearish",
                    "news_side": "short",
                    "actionable": True,
                }
            elif SC_TO_RE.search(title) or form.upper().startswith("SC"):
                tags = {
                    "event_type": "mna",
                    "direction": "bullish",
                    "news_side": "long",
                    "actionable": True,
                }
            elif ITEM_502_RE.search(summary) and tags.get("event_type") in ("other", "other_8k"):
                tags = tag_text(title + " officer resigns", summary)
            elif ANALYST_RE.search(f"{title} {summary}"):
                tags = {
                    "event_type": "analyst",
                    "direction": "neutral",
                    "news_side": None,
                    "actionable": True,
                }
            tickers = extract_tickers(title, summary, company)
            out.append(
                {
                    "id": _id_for("edgar", form, link, title),
                    "source": f"edgar_{form.lower().replace(' ', '_')}",
                    "form": form,
                    "title": title[:300],
                    "summary": summary,
                    "url": link,
                    "published_at": _parse_when(e) or utc_now_iso(),
                    "tickers": tickers,
                    "company": company,
                    **tags,
                    "scanned_at": utc_now_iso(),
                }
            )
    except Exception as exc:
        print(f"edgar {form} parse error: {exc}", flush=True)
    return out


def fetch_edgar_filings() -> list[dict]:
    """Poll 8-K / 424B / SC TO current feeds. Respects SEC fair-access via UA + pacing."""
    fresh: list[dict] = []
    for i, form in enumerate(EDGAR_FORMS):
        rows = fetch_edgar_form(form)
        print(f"edgar_{form}: {len(rows)}", flush=True)
        fresh.extend(rows)
        if i < len(EDGAR_FORMS) - 1:
            time.sleep(0.35)  # SEC fair-access pacing
    return fresh


def classify_bigmove_category(
    *,
    event_type: str | None,
    title: str = "",
    summary: str = "",
    form: str = "",
    has_news: bool = True,
) -> str:
    """Map news/filing signal → bigmove rule category. Never chase earnings guidance."""
    if not has_news:
        return "no_news_found"
    blob = f"{title} {summary} {form}"
    if OFFERING_FORM_RE.search(blob) or (form or "").upper().startswith("424"):
        return "offering"
    if SC_TO_RE.search(blob) or (form or "").upper().startswith("SC"):
        return "mna"
    if ANALYST_RE.search(blob):
        return "analyst"
    et = (event_type or "other").lower().strip()
    # Explicitly mark guidance as earnings_chase so rules exclude it
    if et in ("guidance_raise", "guidance_cut"):
        return "earnings_chase"
    return _CATEGORY_MAP.get(et, "other_8k")


def _news_by_ticker(news: dict) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for it in news.get("items") or []:
        for t in it.get("tickers") or []:
            by.setdefault(str(t).upper(), []).append(it)
    return by


def _universe_ok(ticker: str, quote: dict, cfg) -> tuple[bool, float | None, str | None]:
    uni = cfg.universe
    prev = quote.get("prev_close")
    last = quote.get("last")
    if uni.min_prev_close is not None and (prev is None or float(prev) < uni.min_prev_close):
        return False, None, "below_min_price"
    adv = adv20_usd(ticker)
    if uni.min_adv20_usd is not None and (adv is None or adv < uni.min_adv20_usd):
        return False, adv, "below_min_adv"
    if uni.exclude_etfs:
        etf = is_etf_ticker(ticker)
        if etf is True:
            return False, adv, "etf_excluded"
    return True, adv, None


def _is_mega_cap_alert(
    *,
    ticker: str,
    gap_pct: float,
    adv: float | None,
    news_items: list[dict],
    cfg,
) -> dict | None:
    mega = cfg.mega_cap_shock
    if mega is None or mega.action != "alert_only":
        return None
    if adv is None or adv < mega.min_adv_usd:
        return None
    if gap_pct > -abs(mega.min_gap_down_pct):
        return None
    # CEO/exec 8-K Item 5.02 OR >=3 negative analyst actions
    item_502 = False
    neg_analyst = 0
    for it in news_items:
        blob = f"{it.get('title') or ''} {it.get('summary') or ''}"
        if ITEM_502_RE.search(blob) or (it.get("event_type") == "exec_departure"):
            item_502 = True
        if NEGATIVE_ANALYST_RE.search(blob):
            neg_analyst += 1
    if not item_502 and neg_analyst < 3:
        return None
    return {
        "id": _id_for("mega", ticker, utc_now_iso()),
        "type": "mega_cap_shock",
        "ticker": ticker,
        "gap_pct": gap_pct,
        "adv20_usd": adv,
        "item_502": item_502,
        "neg_analyst_count": neg_analyst,
        "action": "alert_only",
        "session": market_session(),
        "as_of": utc_now_iso(),
        "note": mega.trigger,
    }


def detect_movers(
    tickers: list[str],
    *,
    news_by_ticker: dict[str, list[dict]],
    cfg,
    spy_above: bool | None,
) -> tuple[list[dict], list[dict]]:
    """Flag eligible movers and mega-cap alerts. Returns (candidates, alerts)."""
    cands: list[dict] = []
    alerts: list[dict] = []
    seen: set[str] = set()
    for ticker in tickers:
        ticker = str(ticker).upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            quote = extended_hours_quote(ticker)
        except Exception:
            continue
        gap = quote.get("gap_pct")
        last = quote.get("last")
        if gap is None or last is None:
            continue
        if abs(float(gap)) < MIN_GAP_ABS:
            continue
        rel = quote.get("rel_vol")
        # If rel_vol unavailable, still allow when we have a fresh EDGAR/news hit
        has_fresh_news = bool(news_by_ticker.get(ticker))
        if rel is not None and rel < MIN_REL_VOL and not has_fresh_news:
            continue
        if rel is None and not has_fresh_news:
            continue
        ok, adv, reason = _universe_ok(ticker, quote, cfg)
        if not ok:
            continue
        items = news_by_ticker.get(ticker) or []
        mega = _is_mega_cap_alert(
            ticker=ticker, gap_pct=float(gap), adv=adv, news_items=items, cfg=cfg
        )
        if mega:
            alerts.append(mega)
            continue  # alert only — no trade candidate

        if items:
            top = items[0]
            cat = classify_bigmove_category(
                event_type=top.get("event_type"),
                title=top.get("title") or "",
                summary=top.get("summary") or "",
                form=top.get("form") or "",
                has_news=True,
            )
            headline = top.get("title") or ""
            news_id = top.get("id")
            news_source = top.get("source") or "edgar_8k"
            filing_url = top.get("url") or ""
        else:
            cat = "no_news_found"
            headline = f"{ticker} move {gap:+.1f}% no news found"
            news_id = None
            news_source = "move_trigger"
            filing_url = ""

        # Never emit earnings_chase as a tradeable candidate
        if cat == "earnings_chase":
            continue

        cands.append(
            {
                "ticker": ticker,
                "name": ticker,
                "cal_date": now_et().date().isoformat(),
                "gap_pct": float(gap),
                "last": last,
                "prev_close": quote.get("prev_close"),
                "rel_vol": rel,
                "adv20_usd": adv,
                "avg_dollar_volume": adv,
                "spy_above_ma50": spy_above,
                "session": quote.get("session") or market_session(),
                "headline": headline[:200],
                "news_event_type": cat,
                "news_direction": "bearish" if float(gap) < 0 else "bullish",
                "news_side": None,
                "news_actionable": True,
                "news_source": news_source,
                "news_id": news_id,
                "filing_url": filing_url,
                "rule_tags": ["bigmove", cat, BOOK_ID],
                "source": "bigmove_move_trigger",
                "suggested_side": None,
                "suggested_hold": 5,
                "as_of": utc_now_iso(),
            }
        )
    return cands, alerts


def merge_candidates(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Upsert by ticker+source for bigmove rows; keep other books' candidates intact."""
    out: list[dict] = []
    bigmove_tickers = {c.get("ticker") for c in fresh if c.get("ticker")}
    for c in existing:
        # Drop prior bigmove rows for refreshed tickers; keep everything else
        if c.get("source") == "bigmove_move_trigger" and c.get("ticker") in bigmove_tickers:
            continue
        if c.get("source") == "bigmove_move_trigger" and c.get("ticker") not in {
            x.get("ticker") for x in existing
        }:
            # keep stale bigmove rows for tickers not rescanned this tick
            pass
        out.append(c)
    # Remove old bigmove rows for tickers we refreshed
    out = [
        c
        for c in out
        if not (
            c.get("source") == "bigmove_move_trigger"
            and c.get("ticker") in bigmove_tickers
        )
    ]
    out.extend(fresh)
    return out


def merge_alerts(existing: list[dict], fresh: list[dict], *, keep: int = 100) -> list[dict]:
    seen = {a.get("id") for a in existing if a.get("id")}
    out = list(existing)
    for a in fresh:
        if a.get("id") and a["id"] in seen:
            continue
        out.append(a)
        if a.get("id"):
            seen.add(a["id"])
    return out[-keep:]


def merge_news_items(existing: list[dict], fresh: list[dict], *, keep: int = 300) -> list[dict]:
    by_id = {it.get("id"): it for it in existing if it.get("id")}
    for it in fresh:
        iid = it.get("id")
        if iid:
            by_id[iid] = it
        else:
            existing.append(it)
    items = list(by_id.values())
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:keep]


def collect_watchlist(news: dict, filings: list[dict], candidates: dict) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()

    def _add(t: str):
        t = str(t).upper().strip()
        if t and t not in seen and len(t) <= 5:
            seen.add(t)
            tickers.append(t)

    for it in filings:
        for t in it.get("tickers") or []:
            _add(t)
    for it in news.get("items") or []:
        for t in it.get("tickers") or []:
            _add(t)
    for c in candidates.get("candidates") or []:
        if c.get("ticker"):
            _add(c["ticker"])
    return tickers[:80]  # bound yfinance calls per poll


def scan_once() -> dict[str, Any]:
    """One detection cycle: EDGAR poll → movers → candidates + alerts."""
    ensure_dirs()
    cfg = load_strategy_config(BOOK_ID)
    spy_above = spy_above_ma50()
    news = read_json(NEWS, default={"items": []})
    cands_doc = read_json(CANDIDATES, default={"candidates": []})
    alerts_doc = read_json(ALERTS_PATH, default={"alerts": [], "mode": "paper"})

    filings = fetch_edgar_filings()
    # Merge filings into news feed so other books / UI can see them
    news_items = merge_news_items(list(news.get("items") or []), filings)
    news["items"] = news_items
    news["as_of"] = utc_now_iso()
    news["n_items"] = len(news_items)
    news["n_actionable"] = sum(1 for it in news_items if it.get("actionable"))
    sources = list(news.get("sources") or [])
    for s in ("edgar_8k", "edgar_424b", "edgar_sc_to"):
        if s not in sources:
            sources.append(s)
    news["sources"] = sources
    write_json(NEWS, news)

    news_by = _news_by_ticker(news)
    # Also index fresh filings
    for it in filings:
        for t in it.get("tickers") or []:
            news_by.setdefault(str(t).upper(), []).insert(0, it)

    watch = collect_watchlist(news, filings, cands_doc)
    movers, alerts = detect_movers(
        watch, news_by_ticker=news_by, cfg=cfg, spy_above=spy_above
    )
    merged = merge_candidates(list(cands_doc.get("candidates") or []), movers)
    cands_doc["candidates"] = merged
    cands_doc["as_of"] = utc_now_iso()
    cands_doc["spy_above_ma50"] = spy_above
    cands_doc.setdefault("rules", {})
    cands_doc["rules"][BOOK_ID] = cfg.description or (
        "Fade offering/PR/M&A gaps + selective gap-down longs; extended hours; alert-only mega-caps."
    )
    write_json(CANDIDATES, cands_doc)

    alerts_doc["alerts"] = merge_alerts(list(alerts_doc.get("alerts") or []), alerts)
    alerts_doc["as_of"] = utc_now_iso()
    alerts_doc["mode"] = "paper"
    alerts_doc["book_id"] = BOOK_ID
    write_json(ALERTS_PATH, alerts_doc)

    return {
        "as_of": utc_now_iso(),
        "session": market_session(),
        "n_filings": len(filings),
        "n_watch": len(watch),
        "n_movers": len(movers),
        "n_alerts": len(alerts),
        "movers": [m["ticker"] for m in movers],
        "alerts": [a["ticker"] for a in alerts],
    }


def run_loop(
    *,
    duration_minutes: float = 22.0,
    poll_seconds: float = 150.0,
    engine_each_poll: bool = True,
) -> dict[str, Any]:
    """Poll for ~duration_minutes every poll_seconds; optionally run engine each cycle."""
    if not is_weekday() and market_session() == "closed":
        print("weekend/closed — single scan only", flush=True)
        return scan_once()

    started = time.time()
    cycles = []
    while True:
        print(f"bigmove poll session={market_session()} t+{time.time()-started:.0f}s", flush=True)
        try:
            result = scan_once()
            cycles.append(result)
            print(json_dump(result), flush=True)
        except Exception as exc:
            print(f"bigmove scan error: {exc}", flush=True)
            cycles.append({"error": str(exc), "as_of": utc_now_iso()})

        if engine_each_poll:
            try:
                from stocks.engine import run_book

                eng = run_book(BOOK_ID, apply=True)
                print(
                    f"engine[{BOOK_ID}]: fills={len(eng.get('fills') or [])} "
                    f"closed={len(eng.get('closed') or [])} open={eng.get('n_open')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"bigmove engine error: {exc}", flush=True)

        elapsed = time.time() - started
        if elapsed + poll_seconds > duration_minutes * 60:
            break
        # Stop looping deep outside coverage window (before 04:00 ET / after 20:00 ET)
        sess = market_session()
        if sess == "closed" and now_et().hour >= 20:
            break
        time.sleep(poll_seconds)

    return {
        "as_of": utc_now_iso(),
        "cycles": len(cycles),
        "duration_s": round(time.time() - started, 1),
        "last": cycles[-1] if cycles else None,
    }


def json_dump(obj: Any) -> str:
    import json

    from stocks.paths import json_safe

    return json.dumps(json_safe(obj), default=str)


def main() -> int:
    ap = argparse.ArgumentParser(description="bigmove_unified_v1 paper detector")
    ap.add_argument("--once", action="store_true", help="single poll (default)")
    ap.add_argument("--loop", action="store_true", help="poll for ~22 minutes")
    ap.add_argument("--duration-minutes", type=float, default=22.0)
    ap.add_argument("--poll-seconds", type=float, default=150.0)
    ap.add_argument("--no-engine", action="store_true", help="skip engine each poll")
    a = ap.parse_args()
    if a.loop:
        out = run_loop(
            duration_minutes=a.duration_minutes,
            poll_seconds=a.poll_seconds,
            engine_each_poll=not a.no_engine,
        )
    else:
        out = scan_once()
    print(json_dump(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

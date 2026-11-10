#!/usr/bin/env python3
"""24/7 US stock news scanner → state/stocks/news.json (+ candidates merge).

Free sources (no paid keys), GitHub Actions friendly:
  - SEC EDGAR current 8-K Atom feed
  - GlobeNewswire / PR Newswire / Business Wire RSS
  - Yahoo Finance market news RSS

Paper only. Dedupes by id/url, tags event type + direction, keeps a rolling window.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks import EDGAR_UA, START_DATE
from stocks.market import now_et, utc_now_iso
from stocks.news_tagger import extract_tickers, tag_text
from stocks.paths import CANDIDATES, STATE, ensure_dirs, read_json, write_json
from stocks.rules import suggested_hold, suggested_side, tag_candidate

NEWS_PATH = STATE / "news.json"
ROLLING_HOURS = 72
MAX_ITEMS = 400

RSS_FEEDS = [
    {
        "id": "prnewswire",
        "url": "https://www.prnewswire.com/rss/news-releases-list.rss",
    },
    {
        "id": "prnewswire_financial",
        "url": "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
    },
    {
        "id": "cnbc_business",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    },
    {
        "id": "cnbc_finance",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    },
    {
        "id": "marketwatch",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
    {
        "id": "marketwatch_realtime",
        "url": "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/",
    },
]

# SEC current filings Atom (8-K). Requires a descriptive User-Agent with contact email.
EDGAR_ATOM = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom"
)

HEADERS = {
    "User-Agent": EDGAR_UA,
    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*",
}
EDGAR_HEADERS = {
    "User-Agent": "OrcaPaper forward-test jonas@prgrmmrjns.dev",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/atom+xml, application/xml, text/xml, */*",
}


def _id_for(source: str, url: str, title: str) -> str:
    raw = f"{source}|{url}|{title}".encode()
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


def _fetch_url(url: str, timeout: int = 25) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200 and r.text and len(r.text) > 40:
            return r.text
    except Exception:
        return None
    return None


def fetch_rss(source_id: str, url: str) -> list[dict]:
    text = _fetch_url(url)
    if not text:
        return []
    parsed = feedparser.parse(text)
    out = []
    for e in parsed.entries[:60]:
        title = (e.get("title") or "").strip()
        summary = (e.get("summary") or e.get("description") or "")
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()[:500]
        link = (e.get("link") or "").strip()
        if not title:
            continue
        tags = tag_text(title, summary)
        tickers = extract_tickers(title, summary)
        out.append(
            {
                "id": _id_for(source_id, link, title),
                "source": source_id,
                "title": title[:300],
                "summary": summary,
                "url": link,
                "published_at": _parse_when(e) or utc_now_iso(),
                "tickers": tickers,
                **tags,
                "scanned_at": utc_now_iso(),
            }
        )
    return out


def fetch_edgar_8k() -> list[dict]:
    text = None
    try:
        r = requests.get(EDGAR_ATOM, headers=EDGAR_HEADERS, timeout=25)
        if r.status_code == 200 and "atom" in (r.text[:200].lower() + r.headers.get("content-type", "").lower()):
            text = r.text
        elif r.status_code == 200 and "<feed" in r.text[:500].lower():
            text = r.text
        else:
            # curl fallback (SEC sometimes blocks generic clients)
            import subprocess

            proc = subprocess.run(
                [
                    "curl", "-sS", "-m", "25", "--http1.1", "--compressed",
                    "-A", EDGAR_HEADERS["User-Agent"],
                    "-H", "Accept: application/atom+xml",
                    EDGAR_ATOM,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout and "<feed" in proc.stdout[:800].lower():
                text = proc.stdout
    except Exception:
        text = None
    if not text:
        return []
    out = []
    try:
        # feedparser handles Atom well
        parsed = feedparser.parse(text)
        for e in parsed.entries[:50]:
            title = (e.get("title") or "").strip()
            summary = (e.get("summary") or "")
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:500]
            link = (e.get("link") or "").strip()
            # EDGAR titles look like: "8-K - Apple Inc. (0000320193) (Filer)"
            company = ""
            m = re.search(r"8-K\s*[-–]\s*(.+?)(?:\s*\(\d{10}\))", title, re.I)
            if m:
                company = m.group(1).strip()
            tags = tag_text(title, summary)
            # Prefer Item codes in summary for tagging boost
            if re.search(r"Item\s*2\.02", summary, re.I) and tags["event_type"] == "other":
                tags = tag_text(title + " earnings results", summary)
            if re.search(r"Item\s*1\.01", summary, re.I) and tags["event_type"] == "other":
                tags = tag_text(title + " definitive agreement", summary)
            if re.search(r"Item\s*5\.02", summary, re.I) and tags["event_type"] == "other":
                tags = tag_text(title + " officer resigns", summary)
            tickers = extract_tickers(title, summary, company)
            out.append(
                {
                    "id": _id_for("edgar_8k", link, title),
                    "source": "edgar_8k",
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
    except Exception as e:
        print(f"edgar parse error: {e}", flush=True)
    return out


def _retag(it: dict) -> dict:
    """Refresh keyword tags from current title/summary (tagger rules evolve)."""
    tags = tag_text(it.get("title") or "", it.get("summary") or "")
    it.update(tags)
    tickers = extract_tickers(it.get("title") or "", it.get("summary") or "", it.get("company") or "")
    if tickers:
        it["tickers"] = tickers[:4]
    else:
        it["tickers"] = [t for t in (it.get("tickers") or []) if t not in {"US", "UK", "EU"}][:4]
    return it


def merge_items(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by_id = {x["id"]: _retag(x) for x in existing if x.get("id")}
    for it in fresh:
        it = _retag(it)
        prev = by_id.get(it["id"])
        if prev:
            tickers = list(dict.fromkeys((prev.get("tickers") or []) + (it.get("tickers") or [])))
            prev["tickers"] = [t for t in tickers if t not in {"US", "UK", "EU"}][:4]
            prev["scanned_at"] = it.get("scanned_at") or prev.get("scanned_at")
            # Always refresh tags from latest text/rules
            prev.update(
                {
                    "event_type": it.get("event_type"),
                    "direction": it.get("direction"),
                    "news_side": it.get("news_side"),
                    "actionable": it.get("actionable"),
                    "title": it.get("title") or prev.get("title"),
                    "summary": it.get("summary") or prev.get("summary"),
                }
            )
        else:
            by_id[it["id"]] = it
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ROLLING_HOURS)

    def _keep(it: dict) -> bool:
        ts = it.get("published_at") or it.get("scanned_at")
        if not ts:
            return True
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt >= cutoff
        except Exception:
            return True

    items = [x for x in by_id.values() if _keep(x)]
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:MAX_ITEMS]


def news_to_candidate(item: dict, ticker: str) -> dict:
    """Build a candidate-shaped row so rule books / agent can see the news."""
    tags = ["news_event"]
    if item.get("event_type") and item["event_type"] != "other":
        tags.append(item["event_type"])
    if item.get("actionable") and item.get("news_side"):
        tags.append(f"news_{item['news_side']}")
    return {
        "ticker": ticker,
        "name": item.get("company") or ticker,
        "source": "news",
        "cal_date": (item.get("published_at") or "")[:10] or now_et().date().isoformat(),
        "report_date": (item.get("published_at") or "")[:10],
        "timing": "news",
        "surprise_pct": None,
        "gap_pct": None,
        "guidance": (
            "raised"
            if item.get("event_type") == "guidance_raise"
            else "cut"
            if item.get("event_type") == "guidance_cut"
            else "none"
        ),
        "headline": item.get("title"),
        "excerpt": item.get("summary"),
        "filing_url": item.get("url") if item.get("source") == "edgar_8k" else "",
        "exhibit_url": item.get("url") or "",
        "news_id": item.get("id"),
        "news_source": item.get("source"),
        "news_event_type": item.get("event_type"),
        "news_direction": item.get("direction"),
        "news_side": item.get("news_side"),
        "news_actionable": bool(item.get("actionable")),
        "news_published_at": item.get("published_at"),
        "rule_tags": tags,
        "suggested_side": item.get("news_side"),
        "suggested_hold": 2 if item.get("actionable") else None,
        "spy_above_ma50": None,
        "scanned_at": utc_now_iso(),
    }


def merge_into_candidates(news_items: list[dict]) -> int:
    """Upsert actionable (and strong neutral) news rows into candidates.json."""
    cands = read_json(CANDIDATES, default={})
    rows = list(cands.get("candidates") or [])
    by_key = {}
    for r in rows:
        key = (r.get("ticker"), r.get("news_id") or r.get("cal_date"), r.get("source") or "earnings")
        by_key[key] = r
    n = 0
    for it in news_items:
        if not it.get("tickers"):
            continue
        # Prefer actionable; also keep earnings/FDA/M&A neutrals with tickers for the agent
        if not (
            it.get("actionable")
            or it.get("event_type") in {
                "earnings",
                "earnings_beat",
                "mna_deal",
                "lawsuit",
                "exec_departure",
                "fda_approval",
                "guidance_raise",
                "guidance_cut",
            }
        ):
            continue
        for ticker in it["tickers"][:2]:
            row = news_to_candidate(it, ticker)
            # also attach classic earnings tags if applicable
            extra = tag_candidate(row, cands.get("spy_above_ma50"))
            row["rule_tags"] = list(dict.fromkeys((row.get("rule_tags") or []) + extra))
            if not row.get("suggested_side"):
                row["suggested_side"] = suggested_side(row["rule_tags"])
            key = (ticker, it.get("id"), "news")
            if key not in by_key:
                n += 1
            by_key[key] = row
    merged = list(by_key.values())

    def rank(c):
        tags = c.get("rule_tags") or []
        actionable = 0 if c.get("news_actionable") or tags else 1
        return (actionable, -(abs(c.get("surprise_pct") or 0)), c.get("news_published_at") or "")

    merged.sort(key=rank)
    cands["candidates"] = merged[:800]
    cands["n_candidates"] = len(cands["candidates"])
    cands["n_tagged"] = sum(1 for c in cands["candidates"] if c.get("rule_tags"))
    cands["news_merged_at"] = utc_now_iso()
    cands.setdefault("forward_test_start", START_DATE)
    write_json(CANDIDATES, cands)
    return n


def scan() -> dict:
    ensure_dirs()
    prev = read_json(NEWS_PATH, default={"items": []})
    fresh: list[dict] = []
    errors = []

    try:
        edgar = fetch_edgar_8k()
        fresh.extend(edgar)
        print(f"edgar_8k: {len(edgar)}", flush=True)
    except Exception as e:
        errors.append(f"edgar: {e}")
        print(f"edgar error: {e}", flush=True)

    for feed in RSS_FEEDS:
        try:
            rows = fetch_rss(feed["id"], feed["url"])
            fresh.extend(rows)
            print(f"{feed['id']}: {len(rows)}", flush=True)
            time.sleep(0.4)
        except Exception as e:
            errors.append(f"{feed['id']}: {e}")
            print(f"{feed['id']} error: {e}", flush=True)

    items = merge_items(prev.get("items") or [], fresh)
    actionable = [x for x in items if x.get("actionable")]
    with_ticker = [x for x in items if x.get("tickers")]
    out = {
        "mode": "paper",
        "as_of": utc_now_iso(),
        "as_of_et": now_et().strftime("%Y-%m-%d %H:%M ET"),
        "rolling_hours": ROLLING_HOURS,
        "n_items": len(items),
        "n_actionable": len(actionable),
        "n_with_ticker": len(with_ticker),
        "sources": [f["id"] for f in RSS_FEEDS] + ["edgar_8k"],
        "errors": errors[:20],
        "note": (
            "Paper news feed. Keyword tagging is heuristic. Shorts have no borrow model. "
            "RSS/EDGAR can lag minutes–hours."
        ),
        "items": items,
    }
    write_json(NEWS_PATH, out)
    added = merge_into_candidates(items)
    print(
        f"wrote {NEWS_PATH} items={len(items)} actionable={len(actionable)} "
        f"candidates_upserted≈{added} errors={len(errors)}",
        flush=True,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan US stock news (paper)")
    ap.parse_args()
    scan()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

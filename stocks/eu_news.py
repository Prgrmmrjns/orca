"""European regulatory / corporate news ingest for bigmove_eu_v1 (paper only).

EQS News / DGAP no longer expose a free Atom/RSS endpoint (HTML SPA only).
We poll free aggregators that reliably surface MAR Art. 17 ad-hoc, directors'
dealings, Kapitalerhöhung / accelerated bookbuild, WpÜG takeover offers,
Gewinnwarnung, and analyst rating changes:

  - Google News RSS (site:eqs-news.com / dgap.de + keyword queries)
  - wallstreet-online.de Nachrichten RSS
  - finanzen.net news RSS
  - Yahoo Finance per-ticker headline RSS (analyst flashes)

Categories map to the US bigmove set: offering, mna, analyst, earnings_nosurprise,
other_8k/contract, earnings_chase, no_news_found.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

from stocks.eu_universe import EU_UNIVERSE_SEED, match_company_to_ticker, normalize_eu_ticker
from stocks.market import utc_now_iso
from stocks.news_tagger import tag_text

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OrcaPaper/1.0; +https://orca-paper.vercel.app)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Free RSS sources (EQS/DGAP direct RSS is HTML-only as of 2026-11)
GOOGLE_QUERIES = [
    'site:eqs-news.com OR site:dgap.de Ad-hoc OR "Inside Information"',
    "Ad-hoc OR Gewinnwarnung OR Kapitalerhöhung OR WpÜG OR Directors Dealings",
    '"accelerated bookbuild" OR "capital increase" OR "profit warning" Europe',
    "Analyst Downgrade OR Upgrade OR Kursziel Aktie",
]

STATIC_FEEDS = [
    ("wso_nachrichten", "https://www.wallstreet-online.de/rss/nachrichten"),
    ("finanzen_net", "https://www.finanzen.net/rss/news"),
]

OFFERING_RE = re.compile(
    r"\b("
    r"kapitalerhöhung|kapitalerhoehung|capital increase|accelerated bookbuild|"
    r"bookbuild|bezugsrecht|rights issue|placement|secondary offering|"
    r"aktienplatzierung|capital raise|dilution|wandelanleihe|convertible"
    r")\b",
    re.I,
)
MNA_RE = re.compile(
    r"\b("
    r"wpüg|wpug|takeover|übernahme|uebernahme|acquisition|merger|"
    r"pflichtangebot|tender offer|offer document|squeeze[- ]out|delisting"
    r")\b",
    re.I,
)
ANALYST_RE = re.compile(
    r"\b("
    r"analyst|analyse[- ]flash|downgrade|upgrade|kursziel|price[- ]target|"
    r"overweight|underweight|outperform|underperform|einstufung|rating"
    r")\b",
    re.I,
)
EARNINGS_RE = re.compile(
    r"\b("
    r"quartalszahlen|jahreszahlen|earnings|results|gewinnwarnung|profit warning|"
    r"guidance|ausblick|forecast|umsatzwarnung|profit warning"
    r")\b",
    re.I,
)
GUIDANCE_RE = re.compile(
    r"\b("
    r"guidance|ausblick angehoben|ausblick gesenkt|raises outlook|cuts outlook|"
    r"anhebung der prognose|senkung der prognose|guidance raise|guidance cut"
    r")\b",
    re.I,
)
CONTRACT_RE = re.compile(
    r"\b("
    r"großauftrag|grossauftrag|major contract|auftrag|contract win|rahmenvertrag|"
    r"supply agreement|bestellung"
    r")\b",
    re.I,
)
DIRECTORS_RE = re.compile(
    r"\b("
    r"directors['’]? dealings|directors dealings|managers['’]? transactions|"
    r"eigene geschäfte|directors dealing|pdmr"
    r")\b",
    re.I,
)
ADHOC_RE = re.compile(r"\b(ad[- ]?hoc|inside information|art\.?\s*17|MAR)\b", re.I)


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


def classify_eu_news(title: str, summary: str = "") -> dict[str, Any]:
    """Map European headline → bigmove category + direction tags."""
    blob = f"{title} {summary}"
    if GUIDANCE_RE.search(blob) and not OFFERING_RE.search(blob):
        # Guidance changes are earnings_chase (excluded from trading rules)
        side = "long" if re.search(r"anheben|raises?|erhöht|erhoeht|lift", blob, re.I) else "short"
        return {
            "event_type": "guidance_raise" if side == "long" else "guidance_cut",
            "direction": "bullish" if side == "long" else "bearish",
            "news_side": side,
            "actionable": False,
            "eu_category": "earnings_chase",
        }
    if OFFERING_RE.search(blob):
        return {
            "event_type": "offering",
            "direction": "bearish",
            "news_side": "short",
            "actionable": True,
            "eu_category": "offering",
        }
    if MNA_RE.search(blob):
        return {
            "event_type": "mna",
            "direction": "bullish",
            "news_side": "long",
            "actionable": True,
            "eu_category": "mna",
        }
    if ANALYST_RE.search(blob):
        bear = bool(re.search(r"downgrade|senkt|unter|sell|underweight|underperform", blob, re.I))
        return {
            "event_type": "analyst",
            "direction": "bearish" if bear else "neutral",
            "news_side": None,
            "actionable": True,
            "eu_category": "analyst",
        }
    if CONTRACT_RE.search(blob):
        return {
            "event_type": "contract",
            "direction": "bullish",
            "news_side": "long",
            "actionable": True,
            "eu_category": "contract",
        }
    if EARNINGS_RE.search(blob):
        # Gewinnwarnung → treat as other (PR) unless pure guidance chase already caught
        if re.search(r"gewinnwarnung|profit warning|umsatzwarnung", blob, re.I):
            return {
                "event_type": "other",
                "direction": "bearish",
                "news_side": "short",
                "actionable": True,
                "eu_category": "other_8k",
            }
        return {
            "event_type": "earnings",
            "direction": "neutral",
            "news_side": None,
            "actionable": True,
            "eu_category": "earnings_nosurprise",
        }
    if DIRECTORS_RE.search(blob) or ADHOC_RE.search(blob):
        tags = tag_text(title, summary)
        et = tags.get("event_type") or "other"
        cat = {
            "dilution": "offering",
            "offering": "offering",
            "mna_deal": "mna",
            "acquisition_target": "mna",
            "big_contract": "contract",
            "analyst": "analyst",
            "guidance_raise": "earnings_chase",
            "guidance_cut": "earnings_chase",
        }.get(et, "other_8k")
        return {
            **tags,
            "eu_category": cat,
            "actionable": cat != "earnings_chase",
        }
    tags = tag_text(title, summary)
    et = tags.get("event_type") or "other"
    cat = {
        "dilution": "offering",
        "offering": "offering",
        "mna_deal": "mna",
        "acquisition_target": "mna",
        "big_contract": "contract",
        "analyst": "analyst",
        "earnings": "earnings_nosurprise",
        "earnings_beat": "earnings_nosurprise",
        "earnings_miss": "earnings_nosurprise",
        "guidance_raise": "earnings_chase",
        "guidance_cut": "earnings_chase",
    }.get(et, "other_8k")
    return {**tags, "eu_category": cat, "actionable": cat != "earnings_chase"}


def _fetch_feed(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or len(r.content) < 50:
            return []
        # Skip HTML SPA shells (EQS/DGAP)
        head = r.content[:200].lower()
        if b"<!doctype html" in head or b"<html" in head:
            return []
        parsed = feedparser.parse(r.content)
        return list(parsed.entries or [])
    except Exception:
        return []


def _entry_to_item(entry: dict, source: str) -> dict | None:
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    summary = entry.get("summary") or entry.get("description") or ""
    summary = re.sub(r"<[^>]+>", " ", str(summary))
    summary = re.sub(r"\s+", " ", summary).strip()[:500]
    link = (entry.get("link") or "").strip()
    tags = classify_eu_news(title, summary)
    tickers = match_company_to_ticker(f"{title} {summary}")
    # Also pull explicit symbols
    for raw in re.findall(r"\b([A-Z0-9]{1,5}\.(?:DE|PA|AS|MI|MC|SW|L))\b", f"{title} {summary}".upper()):
        nt = normalize_eu_ticker(raw)
        if nt and nt not in tickers:
            tickers.append(nt)
    return {
        "id": _id_for("eu", source, link, title),
        "source": source,
        "title": title[:300],
        "summary": summary,
        "url": link,
        "published_at": _parse_when(entry) or utc_now_iso(),
        "tickers": tickers,
        "form": "EU_ADHOC" if ADHOC_RE.search(title) else "EU_NEWS",
        "event_type": tags.get("event_type") or "other",
        "direction": tags.get("direction"),
        "news_side": tags.get("news_side"),
        "actionable": bool(tags.get("actionable")),
        "eu_category": tags.get("eu_category") or "other_8k",
        "scanned_at": utc_now_iso(),
        "region": "EU",
    }


def fetch_google_eu_news() -> list[dict]:
    out: list[dict] = []
    for q in GOOGLE_QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(q)
            + "&hl=de&gl=DE&ceid=DE:de"
        )
        for e in _fetch_feed(url)[:40]:
            item = _entry_to_item(e, "eqs_google")
            if item:
                out.append(item)
        time.sleep(0.2)
    return out


def fetch_static_eu_feeds() -> list[dict]:
    out: list[dict] = []
    for source, url in STATIC_FEEDS:
        for e in _fetch_feed(url)[:40]:
            item = _entry_to_item(e, source)
            if item:
                out.append(item)
        time.sleep(0.15)
    return out


def fetch_yahoo_eu_headlines(tickers: list[str] | None = None, limit: int = 25) -> list[dict]:
    """Per-ticker Yahoo RSS — useful for Analyse-Flash / rating changes."""
    tickers = tickers or EU_UNIVERSE_SEED[:limit]
    out: list[dict] = []
    for t in tickers[:limit]:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=DE&lang=de-DE"
        for e in _fetch_feed(url)[:8]:
            item = _entry_to_item(e, "yahoo_eu")
            if item:
                if t not in (item.get("tickers") or []):
                    item["tickers"] = [t] + list(item.get("tickers") or [])
                out.append(item)
        time.sleep(0.05)
    return out


def fetch_eu_news(*, yahoo_tickers: list[str] | None = None, yahoo_limit: int = 20) -> list[dict]:
    """Aggregate free EU news sources into tagged items."""
    fresh: list[dict] = []
    fresh.extend(fetch_google_eu_news())
    fresh.extend(fetch_static_eu_feeds())
    fresh.extend(fetch_yahoo_eu_headlines(yahoo_tickers, limit=yahoo_limit))
    # Dedup by id
    by_id: dict[str, dict] = {}
    for it in fresh:
        iid = it.get("id")
        if iid:
            by_id[iid] = it
    items = list(by_id.values())
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items

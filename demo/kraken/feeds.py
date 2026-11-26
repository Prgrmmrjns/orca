"""News scrapers. Fast sources are polled every minute, the rest every 5 minutes. Public pages/feeds only.

Fastest free sources measured in research (REPORT section 14): Binance CMS API for listings (t=0),
FinancialJuice Telegram for macro releases (~31 s), unfolded / WatcherGuru Telegram, PR Newswire RSS (~2 min)."""
from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import requests

from .config import UA

S = requests.Session()
S.headers["User-Agent"] = UA

FAST, SLOW = 1, 5  # minutes
RSS = {  # name: (url, every_minutes)
    "fed": ("https://www.federalreserve.gov/feeds/press_all.xml", FAST),
    "sec_press": ("https://www.sec.gov/news/pressreleases.rss", SLOW),
    "bls": ("https://www.bls.gov/feed/bls_latest.rss", SLOW),
    "prnewswire": ("https://www.prnewswire.com/rss/news-releases-list.rss", SLOW),
    "businesswire": ("https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA==", SLOW),
    "coindesk": ("https://www.coindesk.com/arc/outboundfeeds/rss/", SLOW),
    "cointelegraph": ("https://cointelegraph.com/rss", SLOW),
    "theblock": ("https://www.theblock.co/rss.xml", SLOW),
    "decrypt": ("https://decrypt.co/feed", SLOW),
    "cryptoslate": ("https://cryptoslate.com/feed/", SLOW),
    "bitcoinmagazine": ("https://bitcoinmagazine.com/feed", SLOW),
    "kraken_blog": ("https://blog.kraken.com/feed", SLOW),
    "kraken_status": ("https://status.kraken.com/history.rss", SLOW),
    "marketwatch": ("https://feeds.content.dowjones.io/public/rss/mw_topstories", SLOW),
    "investing": ("https://www.investing.com/rss/news.rss", SLOW),
    "seekingalpha": ("https://seekingalpha.com/market_currents.xml", SLOW),
    "benzinga": ("https://www.benzinga.com/feed", SLOW),
}
TELEGRAM = {"financialjuice": FAST, "WatcherGuru": FAST, "unfolded": FAST, "db_news": FAST,
            "binance_announcements": FAST, "wublockchainenglish": SLOW, "whale_alert_io": SLOW}
BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={}&pageNo=1&pageSize=10"
BINANCE_CATS = {"binance_listing": 48, "binance_delisting": 161}

POST = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)
TAG = re.compile(r"<[^>]+>")


def _id(src: str, key: str) -> str:
    return hashlib.sha1(f"{src}|{key}".encode()).hexdigest()[:20]


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def rss(name, url):
    f = feedparser.parse(S.get(url, timeout=10).content)
    out = []
    for e in f.entries[:40]:
        pt = e.get("published_parsed") or e.get("updated_parsed")
        key = e.get("id") or e.get("link") or e.get("title")
        summ = html.unescape(TAG.sub(" ", e.get("summary", "") or ""))
        out.append(dict(id=_id(name, key), source=name, title=html.unescape(e.get("title", "")).strip()[:400],
                        url=e.get("link"), summary=re.sub(r"\s+", " ", summ).strip()[:800],
                        published=_iso(calendar.timegm(pt)) if pt else None))
    return out


def telegram(ch):
    h = S.get(f"https://t.me/s/{ch}", timeout=10).text
    out = []
    for pid, body in POST.findall(h):
        t = re.search(r'<time datetime="([^"]+)"', body)
        txt = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', body, re.S)
        if not txt:
            continue
        text = re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", txt.group(1)))).strip()
        if not text:
            continue
        out.append(dict(id=_id("tg:" + ch, pid), source="tg:" + ch, title=text[:400], url=f"https://t.me/{pid}",
                        summary=text[400:1200] or None,
                        published=datetime.fromisoformat(t.group(1)).astimezone(timezone.utc).isoformat() if t else None))
    return out


def binance(name, cat):
    d = S.get(BINANCE.format(cat), timeout=10).json()
    out = []
    for a in d["data"]["catalogs"][0]["articles"]:
        out.append(dict(id=_id(name, a["code"]), source=name, title=a["title"],
                        url=f"https://www.binance.com/en/support/announcement/{a['code']}", summary=None,
                        published=_iso(a["releaseDate"] / 1000)))
    return out


def sources():
    s = {f"rss:{k}": (lambda u=u, k=k: rss(k, u), every) for k, (u, every) in RSS.items()}
    s.update({f"tg:{c}": (lambda c=c: telegram(c), every) for c, every in TELEGRAM.items()})
    s.update({n: (lambda n=n, c=c: binance(n, c), FAST) for n, c in BINANCE_CATS.items()})
    return s


def poll(minute: int, force_all: bool = False) -> tuple[list[dict], dict]:
    """Fetch every due source in parallel. Returns (items, status per source)."""
    src = sources()
    due = {k: v for k, v in src.items() if force_all or minute % v[1] == 0}
    items, status = [], {}

    def run(k):
        try:
            return k, due[k][0](), None
        except Exception as e:  # noqa
            return k, [], f"{type(e).__name__}: {str(e)[:120]}"
    with ThreadPoolExecutor(12) as ex:
        for k, rows, err in ex.map(run, list(due)):
            items += rows
            status[k] = err or len(rows)
    return items, status

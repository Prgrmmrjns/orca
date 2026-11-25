"""Pollers for news sources. Each poller returns a list of Item dicts:

  {"id", "source", "title", "url", "published" (ISO UTC or None), "summary", "tickers", "form"}

Conditional GETs (ETag / Last-Modified) are used wherever the server honours
them, so a poll that sees nothing new usually costs a few hundred bytes.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import html
import os
import re
import time
from zoneinfo import ZoneInfo

import feedparser
import requests

WEB_UA = "Mozilla/5.0 (compatible; orca-paper-research/0.1; +https://github.com/Prgrmmrjns/orca)"
# SEC and BLS reject requests whose User-Agent has no contact e-mail. Set ORCA_CONTACT_UA to
# "<name> <your-email>" (SEC fair-access policy asks for a real contact); the default is a placeholder.
SEC_UA = os.environ.get("ORCA_CONTACT_UA", "orca-paper-research research@example.com")
Q4_PATH = ("/feed/PressRelease.svc/GetPressReleaseList?LanguageId=1&bodyType=3&pressReleaseDateFilter=3"
           "&categoryId=1cb807d2-208f-4bc3-9133-6a9ad45ac3b0&pageSize=10&pageNumber=0&tagList="
           "&includeTags=true&year=-1&excludeSelection=1")
# Q4 sites report local wall-clock time without a zone
Q4_TZ = {"abc.xyz": "America/Los_Angeles"}

def _iso(t: dt.datetime | None):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None

def _clean(s: str | None, n: int = 1500) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]

class Poller:
    """Holds per-source HTTP validators and error back-off."""

    def __init__(self, src: dict, universe: dict):
        self.src, self.universe = src, universe
        self.etag = self.last_mod = self.head_sig = self.body_hash = None
        self.errors = 0
        self.next_due = 0.0
        self.stats = dict(polls=0, not_modified=0, errors=0, last_ok=None, last_error=None, bytes=0)
        self.session = requests.Session()

    # ---- HTTP -------------------------------------------------------------
    def _get(self, url, ua=WEB_UA, conditional=True, timeout=15):
        h = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        if conditional and self.etag: h["If-None-Match"] = self.etag
        if conditional and self.last_mod: h["If-Modified-Since"] = self.last_mod
        r = self.session.get(url, headers=h, timeout=timeout)
        self.stats["bytes"] += len(r.content)
        if r.status_code == 304:
            self.stats["not_modified"] += 1
            return None
        r.raise_for_status()
        self.etag, self.last_mod = r.headers.get("ETag"), r.headers.get("Last-Modified")
        h = hashlib.sha1(r.content).hexdigest()
        if h == self.body_hash:           # server ignored validators but nothing changed
            self.stats["not_modified"] += 1
            return None
        self.body_hash = h
        return r

    def _head_changed(self, url) -> bool:
        """Cheap HEAD check for feeds that ignore If-None-Match (e.g. NVIDIA)."""
        r = self.session.head(url, headers={"User-Agent": WEB_UA}, timeout=10)
        sig = (r.headers.get("ETag"), r.headers.get("Last-Modified"), r.headers.get("Content-Length"))
        if sig == self.head_sig and any(sig):
            self.stats["not_modified"] += 1
            return False
        self.head_sig = sig
        return True

    # ---- polling ----------------------------------------------------------
    def poll(self) -> list[dict]:
        self.stats["polls"] += 1
        kind = self.src["kind"]
        try:
            if kind == "rss": items = self._rss()
            elif kind == "q4_json": items = self._q4()
            elif kind == "sec_submissions": items = self._sec()
            else: raise ValueError(kind)
            self.errors = 0
            self.stats["last_ok"] = _iso(dt.datetime.now(dt.timezone.utc))
            return items
        except Exception as e:  # network/parse problems must never kill the loop
            self.errors += 1
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{_iso(dt.datetime.now(dt.timezone.utc))} {type(e).__name__}: {str(e)[:160]}"
            return []

    def backoff(self) -> float:
        """Seconds until the next poll (exponential back-off after errors, max 15 min)."""
        base = float(self.src["interval"])
        return base if not self.errors else min(900.0, base * 2 ** min(self.errors, 8))

    def _rss(self):
        url = self.src["url"]
        if self.src.get("head_check") and not self._head_changed(url):
            return []
        r = self._get(url, ua=SEC_UA if self.src.get("contact_ua") else WEB_UA)
        if r is None: return []
        feed = feedparser.parse(r.content)
        out = []
        for e in feed.entries[:50]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            pub = dt.datetime.fromtimestamp(calendar.timegm(t), dt.timezone.utc) if t else None
            out.append(dict(id=e.get("id") or e.get("link") or e.get("title"), source=self.src["id"],
                            title=_clean(e.get("title"), 300), url=e.get("link"), published=_iso(pub),
                            summary=_clean(e.get("summary") or e.get("description")),
                            tickers=list(self.src.get("tickers", []))))
        return out

    def _q4(self):
        base = self.src["url"].rstrip("/")
        r = self._get(base + Q4_PATH)
        if r is None: return []
        tz = ZoneInfo(Q4_TZ.get(base.split("//")[-1], "America/New_York"))
        out = []
        for it in r.json().get("GetPressReleaseListResult", []):
            try:
                pub = dt.datetime.strptime(it["PressReleaseDate"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=tz)
            except Exception:
                pub = None
            link = it.get("LinkToDetailPage") or it.get("LinkToUrl") or ""
            out.append(dict(id=str(it.get("PressReleaseId") or it.get("Headline")), source=self.src["id"],
                            title=_clean(it.get("Headline"), 300), url=(base + link) if link.startswith("/") else link,
                            published=_iso(pub),
                            summary=_clean(" ".join(str(it.get(k) or "") for k in ("Subheadline", "ShortBody")
                                                    if it.get(k) not in (None, "None"))),
                            tickers=list(self.src.get("tickers", []))))
        return out

    def _sec(self):
        """Round-robin one CIK per poll via data.sec.gov submissions JSON."""
        ciks = [(t, u["cik"]) for t, u in self.universe.items() if u.get("cik")]
        self._rr = (getattr(self, "_rr", -1) + 1) % len(ciks)
        tkr, cik = ciks[self._rr]
        r = self.session.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                             headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, timeout=15)
        r.raise_for_status()
        self.stats["bytes"] += len(r.content)
        f = r.json()["filings"]["recent"]
        out = []
        for i in range(min(20, len(f["form"]))):
            if f["form"][i] not in self.src.get("forms", ["8-K"]): continue
            acc = f["accessionNumber"][i]
            items = f.get("items", [""] * len(f["form"]))[i]
            doc = f["primaryDocument"][i]
            out.append(dict(id=acc, source=self.src["id"], form=f["form"][i],
                            title=f"{tkr} {f['form'][i]} items {items}: {f.get('primaryDocDescription', [''] * 99)[i] or ''}".strip(),
                            url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}",
                            published=f["acceptanceDateTime"][i].replace(".000Z", "Z"), summary=f"8-K items {items}",
                            tickers=[tkr], cik=cik))
        return out

def fetch_text(url: str, n: int = 6000, sec: bool = False) -> str:
    """Body text of a release/filing for the classifier (best effort)."""
    try:
        r = requests.get(url, headers={"User-Agent": SEC_UA if sec else WEB_UA}, timeout=10)
        r.raise_for_status()
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", r.text)
        return _clean(t, n)
    except Exception:
        return ""

def sec_exhibit_text(item: dict, n: int = 6000) -> str:
    """For an 8-K, the press release is usually exhibit 99.1; fall back to the main document."""
    try:
        idx = item["url"].rsplit("/", 1)[0] + "/index.json"
        r = requests.get(idx, headers={"User-Agent": SEC_UA}, timeout=10).json()
        names = [x["name"] for x in r["directory"]["item"]]
        ex = [x for x in names if re.search(r"ex-?99", x, re.I) and x.endswith((".htm", ".html", ".txt"))]
        time.sleep(0.15)
        return fetch_text(item["url"].rsplit("/", 1)[0] + "/" + ex[0], n, sec=True) if ex else fetch_text(item["url"], n, sec=True)
    except Exception:
        return fetch_text(item["url"], n, sec=True)

def match_tickers(text: str, universe: dict) -> list[str]:
    hits = []
    for t, u in universe.items():
        for name in u.get("names", []):
            if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text):
                hits.append(t); break
    return hits

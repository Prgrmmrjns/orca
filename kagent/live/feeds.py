"""Poll fast public news feeds and emit new items with first-seen latency. No keys needed.
SEC asks automated clients to send a descriptive User-Agent with contact info: set SEC_USER_AGENT."""
from __future__ import annotations

import calendar
import os
import time

import feedparser

FEEDS = {
    "fed": ("https://www.federalreserve.gov/feeds/press_all.xml", 5),
    "bls_cpi": ("https://www.bls.gov/feed/cpi.rss", 10),
    "sec_8k": ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=40&output=atom", 10),
    "businesswire": ("https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA==", 15),
    "prnewswire": ("https://www.prnewswire.com/rss/news-releases-list.rss", 15),
}


class Poller:
    def __init__(self, feeds=FEEDS):
        self.feeds = feeds
        self.seen: set[str] = set()
        self.next_due = {k: 0.0 for k in feeds}
        self.ua = os.environ.get("SEC_USER_AGENT", "OrcaPaperResearch")
        self.primed = set()

    def poll(self):
        """Returns list of new items: dict(source, title, summary, link, published_ts, seen_ts, delay_s)."""
        out, now = [], time.time()
        for name, (url, every) in self.feeds.items():
            if now < self.next_due[name]:
                continue
            self.next_due[name] = now + every
            try:
                f = feedparser.parse(url, agent=self.ua)
            except Exception:
                continue
            for e in f.entries:
                key = e.get("id") or e.get("link") or e.get("title")
                if key in self.seen:
                    continue
                self.seen.add(key)
                if name not in self.primed:
                    continue  # first poll only records what's already there
                pt = e.get("published_parsed") or e.get("updated_parsed")
                pts = calendar.timegm(pt) if pt else None
                out.append(dict(source=name, title=e.get("title", ""), summary=e.get("summary", "")[:1000],
                                link=e.get("link", ""), published_ts=pts, seen_ts=now,
                                delay_s=round(now - pts, 1) if pts else None))
            self.primed.add(name)
        return out

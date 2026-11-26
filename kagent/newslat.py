"""Measure news-source latency live: poll many free sources fast, log first-seen time vs the source's own timestamp.
Cross-source: the same story is matched by title similarity, so we can see who reports first.
python -m kagent.newslat --hours 6   (writes research/kagent_live/latency.jsonl)"""
from __future__ import annotations

import argparse, calendar, html, json, re, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import feedparser

UA = "Mozilla/5.0 (OrcaResearch)"
RSS = {  # name: (url, poll seconds)
    "fed": ("https://www.federalreserve.gov/feeds/press_all.xml", 10),
    "businesswire": ("https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA==", 15),
    "prnewswire": ("https://www.prnewswire.com/rss/news-releases-list.rss", 15),
    "marketwatch": ("https://feeds.content.dowjones.io/public/rss/mw_topstories", 20),
    "wsj_markets": ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml", 20),
    "investing": ("https://www.investing.com/rss/news.rss", 20),
    "benzinga": ("https://www.benzinga.com/feed", 20),
    "seekingalpha_currents": ("https://seekingalpha.com/market_currents.xml", 20),
    "bbc_business": ("https://feeds.bbci.co.uk/news/business/rss.xml", 30),
    "nyt_business": ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", 30),
    "cointelegraph": ("https://cointelegraph.com/rss", 20),
    "theblock": ("https://www.theblock.co/rss.xml", 20),
    "decrypt": ("https://decrypt.co/feed", 20),
    "kraken_blog": ("https://blog.kraken.com/feed", 60),
    "kraken_status": ("https://status.kraken.com/history.rss", 60),
}
TELEGRAM = {c: 10 for c in ["WatcherGuru", "financialjuice", "unfolded", "db_news", "wublockchainenglish",
                            "binance_announcements", "whale_alert_io", "cointelegraph"]}
BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={}&pageNo=1&pageSize=10"


def _get(url, timeout=10):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()


def rss(name, url):
    f = feedparser.parse(_get(url))
    for e in f.entries:
        pt = e.get("published_parsed") or e.get("updated_parsed")
        yield (e.get("id") or e.get("link") or e.get("title")), e.get("title", ""), calendar.timegm(pt) if pt else None


POST = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)


def telegram(ch):
    h = _get(f"https://t.me/s/{ch}").decode("utf8", "ignore")
    for pid, body in POST.findall(h):
        t = re.search(r'<time datetime="([^"]+)"', body)
        txt = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', body, re.S)
        if t:
            yield pid, html.unescape(re.sub("<[^>]+>", " ", txt.group(1))).strip()[:300] if txt else "", \
                datetime.fromisoformat(t.group(1)).timestamp()


def binance(cat):
    d = json.loads(_get(BINANCE.format(cat)))
    for a in d["data"]["catalogs"][0]["articles"]:
        yield a["code"], a["title"], a["releaseDate"] / 1000


def sources():
    s = {f"rss:{k}": (lambda u=u, k=k: rss(k, u), p) for k, (u, p) in RSS.items()}
    s.update({f"tg:{c}": (lambda c=c: telegram(c), p) for c, p in TELEGRAM.items()})
    s.update({"binance:listing": (lambda: binance(48), 5), "binance:delisting": (lambda: binance(161), 10)})
    return s


def main(hours, out):
    src, seen, primed, due = sources(), set(), set(), {}
    end = time.time() + hours * 3600
    with open(out, "a") as fh, ThreadPoolExecutor(8) as ex:
        while time.time() < end:
            now = time.time()
            todo = [k for k in src if due.get(k, 0) <= now]
            for k in todo:
                due[k] = now + src[k][1]
            futs = {k: ex.submit(_safe, src[k][0]) for k in todo}
            res = {}
            for k, f in futs.items():
                try:
                    res[k] = f.result(timeout=25)
                except Exception:
                    res[k] = []
            t = time.time()
            for k, items in res.items():
                for key, title, pts in items:
                    if (k, key) in seen:
                        continue
                    seen.add((k, key))
                    if k in primed:
                        fh.write(json.dumps(dict(src=k, title=title, pub=pts, seen=round(t, 1),
                                                 lag=round(t - pts, 1) if pts else None)) + "\n")
                if items:
                    primed.add(k)
            fh.flush()
            time.sleep(1)


def _safe(fn):
    try:
        return list(fn())
    except Exception:
        return []


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--out", default="research/kagent_live/latency.jsonl")
    a = ap.parse_args(); main(a.hours, a.out)

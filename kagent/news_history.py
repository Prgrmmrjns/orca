"""Timestamped news history for minute-level event studies (last ~2 years).
- Telegram public channels via t.me/s/<channel>?before=<id> (20 posts/page; second-resolution timestamps).
- Binance announcements (listings, delistings) via the CMS API (millisecond timestamps).
python -m kagent.news_history  -> research/kagent_data/news/*.csv.gz"""
from __future__ import annotations

import html, json, re, sys, time, urllib.request
from datetime import datetime

import pandas as pd

from kagent.data import DATA

OUT = DATA / "news"
UA = "Mozilla/5.0 (OrcaResearch)"
CHANNELS = ["financialjuice", "WatcherGuru", "unfolded", "wublockchainenglish", "db_news", "binance_announcements"]
SINCE = pd.Timestamp("2024-09-01", tz="UTC")


def _get(url):
    for i in range(5):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20).read().decode("utf8", "ignore")
        except Exception:
            time.sleep(2 * (i + 1))
    return ""


POST = re.compile(r'data-post="[^/]+/(\d+)"(.*?)(?=data-post="|\Z)', re.S)


def _page(ch, before):
    h = _get(f"https://t.me/s/{ch}?before={before}")
    out = []
    for pid, body in POST.findall(h):
        t = re.search(r'<time datetime="([^"]+)"', body)
        txt = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', body, re.S)
        if t:
            out.append((int(pid), datetime.fromisoformat(t.group(1)),
                        html.unescape(re.sub("<[^>]+>", " ", txt.group(1))).strip()[:500] if txt else ""))
    return out


def telegram_parallel(ch, threads=8):
    """Page backwards by post id in parallel (20 ids per page) until posts are older than SINCE."""
    from concurrent.futures import ThreadPoolExecutor
    top = max(r[0] for r in _page(ch, 10**9)) + 1
    rows, hi = [], top
    with ThreadPoolExecutor(threads) as ex:
        while hi > 1:
            befores = list(range(hi, max(1, hi - 20 * 200), -20))
            for res in ex.map(lambda b: _page(ch, b), befores):
                rows += res
            hi = befores[-1]
            oldest = min((r[1] for r in rows), default=None)
            print(ch, "at id", hi, oldest, flush=True)
            if oldest is not None and pd.Timestamp(oldest) < SINCE:
                break
    df = pd.DataFrame(rows, columns=["id", "ts", "text"]).drop_duplicates("id").sort_values("ts")
    df.to_csv(OUT / f"tg_{ch}.csv.gz", index=False)
    print(ch, len(df), df.ts.min(), flush=True)


def telegram(ch):
    rows, before = [], None
    while True:
        h = _get(f"https://t.me/s/{ch}" + (f"?before={before}" if before else ""))
        posts = POST.findall(h)
        if not posts:
            break
        for pid, body in posts:
            t = re.search(r'<time datetime="([^"]+)"', body)
            txt = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', body, re.S)
            if t:
                rows.append((int(pid), datetime.fromisoformat(t.group(1)),
                             html.unescape(re.sub("<[^>]+>", " ", txt.group(1))).strip()[:500] if txt else ""))
        ids = [int(p) for p, _ in posts]
        oldest = min(r[1] for r in rows[-len(posts):])
        if before is not None and min(ids) >= before:
            break
        before = min(ids)
        if pd.Timestamp(oldest) < SINCE or before <= 1:
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["id", "ts", "text"]).drop_duplicates("id").sort_values("ts")
    df.to_csv(OUT / f"tg_{ch}.csv.gz", index=False)
    print(ch, len(df), df.ts.min(), flush=True)


def binance(cat, name):
    rows, page = [], 1
    while True:
        d = json.loads(_get(f"https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={cat}&pageNo={page}&pageSize=50"))
        arts = d["data"]["catalogs"][0]["articles"]
        if not arts:
            break
        rows += [(a["code"], pd.Timestamp(a["releaseDate"], unit="ms", tz="UTC"), a["title"]) for a in arts]
        if rows[-1][1] < SINCE:
            break
        page += 1
        time.sleep(0.5)
    df = pd.DataFrame(rows, columns=["id", "ts", "text"]).sort_values("ts")
    df.to_csv(OUT / f"binance_{name}.csv.gz", index=False)
    print(name, len(df), df.ts.min(), flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for c in (sys.argv[1:] or CHANNELS):
        telegram_parallel(c)

"""Finance/crypto news ingestion + daily features (paper research).

Prefer free public RSS (CoinDesk, CoinTelegraph, Decrypt, Google News).
Cache under data/news/. Features for bar timestamp T use only articles with
published_at < T (strict; no same-bar / same-day look-ahead on daily bars
dated at 00:00 UTC).
"""

from __future__ import annotations

import json
import hashlib
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import feedparser
import requests

from orca.logging_setup import setup_logging

logger = setup_logging()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NEWS_DIR = ROOT / "data" / "news"

# RSS sources (recent window)
RSS_FEEDS: dict[str, str] = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "cryptoslate": "https://cryptoslate.com/feed/",
    "theblock": "https://www.theblock.co/rss.xml",
    "google_btc": (
        "https://news.google.com/rss/search?q=Bitcoin+OR+BTC"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
}

# Headlines for the reward model. Google News is queried month by month because
# each RSS response is capped near 100 items.
GOOGLE_QUERIES: tuple[tuple[str, str], ...] = (
    ("market", "cryptocurrency OR bitcoin OR ethereum"),
    ("majors", "Bitcoin OR Ethereum OR Solana OR XRP OR Cardano OR Dogecoin"),
    ("l1", "Avalanche OR Chainlink OR Polkadot OR Litecoin OR Algorand OR Stellar OR Tron"),
    ("defi", "Uniswap OR Aave OR Injective OR \"Bitcoin Cash\" OR Zcash OR BNB OR Curve"),
    ("l2", "Sui OR Aptos OR Arbitrum OR \"NEAR Protocol\" OR Hedera OR \"Internet Computer\""),
    ("ai", "Bittensor OR Render OR \"Fetch.ai\" OR Worldcoin OR Ondo OR Filecoin"),
    ("meme", "Pepe OR Bonk OR \"Pudgy Penguins\" OR pump.fun OR Ethena OR \"Official Trump\""),
    ("misc", "\"PAX Gold\" OR Aerodrome OR \"Mina Protocol\" OR Jupiter OR Raydium OR Polygon OR Dash"),
)

# Phrases that count as a mention of that base. Short ambiguous words are the full name.
COIN_ALIASES: dict[str, tuple[str, ...]] = {
    "AAVE": ("aave",),
    "ADA": ("cardano", "ada"),
    "AERO": ("aerodrome", " (aero)"),
    "ALGO": ("algorand",),
    "APT": ("aptos",),
    "ARB": ("arbitrum",),
    "ASTER": ("aster",),
    "AVAX": ("avalanche", "avax"),
    "BCH": ("bitcoin cash", "bch"),
    "BNB": ("bnb", "binance coin"),
    "BONK": ("bonk",),
    "BTC": ("bitcoin", "btc"),
    "CRV": ("curve finance", "curve dao", "crv"),
    "DASH": ("dash",),
    "DOGE": ("dogecoin", "doge"),
    "DOT": ("polkadot",),
    "ENA": ("ethena", " (ena)"),
    "ETH": ("ethereum", "ether"),
    "FET": ("fetch.ai", "fetch ai", "artificial superintelligence"),
    "FIL": ("filecoin",),
    "HBAR": ("hedera", "hbar"),
    "ICP": ("internet computer",),
    "INJ": ("injective",),
    "JUP": ("jupiter",),
    "LINK": ("chainlink",),
    "LTC": ("litecoin",),
    "MINA": ("mina protocol", " (mina)"),
    "NEAR": ("near protocol", " (near)"),
    "ONDO": ("ondo",),
    "PAXG": ("pax gold", "paxg"),
    "PENGU": ("pudgy penguin",),
    "PEPE": ("pepe",),
    "POL": ("polygon", "matic"),
    "PUMP": ("pump.fun", "pumpfun"),
    "RAY": ("raydium", " (ray)"),
    "RENDER": ("render network", "render token", "rndr"),
    "SOL": ("solana",),
    "SUI": ("sui",),
    "TAO": ("bittensor",),
    "TRUMP": ("official trump", "trump coin", "trump token", "trump meme"),
    "TRX": ("tron", "trx"),
    "UNI": ("uniswap",),
    "WLD": ("worldcoin", "world coin"),
    "XLM": ("stellar", "xlm"),
    "XPL": ("xpl",),
    "XRP": ("ripple", "xrp"),
    "ZEC": ("zcash",),
}

BULLISH_WORDS = frozenset(
    {
        "surge",
        "surges",
        "rally",
        "rallies",
        "bull",
        "bullish",
        "soar",
        "soars",
        "soaring",
        "gain",
        "gains",
        "jump",
        "jumps",
        "breakout",
        "ath",
        "record",
        "high",
        "highs",
        "approval",
        "approve",
        "approved",
        "etf",
        "inflow",
        "inflows",
        "adoption",
        "accumulate",
        "accumulation",
        "buy",
        "buying",
        "optimism",
        "optimistic",
        "upgrade",
        "moon",
        "pump",
        "recovery",
        "recover",
        "rebound",
        "all-time",
        "green",
        "support",
        "institutional",
    }
)

BEARISH_WORDS = frozenset(
    {
        "crash",
        "crashes",
        "dump",
        "dumps",
        "bear",
        "bearish",
        "plunge",
        "plunges",
        "drop",
        "drops",
        "fall",
        "falls",
        "selloff",
        "sell-off",
        "liquidation",
        "liquidations",
        "hack",
        "hacked",
        "ban",
        "banned",
        "lawsuit",
        "sec",
        "fraud",
        "collapse",
        "fear",
        "panic",
        "outflow",
        "outflows",
        "rejection",
        "reject",
        "rejected",
        "warning",
        "risk",
        "risks",
        "decline",
        "declines",
        "low",
        "lows",
        "weak",
        "weakness",
        "sell",
        "selling",
        "규제",  # ignored by ascii tokenize; harmless
    }
)

# Drop non-ascii tokens from word lists for tokenize simplicity
BULLISH_WORDS = frozenset(w for w in BULLISH_WORDS if w.isascii())
BEARISH_WORDS = frozenset(w for w in BEARISH_WORDS if w.isascii())

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_published(entry: dict[str, Any]) -> datetime | None:
    """Best-effort published time → UTC."""
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if not struct:
            continue
        try:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def _article_id(source: str, title: str, link: str, published: str) -> str:
    h = hashlib.sha1(f"{source}|{link}|{title}|{published}".encode("utf-8")).hexdigest()
    return h[:16]


def _normalize_article(source: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    # Google News titles often end with " - Publisher"
    link = (entry.get("link") or "").strip()
    published_dt = _parse_published(entry)
    if published_dt is None:
        return None
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    # Strip simple HTML
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    pub_iso = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": _article_id(source, title, link, pub_iso),
        "source": source,
        "title": title,
        "link": link,
        "published_at": pub_iso,
        "summary": summary[:500],
    }


def fetch_rss_feed(source: str, url: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Fetch one RSS/Atom feed; return normalized articles."""
    logger.info("Fetching RSS %s", source)
    # feedparser can take a URL; use requests for clearer timeout/UA
    headers = {"User-Agent": "OrcaPaperBot/0.2 (+research; paper-only)"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RSS fetch failed %s: %s", source, exc)
        return []
    out: list[dict[str, Any]] = []
    for entry in parsed.entries:
        art = _normalize_article(source, entry)
        if art:
            out.append(art)
    logger.info("RSS %s -> %d articles", source, len(out))
    return out


def _month_starts(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Inclusive month windows covering [start, end]."""
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    end_m = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    windows: list[tuple[datetime, datetime]] = []
    while cur <= end_m:
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)
        windows.append((cur, nxt))
        cur = nxt
    return windows


def fetch_google_news_range(
    start: datetime,
    end: datetime,
    query: str = "Bitcoin OR BTC",
    pause_s: float = 0.4,
) -> list[dict[str, Any]]:
    """Pull Google News RSS month-by-month for historical headline coverage."""
    articles: list[dict[str, Any]] = []
    for win_start, win_end in _month_starts(start, end):
        # Google supports after:/before: (date exclusive-ish; use day strings)
        after = (win_start - timedelta(days=1)).strftime("%Y-%m-%d")
        before = win_end.strftime("%Y-%m-%d")
        q = f"{query} after:{after} before:{before}"
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(q)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        source = f"google_btc_{win_start.strftime('%Y%m')}"
        batch = fetch_rss_feed(source, url)
        # Re-tag source for dedupe clarity
        for a in batch:
            a["source"] = "google_btc_hist"
        articles.extend(batch)
        time.sleep(pause_s)
    logger.info("Google News historical total raw=%d", len(articles))
    return articles


def dedupe_articles(articles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by id, then by normalized title+day."""
    by_id: dict[str, dict[str, Any]] = {}
    for a in articles:
        by_id[a["id"]] = a
    items = list(by_id.values())
    seen_title_day: set[str] = set()
    out: list[dict[str, Any]] = []
    for a in sorted(items, key=lambda x: x["published_at"]):
        title_key = re.sub(r"\s+", " ", a["title"].lower())
        # strip publisher suffix
        title_key = re.sub(r"\s+-\s+[^-]+$", "", title_key).strip()
        day = a["published_at"][:10]
        key = f"{day}|{title_key}"
        if key in seen_title_day:
            continue
        seen_title_day.add(key)
        out.append(a)
    return out


def save_articles_cache(articles: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_articles": len(articles),
        "articles": articles,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Cached %d articles -> %s", len(articles), path)
    return path


def load_articles_cache(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("articles") or [])


def collect_news(
    news_dir: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    refresh: bool = False,
    include_historical_google: bool = True,
) -> list[dict[str, Any]]:
    """Fetch + cache news articles. Reuse cache unless refresh=True."""
    news_dir = Path(news_dir or DEFAULT_NEWS_DIR)
    news_dir.mkdir(parents=True, exist_ok=True)
    cache_path = news_dir / "articles.json"

    if cache_path.exists() and not refresh:
        arts = load_articles_cache(cache_path)
        logger.info("Loaded %d cached articles from %s", len(arts), cache_path)
        return arts

    end = end or _utcnow()
    start = start or (end - timedelta(days=750))

    collected: list[dict[str, Any]] = []
    for name, url in RSS_FEEDS.items():
        collected.extend(fetch_rss_feed(name, url))
        time.sleep(0.2)

    if include_historical_google:
        collected.extend(fetch_google_news_range(start, end))

    articles = dedupe_articles(collected)
    save_articles_cache(articles, cache_path)
    # Also write a slim CSV for inspection
    csv_path = news_dir / "articles.csv"
    pd.DataFrame(articles).to_csv(csv_path, index=False)
    logger.info("Wrote %s", csv_path)
    return articles


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def keyword_sentiment(text: str) -> float:
    """Simple (bull - bear) / (bull + bear + eps) in [-1, 1]."""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    bull = sum(1 for t in tokens if t in BULLISH_WORDS)
    bear = sum(1 for t in tokens if t in BEARISH_WORDS)
    denom = bull + bear
    if denom == 0:
        return 0.0
    return (bull - bear) / denom


_vader = None


def _get_vader():
    global _vader
    if _vader is False:
        return None
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            _vader = SentimentIntensityAnalyzer()
        except Exception:  # noqa: BLE001
            logger.warning("vaderSentiment unavailable; keyword sentiment only")
            _vader = False
            return None
    return _vader


def vader_compound(text: str) -> float:
    analyzer = _get_vader()
    if analyzer is None:
        return keyword_sentiment(text)
    return float(analyzer.polarity_scores(text)["compound"])


def score_article(article: dict[str, Any]) -> dict[str, float]:
    text = f"{article.get('title', '')}. {article.get('summary', '')}"
    kw = keyword_sentiment(text)
    vd = vader_compound(text)
    # Mention intensity: bitcoin/btc occurrences
    tokens = _tokenize(text)
    mentions = sum(1 for t in tokens if t in ("bitcoin", "btc", "btc-usd", "xbt"))
    return {
        "kw_sentiment": kw,
        "vader_sentiment": vd,
        "btc_mentions": float(mentions),
    }


def articles_to_frame(articles: list[dict[str, Any]]) -> pd.DataFrame:
    if not articles:
        return pd.DataFrame(
            columns=[
                "published_at",
                "source",
                "title",
                "kw_sentiment",
                "vader_sentiment",
                "btc_mentions",
            ]
        )
    rows = []
    for a in articles:
        scores = score_article(a)
        rows.append(
            {
                "published_at": pd.Timestamp(a["published_at"]),
                "source": a.get("source"),
                "title": a.get("title"),
                **scores,
            }
        )
    df = pd.DataFrame(rows)
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    return df.sort_values("published_at").reset_index(drop=True)


def build_daily_news_features(
    articles: list[dict[str, Any]],
    bar_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Align news to daily bars WITHOUT look-ahead.

    For each bar timestamp T (typically 00:00 UTC of day D), aggregate articles
    with published_at < T only. That excludes same-calendar-day news when bars
    are dated at day start — i.e. features as of prior day close.
    """
    idx = pd.DatetimeIndex(bar_index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    empty = pd.DataFrame(
        {
            "news_count": 0.0,
            "news_sentiment_kw": 0.0,
            "news_sentiment_vader": 0.0,
            "news_mention_intensity": 0.0,
            "news_sentiment": 0.0,
        },
        index=idx,
    )
    if not articles:
        return empty

    adf = articles_to_frame(articles)
    if adf.empty:
        return empty

    # Daily bins of articles by publish calendar day (UTC)
    adf = adf.copy()
    adf["day"] = adf["published_at"].dt.floor("D")
    daily = (
        adf.groupby("day")
        .agg(
            news_count=("title", "count"),
            news_sentiment_kw=("kw_sentiment", "mean"),
            news_sentiment_vader=("vader_sentiment", "mean"),
            news_mention_intensity=("btc_mentions", "sum"),
        )
        .sort_index()
    )

    # Calendar spans the bar index (plus article span) so trailing zero-news
    # days exist and ffill cannot carry a stale rolling window past gaps.
    bar_days = pd.DatetimeIndex(idx).floor("D")
    cal_start = min(daily.index.min(), bar_days.min())
    cal_end = max(daily.index.max(), bar_days.max())
    cal = pd.date_range(cal_start, cal_end, freq="D", tz="UTC")
    daily = daily.reindex(cal).fillna(
        {
            "news_count": 0.0,
            "news_sentiment_kw": 0.0,
            "news_sentiment_vader": 0.0,
            "news_mention_intensity": 0.0,
        }
    )

    daily["kw_sum"] = daily["news_sentiment_kw"] * daily["news_count"]
    daily["vd_sum"] = daily["news_sentiment_vader"] * daily["news_count"]
    cum_count = daily["news_count"].cumsum()
    cum_kw = daily["kw_sum"].cumsum()
    cum_vd = daily["vd_sum"].cumsum()
    cum_ment = daily["news_mention_intensity"].cumsum()

    # Rolling 7 calendar days
    roll_count = daily["news_count"].rolling(7, min_periods=1).sum()
    roll_den = daily["news_count"].rolling(7, min_periods=1).sum()
    roll_kw = (daily["kw_sum"].rolling(7, min_periods=1).sum() / roll_den.replace(0, float("nan"))).fillna(0.0)
    roll_vd = (daily["vd_sum"].rolling(7, min_periods=1).sum() / roll_den.replace(0, float("nan"))).fillna(0.0)
    roll_ment = daily["news_mention_intensity"].rolling(7, min_periods=1).sum()

    # Same-day features available next bar open / next day 00:00:
    feat_daily = pd.DataFrame(
        {
            "news_count": daily["news_count"],
            "news_sentiment_kw": daily["news_sentiment_kw"],
            "news_sentiment_vader": daily["news_sentiment_vader"],
            "news_mention_intensity": daily["news_mention_intensity"],
            "news_count_7d": roll_count,
            "news_sentiment_kw_7d": roll_kw,
            "news_sentiment_vader_7d": roll_vd,
            "news_mention_intensity_7d": roll_ment,
            "news_count_cum": cum_count,
            "news_kw_cum_mean": (cum_kw / cum_count.replace(0, pd.NA)).fillna(0.0),
            "news_vd_cum_mean": (cum_vd / cum_count.replace(0, pd.NA)).fillna(0.0),
        }
    )
    # Shift by +1 day so bar D 00:00 sees through day D-1 only
    feat_daily.index = feat_daily.index + pd.Timedelta(days=1)

    out = feat_daily.reindex(idx)
    # Forward-fill so weekends/gaps keep last known prior-day features;
    # days before first news stay 0
    out = out.ffill().fillna(0.0)

    # Primary "news_sentiment" = blend of VADER (if present) and keyword on 7d window
    out["news_sentiment"] = 0.6 * out["news_sentiment_vader_7d"] + 0.4 * out[
        "news_sentiment_kw_7d"
    ]
    # Prefer 7d count/mentions as trading features
    out["news_count"] = out["news_count_7d"]
    out["news_mention_intensity"] = out["news_mention_intensity_7d"]
    out["news_sentiment_kw"] = out["news_sentiment_kw_7d"]
    out["news_sentiment_vader"] = out["news_sentiment_vader_7d"]

    keep = [
        "news_count",
        "news_sentiment_kw",
        "news_sentiment_vader",
        "news_mention_intensity",
        "news_sentiment",
    ]
    result = out[keep].copy()
    result.index = idx
    return result


def attach_news_features(
    ohlcv: pd.DataFrame,
    articles: list[dict[str, Any]],
) -> pd.DataFrame:
    """Return OHLCV copy with news feature columns aligned (no look-ahead)."""
    feats = build_daily_news_features(articles, ohlcv.index)
    out = ohlcv.copy()
    for col in feats.columns:
        out[col] = feats[col].reindex(out.index).fillna(0.0)
    return out


def save_daily_features(features: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(path)
    return path


def _alias_hit(text: str, alias: str) -> bool:
    if " " in alias or "." in alias:
        return alias in text
    return re.search(rf"\b{re.escape(alias)}\b", text) is not None


def mentioned_bases(text: str, pairs: Iterable[str]) -> list[str]:
    """Bases whose name appears in the headline. Case already folded."""
    found: list[str] = []
    for pair in pairs:
        base = pair.split("/")[0]
        aliases = COIN_ALIASES.get(base, (base.lower(),))
        if any(_alias_hit(text, alias) for alias in aliases):
            found.append(base)
    return found


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return list(data.get("articles") or [])


def collect_reward_news(
    pairs: list[str] | tuple[str, ...],
    *,
    start: datetime,
    end: datetime,
    news_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Headlines for the reward model, from crypto RSS plus monthly Google News.

    Month files are cached under data/news/google/. A bar later uses only
    articles whose published_at is strictly before that bar.
    """
    news_dir = Path(news_dir or DEFAULT_NEWS_DIR)
    cache = news_dir / "google"
    cache.mkdir(parents=True, exist_ok=True)
    collected: list[dict[str, Any]] = []

    for name, url in RSS_FEEDS.items():
        if name == "google_btc":
            continue
        collected.extend(fetch_rss_feed(name, url))
        time.sleep(0.2)

    for slug, query in GOOGLE_QUERIES:
        for win_start, win_end in _month_starts(start, end):
            key = f"{slug}_{win_start.strftime('%Y%m')}"
            path = cache / f"{key}.json"
            if path.exists():
                collected.extend(_load_json_list(path))
                continue
            after = (win_start - timedelta(days=1)).strftime("%Y-%m-%d")
            before = win_end.strftime("%Y-%m-%d")
            q = f"{query} after:{after} before:{before}"
            url = (
                "https://news.google.com/rss/search?q="
                + urllib.parse.quote(q)
                + "&hl=en-US&gl=US&ceid=US:en"
            )
            batch = fetch_rss_feed(key, url)
            for article in batch:
                article["source"] = "google_news"
            if batch or slug != "market":
                path.write_text(json.dumps(batch), encoding="utf-8")
            else:
                logger.warning("Google News %s returned nothing; not caching", key)
            collected.extend(batch)
            time.sleep(0.35)

    articles = dedupe_articles(collected)
    logger.info(
        "Reward news: %d headlines, %d pairs tagged from aliases",
        len(articles),
        len(pairs),
    )
    return articles


def hourly_news_features(
    articles: list[dict[str, Any]],
    pairs: list[str] | tuple[str, ...],
    *,
    through: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Market and per-pair news features. Hour H sees only articles published before H.

    Market columns: news_sent_1d, news_n_1d, news_sent_7d.
    Pair columns: news_pair_1d (mean sentiment of mentions), news_pair_n_1d (log count).
    """
    empty_market = pd.DataFrame(columns=["news_sent_1d", "news_n_1d", "news_sent_7d"])
    empty_pairs = {
        pair: pd.DataFrame(columns=["news_pair_1d", "news_pair_n_1d"]) for pair in pairs
    }
    if not articles:
        return empty_market, empty_pairs

    rows: list[dict[str, Any]] = []
    for article in articles:
        raw = f"{article.get('title', '')}. {article.get('summary', '')}"
        published = pd.Timestamp(article["published_at"])
        if published.tzinfo is None:
            published = published.tz_localize("UTC")
        else:
            published = published.tz_convert("UTC")
        sent = 0.6 * vader_compound(raw) + 0.4 * keyword_sentiment(raw)
        rows.append(
            {
                "hour": published.floor("h"),
                "sent": sent,
                "bases": mentioned_bases(raw.lower(), pairs),
            }
        )
    adf = pd.DataFrame(rows)
    start = adf["hour"].min()
    end = adf["hour"].max()
    if through is not None:
        end = max(end, pd.Timestamp(through).tz_convert("UTC").floor("h"))
    hours = pd.date_range(start, end, freq="h", tz="UTC")

    market = adf.groupby("hour").agg(n=("sent", "size"), sent_sum=("sent", "sum"))
    market = market.reindex(hours).fillna(0.0)
    n_1d = market["n"].rolling(24, min_periods=1).sum()
    n_7d = market["n"].rolling(24 * 7, min_periods=1).sum()
    sent_1d = (market["sent_sum"].rolling(24, min_periods=1).sum() / n_1d.replace(0, np.nan)).fillna(0.0)
    sent_7d = (market["sent_sum"].rolling(24 * 7, min_periods=1).sum() / n_7d.replace(0, np.nan)).fillna(0.0)
    # Label each window at the next hour so the bar does not see its own hour.
    visible = hours + pd.Timedelta(hours=1)
    market_out = pd.DataFrame(
        {
            "news_sent_1d": sent_1d.to_numpy(),
            "news_n_1d": np.log1p(n_1d.to_numpy()),
            "news_sent_7d": sent_7d.to_numpy(),
        },
        index=visible,
    )
    market_out.index.name = "timestamp"

    by_pair: dict[str, pd.DataFrame] = {}
    mention_rows: list[dict[str, Any]] = []
    for hour, sent, bases in zip(adf["hour"], adf["sent"], adf["bases"]):
        for base in bases:
            mention_rows.append({"hour": hour, "base": base, "sent": sent})
    mentions = pd.DataFrame(mention_rows) if mention_rows else pd.DataFrame(columns=["hour", "base", "sent"])
    for pair in pairs:
        base = pair.split("/")[0]
        sub = mentions[mentions["base"] == base] if len(mentions) else mentions
        if len(sub):
            grouped = sub.groupby("hour").agg(n=("sent", "size"), sent_sum=("sent", "sum"))
        else:
            grouped = pd.DataFrame(columns=["n", "sent_sum"])
        grouped = grouped.reindex(hours)
        grouped["n"] = pd.to_numeric(grouped["n"], errors="coerce").fillna(0.0)
        grouped["sent_sum"] = pd.to_numeric(grouped["sent_sum"], errors="coerce").fillna(0.0)
        pn = grouped["n"].rolling(24, min_periods=1).sum()
        ps = (grouped["sent_sum"].rolling(24, min_periods=1).sum() / pn.replace(0, np.nan)).fillna(0.0)
        frame = pd.DataFrame(
            {"news_pair_1d": ps.to_numpy(), "news_pair_n_1d": np.log1p(pn.to_numpy())},
            index=visible,
        )
        frame.index.name = "timestamp"
        by_pair[pair] = frame
    return market_out, by_pair

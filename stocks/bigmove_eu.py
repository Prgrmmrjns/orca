"""bigmove_eu_v1 — European primary-session gap-fade detection (paper only).

Polls free EU regulatory/news aggregators (Google News EQS/DGAP, wallstreet-online,
finanzen.net, Yahoo DE headlines), flags liquid European names moving >=4% vs prior
close, classifies into US-equivalent categories, writes candidates + mega-cap alerts.

Hours: primary exchange only (≈09:00–17:30 Berlin). Free yfinance feeds do not
provide reliable Tradegate/Lang & Schwarz 08:00–22:00 prints.
"""
from __future__ import annotations

import argparse
import hashlib
import time
from typing import Any

from stocks.eu_news import classify_eu_news, fetch_eu_news
from stocks.eu_universe import EU_UNIVERSE_SEED, normalize_eu_ticker
from stocks.market import (
    adv20_eur,
    berlin_market_session,
    eu_session_quote,
    is_etf_ticker,
    is_eu_holiday,
    is_weekday,
    now_berlin,
    stoxx_above_ma50,
    utc_now_iso,
)
from stocks.paths import CANDIDATES, NEWS, STATE, ensure_dirs, read_json, write_json
from stocks.strategy_config import BIGMOVE_EU_BOOK_ID, load_strategy_config

BOOK_ID = BIGMOVE_EU_BOOK_ID
ALERTS_PATH = STATE / "alerts.json"
EU_NEWS_PATH = STATE / "eu_news.json"
MIN_GAP_ABS = 4.0
MIN_REL_VOL = 2.0

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
    "guidance_raise": "earnings_chase",
    "guidance_cut": "earnings_chase",
    "other_8k": "other_8k",
    "earnings_nosurprise": "earnings_nosurprise",
    "earnings_chase": "earnings_chase",
}


def _id_for(*parts: str) -> str:
    raw = "|".join(parts).encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def classify_bigmove_eu_category(
    *,
    event_type: str | None = None,
    eu_category: str | None = None,
    title: str = "",
    summary: str = "",
    has_news: bool = True,
) -> str:
    if not has_news:
        return "no_news_found"
    if eu_category:
        return _CATEGORY_MAP.get(eu_category, eu_category)
    if event_type:
        mapped = _CATEGORY_MAP.get(str(event_type).lower().strip())
        if mapped:
            return mapped
    tags = classify_eu_news(title, summary)
    return tags.get("eu_category") or "other_8k"


def _news_by_ticker(news: dict) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for it in news.get("items") or []:
        for t in it.get("tickers") or []:
            nt = normalize_eu_ticker(str(t)) or str(t).upper()
            by.setdefault(nt, []).append(it)
    return by


def _universe_ok(ticker: str, quote: dict, cfg) -> tuple[bool, float | None, str | None]:
    uni = cfg.universe
    prev = quote.get("prev_close")
    # Convert native prev_close to EUR for min-price check when needed
    from stocks.market import quote_to_eur_divisor

    div = quote_to_eur_divisor(ticker) or 1.0
    prev_eur = (float(prev) / div) if prev is not None else None
    if uni.min_prev_close is not None and (prev_eur is None or prev_eur < uni.min_prev_close):
        return False, None, "below_min_price"
    adv = adv20_eur(ticker)
    # YAML keeps min_adv20_usd key; for EU book it means EUR
    min_adv = uni.min_adv20_usd
    if min_adv is not None and (adv is None or adv < min_adv):
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
    # CEO/exec departure or >=3 negative analyst actions
    exec_hit = False
    neg_analyst = 0
    for it in news_items:
        blob = f"{it.get('title') or ''} {it.get('summary') or ''}"
        if re_search_exec(blob) or it.get("event_type") == "exec_departure":
            exec_hit = True
        if re_search_neg_analyst(blob):
            neg_analyst += 1
    if not exec_hit and neg_analyst < 3:
        return None
    return {
        "id": _id_for("mega_eu", ticker, utc_now_iso()),
        "type": "mega_cap_shock",
        "ticker": ticker,
        "gap_pct": gap_pct,
        "adv20_eur": adv,
        "adv20_usd": adv,  # dashboard field reuse
        "exec_hit": exec_hit,
        "neg_analyst_count": neg_analyst,
        "action": "alert_only",
        "session": berlin_market_session(),
        "as_of": utc_now_iso(),
        "region": "EU",
        "book_id": BOOK_ID,
        "note": mega.trigger,
    }


def re_search_exec(blob: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(CEO|Vorstandsvorsitzende[rn]?|Rücktritt|ruecktritt|resigns?|"
            r"stepping down|chief executive)\b",
            blob,
            re.I,
        )
    )


def re_search_neg_analyst(blob: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(downgrade|senkt (?:das )?kursziel|underweight|underperform|sell[- ]rated)\b",
            blob,
            re.I,
        )
    )


def detect_movers(
    tickers: list[str],
    *,
    news_by_ticker: dict[str, list[dict]],
    cfg,
    index_above: bool | None,
) -> tuple[list[dict], list[dict]]:
    cands: list[dict] = []
    alerts: list[dict] = []
    seen: set[str] = set()
    for ticker in tickers:
        ticker = normalize_eu_ticker(str(ticker)) or str(ticker).upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            quote = eu_session_quote(ticker)
        except Exception:
            continue
        gap = quote.get("gap_pct")
        last = quote.get("last")
        if gap is None or last is None:
            continue
        if abs(float(gap)) < MIN_GAP_ABS:
            continue
        rel = quote.get("rel_vol")
        has_fresh_news = bool(news_by_ticker.get(ticker))
        if rel is not None and rel < MIN_REL_VOL and not has_fresh_news:
            continue
        if rel is None and not has_fresh_news:
            continue
        ok, adv, _reason = _universe_ok(ticker, quote, cfg)
        if not ok:
            continue
        items = news_by_ticker.get(ticker) or []
        mega = _is_mega_cap_alert(
            ticker=ticker, gap_pct=float(gap), adv=adv, news_items=items, cfg=cfg
        )
        if mega:
            alerts.append(mega)
            continue

        if items:
            top = items[0]
            cat = classify_bigmove_eu_category(
                event_type=top.get("event_type"),
                eu_category=top.get("eu_category"),
                title=top.get("title") or "",
                summary=top.get("summary") or "",
                has_news=True,
            )
            headline = top.get("title") or ""
            news_id = top.get("id")
            news_source = top.get("source") or "eqs_google"
            filing_url = top.get("url") or ""
        else:
            cat = "no_news_found"
            headline = f"{ticker} move {gap:+.1f}% no news found"
            news_id = None
            news_source = "move_trigger"
            filing_url = ""

        if cat == "earnings_chase":
            continue

        cands.append(
            {
                "ticker": ticker,
                "name": ticker,
                "cal_date": now_berlin().date().isoformat(),
                "gap_pct": float(gap),
                "last": last,
                "prev_close": quote.get("prev_close"),
                "rel_vol": rel,
                "adv20_usd": adv,  # EUR ADV; field name shared with matcher
                "avg_dollar_volume": adv,
                "spy_above_ma50": index_above,  # STOXX/DAX regime reused by matcher
                "session": quote.get("session") or berlin_market_session(),
                "headline": headline[:200],
                "news_event_type": cat,
                "news_direction": "bearish" if float(gap) < 0 else "bullish",
                "news_side": None,
                "news_actionable": True,
                "news_source": news_source,
                "news_id": news_id,
                "filing_url": filing_url,
                "rule_tags": ["bigmove_eu", cat, BOOK_ID],
                "source": "bigmove_eu_move_trigger",
                "suggested_side": None,
                "suggested_hold": 5,
                "region": "EU",
                "as_of": utc_now_iso(),
            }
        )
    return cands, alerts


def merge_candidates(existing: list[dict], fresh: list[dict]) -> list[dict]:
    bigmove_tickers = {c.get("ticker") for c in fresh if c.get("ticker")}
    out = [
        c
        for c in existing
        if not (
            c.get("source") == "bigmove_eu_move_trigger"
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


def merge_news_items(existing: list[dict], fresh: list[dict], *, keep: int = 400) -> list[dict]:
    by_id = {it.get("id"): it for it in existing if it.get("id")}
    for it in fresh:
        iid = it.get("id")
        if iid:
            by_id[iid] = it
    items = list(by_id.values())
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:keep]


def collect_watchlist(news: dict, candidates: dict) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    allowed = {normalize_eu_ticker(t) or t for t in EU_UNIVERSE_SEED}

    def _add(t: str):
        nt = normalize_eu_ticker(str(t)) or str(t).upper().strip()
        # Only quote names in the curated liquid universe (drop junk like NEWS.DE)
        if nt and nt in allowed and nt not in seen:
            seen.add(nt)
            tickers.append(nt)

    for it in news.get("items") or []:
        for t in it.get("tickers") or []:
            _add(t)
    for c in candidates.get("candidates") or []:
        if c.get("source") == "bigmove_eu_move_trigger" and c.get("ticker"):
            _add(c["ticker"])
    # Always include a rotating slice of the seed universe so silent movers surface
    day_i = now_berlin().timetuple().tm_yday
    seed = EU_UNIVERSE_SEED
    start = (day_i * 7) % max(1, len(seed))
    for t in seed[start : start + 40] + seed[: max(0, 40 - (len(seed) - start))]:
        _add(t)
    return tickers[:90]


def scan_once() -> dict[str, Any]:
    ensure_dirs()
    cfg = load_strategy_config(BOOK_ID)
    index_above = stoxx_above_ma50()
    news = read_json(EU_NEWS_PATH, default={"items": [], "region": "EU"})
    shared_news = read_json(NEWS, default={"items": []})
    cands_doc = read_json(CANDIDATES, default={"candidates": []})
    alerts_doc = read_json(ALERTS_PATH, default={"alerts": [], "mode": "paper"})

    watch_seed = collect_watchlist(news, cands_doc)
    filings = fetch_eu_news(yahoo_tickers=watch_seed[:20], yahoo_limit=20)
    print(f"eu_news: {len(filings)}", flush=True)

    news_items = merge_news_items(list(news.get("items") or []), filings)
    news["items"] = news_items
    news["as_of"] = utc_now_iso()
    news["n_items"] = len(news_items)
    news["region"] = "EU"
    news["sources"] = sorted(
        {
            *(news.get("sources") or []),
            "eqs_google",
            "wso_nachrichten",
            "finanzen_net",
            "yahoo_eu",
        }
    )
    write_json(EU_NEWS_PATH, news)

    # Also merge into shared news feed for the site
    shared_items = merge_news_items(list(shared_news.get("items") or []), filings, keep=400)
    shared_news["items"] = shared_items
    shared_news["as_of"] = utc_now_iso()
    shared_news["n_items"] = len(shared_items)
    srcs = list(shared_news.get("sources") or [])
    for s in ("eqs_google", "wso_nachrichten", "finanzen_net", "yahoo_eu"):
        if s not in srcs:
            srcs.append(s)
    shared_news["sources"] = srcs
    write_json(NEWS, shared_news)

    news_by = _news_by_ticker(news)
    watch = collect_watchlist(news, cands_doc)
    movers, alerts = detect_movers(
        watch, news_by_ticker=news_by, cfg=cfg, index_above=index_above
    )
    merged = merge_candidates(list(cands_doc.get("candidates") or []), movers)
    cands_doc["candidates"] = merged
    cands_doc["as_of"] = utc_now_iso()
    cands_doc["stoxx_above_ma50"] = index_above
    cands_doc.setdefault("rules", {})
    cands_doc["rules"][BOOK_ID] = cfg.description or (
        "EU fade offering/PR/M&A gaps + selective gap-down longs; primary session; alert-only mega-caps."
    )
    write_json(CANDIDATES, cands_doc)

    alerts_doc["alerts"] = merge_alerts(list(alerts_doc.get("alerts") or []), alerts)
    alerts_doc["as_of"] = utc_now_iso()
    alerts_doc["mode"] = "paper"
    write_json(ALERTS_PATH, alerts_doc)

    return {
        "as_of": utc_now_iso(),
        "session": berlin_market_session(),
        "n_filings": len(filings),
        "n_watch": len(watch),
        "n_movers": len(movers),
        "n_alerts": len(alerts),
        "movers": [m["ticker"] for m in movers],
        "alerts": [a["ticker"] for a in alerts],
        "index_above": index_above,
        "book_id": BOOK_ID,
    }


def run_loop(
    *,
    duration_minutes: float = 22.0,
    poll_seconds: float = 150.0,
    engine_each_poll: bool = True,
) -> dict[str, Any]:
    if is_eu_holiday() or (not is_weekday(now_berlin().date()) and berlin_market_session() == "closed"):
        print("EU holiday/weekend/closed — single scan only", flush=True)
        return scan_once()

    started = time.time()
    cycles = []
    while True:
        sess = berlin_market_session()
        print(f"bigmove_eu poll session={sess} t+{time.time()-started:.0f}s", flush=True)
        try:
            result = scan_once()
            cycles.append(result)
            print(json_dump(result), flush=True)
        except Exception as exc:
            print(f"bigmove_eu scan error: {exc}", flush=True)
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
                print(f"bigmove_eu engine error: {exc}", flush=True)

        elapsed = time.time() - started
        if elapsed + poll_seconds > duration_minutes * 60:
            break
        # Stop after primary session (17:30 Berlin)
        if berlin_market_session() == "closed" and now_berlin().hour >= 17:
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
    ap = argparse.ArgumentParser(description="bigmove_eu_v1 paper detector")
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

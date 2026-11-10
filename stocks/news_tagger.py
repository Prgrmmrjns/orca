"""Keyword tagging for US stock news / 8-K headlines → event type + direction."""
from __future__ import annotations

import re
from typing import Any

# (event_type, direction, patterns) — first match wins within each polarity group.
# direction: "bullish" | "bearish" | "neutral"
_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    # Bearish first when both could match (fraud > earnings).
    (
        "fraud_probe",
        "bearish",
        re.compile(
            r"\b(accounting (?:fraud|irregularit(?:y|ies)|restatement)|sec (?:probe|investigation|subpoena)|"
            r"doj (?:probe|investigation)|department of justice|indictment|whistleblower|"
            r"securities fraud|forensic accounting|fbi (?:raid|probe|investigation))\b",
            re.I,
        ),
    ),
    (
        "short_seller",
        "bearish",
        re.compile(
            r"\b(short[- ]sell(?:er|ing)? (?:report|research)|hindenburg|citron research|"
            r"muddy waters|spruce point|fuzzy panda|going concern (?:report)?)\b",
            re.I,
        ),
    ),
    (
        "dilution",
        "bearish",
        re.compile(
            r"\b(public offering|follow[- ]on offering|atm offering|at[- ]the[- ]market|"
            r"dilutive|registered direct|convertible notes? offering|equity offering|"
            r"prices (?:an? )?(?:upsized )?(?:public )?offering)\b",
            re.I,
        ),
    ),
    (
        "guidance_cut",
        "bearish",
        re.compile(
            r"\b(cuts?|lowers?|reduces?|withdraws?|suspends?|slashes?)\b.{0,40}\b(guidance|outlook|forecast)\b|"
            r"\b(guidance|outlook|forecast)\b.{0,40}\b(cut|lowered|reduced|withdrawn|suspended|slashed)\b|"
            r"\b(below (?:prior )?(?:guidance|expectations)|misses? (?:estimates?|expectations?))\b",
            re.I,
        ),
    ),
    (
        "lawsuit",
        "bearish",
        re.compile(
            r"\b(lawsuit|sued by|files? suit|litigation|complaint filed|securities class)\b",
            re.I,
        ),
    ),
    (
        "exec_departure",
        "bearish",
        re.compile(
            r"\b(ceo|cfo|coo|chief (?:executive|financial|operating) officer)\b.{0,40}\b("
            r"resigns?|steps? down|depart(?:s|ure)|terminated|ousted|leaves? (?:the )?(?:company|role))\b|"
            r"\b(resigns?|steps? down)\b.{0,40}\b(ceo|cfo)\b",
            re.I,
        ),
    ),
    # Bullish
    (
        "acquisition_target",
        "bullish",
        re.compile(
            r"\b(to be acquired|agree(?:s|d)? to be acquired|acquisition of .{0,40} by|"
            r"buyout|take[- ]private|tender offer for|merger agreement|"
            r"definitive agreement to (?:be )?acquir)\b",
            re.I,
        ),
    ),
    (
        "mna_deal",
        "bullish",
        re.compile(
            r"\b(acquires?|to acquire|enters? into (?:a )?definitive agreement|"
            r"strategic (?:partnership|alliance)|joint venture)\b",
            re.I,
        ),
    ),
    (
        "big_contract",
        "bullish",
        re.compile(
            r"\b(awarded|wins?|secures?|signs?)\b.{0,50}\b(contract|order|deal|agreement)\b|"
            r"\b(multi[- ](?:year|billion)|\$[0-9.]+\s*(?:billion|million))\b.{0,40}\b(contract|order|deal)\b",
            re.I,
        ),
    ),
    (
        "fda_approval",
        "bullish",
        re.compile(
            r"\b(fda)\b.{0,40}\b(approv(?:es|al)|clear(?:s|ance)|accepts? .{0,20}application)\b|"
            r"\b(approv(?:es|al)|clear(?:s|ance))\b.{0,40}\b(fda|bla|nda|510\(?k\)?)\b|"
            r"\b(pdufa|breakthrough (?:therapy|device))\b",
            re.I,
        ),
    ),
    (
        "guidance_raise",
        "bullish",
        re.compile(
            r"\b(raises?|increases?|lifts?|boosts?)\b.{0,40}\b(guidance|outlook|forecast)\b|"
            r"\b(guidance|outlook|forecast)\b.{0,40}\b(rais(?:ed|es)|increas(?:ed|es)|lifted|boosted)\b|"
            r"\b(raises? full[- ]year|above (?:prior )?guidance)\b",
            re.I,
        ),
    ),
    (
        "earnings_beat",
        "bullish",
        re.compile(
            r"\b(beats? (?:estimates?|expectations?|consensus)|tops? estimates?|"
            r"better[- ]than[- ]expected (?:earnings|results|eps)|record (?:revenue|earnings))\b",
            re.I,
        ),
    ),
    (
        "earnings",
        "neutral",
        re.compile(
            r"\b(reports? (?:first|second|third|fourth|first-|second-|third-|fourth-)? ?quarter|"
            r"q[1-4] results|earnings (?:release|results|call)|fiscal .{0,12} results|"
            r"pre[- ]announc(?:es|ement))\b",
            re.I,
        ),
    ),
]

# Event types the news_event book will trade
ACTIONABLE_LONG = {"acquisition_target", "big_contract", "fda_approval", "guidance_raise"}
ACTIONABLE_SHORT = {"fraud_probe", "guidance_cut", "short_seller", "dilution"}

TICKER_RE = re.compile(r"\b([A-Z]{1,5})(?:\s*[:\-–]\s|\s+Inc|\s+Corp|\s+Ltd|\s+Co\b)")
TICKER_PAREN_RE = re.compile(r"\(([A-Z]{1,5})\)")
NASDAQ_TICKER_RE = re.compile(r"(?:nasdaq|nyse|amex)[\s:]+([A-Z]{1,5})\b", re.I)

# Common false positives in caps extraction
_STOP = {
    "CEO", "CFO", "COO", "SEC", "FDA", "DOJ", "USD", "EPS", "GAAP", "IPO", "ATM", "FDA",
    "THE", "AND", "FOR", "NEW", "INC", "CORP", "LTD", "LLC", "PLC", "USA", "PDF", "HTML",
    "HTTP", "HTTPS", "RSS", "API", "Q1", "Q2", "Q3", "Q4", "FY", "PR", "NYSE", "PRESS",
    "US", "UK", "EU", "ETF", "AI", "IPO", "CEO", "CFO", "AM", "PM", "EST", "EDT", "UTC",
    "FORM", "ITEM", "FILE", "FILER",
}


def tag_text(title: str, summary: str = "") -> dict[str, Any]:
    text = f"{title or ''}. {summary or ''}"
    event_type = "other"
    direction = "neutral"
    for etype, direc, pat in _RULES:
        if pat.search(text):
            event_type = etype
            direction = direc
            break
    actionable = None
    news_side = None
    if event_type in ACTIONABLE_LONG:
        actionable = True
        news_side = "long"
        direction = "bullish"
    elif event_type in ACTIONABLE_SHORT:
        actionable = True
        news_side = "short"
        direction = "bearish"
    return {
        "event_type": event_type,
        "direction": direction,
        "news_side": news_side,
        "actionable": bool(actionable),
    }


def extract_tickers(title: str, summary: str = "", extra: str = "") -> list[str]:
    blob = f"{title or ''} {summary or ''} {extra or ''}"
    found: list[str] = []
    for rx in (NASDAQ_TICKER_RE, TICKER_PAREN_RE):
        for m in rx.finditer(blob):
            t = m.group(1).upper()
            if t not in _STOP and t not in found:
                found.append(t)
    # light title scan for "TICK: headline" patterns
    m = re.match(r"^([A-Z]{1,5})\b", (title or "").strip())
    if m:
        t = m.group(1)
        if t not in _STOP and t not in found:
            found.append(t)
    return found[:4]

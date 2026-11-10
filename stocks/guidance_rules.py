#!/usr/bin/env python3
"""Keyword rules for earnings-press-release guidance (from backtest samples)."""
from __future__ import annotations

import re

RAISE_RE = re.compile(
    r"(?:rais(?:e|ed|es|ing)|increas(?:e|ed|es|ing)|improv(?:e|ed|es|ing)|lift(?:s|ed|ing)?)\s+"
    r".{0,50}?\b(guidance|outlook|forecast)\b"
    r"|"
    r"\b(guidance|outlook|forecast)\s+.{0,50}?\b(?:rais(?:e|ed|es|ing)|increas(?:e|ed|es|ing)|improv(?:e|ed|es|ing))\b"
    r"|"
    r"\bguidance increase\b"
    r"|"
    r"\bincreased full[- ]year (?:outlook|guidance|forecast)"
    r"|"
    r"\braises full[- ]year .{0,20}guidance",
    re.I | re.S,
)

CUT_RE = re.compile(
    r"(?:lower(?:s|ed|ing)?|cut(?:s|ting)?|reduc(?:e|ed|es|ing)|trim(?:s|med|ming)?|"
    r"slash(?:ed|es|ing)?|revis(?:e|ed|es|ing)\s+down(?:ward)?)\s+"
    r".{0,50}?\b(guidance|outlook|forecast)\b"
    r"|"
    r"\b(guidance|outlook|forecast)\s+.{0,50}?\b(?:lower(?:s|ed|ing)?|cut(?:s|ting)?|reduc(?:e|ed|es|ing)|downward)\b"
    r"|"
    r"\bwithdraw(?:s|n|ing)?\s+(?:its\s+)?(?:full[- ]year\s+)?guidance\b"
    r"|"
    r"\bsuspend(?:s|ed|ing)?\s+(?:its\s+)?guidance\b",
    re.I | re.S,
)

REAFFIRM_RE = re.compile(
    r"(?:reaffirm(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|maintain(?:s|ed|ing)?|unchanged)\s+"
    r".{0,50}?\b(guidance|outlook|forecast)\b"
    r"|"
    r"\b(guidance|outlook|forecast)\s+.{0,40}?(?:reaffirm|reiterat|unchanged|maintain)",
    re.I | re.S,
)

EXCLUDE_RE = re.compile(
    r"(capital expenditure|\bcapex\b|share repurchase|quarterly dividend|cash dividend|"
    r"interest rate|outstanding debt|weighted average interest)",
    re.I,
)

EARNINGS_WORD = re.compile(r"\b(eps|earnings|revenue|sales|ebitda|profit|income)\b", re.I)

HEADLINE_RE = re.compile(
    r"([A-Z][A-Za-z0-9&.,'’ \-]{2,80}(?:Reports|Announces|Provides|Posts)[^.]{5,160})",
)


def _keep(text: str, match: re.Match) -> bool:
    window = text[max(0, match.start() - 60) : min(len(text), match.end() + 60)]
    if EXCLUDE_RE.search(window) and not EARNINGS_WORD.search(window):
        return False
    return True


def _hits(regex: re.Pattern, text: str) -> list[str]:
    out = []
    for m in regex.finditer(text):
        if _keep(text, m):
            frag = " ".join(m.group(0).split())
            if frag not in out:
                out.append(frag[:220])
    return out


def extract_headline(text: str) -> str:
    chunk = text[:2000]
    chunk = re.sub(r"EX-99[^\n]{0,80}", " ", chunk, flags=re.I)
    chunk = re.sub(r"Document Exhibit 99\.1", " ", chunk, flags=re.I)
    m = HEADLINE_RE.search(chunk)
    if m:
        return " ".join(m.group(1).split())[:220]
    for sent in re.split(r"(?<=[\.!?])\s+", chunk):
        s = " ".join(sent.split())
        if 40 <= len(s) <= 220 and re.search(r"[A-Za-z]{4}", s):
            if not re.match(r"(Exhibit|UNITED STATES|WASHINGTON|FORM 8-K|Contact:)", s, re.I):
                return s
    return ""


def label_text(text: str) -> dict:
    if not text or len(text) < 80:
        return {
            "guidance": "unknown",
            "tone": "unknown",
            "headline": "",
            "raise_hits": "",
            "cut_hits": "",
            "reaffirm_hits": "",
        }
    raises = _hits(RAISE_RE, text)
    cuts = _hits(CUT_RE, text)
    reaff = _hits(REAFFIRM_RE, text)
    if raises and cuts:
        guidance = "mixed"
    elif raises:
        guidance = "raised"
    elif cuts:
        guidance = "cut"
    elif reaff:
        guidance = "reaffirm"
    else:
        guidance = "none"
    pos = len(re.findall(r"\b(record|strong|beat|ahead of|outperform(?:ed|s)?)\b", text, re.I))
    neg = len(re.findall(r"\b(impairment|challenging|weak(?:ness|er)?|miss(?:ed)?|loss from operations)\b", text, re.I))
    if guidance == "raised" or pos >= neg + 2:
        tone = "positive"
    elif guidance == "cut" or neg >= pos + 2:
        tone = "negative"
    else:
        tone = "neutral"
    if guidance == "mixed":
        tone = "mixed"
    return {
        "guidance": guidance,
        "tone": tone,
        "headline": extract_headline(text),
        "raise_hits": " | ".join(raises[:4]),
        "cut_hits": " | ".join(cuts[:4]),
        "reaffirm_hits": " | ".join(reaff[:4]),
    }

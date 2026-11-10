"""Parallel paper strategy books for the earnings forward test.

Each book has its own €1,000 ledger and decisions file. Candidates are shared.
Rule books (2–5) auto-decide inside the engine; `agent` is discretionary via stocks.decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BookSpec:
    id: str
    title: str
    kind: str  # "agent" | "rule"
    description: str
    size: float
    hold_days: int
    max_open: int
    stop_loss: float | None = None
    take_profit: float | None = None
    side: str = "long"  # "long" | "short" | "dynamic" (read candidate news_side/suggested_side)
    prefer_open: bool = True  # False → fill at session/last during RTH (news mid-day)
    matcher: Callable[[dict[str, Any], bool | None], bool] | None = None
    started: str = "2026-11-10"


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def match_bigbeat_gap(c: dict[str, Any], spy_above: bool | None = None) -> bool:
    """EPS surprise >15%, gap up >5%, SPY above 50-day SMA."""
    surprise = _f(c.get("surprise_pct"))
    gap = _f(c.get("gap_pct"))
    spy = c.get("spy_above_ma50") if c.get("spy_above_ma50") is not None else spy_above
    if surprise is None or gap is None or spy is not True:
        return False
    return surprise > 15 and gap > 5


def match_guidance_beat(c: dict[str, Any], spy_above: bool | None = None) -> bool:
    """Raised guidance + EPS beat >8%; release must be out before entry open.

    Release-out heuristic: guidance label is `raised` from 8-K/press-release keywords
    (see stocks/guidance_rules.py). Timing BMO/AMC/during or a filing URL means the
    print is already public before/at the first tradable open we buy.
    """
    del spy_above  # unused — no regime filter
    surprise = _f(c.get("surprise_pct"))
    guidance = (c.get("guidance") or "").lower()
    if surprise is None or surprise <= 8 or guidance != "raised":
        return False
    timing = (c.get("timing") or "").upper()
    has_filing = bool(c.get("filing_url") or c.get("exhibit_url") or c.get("raise_hits"))
    # Require evidence the release is out (not merely an upcoming calendar row).
    if not has_filing and timing not in ("BMO", "AMC", "DURING"):
        return False
    return True


def match_beat5_raise(c: dict[str, Any], spy_above: bool | None = None) -> bool:
    """EPS beat >5% + raised outlook; no SPY filter."""
    del spy_above
    surprise = _f(c.get("surprise_pct"))
    guidance = (c.get("guidance") or "").lower()
    return surprise is not None and surprise > 5 and guidance == "raised"


def match_miss_short(c: dict[str, Any], spy_above: bool | None = None) -> bool:
    """EPS miss worse than -10% AND gap down >5% at first open."""
    del spy_above
    surprise = _f(c.get("surprise_pct"))
    gap = _f(c.get("gap_pct"))
    if surprise is None or gap is None:
        return False
    return surprise < -10 and gap < -5


def match_news_event(c: dict[str, Any], spy_above: bool | None = None) -> bool:
    """Actionable news: bullish → long, bearish → short (see news_tagger ACTIONABLE_*)."""
    del spy_above
    if not c.get("news_actionable"):
        return False
    side = (c.get("news_side") or c.get("suggested_side") or "").lower()
    et = (c.get("news_event_type") or "").lower()
    if side not in ("long", "short"):
        return False
    # Require a concrete event type we trust enough to auto-trade
    if side == "long" and et not in {
        "acquisition_target",
        "big_contract",
        "fda_approval",
        "guidance_raise",
    }:
        return False
    if side == "short" and et not in {
        "fraud_probe",
        "guidance_cut",
        "short_seller",
        "dilution",
    }:
        return False
    return bool(c.get("ticker"))


BOOKS: list[BookSpec] = [
    BookSpec(
        id="agent",
        title="Agent (Chewbacca)",
        kind="agent",
        description=(
            "Discretionary agentic book. Cursor agent decides via stocks.decide from "
            "shared candidates. Baseline — not auto-traded by the engine."
        ),
        size=0.08,
        hold_days=5,
        max_open=8,
        matcher=None,
    ),
    BookSpec(
        id="bigbeat_gap",
        title="Big beat + gap",
        kind="rule",
        description=(
            "Long when EPS surprise >15%, first open gaps up >5% vs prior close, "
            "and SPY is above its 50-day SMA. 10% equity, max 8, hold 3 days, no stop."
        ),
        size=0.10,
        hold_days=3,
        max_open=8,
        side="long",
        matcher=match_bigbeat_gap,
    ),
    BookSpec(
        id="guidance_beat",
        title="Guidance beat",
        kind="rule",
        description=(
            "Long when 8-K/press release shows raised guidance AND EPS beat >8%, "
            "with the release out before the entry open. 10% equity, max 8, hold 5, no stop."
        ),
        size=0.10,
        hold_days=5,
        max_open=8,
        side="long",
        matcher=match_guidance_beat,
    ),
    BookSpec(
        id="beat5_raise",
        title="Beat 5% + raise",
        kind="rule",
        description=(
            "Long on EPS beat >5% plus raised outlook. No SPY regime filter. "
            "10% equity, max 8, hold 5 days, no stop."
        ),
        size=0.10,
        hold_days=5,
        max_open=8,
        side="long",
        matcher=match_beat5_raise,
    ),
    BookSpec(
        id="miss_short",
        title="Miss short (experimental)",
        kind="rule",
        description=(
            "Short when EPS misses by more than 10% AND the stock gaps down >5% "
            "at the first open. 5% equity, max 4, hold 2 days, hard 8% stop."
        ),
        size=0.05,
        hold_days=2,
        max_open=4,
        stop_loss=0.08,
        side="short",
        matcher=match_miss_short,
    ),
    BookSpec(
        id="news_event",
        title="News event",
        kind="rule",
        description=(
            "Long on clearly bullish news (acquisition target, big contract, FDA approval, "
            "guidance raise); short on clearly bearish (fraud/probe, guidance cut, "
            "short-seller report, dilutive offering). 5% equity, max 6, hold 2 days, "
            "hard 8% stop both sides. Entry at next tradable price. Paper shorts — no borrow model."
        ),
        size=0.05,
        hold_days=2,
        max_open=6,
        stop_loss=0.08,
        side="dynamic",
        prefer_open=False,
        matcher=match_news_event,
        started="2026-11-10",
    ),
]

BOOK_BY_ID: dict[str, BookSpec] = {b.id: b for b in BOOKS}
BOOK_IDS = [b.id for b in BOOKS]
AGENT_BOOK_ID = "agent"
FORWARD_TEST_START = "2026-11-10"
START_CAPITAL_EUR = 1000.0


def book_rules_public() -> dict[str, str]:
    return {b.id: b.description for b in BOOKS}


def matches(book: BookSpec, candidate: dict[str, Any], spy_above: bool | None = None) -> bool:
    if book.kind != "rule" or book.matcher is None:
        return False
    return bool(book.matcher(candidate, spy_above))

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
    """Config-driven news matcher — see config/strategies/news_event.yaml.

    Reloads YAML every call so parameter edits apply on the next engine tick.
    Defaults reproduce the 2026-11-10 rules (long acquisition/contract/FDA/raise;
    short fraud/cut/short-report/dilution; exclude shareholder_alert).
    """
    del spy_above
    from stocks.strategy_config import load_news_event_config, match_candidate_to_news_config

    return match_candidate_to_news_config(c, load_news_event_config()) is not None


def make_config_matcher(book_id: str) -> Callable[[dict[str, Any], bool | None], bool]:
    """Matcher that reloads config/strategies/<book_id>.yaml every call."""

    def _match(c: dict[str, Any], spy_above: bool | None = None) -> bool:
        del spy_above
        from stocks.strategy_config import load_strategy_config, match_candidate_to_news_config

        try:
            cfg = load_strategy_config(book_id)
        except Exception:
            return False
        return match_candidate_to_news_config(c, cfg) is not None

    _match.__name__ = f"match_{book_id}"
    return _match


def _tuned_book(
    book_id: str,
    title: str,
    description: str,
    *,
    size: float = 0.05,
    hold_days: int = 2,
    max_open: int = 8,
    side: str = "long",
    prefer_open: bool = True,
    stop_loss: float | None = None,
) -> BookSpec:
    return BookSpec(
        id=book_id,
        title=title,
        kind="rule",
        description=description,
        size=size,
        hold_days=hold_days,
        max_open=max_open,
        stop_loss=stop_loss,
        side=side,
        prefer_open=prefer_open,
        matcher=make_config_matcher(book_id),
        started="2026-11-10",
    )


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
            "Config-driven news book (config/strategies/news_event.yaml). Defaults: long "
            "acquisition_target / big_contract / fda_approval / guidance_raise; short "
            "fraud_probe / guidance_cut / short_report / dilution; exclude shareholder_alert; "
            "5% equity, max 6, hold 2 days, 8% stop, entry next_bar. Edit YAML to retune — "
            "engine reloads on next tick. Paper shorts — no borrow model."
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
    # --- Tuned news books (box backtest → live paper from 2026-11-10) ---
    # Existing six books above stay unchanged through the Nov 17 review.
    _tuned_book(
        "guidance_raise_tuned",
        "Guidance raise tuned",
        "Config: long guidance_raise only · 5% · max 15 · hold 5d · no stop · next_open "
        "(config/strategies/guidance_raise_tuned.yaml). Box test +11.2%.",
        hold_days=5,
        max_open=15,
    ),
    _tuned_book(
        "dilution_shorts_tuned",
        "Dilution shorts tuned",
        "Config: short dilution · 5% · max 8 · hold 3d · no stop · next_open "
        "(config/strategies/dilution_shorts_tuned.yaml). Paper shorts. Box test +10.0%.",
        hold_days=3,
        max_open=8,
        side="short",
    ),
    _tuned_book(
        "quality_longs_tuned",
        "Quality longs tuned",
        "Config: long guidance_raise / acquisition_target / big_contract · 5% · max 15 · "
        "hold 5d · no stop · next_open (config/strategies/quality_longs_tuned.yaml). Box test +4.8%.",
        hold_days=5,
        max_open=15,
    ),
    _tuned_book(
        "guidance_raise_score",
        "Guidance raise score",
        "Config: long guidance_raise · score-scaled size (|EPS| or |gap| vs 10%, 0.25x–2x) · "
        "max 8 · hold 2d · no stop · next_open (config/strategies/guidance_raise_score.yaml). Box test +4.5%.",
        hold_days=2,
        max_open=8,
    ),
    _tuned_book(
        "gap_filtered_guidance",
        "Gap-filtered guidance",
        "Config: long guidance_raise with gap ≥5% · 5% · max 8 · hold 5d · no stop · next_open "
        "(config/strategies/gap_filtered_guidance.yaml). Box test +4.4%.",
        hold_days=5,
        max_open=8,
    ),
    _tuned_book(
        "wide_capacity",
        "Wide capacity",
        "Config: quality longs · 5% · max 15 · hold 2d · no stop · next_open "
        "(config/strategies/wide_capacity.yaml). Box test +2.1%.",
        hold_days=2,
        max_open=15,
    ),
    # Extended-hours gap-fade book (paper, start 2026-11-13). Existing 12 books unchanged.
    BookSpec(
        id="bigmove_unified_v1",
        title="Bigmove unified v1",
        kind="rule",
        description=(
            "Fade offering / PR-pop / M&A gap-downs and selective gap-down fades across "
            "pre/regular/after hours. Alert-only mega-cap shocks. No earnings/guidance chase. "
            "5% · max 20 · hold 5d · 0.5%/fill + 0.5% ext slippage · "
            "config/strategies/bigmove_unified_v1.yaml."
        ),
        size=0.05,
        hold_days=5,
        max_open=20,
        stop_loss=None,
        side="dynamic",
        prefer_open=False,
        matcher=make_config_matcher("bigmove_unified_v1"),
        started="2026-11-13",
    ),
    # European primary-session gap-fade book (paper, start 2026-11-13). Existing books unchanged.
    BookSpec(
        id="bigmove_eu_v1",
        title="Bigmove EU v1",
        kind="rule",
        description=(
            "EU liquid large/mid caps: fade ≥10% gap-downs (earnings / other-PR / no-news) "
            "on Xetra/Euronext primary hours (09:00–17:30 Berlin). Gap-up shorts dropped "
            "after EU event study. Alert-only mega-cap shocks. No earnings/guidance chase. "
            "5% · max 20 · hold 5d · 0.5%/fill (+0.5% outside primary) · "
            "config/strategies/bigmove_eu_v1.yaml."
        ),
        size=0.05,
        hold_days=5,
        max_open=20,
        stop_loss=None,
        side="dynamic",
        prefer_open=False,
        matcher=make_config_matcher("bigmove_eu_v1"),
        started="2026-11-13",
    ),
]

BOOK_BY_ID: dict[str, BookSpec] = {b.id: b for b in BOOKS}
BOOK_IDS = [b.id for b in BOOKS]
AGENT_BOOK_ID = "agent"
FORWARD_TEST_START = "2026-11-10"
START_CAPITAL_EUR = 1000.0
LEGACY_FORWARD_BOOK_IDS = [
    "agent",
    "bigbeat_gap",
    "guidance_beat",
    "beat5_raise",
    "miss_short",
    "news_event",
]


def book_rules_public() -> dict[str, str]:
    return {b.id: b.description for b in BOOKS}


def matches(book: BookSpec, candidate: dict[str, Any], spy_above: bool | None = None) -> bool:
    if book.kind != "rule" or book.matcher is None:
        return False
    return bool(book.matcher(candidate, spy_above))

"""Candidate rule tags aligned with the parallel paper books."""
from __future__ import annotations

from typing import Any

from stocks.books import (
    match_beat5_raise,
    match_bigbeat_gap,
    match_guidance_beat,
    match_miss_short,
)


def tag_candidate(c: dict[str, Any], spy_above: bool | None) -> list[str]:
    """Attach rule tags used by the deciding agent and the dashboard.

    Tags mirror the automated books (except `agent`, which is discretionary).
    """
    tags: list[str] = []
    if match_bigbeat_gap(c, spy_above):
        tags.append("bigbeat_gap")
    if match_guidance_beat(c, spy_above):
        tags.append("guidance_beat")
    if match_beat5_raise(c, spy_above):
        tags.append("beat5_raise")
    if match_miss_short(c, spy_above):
        tags.append("miss_short")
    return tags


def suggested_hold(tags: list[str]) -> int:
    if "bigbeat_gap" in tags and "guidance_beat" not in tags and "beat5_raise" not in tags:
        return 3
    if "miss_short" in tags and not any(t != "miss_short" for t in tags):
        return 2
    if tags:
        return 5
    return 5


def suggested_side(tags: list[str]) -> str | None:
    if "news_short" in tags:
        return "short"
    if "news_long" in tags:
        return "long"
    if "miss_short" in tags and not any(t in tags for t in ("bigbeat_gap", "guidance_beat", "beat5_raise")):
        return "short"
    if any(t in tags for t in ("bigbeat_gap", "guidance_beat", "beat5_raise")):
        return "long"
    return None

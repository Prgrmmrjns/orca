"""Default rule tags from the earnings backtest findings."""
from __future__ import annotations

from typing import Any


def tag_candidate(c: dict[str, Any], spy_above: bool | None) -> list[str]:
    """Attach rule tags used by the deciding agent.

    Best: long when EPS beat >5% AND 8-K raises outlook, hold 5.
    Safer: SPY above 50-day, EPS beat >15%, gap up >5%, hold 3.
    Gap-and-go (active alternative): beat >5%, gap >5%, hold 5.
    Short (weak / small size only): strong miss + cut guidance.
    """
    tags: list[str] = []
    surprise = c.get("surprise_pct")
    gap = c.get("gap_pct")
    guidance = (c.get("guidance") or "").lower()

    if surprise is None:
        return tags

    # longs
    if surprise > 5 and guidance == "raised":
        tags.append("best_long_guidance")  # hold 5
    if spy_above and surprise > 15 and gap is not None and gap > 5:
        tags.append("safer_long")  # hold 3
    if surprise > 5 and gap is not None and gap > 5:
        tags.append("gap_and_go_long")  # hold 5, active alternative

    # shorts — only strong miss + cut
    if surprise < -10 and guidance == "cut":
        tags.append("short_cut_guidance")  # small size only

    return tags


def suggested_hold(tags: list[str]) -> int:
    if "safer_long" in tags and "best_long_guidance" not in tags and "gap_and_go_long" not in tags:
        return 3
    if tags:
        return 5
    return 5


def suggested_side(tags: list[str]) -> str | None:
    if "short_cut_guidance" in tags and not any(t.endswith("long") or "long" in t for t in tags):
        return "short"
    if any(t in tags for t in ("best_long_guidance", "safer_long", "gap_and_go_long")):
        return "long"
    return None

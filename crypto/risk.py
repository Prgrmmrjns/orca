"""Paper-only guard and downside news caps.

Caps from strategy/news_overrides.json may only reduce a weight. They never
add exposure.
"""

from __future__ import annotations

import os


class PaperOnlyError(RuntimeError):
    """Raised when a caller asks the crypto engine to leave paper mode."""


def assert_paper(mode: str | None, *, source: str = "mode") -> None:
    if mode != "paper":
        raise PaperOnlyError(f"refusing {source}={mode!r}; crypto engine is paper only")


def assert_process_is_paper() -> None:
    env = os.environ.get("ORCA_MODE")
    if env is not None and env != "paper":
        raise PaperOnlyError(f"refusing ORCA_MODE={env!r}; crypto engine is paper only")


def apply_downside_caps(weights: dict[str, float], overrides: dict | None) -> dict[str, float]:
    """Lower weights that exceed an active cap. Other assets are unchanged."""
    out = {k: float(v) for k, v in weights.items()}
    for asset, rule in (overrides or {}).items():
        if asset not in out:
            continue
        cap = float(rule.get("max_weight", 0.0))
        if out[asset] > cap:
            out[asset] = cap
    gross = sum(v for v in out.values() if v > 0)
    if gross > 1.0:
        out = {k: v / gross for k, v in out.items()}
    return {k: round(v, 6) for k, v in out.items()}

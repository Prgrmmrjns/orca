"""News-filtered wrappers around rule strategies (paper only).

Combine SMA / RSI / MACD signals with news features that must already be
present on the OHLCV frame (see orca.news.attach_news_features). Filters
never look ahead: they read the same bar's news_* columns, which were built
from articles published strictly before that bar.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from orca.strategy import generate_signals


def _require_news(df: pd.DataFrame) -> None:
    needed = {"news_sentiment", "news_count", "news_mention_intensity"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing news feature columns {sorted(missing)}. "
            "Call attach_news_features() before news strategies."
        )


def apply_news_long_filter(
    signals: pd.DataFrame,
    *,
    sentiment_min: float = 0.0,
    min_count: float = 0.0,
    min_mentions: float = 0.0,
) -> pd.DataFrame:
    """Keep sell/exit signals; only allow long (+1) when news passes thresholds.

    If a long signal fails the news gate, it becomes 0 (hold / skip entry).
    """
    _require_news(signals)
    out = signals.copy()
    ok = (
        (out["news_sentiment"] >= sentiment_min)
        & (out["news_count"] >= min_count)
        & (out["news_mention_intensity"] >= min_mentions)
    )
    long_mask = out["signal"] == 1
    out.loc[long_mask & ~ok, "signal"] = 0
    return out


def apply_news_sentiment_sizing_proxy(
    signals: pd.DataFrame,
    *,
    sentiment_min: float = -0.05,
    weak_below: float = 0.05,
) -> pd.DataFrame:
    """Filter longs: require sentiment_min; skip weak positive zone optionally.

    This is still a binary signal (portfolio uses fixed position_size). Weak
    sentiment (between sentiment_min and weak_below) skips entry — acts as a
    size/filter proxy without changing the portfolio engine.
    """
    _require_news(signals)
    out = signals.copy()
    # Require at least sentiment_min; additionally require >= weak_below to enter
    ok = out["news_sentiment"] >= max(sentiment_min, weak_below)
    long_mask = out["signal"] == 1
    out.loc[long_mask & ~ok, "signal"] = 0
    return out


def generate_news_signals(
    df: pd.DataFrame,
    strategy_cfg: dict[str, Any],
) -> pd.DataFrame:
    """Generate base rule signals then apply news filter from strategy_cfg.

    strategy_cfg keys:
      name: base strategy name (sma_crossover, rsi, macd, ...)
      news_filter: "long_gate" | "sentiment_size" | "none"
      news_sentiment_min: float
      news_min_count: float (7d article count)
      news_min_mentions: float
      news_weak_below: float (for sentiment_size)
    """
    base_cfg = {k: v for k, v in strategy_cfg.items() if not k.startswith("news_")}
    # name may be like "sma_crossover_news_gate" — strip suffix for base
    name = (base_cfg.get("name") or "sma_crossover").lower()
    for suffix in ("_news_gate", "_news_size", "_news_strict"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    base_cfg["name"] = name

    out = generate_signals(df, base_cfg)
    # Carry news columns through
    for col in (
        "news_count",
        "news_sentiment",
        "news_sentiment_kw",
        "news_sentiment_vader",
        "news_mention_intensity",
    ):
        if col in df.columns and col not in out.columns:
            out[col] = df[col]

    filt = (strategy_cfg.get("news_filter") or "long_gate").lower()
    if filt in ("none", "off", "baseline"):
        return out
    if filt in ("long_gate", "gate"):
        return apply_news_long_filter(
            out,
            sentiment_min=float(strategy_cfg.get("news_sentiment_min", 0.0)),
            min_count=float(strategy_cfg.get("news_min_count", 0.0)),
            min_mentions=float(strategy_cfg.get("news_min_mentions", 0.0)),
        )
    if filt in ("sentiment_size", "size", "strict"):
        return apply_news_sentiment_sizing_proxy(
            out,
            sentiment_min=float(strategy_cfg.get("news_sentiment_min", -0.05)),
            weak_below=float(strategy_cfg.get("news_weak_below", 0.05)),
        )
    raise ValueError(f"Unknown news_filter: {filt!r}")


def news_strategy_configs() -> list[dict[str, Any]]:
    """Baseline + news-filtered variants for the comparison harness."""
    configs: list[dict[str, Any]] = []

    bases = [
        {"name": "sma_crossover", "fast_period": 10, "slow_period": 30},
        {
            "name": "rsi_mean_reversion",
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
        },
        {
            "name": "macd",
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
        },
    ]

    for base in bases:
        # Baseline (no news filter)
        configs.append({**base, "news_filter": "none", "label": f"{base['name']}_baseline"})
        # Gate: only long when 7d sentiment >= 0
        configs.append(
            {
                **base,
                "news_filter": "long_gate",
                "news_sentiment_min": 0.0,
                "news_min_count": 1.0,
                "label": f"{base['name']}_news_gate",
            }
        )
        # Stricter gate: sentiment >= 0.05 and some coverage
        configs.append(
            {
                **base,
                "news_filter": "long_gate",
                "news_sentiment_min": 0.05,
                "news_min_count": 3.0,
                "label": f"{base['name']}_news_strict",
            }
        )
        # Size proxy: require stronger sentiment to enter
        configs.append(
            {
                **base,
                "news_filter": "sentiment_size",
                "news_sentiment_min": -0.05,
                "news_weak_below": 0.05,
                "label": f"{base['name']}_news_size",
            }
        )

    return configs

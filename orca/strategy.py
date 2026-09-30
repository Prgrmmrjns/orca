"""Simple time-series strategies: SMA crossover and RSI."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def generate_signals(df: pd.DataFrame, strategy_cfg: dict[str, Any]) -> pd.DataFrame:
    """Return copy of df with columns: signal (+1 buy, -1 sell, 0 hold).

    Position logic is long-only: +1 means enter/hold long, -1 means exit to cash.
    """
    out = df.copy()
    name = (strategy_cfg.get("name") or "sma_crossover").lower()

    if name == "sma_crossover":
        fast = int(strategy_cfg.get("fast_period", 10))
        slow = int(strategy_cfg.get("slow_period", 30))
        out["sma_fast"] = sma(out["close"], fast)
        out["sma_slow"] = sma(out["close"], slow)
        cross_up = (out["sma_fast"] > out["sma_slow"]) & (
            out["sma_fast"].shift(1) <= out["sma_slow"].shift(1)
        )
        cross_down = (out["sma_fast"] < out["sma_slow"]) & (
            out["sma_fast"].shift(1) >= out["sma_slow"].shift(1)
        )
        out["signal"] = 0
        out.loc[cross_up, "signal"] = 1
        out.loc[cross_down, "signal"] = -1

    elif name == "rsi":
        period = int(strategy_cfg.get("rsi_period", 14))
        oversold = float(strategy_cfg.get("rsi_oversold", 30))
        overbought = float(strategy_cfg.get("rsi_overbought", 70))
        out["rsi"] = rsi(out["close"], period)
        out["signal"] = 0
        out.loc[out["rsi"] < oversold, "signal"] = 1
        out.loc[out["rsi"] > overbought, "signal"] = -1

    else:
        raise ValueError(f"Unknown strategy: {name!r} (use sma_crossover or rsi)")

    return out

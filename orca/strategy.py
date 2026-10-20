"""Simple time-series strategies for spot long-only paper trading.

Signal convention: +1 enter/hold long, -1 exit to cash, 0 hold.
Execution (in backtest) uses the *previous* bar's signal at the *current* open
to avoid same-bar lookahead.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

STRATEGY_NAMES = (
    "sma_crossover",
    "rsi",
    "rsi_mean_reversion",
    "bollinger_mean_reversion",
    "macd",
    "dual_ma_volume",
)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return lower, mid, upper


def macd_lines(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _sma_crossover(out: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    fast = int(cfg.get("fast_period", 10))
    slow = int(cfg.get("slow_period", 30))
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
    return out


def _rsi_mean_reversion(out: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    period = int(cfg.get("rsi_period", 14))
    oversold = float(cfg.get("rsi_oversold", 30))
    overbought = float(cfg.get("rsi_overbought", 70))
    out["rsi"] = rsi(out["close"], period)
    out["signal"] = 0
    out.loc[out["rsi"] < oversold, "signal"] = 1
    out.loc[out["rsi"] > overbought, "signal"] = -1
    return out


def _bollinger_mean_reversion(out: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    period = int(cfg.get("bb_period", 20))
    num_std = float(cfg.get("bb_std", 2.0))
    lower, mid, upper = bollinger_bands(out["close"], period, num_std)
    out["bb_lower"] = lower
    out["bb_mid"] = mid
    out["bb_upper"] = upper
    out["signal"] = 0
    # Buy when price pierces below lower band; sell when it recovers to mid or above upper
    out.loc[out["close"] < lower, "signal"] = 1
    exit_on = (cfg.get("bb_exit") or "mid").lower()
    if exit_on == "upper":
        out.loc[out["close"] > upper, "signal"] = -1
    else:
        out.loc[out["close"] > mid, "signal"] = -1
    return out


def _macd(out: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    fast = int(cfg.get("macd_fast", 12))
    slow = int(cfg.get("macd_slow", 26))
    signal_p = int(cfg.get("macd_signal", 9))
    macd_line, signal_line, hist = macd_lines(out["close"], fast, slow, signal_p)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    cross_up = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    cross_down = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    out["signal"] = 0
    out.loc[cross_up, "signal"] = 1
    out.loc[cross_down, "signal"] = -1
    return out


def _dual_ma_volume(out: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """SMA crossover that only enters long when volume is above its SMA."""
    fast = int(cfg.get("fast_period", 10))
    slow = int(cfg.get("slow_period", 30))
    vol_period = int(cfg.get("volume_period", 20))
    out["sma_fast"] = sma(out["close"], fast)
    out["sma_slow"] = sma(out["close"], slow)
    out["vol_sma"] = sma(out["volume"], vol_period)
    cross_up = (out["sma_fast"] > out["sma_slow"]) & (
        out["sma_fast"].shift(1) <= out["sma_slow"].shift(1)
    )
    cross_down = (out["sma_fast"] < out["sma_slow"]) & (
        out["sma_fast"].shift(1) >= out["sma_slow"].shift(1)
    )
    vol_ok = out["volume"] > out["vol_sma"]
    out["signal"] = 0
    out.loc[cross_up & vol_ok, "signal"] = 1
    out.loc[cross_down, "signal"] = -1
    return out


def generate_signals(df: pd.DataFrame, strategy_cfg: dict[str, Any]) -> pd.DataFrame:
    """Return copy of df with column signal (+1 buy, -1 sell, 0 hold)."""
    out = df.copy()
    name = (strategy_cfg.get("name") or "sma_crossover").lower()

    if name == "sma_crossover":
        return _sma_crossover(out, strategy_cfg)
    if name in ("rsi", "rsi_mean_reversion"):
        return _rsi_mean_reversion(out, strategy_cfg)
    if name in ("bollinger_mean_reversion", "bollinger", "bb"):
        return _bollinger_mean_reversion(out, strategy_cfg)
    if name == "macd":
        return _macd(out, strategy_cfg)
    if name in ("dual_ma_volume", "sma_volume"):
        return _dual_ma_volume(out, strategy_cfg)

    raise ValueError(
        f"Unknown strategy: {name!r}. Choose from: {', '.join(STRATEGY_NAMES)}"
    )


def default_strategy_configs() -> list[dict[str, Any]]:
    """Canonical configs used by the comparison harness."""
    return [
        {
            "name": "sma_crossover",
            "fast_period": 10,
            "slow_period": 30,
        },
        {
            "name": "rsi_mean_reversion",
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
        },
        {
            "name": "bollinger_mean_reversion",
            "bb_period": 20,
            "bb_std": 2.0,
            "bb_exit": "mid",
        },
        {
            "name": "macd",
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
        },
        {
            "name": "dual_ma_volume",
            "fast_period": 10,
            "slow_period": 30,
            "volume_period": 20,
        },
    ]

"""Shared paper-trading constants for the crypto books.

Numeric risk limits match orca.strategy_v2 (40% vol target, 50% cap, 5% band).
"""

from __future__ import annotations

ASSETS: tuple[str, ...] = ("BTC", "ETH", "SOL", "XRP")
PAIRS: dict[str, str] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XRP": "XRP/USD",
}
KRAKEN_REST: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
}
# Archive file stems. Kraken has used both the short and the X-prefixed names.
PAIR_STEMS: dict[str, tuple[str, ...]] = {
    "BTC": ("XBTUSD", "XXBTZUSD"),
    "ETH": ("ETHUSD", "XETHZUSD"),
    "SOL": ("SOLUSD",),
    "XRP": ("XRPUSD", "XXRPZUSD"),
}

INTERVALS = {"1h": 60, "4h": 240}
PANEL_START = "2018-01-01"

TARGET_VOL = 0.40
MAX_WEIGHT = 0.50
BAND = 0.05
CORR_HAIRCUT = 0.85
SMA_WINDOWS = (50, 100, 200)
VOL_WINDOW_DAYS = 30
START_CAPITAL_EUR = 1000.0
DRAWDOWN_ALERT = 0.25

# Per-side cost grid. 1x is the training and headline backtest cost.
COST_BPS = (10.0, 20.0, 30.0)
BASE_COST_BPS = 10.0

EMBARGO_DAYS = 7
HORIZON_BARS = 1
MIN_TRAIN_YEARS = 2

FEATURE_NAMES: tuple[str, ...] = (
    "log_ret_1",
    "log_ret_6",
    "log_ret_42",
    "log_ret_180",
    "rv_ratio",
    "volume_z",
    "xs_rank",
    "btc_dist_sma50",
    "btc_dist_sma100",
    "btc_dist_sma200",
    "ret_z",
)

LOOKBACK = 32


def periods_per_year(interval: str) -> int:
    minutes = INTERVALS[interval]
    return int(round((24 * 60 / minutes) * 365))


def vol_window_bars(interval: str) -> int:
    minutes = INTERVALS[interval]
    bars_per_day = int(round(24 * 60 / minutes))
    return VOL_WINDOW_DAYS * bars_per_day

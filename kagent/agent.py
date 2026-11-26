"""The Kraken agent's decision rule (frozen 2026-11-25 before the holdout run).

Universe: 8 most liquid long-history Kraken perps (BTC ETH SOL XRP ADA DOGE LINK AVAX).
Signal:   for each SMA window N in (20, 50, 100, 200): hold coins above their own SMA(N) while BTC is
          above its SMA(N); inverse-vol weights, sub-book vol target 30%, max 50% per coin. Average the 4 sub-books.
Cadence:  weekly (Monday 00:00 UTC close), skip changes < 5% of equity, always fully exit.
Venue:    Kraken perps (0.05% taker vs 0.80% spot taker at tier 1), long only, no leverage (gross <= 1).
Risk:     drawdown brake on strategy equity: >=15% below high-water mark -> half size, >=25% -> quarter size.
          Paper only. Never places real orders.
"""
from __future__ import annotations

import pandas as pd

from kagent import strategies as S

UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX"]
WINDOWS = (20, 50, 100, 200)
TARGET_VOL = 0.30
BAND = 0.05
BRAKE = ((0.15, 0.5), (0.25, 0.25))
MAX_GROSS = 1.0


def targets(close: pd.DataFrame) -> pd.DataFrame:
    w = S.regime_trend(close, UNIVERSE, WINDOWS, TARGET_VOL, gate=True, short_bear=0.0)
    w = S.cap(w, gross=MAX_GROSS)
    return S.weekly(w)

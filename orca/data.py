"""Fetch Kraken OHLC via ccxt (public; no API keys required)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

from orca.logging_setup import setup_logging

logger = setup_logging()

# Kraken public REST OHLC returns at most ~720 candles per call.
KRAKEN_OHLC_MAX = 720


def make_exchange(exchange_cfg: dict[str, Any] | None = None) -> ccxt.Exchange:
    cfg = dict(exchange_cfg or {})
    cfg.setdefault("enableRateLimit", True)
    # Strip empty credentials so public calls stay anonymous
    if not cfg.get("apiKey"):
        cfg.pop("apiKey", None)
        cfg.pop("secret", None)
    exchange = ccxt.kraken(cfg)
    return exchange


def fetch_ohlcv(
    pair: str = "BTC/USD",
    timeframe: str = "1d",
    limit: int = KRAKEN_OHLC_MAX,
    since: int | None = None,
    exchange_cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV candles from Kraken public API via ccxt.

    Note: Kraken's public OHLC endpoint caps at ~720 candles. For deeper history,
    aggregate from the public Trades endpoint (not implemented here).
    """
    exchange = make_exchange(exchange_cfg)
    limit = min(int(limit), KRAKEN_OHLC_MAX)
    logger.info("Fetching %s %s limit=%s since=%s", pair, timeframe, limit, since)
    raw = exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
    if not raw:
        raise RuntimeError(f"No OHLCV returned for {pair} {timeframe}")

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    logger.info("Fetched %d candles from %s to %s", len(df), df.index[0], df.index[-1])
    return df


def save_ohlcv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    logger.info("Saved OHLCV -> %s", path)
    return path


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df

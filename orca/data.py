"""Fetch Kraken OHLC via ccxt (public; no API keys required)."""

from __future__ import annotations

from io import BytesIO
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


# Kraken's OHLC endpoint only keeps ~720 hourly bars. Longer history comes from
# the quarterly OHLCVT archives (60-minute files).
HOURLY_ZIPS = (
    "Kraken_OHLCVT_2026Q1.zip",
    "Kraken_OHLCVT_2026Q2.zip",
)
# Quoted in USD but not a coin the book should rotate into.
FIAT_OR_STABLE = {
    "USD",
    "USDT",
    "USDC",
    "DAI",
    "PYUSD",
    "TUSD",
    "USDE",
    "USDP",
    "FDUSD",
    "GUSD",
    "USDG",
    "RLUSD",
    "USD1",
    "USDS",
    "EUR",
    "EURQ",
    "GBP",
    "AUD",
    "CAD",
    "JPY",
    "CHF",
}


def kraken_usd_pairs() -> list[str]:
    """Active Kraken spot pairs quoted in USD, stables and fiat excluded."""
    exchange = ccxt.kraken({"enableRateLimit": True})
    exchange.load_markets()
    found: list[str] = []
    for market in exchange.markets.values():
        if market.get("quote") != "USD" or market.get("spot") is False:
            continue
        if market.get("active") is False:
            continue
        base = market.get("base") or ""
        if base in FIAT_OR_STABLE:
            continue
        symbol = market.get("symbol") or ""
        if symbol.endswith("/USD"):
            found.append(symbol)
    pairs = sorted(set(found))
    logger.info("Kraken USD pairs: %d", len(pairs))
    return pairs


HOURLY_STEMS = {
    "BTC/USD": ("XBTUSD_60.csv", "XXBTZUSD_60.csv"),
    "ETH/USD": ("ETHUSD_60.csv", "XETHZUSD_60.csv"),
    "SOL/USD": ("SOLUSD_60.csv", "SOLUSD_60.csv"),
    "XRP/USD": ("XRPUSD_60.csv", "XXRPZUSD_60.csv"),
}


def _read_ohlcvt_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(
        BytesIO(raw),
        header=None,
        names=["timestamp", "open", "high", "low", "close", "volume", "trades"],
    )
    unit = "ms" if float(df["timestamp"].iloc[0]) > 1e12 else "s"
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]]


def load_hourly_history(
    pairs: list[str] | tuple[str, ...],
    archive_dir: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Hourly Kraken candles from the 2026 Q1–Q2 OHLCVT zips (through 30 Jun 2026)."""
    import zipfile

    archive_dir = Path(archive_dir) if archive_dir else Path(__file__).resolve().parents[1] / "data" / "ohlcvt"
    frames: dict[str, list[pd.DataFrame]] = {pair: [] for pair in pairs}
    for zip_name in HOURLY_ZIPS:
        path = archive_dir / zip_name
        if not path.exists():
            raise FileNotFoundError(f"Missing hourly archive {path}")
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            for pair in pairs:
                stems = HOURLY_STEMS.get(pair, ())
                member = next((n for n in names if n.rsplit("/", 1)[-1] in stems), None)
                if member is None:
                    raise FileNotFoundError(f"No hourly file for {pair} in {zip_name}")
                frames[pair].append(_read_ohlcvt_csv(zf.read(member)))
                logger.info("Read %s from %s (%d rows)", member, zip_name, len(frames[pair][-1]))
    out: dict[str, pd.DataFrame] = {}
    for pair, parts in frames.items():
        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        out[pair] = df
        logger.info("Hourly history %s: %d bars %s → %s", pair, len(df), df.index[0], df.index[-1])
    return out


def fetch_binance_hourly(
    pairs: list[str] | tuple[str, ...],
    since: str = "2024-10-11T00:00:00Z",
    until_ms: int | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Hourly Binance USDT candles. Kraken's own hourly feed does not keep this span.

    Returned frames are keyed by the Kraken pair name (BTC/USD, ...).
    """
    cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parents[1] / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    missing_path = cache_dir / "binance_missing_usdt.txt"
    missing = set(missing_path.read_text(encoding="utf-8").split()) if missing_path.exists() else set()
    exchange = ccxt.binance({"enableRateLimit": True})
    since_ms = exchange.parse8601(since)
    stop = int(until_ms) if until_ms is not None else exchange.milliseconds()
    step = 60 * 60 * 1000
    out: dict[str, pd.DataFrame] = {}

    def _remember_missing(base: str) -> None:
        if base in missing:
            return
        missing.add(base)
        missing_path.write_text("\n".join(sorted(missing)) + "\n", encoding="utf-8")

    for pair in pairs:
        base = pair.split("/")[0]
        symbol = f"{base}/USDT"
        if base in missing:
            continue
        cache = cache_dir / f"binance_{base}_USDT_1h.csv"
        cached = load_ohlcv(cache) if cache.exists() else None
        if cached is not None and len(cached) and int(cached.index[-1].timestamp() * 1000) >= stop - step:
            out[pair] = cached[
                (cached.index >= pd.Timestamp(since_ms, unit="ms", tz="UTC"))
                & (cached.index < pd.Timestamp(stop, unit="ms", tz="UTC"))
            ]
            logger.info("Loading cached Binance hourly %s (%d bars)", symbol, len(out[pair]))
            continue
        rows: list[list[float]] = []
        cursor = since_ms
        if cached is not None and len(cached):
            rows = [
                [
                    int(ts.timestamp() * 1000),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                ]
                for ts, row in cached.iterrows()
            ]
            cursor = int(cached.index[-1].timestamp() * 1000) + step
        try:
            while cursor < stop:
                batch = exchange.fetch_ohlcv(symbol, timeframe="1h", since=cursor, limit=1000)
                if not batch:
                    break
                rows.extend(r for r in batch if r[0] < stop)
                nxt = int(batch[-1][0]) + step
                if nxt <= cursor:
                    break
                cursor = nxt
                if batch[-1][0] >= stop - step or len(batch) < 1000:
                    break
        except ccxt.BaseError as exc:
            logger.info("Skip Binance %s: %s", symbol, type(exc).__name__)
            _remember_missing(base)
            continue
        if not rows:
            logger.info("Skip Binance %s: no candles", symbol)
            _remember_missing(base)
            continue
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df[df.index < pd.Timestamp(stop, unit="ms", tz="UTC")]
        save_ohlcv(df, cache)
        out[pair] = df
        logger.info("Binance hourly %s: %d bars %s → %s", symbol, len(df), df.index[0], df.index[-1])
    return out


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df

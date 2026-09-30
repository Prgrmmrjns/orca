"""Run a paper backtest: fetch -> signals -> simulate fills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from orca.data import fetch_ohlcv, save_ohlcv
from orca.logging_setup import setup_logging
from orca.portfolio import PaperPortfolio
from orca.strategy import generate_signals

logger = setup_logging()


def run_backtest(cfg: dict[str, Any]) -> dict[str, Any]:
    pair = cfg.get("pair", "BTC/USD")
    timeframe = cfg.get("timeframe", "1d")
    bt = cfg.get("backtest", {})
    limit = int(bt.get("limit", 720))
    since = bt.get("since")

    df = fetch_ohlcv(
        pair=pair,
        timeframe=timeframe,
        limit=limit,
        since=since,
        exchange_cfg=cfg.get("exchange"),
    )

    data_dir = Path(__file__).resolve().parents[1] / "data"
    csv_name = f"{pair.replace('/', '_')}_{timeframe}.csv"
    save_ohlcv(df, data_dir / csv_name)

    signals = generate_signals(df, cfg.get("strategy", {}))
    port_cfg = cfg.get("portfolio", {})
    portfolio = PaperPortfolio(
        initial_cash=float(port_cfg.get("initial_cash", 10_000)),
        fee_rate=float(port_cfg.get("fee_rate", 0.0026)),
        position_size=float(port_cfg.get("position_size", 0.95)),
    )

    # Execute on next bar open after signal (avoids same-bar lookahead)
    sig = signals["signal"]
    opens = signals["open"]
    closes = signals["close"]

    for i in range(1, len(signals)):
        prev_signal = int(sig.iloc[i - 1])
        ts = signals.index[i]
        price = float(opens.iloc[i])
        if prev_signal == 1 and portfolio.base == 0:
            fill = portfolio.buy(ts, price)
            if fill:
                logger.info("BUY  %s @ %.2f amount=%.6f fee=%.4f", ts, price, fill.amount, fill.fee)
        elif prev_signal == -1 and portfolio.base > 0:
            fill = portfolio.sell(ts, price)
            if fill:
                logger.info("SELL %s @ %.2f amount=%.6f fee=%.4f", ts, price, fill.amount, fill.fee)

    last_price = float(closes.iloc[-1])
    # Mark-to-market; optionally flatten (commented — leave open position valued)
    summary = portfolio.summary(last_price)
    equity_curve = _equity_curve(signals, portfolio, cfg)

    result = {
        "pair": pair,
        "timeframe": timeframe,
        "strategy": cfg.get("strategy", {}).get("name", "sma_crossover"),
        "candles": len(signals),
        "summary": summary,
        "fills": portfolio.fills_frame(),
        "equity_curve": equity_curve,
        "ohlcv_path": str(data_dir / csv_name),
    }
    logger.info(
        "Backtest done | equity=%.2f return=%.2f%% trades=%d fees=%.2f",
        summary["final_equity"],
        summary["return_pct"],
        summary["n_trades"],
        summary["total_fees"],
    )
    return result


def _equity_curve(
    signals: pd.DataFrame, portfolio: PaperPortfolio, cfg: dict[str, Any]
) -> pd.DataFrame:
    """Replay fills chronologically to build an equity series (mark-to-market)."""
    port_cfg = cfg.get("portfolio", {})
    sim = PaperPortfolio(
        initial_cash=float(port_cfg.get("initial_cash", 10_000)),
        fee_rate=float(port_cfg.get("fee_rate", 0.0026)),
        position_size=float(port_cfg.get("position_size", 0.95)),
    )
    fill_map = {f.timestamp: f for f in portfolio.fills}
    rows = []
    sig = signals["signal"]
    for i in range(1, len(signals)):
        ts = signals.index[i]
        price_open = float(signals["open"].iloc[i])
        prev_signal = int(sig.iloc[i - 1])
        if prev_signal == 1 and sim.base == 0:
            sim.buy(ts, price_open)
        elif prev_signal == -1 and sim.base > 0:
            sim.sell(ts, price_open)
        mark = float(signals["close"].iloc[i])
        rows.append({"timestamp": ts, "equity": sim.equity(mark), "price": mark})
    # unused fill_map kept for potential audit
    _ = fill_map
    return pd.DataFrame(rows).set_index("timestamp")

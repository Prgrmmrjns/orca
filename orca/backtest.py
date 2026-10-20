"""Run a paper backtest: fetch -> signals -> simulate fills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from orca.data import fetch_ohlcv, load_ohlcv, save_ohlcv
from orca.logging_setup import setup_logging
from orca.metrics import compute_metrics
from orca.portfolio import PaperPortfolio
from orca.strategy import generate_signals

logger = setup_logging()

ROOT = Path(__file__).resolve().parents[1]


def simulate_portfolio(
    signals: pd.DataFrame,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.0026,
    position_size: float = 0.95,
) -> tuple[PaperPortfolio, pd.DataFrame]:
    """Execute long-only signals: prior-bar signal fills at current open."""
    portfolio = PaperPortfolio(
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
    )
    sig = signals["signal"]
    opens = signals["open"]
    closes = signals["close"]
    rows: list[dict[str, Any]] = []

    for i in range(1, len(signals)):
        prev_signal = int(sig.iloc[i - 1])
        ts = signals.index[i]
        price = float(opens.iloc[i])
        if prev_signal == 1 and portfolio.base == 0:
            fill = portfolio.buy(ts, price)
            if fill:
                logger.debug(
                    "BUY  %s @ %.2f amount=%.6f fee=%.4f",
                    ts,
                    price,
                    fill.amount,
                    fill.fee,
                )
        elif prev_signal == -1 and portfolio.base > 0:
            fill = portfolio.sell(ts, price)
            if fill:
                logger.debug(
                    "SELL %s @ %.2f amount=%.6f fee=%.4f",
                    ts,
                    price,
                    fill.amount,
                    fill.fee,
                )
        mark = float(closes.iloc[i])
        rows.append({"timestamp": ts, "equity": portfolio.equity(mark), "price": mark})

    equity_curve = pd.DataFrame(rows).set_index("timestamp") if rows else pd.DataFrame(
        columns=["equity", "price"]
    )
    return portfolio, equity_curve


def run_backtest(
    cfg: dict[str, Any],
    df: pd.DataFrame | None = None,
    *,
    save_data: bool = True,
) -> dict[str, Any]:
    pair = cfg.get("pair", "BTC/USD")
    timeframe = cfg.get("timeframe", "1d")
    bt = cfg.get("backtest", {})
    limit = int(bt.get("limit", 720))
    since = bt.get("since")
    data_dir = ROOT / "data"
    csv_name = f"{pair.replace('/', '_')}_{timeframe}.csv"
    csv_path = data_dir / csv_name

    if df is None:
        use_cache = bool(bt.get("use_cache", False)) and csv_path.exists()
        if use_cache:
            logger.info("Loading cached OHLCV %s", csv_path)
            df = load_ohlcv(csv_path)
        else:
            df = fetch_ohlcv(
                pair=pair,
                timeframe=timeframe,
                limit=limit,
                since=since,
                exchange_cfg=cfg.get("exchange"),
            )
            if save_data:
                save_ohlcv(df, csv_path)
    elif save_data:
        save_ohlcv(df, csv_path)

    signals = generate_signals(df, cfg.get("strategy", {}))
    port_cfg = cfg.get("portfolio", {})
    initial_cash = float(port_cfg.get("initial_cash", 10_000))
    fee_rate = float(port_cfg.get("fee_rate", 0.0026))
    position_size = float(port_cfg.get("position_size", 0.95))

    portfolio, equity_curve = simulate_portfolio(
        signals,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
    )

    periods = 365.0 if timeframe.endswith("d") else 365.0 * 24  # rough for 1h
    metrics = compute_metrics(portfolio, equity_curve, signals["close"], periods_per_year=periods)

    result = {
        "pair": pair,
        "timeframe": timeframe,
        "strategy": cfg.get("strategy", {}).get("name", "sma_crossover"),
        "strategy_cfg": cfg.get("strategy", {}),
        "fee_rate": fee_rate,
        "candles": len(signals),
        "start": str(signals.index[0]) if len(signals) else None,
        "end": str(signals.index[-1]) if len(signals) else None,
        "summary": metrics,
        "fills": portfolio.fills_frame(),
        "equity_curve": equity_curve,
        "ohlcv_path": str(csv_path),
    }
    logger.info(
        "Backtest done | %s %s | equity=%.2f return=%.2f%% mdd=%.2f%% trades=%d",
        result["strategy"],
        pair,
        metrics["final_equity"],
        metrics["return_pct"],
        metrics["max_drawdown_pct"],
        metrics["n_trades"],
    )
    return result

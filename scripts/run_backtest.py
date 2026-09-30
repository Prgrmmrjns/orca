#!/usr/bin/env python3
"""Run a local paper backtest (no API keys, no live orders)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.backtest import run_backtest
from orca.config import load_config
from orca.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Orca paper backtest (Kraken public data)")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config YAML (default: config/config.yaml or config.example.yaml)",
    )
    parser.add_argument("--pair", default=None, help="Override pair, e.g. BTC/USD or BTC/EUR")
    parser.add_argument("--strategy", default=None, choices=["sma_crossover", "rsi"])
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"), cfg.get("logging", {}).get("dir", "logs"))

    if args.pair:
        cfg["pair"] = args.pair
    if args.strategy:
        cfg.setdefault("strategy", {})["name"] = args.strategy

    print(f"Mode: {cfg['mode']} | pair={cfg.get('pair')} | strategy={cfg.get('strategy', {}).get('name')}")
    print("Paper only — no live trades, no API keys required.\n")

    result = run_backtest(cfg)
    summary = result["summary"]
    print("--- Paper backtest summary ---")
    print(f"Pair:         {result['pair']} ({result['timeframe']})")
    print(f"Strategy:     {result['strategy']}")
    print(f"Candles:      {result['candles']}")
    print(f"Initial cash: {summary['initial_cash']:.2f}")
    print(f"Final equity: {summary['final_equity']:.2f}")
    print(f"Return:       {summary['return_pct']:.2f}%")
    print(f"Trades:       {summary['n_trades']}")
    print(f"Fees paid:    {summary['total_fees']:.2f}")
    print(f"OHLCV saved:  {result['ohlcv_path']}")

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    fills_path = logs / "last_fills.csv"
    result["fills"].to_csv(fills_path, index=False)
    eq_path = logs / "last_equity.csv"
    result["equity_curve"].to_csv(eq_path)
    meta = {k: v for k, v in result.items() if k not in ("fills", "equity_curve")}
    meta["summary"] = summary
    with (logs / "last_backtest.json").open("w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"Fills:        {fills_path}")
    print(f"Equity curve: {eq_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

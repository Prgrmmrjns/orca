#!/usr/bin/env python3
"""Compare multiple paper strategies on the same pairs/timeframe.

Paper only — no live orders. Uses Kraken public OHLCV (max ~720 candles).
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.backtest import run_backtest
from orca.config import load_config
from orca.data import fetch_ohlcv, load_ohlcv, save_ohlcv
from orca.logging_setup import setup_logging
from orca.strategy import STRATEGY_NAMES, default_strategy_configs

DEFAULT_PAIRS = ["BTC/USD", "BTC/EUR", "ETH/USD"]


def _load_or_fetch(
    pair: str,
    timeframe: str,
    limit: int,
    exchange_cfg: dict[str, Any] | None,
    data_dir: Path,
    refresh: bool,
) -> tuple[Any, str | None]:
    """Return (df, error). error set if fetch failed and no cache."""
    csv_path = data_dir / f"{pair.replace('/', '_')}_{timeframe}.csv"
    if not refresh and csv_path.exists():
        return load_ohlcv(csv_path), None
    try:
        df = fetch_ohlcv(pair, timeframe=timeframe, limit=limit, exchange_cfg=exchange_cfg)
        save_ohlcv(df, csv_path)
        return df, None
    except Exception as exc:  # noqa: BLE001 — soft-fail per pair
        if csv_path.exists():
            return load_ohlcv(csv_path), None
        return None, str(exc)


def _row_from_result(result: dict[str, Any]) -> dict[str, Any]:
    s = result["summary"]
    return {
        "strategy": result["strategy"],
        "pair": result["pair"],
        "timeframe": result["timeframe"],
        "fee_rate": result["fee_rate"],
        "candles": result["candles"],
        "start": result.get("start"),
        "end": result.get("end"),
        "return_pct": round(s["return_pct"], 4),
        "max_drawdown_pct": round(s["max_drawdown_pct"], 4),
        "n_trades": s["n_trades"],
        "round_trips": s.get("round_trips"),
        "win_rate_pct": None if s.get("win_rate_pct") is None else round(s["win_rate_pct"], 2),
        "sharpe_like": None if s.get("sharpe_like") is None else round(s["sharpe_like"], 4),
        "buy_hold_return_pct": round(s["buy_hold_return_pct"], 4),
        "excess_vs_buy_hold_pct": round(s["excess_vs_buy_hold_pct"], 4),
        "final_equity": round(s["final_equity"], 2),
        "total_fees": round(s["total_fees"], 2),
        "strategy_cfg": result.get("strategy_cfg"),
    }


def _rank_key(row: dict[str, Any]) -> tuple[float, float, float]:
    """Prefer higher excess vs B&H, then higher return, then shallower drawdown."""
    return (
        float(row["excess_vs_buy_hold_pct"]),
        float(row["return_pct"]),
        float(row["max_drawdown_pct"]),  # less negative is better
    )


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orca strategy comparison (paper)",
        "",
        f"Generated: {payload['generated_at']} (UTC)",
        f"Mode: `{payload['mode']}` — no live orders.",
        f"Timeframe: `{payload['timeframe']}` | fee_rate: `{payload['fee_rate']}` "
        f"({payload['fee_rate'] * 100:.2f}% per side) | initial_cash: `{payload['initial_cash']}`",
        "",
        "## Ranking (all pair×strategy rows)",
        "",
        "| Rank | Strategy | Pair | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for i, r in enumerate(payload["ranking"], 1):
        wr = "—" if r["win_rate_pct"] is None else f"{r['win_rate_pct']:.1f}"
        sh = "—" if r["sharpe_like"] is None else f"{r['sharpe_like']:.2f}"
        lines.append(
            f"| {i} | `{r['strategy']}` | {r['pair']} | {r['return_pct']:.2f} | "
            f"{r['max_drawdown_pct']:.2f} | {r['n_trades']} | {wr} | {sh} | "
            f"{r['buy_hold_return_pct']:.2f} | {r['excess_vs_buy_hold_pct']:.2f} |"
        )

    best = payload.get("best_candidate")
    lines.extend(["", "## Best candidate (paper constraints)", ""])
    if best:
        lines.append(
            f"**`{best['strategy']}`** on **{best['pair']}** — return "
            f"{best['return_pct']:.2f}%, excess vs B&H {best['excess_vs_buy_hold_pct']:.2f}%, "
            f"max DD {best['max_drawdown_pct']:.2f}%, trades {best['n_trades']}."
        )
        lines.append("")
        lines.append(
            "Selection rule: maximize excess return vs buy-and-hold, then total return, "
            "then shallower drawdown. Prefer strategies with ≥1 round trip when tied on zeros."
        )
    else:
        lines.append("_No results._")

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Short history: Kraken public OHLC caps at ~720 daily candles (~2 years).",
            "- In-sample only; no walk-forward / out-of-sample — high overfitting risk.",
            "- Fees applied per fill; default is configurable (Tier-1 taker is 0.80%).",
            "- Long-only spot; signals execute on next bar open (no same-bar lookahead).",
            "- Sharpe~ uses rf=0 and calendar-day annualization; treat as descriptive.",
            "- Past paper performance does not predict live results.",
            "",
            "## Re-run",
            "",
            "```bash",
            "cd /workspace/orca && source .venv/bin/activate",
            "python scripts/compare_strategies.py",
            "python scripts/compare_strategies.py --fee 0.008 --pairs BTC/USD,BTC/EUR",
            "python scripts/compare_strategies.py --refresh  # re-fetch OHLCV",
            "```",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Orca paper strategies")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--pairs",
        default=",".join(DEFAULT_PAIRS),
        help="Comma-separated pairs (default: BTC/USD,BTC/EUR,ETH/USD)",
    )
    parser.add_argument("--timeframe", default=None)
    parser.add_argument(
        "--fee",
        type=float,
        default=None,
        help="Fee rate per side (e.g. 0.0026 or 0.008). Default from config.",
    )
    parser.add_argument("--refresh", action="store_true", help="Re-fetch OHLCV from Kraken")
    parser.add_argument(
        "--strategies",
        default=None,
        help=f"Comma-separated subset of: {','.join(STRATEGY_NAMES)}",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    assert cfg["mode"] == "paper"
    setup_logging(
        cfg.get("logging", {}).get("level", "INFO"),
        cfg.get("logging", {}).get("dir", "logs"),
    )

    timeframe = args.timeframe or cfg.get("timeframe", "1d")
    fee_rate = float(args.fee if args.fee is not None else cfg.get("portfolio", {}).get("fee_rate", 0.0026))
    initial_cash = float(cfg.get("portfolio", {}).get("initial_cash", 10_000))
    position_size = float(cfg.get("portfolio", {}).get("position_size", 0.95))
    limit = int(cfg.get("backtest", {}).get("limit", 720))
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]

    strat_cfgs = default_strategy_configs()
    if args.strategies:
        wanted = {s.strip().lower() for s in args.strategies.split(",") if s.strip()}
        strat_cfgs = [s for s in strat_cfgs if s["name"].lower() in wanted]
        # Allow rsi alias
        if "rsi" in wanted and not any(s["name"] == "rsi_mean_reversion" for s in strat_cfgs):
            strat_cfgs.append(
                {
                    "name": "rsi_mean_reversion",
                    "rsi_period": 14,
                    "rsi_oversold": 30,
                    "rsi_overbought": 70,
                }
            )

    data_dir = ROOT / "data"
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for pair in pairs:
        df, err = _load_or_fetch(
            pair, timeframe, limit, cfg.get("exchange"), data_dir, args.refresh
        )
        if df is None:
            skipped.append({"pair": pair, "reason": err or "no data"})
            print(f"SKIP {pair}: {err}")
            continue
        print(f"Pair {pair}: {len(df)} candles ({df.index[0]} → {df.index[-1]})")

        for sc in strat_cfgs:
            run_cfg = deepcopy(cfg)
            run_cfg["pair"] = pair
            run_cfg["timeframe"] = timeframe
            run_cfg["strategy"] = sc
            run_cfg.setdefault("portfolio", {})
            run_cfg["portfolio"]["fee_rate"] = fee_rate
            run_cfg["portfolio"]["initial_cash"] = initial_cash
            run_cfg["portfolio"]["position_size"] = position_size
            result = run_backtest(run_cfg, df=df, save_data=False)
            rows.append(_row_from_result(result))

    ranking = sorted(rows, key=_rank_key, reverse=True)

    # Best candidate: among rows with at least one round-trip if any exist; else top of ranking
    candidates = [r for r in ranking if (r.get("round_trips") or 0) >= 1]
    best = (candidates or ranking)[0] if ranking else None

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "paper",
        "timeframe": timeframe,
        "fee_rate": fee_rate,
        "initial_cash": initial_cash,
        "position_size": position_size,
        "pairs": pairs,
        "strategies": [s["name"] for s in strat_cfgs],
        "skipped": skipped,
        "n_results": len(rows),
        "ranking": ranking,
        "best_candidate": best,
        "caveats": [
            "Kraken public OHLC ~720 candle cap",
            "In-sample comparison only; overfitting risk",
            "Paper simulation; no live orders",
        ],
    }

    json_path = logs_dir / "strategy_compare.json"
    md_path = logs_dir / "strategy_compare.md"
    with json_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print("\n=== Ranking (top 10) ===")
    for i, r in enumerate(ranking[:10], 1):
        print(
            f"{i:2d}. {r['strategy']:28s} {r['pair']:8s}  "
            f"ret={r['return_pct']:+7.2f}%  excess={r['excess_vs_buy_hold_pct']:+7.2f}%  "
            f"mdd={r['max_drawdown_pct']:7.2f}%  trades={r['n_trades']}"
        )
    if best:
        print(
            f"\nBest candidate: {best['strategy']} @ {best['pair']} "
            f"(excess {best['excess_vs_buy_hold_pct']:+.2f}%)"
        )
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

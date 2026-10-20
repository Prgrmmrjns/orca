#!/usr/bin/env python3
"""Paper-only multi-pair trajectory model for BTC/USD, SOL/USD, XRP/USD.

HARD RULES: no live trades; no TabPFN (sklearn Ridge MultiOutput only);
paper fees ~0.26%. Fetches/caches daily OHLCV via Orca data layer (ccxt/Kraken).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.config import load_config
from orca.data import fetch_ohlcv, load_ohlcv, save_ohlcv
from orca.logging_setup import setup_logging
from orca.multi_pair_trajectory import (
    DEFAULT_HORIZONS,
    DEFAULT_PAIRS,
    align_closes,
    build_shared_features,
    build_trajectory_targets,
    equal_weight_buy_hold,
    prediction_diagnostics,
    scores_to_long_flat_signals,
    scores_to_relative_weights,
    simulate_per_pair_long_flat,
    simulate_relative_allocation,
    single_asset_buy_hold,
    trajectory_score,
    walk_forward_predict,
)

logger = setup_logging()


def load_or_fetch_pairs(
    pairs: list[str],
    timeframe: str,
    limit: int,
    *,
    refresh: bool,
    exchange_cfg: dict[str, Any] | None,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for pair in pairs:
        csv_path = data_dir / f"{pair.replace('/', '_')}_{timeframe}.csv"
        if (not refresh) and csv_path.exists():
            logger.info("Loading cached OHLCV %s", csv_path)
            out[pair] = load_ohlcv(csv_path)
        else:
            df = fetch_ohlcv(
                pair=pair,
                timeframe=timeframe,
                limit=limit,
                exchange_cfg=exchange_cfg,
            )
            save_ohlcv(df, csv_path)
            out[pair] = df
    return out


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    wf = payload["walk_forward"]
    lf = payload["long_flat"]
    rel = payload["relative_alloc"]
    ew = payload["equal_weight_bh"]
    bh = payload["single_bh"]
    diag = payload.get("diagnostics", {})
    lines = [
        "# Multi-pair trajectory model (paper)",
        "",
        f"Generated: {payload['generated_at_local']} (Europe/Berlin)",
        "Mode: `paper` — no live orders. sklearn MultiOutputRegressor only (no GPU / no TabPFN).",
        "",
        f"- Pairs: {', '.join(f'`{p}`' for p in payload['pairs'])}",
        f"- Timeframe: `{payload['timeframe']}` | aligned candles: {payload['candles']} "
        f"({payload['start']} → {payload['end']})",
        f"- OOS eval window: `{payload.get('oos_start')}` → `{payload.get('oos_end')}` "
        f"({payload.get('oos_candles')} bars); B&H benchmarks use this window.",
        f"- Fee rate: `{payload['fee_rate']}` ({payload['fee_rate'] * 100:.2f}% per side)",
        f"- Initial cash: {payload['initial_cash']:.0f}",
        "",
        "## Method",
        "",
        "- Shared causal features: per-pair lagged returns (1/2/3/5/10), rolling vol,",
        "  RSI-14, momentum, HL range, volume z-score; pairwise rolling correlations",
        "  and 1d return spreads (cross-asset).",
        f"- Targets (trajectory): forward close-to-close returns at horizons "
        f"{list(payload['horizons'])} days × {len(payload['pairs'])} pairs "
        f"({wf['n_targets']} outputs).",
        f"- Model: `MultiOutputRegressor({wf['model_name']})` "
        f"(alpha={wf.get('alpha')}) — CPU only.",
        f"- Walk-forward: expanding window, min_train={wf['min_train']}, "
        f"retrain_every={wf['retrain_every']} bars; "
        f"{wf['fit_calls']} fits / {wf['predict_rows']} predicted rows.",
        "- Signal score: mean predicted trajectory return (also expose 1d).",
        "- **Long/flat**: equal cash split; long if predicted 1d return > 0 else flat.",
        "- **Relative alloc**: long-only weights ∝ max(pred_1d, 0); rebalance when "
        "L1 weight drift ≥ 20% and ≥5 bars since last rebalance; fees on turnover.",
        "- Fills / marks: prior-bar signal @ current open (no same-bar look-ahead).",
        "",
        "## Benchmarks (buy & hold)",
        "",
        "| Benchmark | Return % | Max DD % | Sharpe~ |",
        "| --- | ---: | ---: | ---: |",
    ]
    for pair in payload["pairs"]:
        m = bh[pair]
        # single BH has no equity curve here — DD/sharpe only on equal-weight & strategies
        lines.append(
            f"| B&H `{pair}` | {m['return_pct']:.2f} | n/a | n/a |"
        )
    lines.append(
        f"| Equal-weight B&H | {ew['return_pct']:.2f} | {ew['max_drawdown_pct']:.2f} | "
        f"{_fmt(ew.get('sharpe_like'))} |"
    )

    lines.extend(
        [
            "",
            "## Long/flat (equal split, trajectory 1d sign)",
            "",
            "| Sleeve | Return % | Max DD % | Trades | Sharpe~ | B&H % | Excess % |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for pair in payload["pairs"]:
        m = lf[pair]
        lines.append(
            f"| `{pair}` | {m['return_pct']:.2f} | {m['max_drawdown_pct']:.2f} | "
            f"{m['n_trades']} | {_fmt(m.get('sharpe_like'))} | "
            f"{m['buy_hold_return_pct']:.2f} | {m['excess_vs_buy_hold_pct']:.2f} |"
        )
    port = lf["portfolio"]
    lines.append(
        f"| **Portfolio** | **{port['return_pct']:.2f}** | **{port['max_drawdown_pct']:.2f}** | "
        f"**{port['n_trades']}** | **{_fmt(port.get('sharpe_like'))}** | "
        f"{ew['return_pct']:.2f} (EW) | {port['return_pct'] - ew['return_pct']:.2f} |"
    )

    lines.extend(
        [
            "",
            "## Relative allocation (trajectory scores)",
            "",
            f"- Final equity: **{rel['final_equity']:.2f}** | return "
            f"**{rel['return_pct']:.2f}%** | max DD **{rel['max_drawdown_pct']:.2f}%**",
            f"- Sharpe~: {_fmt(rel.get('sharpe_like'))} | rebalances: {rel['n_rebalances']} | "
            f"fees: {rel['total_fees']:.2f}",
            f"- Excess vs equal-weight B&H: **{rel['return_pct'] - ew['return_pct']:.2f}%**",
            "",
            "## OOS prediction diagnostics (1d)",
            "",
            "| Pair | N | Corr | Dir acc | Pred mean | Realized mean |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for pair in payload["pairs"]:
        d = diag.get(pair, {})
        if not d:
            lines.append(f"| `{pair}` | n/a | n/a | n/a | n/a | n/a |")
            continue
        corr = d.get("corr_1d")
        lines.append(
            f"| `{pair}` | {d['n_oos']} | {_fmt(corr, 3)} | "
            f"{d['dir_acc_1d'] * 100:.1f}% | {d['pred_mean_1d'] * 100:.3f}% | "
            f"{d['realized_mean_1d'] * 100:.3f}% |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Kraken public OHLC ≈ 720 daily bars (~2y); short sample, regime-sensitive.",
            "- Inner-join alignment drops days missing any pair.",
            "- Linear multi-output Ridge is weak on noisy crypto returns; direction accuracy",
            "  near coin-flip is expected — treat results as research, not an edge claim.",
            "- Fees (~0.26%/side) punish frequent long/flat flips and relative rebalances.",
            "- Expanding walk-forward still reuses sequential OOS for reporting (no nested CV).",
            "- B&H benchmarks are computed on the same OOS window as the strategy (after min_train).",
            "- Spot long-only; no shorts, funding, or borrow.",
            "- Paper simulation only — no live trading. Past paper ≠ future live.",
            "",
            "## Re-run",
            "",
            "```bash",
            "cd /workspace/orca && source .venv/bin/activate",
            "python scripts/run_multi_pair_trajectory.py --cache",
            "# or: python scripts/run_multi_pair_trajectory.py --refresh",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper multi-pair trajectory backtest")
    parser.add_argument(
        "--pairs",
        default=",".join(DEFAULT_PAIRS),
        help="Comma-separated pairs (default BTC/USD,SOL/USD,XRP/USD)",
    )
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--cache", action="store_true", default=True)
    parser.add_argument("--refresh", action="store_true", help="Fetch fresh OHLC via ccxt")
    parser.add_argument("--min-train", type=int, default=120)
    parser.add_argument("--retrain-every", type=int, default=21)
    parser.add_argument("--model", default="ridge", choices=["ridge", "gbr"])
    parser.add_argument("--alpha", type=float, default=1.0)
    parser.add_argument("--fee", type=float, default=None)
    parser.add_argument("--horizons", default="1,2,3,5")
    args = parser.parse_args()

    cfg = load_config()
    if cfg.get("mode") != "paper":
        raise SystemExit("Refusing to run: config mode is not paper")

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    if not horizons:
        horizons = DEFAULT_HORIZONS

    port = dict(cfg.get("portfolio", {}))
    if args.fee is not None:
        port["fee_rate"] = args.fee
    fee_rate = float(port.get("fee_rate", 0.0026))
    initial_cash = float(port.get("initial_cash", 10_000.0))
    position_size = float(port.get("position_size", 0.95))
    limit = int(cfg.get("backtest", {}).get("limit", 720))

    print(
        f"Mode: paper | pairs={pairs} | model=MultiOutput({args.model}) | "
        f"fee={fee_rate} | horizons={list(horizons)}"
    )

    ohlcv = load_or_fetch_pairs(
        pairs,
        args.timeframe,
        limit,
        refresh=args.refresh,
        exchange_cfg=cfg.get("exchange"),
    )
    aligned = align_closes(ohlcv, pairs)
    if len(aligned) < args.min_train + max(horizons) + 30:
        raise SystemExit(f"Aligned sample too short: {len(aligned)} bars")

    feat = build_shared_features(aligned, pairs)
    targets = build_trajectory_targets(aligned, pairs, horizons)
    preds, wf_meta = walk_forward_predict(
        feat,
        targets,
        min_train=args.min_train,
        retrain_every=args.retrain_every,
        expanding=True,
        model_name=args.model,
        alpha=args.alpha,
    )
    if wf_meta["fit_calls"] == 0:
        raise SystemExit("No successful walk-forward fits — aborting")

    scores = trajectory_score(preds, pairs, horizons)
    signals = scores_to_long_flat_signals(scores, pairs, use_1d=True)
    weights = scores_to_relative_weights(scores, pairs, use_1d=True)

    # OOS window: from first non-zero signal bar through end (fair vs B&H)
    nz = (signals != 0).any(axis=1)
    if not bool(nz.any()):
        raise SystemExit("No non-zero signals produced")
    oos_start = nz.idxmax()
    aligned_oos = aligned.loc[oos_start:]
    signals_oos = signals.loc[oos_start:]
    weights_oos = weights.loc[oos_start:]
    logger.info(
        "OOS window %s → %s (%d bars)",
        aligned_oos.index[0],
        aligned_oos.index[-1],
        len(aligned_oos),
    )

    lf = simulate_per_pair_long_flat(
        aligned_oos,
        signals_oos,
        pairs,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
    )
    rel = simulate_relative_allocation(
        aligned_oos,
        weights_oos,
        pairs,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        rebalance_threshold=0.20,
        min_hold_bars=5,
    )
    ew = equal_weight_buy_hold(
        aligned_oos,
        pairs,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
    )
    bh = single_asset_buy_hold(
        aligned_oos,
        pairs,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
    )
    diag = prediction_diagnostics(preds, targets, pairs)

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)

    try:
        from zoneinfo import ZoneInfo

        local_now = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:  # noqa: BLE001
        local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def strip_eq(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if k != "equity_curve"}

    lf_json = {k: (strip_eq(v) if isinstance(v, dict) else v) for k, v in lf.items()}
    payload: dict[str, Any] = {
        "mode": "paper",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_at_local": local_now,
        "pairs": pairs,
        "timeframe": args.timeframe,
        "horizons": list(horizons),
        "candles": len(aligned),
        "start": str(aligned.index[0]),
        "end": str(aligned.index[-1]),
        "oos_start": str(aligned_oos.index[0]),
        "oos_end": str(aligned_oos.index[-1]),
        "oos_candles": len(aligned_oos),
        "fee_rate": fee_rate,
        "initial_cash": initial_cash,
        "position_size": position_size,
        "model": f"MultiOutputRegressor({args.model})",
        "walk_forward": wf_meta,
        "long_flat": lf_json,
        "relative_alloc": strip_eq(rel),
        "equal_weight_bh": strip_eq(ew),
        "single_bh": bh,
        "diagnostics": diag,
        "caveats": [
            "Short Kraken public OHLC sample (~720d)",
            "No TabPFN; sklearn Ridge multi-output only",
            "Paper only; fees ~0.26%/side",
            "Weak signal expected on crypto noise",
        ],
    }

    json_path = logs / "multi_pair_trajectory.json"
    md_path = logs / "multi_pair_trajectory.md"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    write_markdown(md_path, {**payload, "long_flat": lf, "relative_alloc": rel, "equal_weight_bh": ew})

    # Equity curves
    if "equity_curve" in lf["portfolio"]:
        lf["portfolio"]["equity_curve"].to_csv(logs / "multi_pair_trajectory_longflat_equity.csv")
    if "equity_curve" in rel:
        rel["equity_curve"].to_csv(logs / "multi_pair_trajectory_rel_equity.csv")
    scores.to_csv(logs / "multi_pair_trajectory_scores.csv")
    signals.to_csv(logs / "multi_pair_trajectory_signals.csv")

    print("--- Paper multi-pair trajectory summary ---")
    print(f"Aligned bars: {len(aligned)} | fits: {wf_meta['fit_calls']}")
    for pair in pairs:
        m = lf[pair]
        print(
            f"  Long/flat {pair}: ret={m['return_pct']:.2f}% "
            f"mdd={m['max_drawdown_pct']:.2f}% vs BH={m['buy_hold_return_pct']:.2f}%"
        )
    print(
        f"  Long/flat portfolio: ret={lf['portfolio']['return_pct']:.2f}% "
        f"mdd={lf['portfolio']['max_drawdown_pct']:.2f}%"
    )
    print(
        f"  Relative alloc: ret={rel['return_pct']:.2f}% "
        f"mdd={rel['max_drawdown_pct']:.2f}% rebals={rel['n_rebalances']}"
    )
    print(f"  Equal-weight B&H: ret={ew['return_pct']:.2f}%")
    for pair in pairs:
        print(f"  B&H {pair}: ret={bh[pair]['return_pct']:.2f}%")
    print(f"JSON:     {json_path}")
    print(f"Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

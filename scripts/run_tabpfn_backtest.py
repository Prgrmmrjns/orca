#!/usr/bin/env python3
"""Paper-only walk-forward TabPFN backtest (Prior Labs cloud API).

HARD RULES: no live trades; never print/log API keys; do not commit .env.
Uses Orca paper portfolio (fee ~0.26%), BTC/USD daily OHLC, long/flat only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from orca.backtest import simulate_portfolio
from orca.data import load_ohlcv
from orca.logging_setup import setup_logging
from orca.metrics import compute_metrics
from orca.strategy import generate_signals, rsi, sma

logger = setup_logging()

# ---------------------------------------------------------------------------
# Auth helpers (never log token values)
# ---------------------------------------------------------------------------


def _mask_token(token: str) -> str:
    n = len(token)
    if n == 0:
        return "(empty)"
    if n <= 8:
        return f"**** (len={n})"
    return f"{token[:4]}...{token[-2:]} (len={n})"


def load_tabpfn_token() -> str:
    """Load token from .env / env. Accepts TABPFN_API_KEY or TABPFN_TOKEN."""
    load_dotenv(ROOT / ".env", override=False)
    token = (os.getenv("TABPFN_API_KEY") or os.getenv("TABPFN_TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "Missing TABPFN_API_KEY (or TABPFN_TOKEN) in /workspace/orca/.env — "
            "paper TabPFN backtest cannot call the Prior Labs API."
        )
    # Client docs look for TABPFN_TOKEN; mirror without printing.
    os.environ.setdefault("TABPFN_TOKEN", token)
    return token


def authenticate_tabpfn(token: str) -> str:
    """Set in-memory access token; return package version string."""
    import tabpfn_client
    from tabpfn_client import set_access_token

    set_access_token(token)
    ver = getattr(tabpfn_client, "__version__", "unknown")
    logger.info("tabpfn-client authenticated (token %s) version=%s", _mask_token(token), ver)
    return ver


# ---------------------------------------------------------------------------
# Features / target (causal — no look-ahead)
# ---------------------------------------------------------------------------


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Lagged returns + simple TA. All columns use only data <= t."""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    ret = close.pct_change()

    feat = pd.DataFrame(index=df.index)
    for lag in (1, 2, 3, 5, 10, 20):
        feat[f"ret_{lag}"] = close.pct_change(lag)

    feat["vol_10"] = ret.rolling(10, min_periods=10).std()
    feat["vol_20"] = ret.rolling(20, min_periods=20).std()
    feat["rsi_14"] = rsi(close, 14)

    sma10 = sma(close, 10)
    sma30 = sma(close, 30)
    feat["sma_ratio_10"] = close / sma10 - 1.0
    feat["sma_ratio_30"] = close / sma30 - 1.0
    feat["sma_spread"] = sma10 / sma30 - 1.0

    feat["hl_range"] = (high - low) / close.replace(0, np.nan)
    vol_sma = sma(volume, 20)
    feat["vol_z"] = volume / vol_sma - 1.0
    feat["mom_5"] = close / close.shift(5) - 1.0
    feat["mom_10"] = close / close.shift(10) - 1.0

    return feat


def build_target(df: pd.DataFrame) -> pd.Series:
    """Next-bar direction: 1 if close[t+1] > close[t], else 0. NaN on last bar."""
    close = df["close"].astype(float)
    nxt = close.shift(-1)
    y = (nxt > close).astype(float)
    y[nxt.isna()] = np.nan
    return y.rename("target")


# ---------------------------------------------------------------------------
# Walk-forward TabPFN
# ---------------------------------------------------------------------------


def walk_forward_signals(
    df: pd.DataFrame,
    *,
    min_train: int = 120,
    train_window: int = 180,
    retrain_every: int = 21,
    n_estimators: int = 4,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Periodic retrain on past window only; predict next-bar direction.

    At each retrain point i:
      - train on rows [i-train_window, i) with known targets (features@t, y=dir t→t+1)
      - batch-predict features for bars [i, i+retrain_every)
    Signal: +1 if predicted up else -1 (long/flat). Execution uses prior signal @ open.
    """
    from tabpfn_client import TabPFNClassifier

    feat = build_features(df)
    target = build_target(df)
    feature_cols = list(feat.columns)

    # Rows usable for training need finite features + known target
    usable = feat.notna().all(axis=1) & target.notna()
    pred_up = pd.Series(np.nan, index=df.index, dtype=float)
    signal = pd.Series(0, index=df.index, dtype=int)

    n = len(df)
    fit_calls = 0
    predict_calls = 0
    predict_rows = 0
    last_error: str | None = None
    t0 = time.time()

    # First index where we have enough prior usable train rows
    usable_idx = np.where(usable.to_numpy())[0]
    if len(usable_idx) < min_train + 1:
        raise RuntimeError(
            f"Not enough usable rows for walk-forward "
            f"(need >={min_train}+1, got {len(usable_idx)})"
        )

    # Start predicting at the first bar where >= min_train prior usable samples exist
    start_i = None
    for i in range(n):
        prior = usable.iloc[:i].sum()
        if prior >= min_train:
            start_i = i
            break
    if start_i is None:
        raise RuntimeError("Could not find walk-forward start index")

    i = start_i
    while i < n:
        # Training pool: usable rows strictly before i (target at j uses close[j+1] <= close[i])
        train_mask = usable & (np.arange(n) < i)
        train_pos = np.where(train_mask.to_numpy())[0]
        if len(train_pos) < min_train:
            i += 1
            continue
        if train_window > 0 and len(train_pos) > train_window:
            train_pos = train_pos[-train_window:]

        X_train = feat.iloc[train_pos][feature_cols]
        y_train = target.iloc[train_pos].astype(int)

        end_pred = min(i + retrain_every, n)
        pred_slice = list(range(i, end_pred))
        # Only predict rows with finite features
        pred_ok = [j for j in pred_slice if bool(feat.iloc[j].notna().all())]
        if not pred_ok:
            i = end_pred
            continue

        X_pred = feat.iloc[pred_ok][feature_cols]

        try:
            clf = TabPFNClassifier(n_estimators=n_estimators, random_state=42)
            clf.fit(X_train, y_train)
            fit_calls += 1
            yhat = clf.predict(X_pred)
            predict_calls += 1
            predict_rows += len(pred_ok)
            for j, pred in zip(pred_ok, yhat):
                up = int(pred) == 1
                pred_up.iloc[j] = 1.0 if up else 0.0
                signal.iloc[j] = 1 if up else -1
            logger.info(
                "WF fit#%d train=%d (%s→%s) predict=%d bars [%s→%s]",
                fit_calls,
                len(train_pos),
                df.index[train_pos[0]].date(),
                df.index[train_pos[-1]].date(),
                len(pred_ok),
                df.index[pred_ok[0]].date(),
                df.index[pred_ok[-1]].date(),
            )
        except Exception as exc:  # noqa: BLE001 — surface API errors, continue if possible
            last_error = f"{type(exc).__name__}: {exc}"
            logger.error("TabPFN API error at i=%d: %s", i, last_error)
            # Skip this block rather than crash entire run mid-way
            i = end_pred
            time.sleep(2.0)
            continue

        i = end_pred
        # Gentle pacing between refits
        time.sleep(0.4)

    out = df.copy()
    out["pred_up"] = pred_up
    out["signal"] = signal
    meta = {
        "min_train": min_train,
        "train_window": train_window,
        "retrain_every": retrain_every,
        "n_estimators": n_estimators,
        "feature_cols": feature_cols,
        "fit_calls": fit_calls,
        "predict_calls": predict_calls,
        "predict_rows": predict_rows,
        "start_predict": str(df.index[start_i]),
        "elapsed_sec": round(time.time() - t0, 1),
        "last_error": last_error,
        "n_signal_long": int((signal == 1).sum()),
        "n_signal_flat": int((signal == -1).sum()),
        "n_signal_zero": int((signal == 0).sum()),
    }
    return out, meta


# ---------------------------------------------------------------------------
# Benchmarks + report
# ---------------------------------------------------------------------------


def run_sma_benchmark(df: pd.DataFrame, port_cfg: dict[str, Any]) -> dict[str, Any]:
    signals = generate_signals(
        df, {"name": "sma_crossover", "fast_period": 10, "slow_period": 30}
    )
    portfolio, equity = simulate_portfolio(
        signals,
        initial_cash=float(port_cfg.get("initial_cash", 10_000)),
        fee_rate=float(port_cfg.get("fee_rate", 0.0026)),
        position_size=float(port_cfg.get("position_size", 0.95)),
    )
    metrics = compute_metrics(portfolio, equity, signals["close"], periods_per_year=365.0)
    return {
        "strategy": "sma_crossover",
        "return_pct": metrics["return_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "n_trades": metrics["n_trades"],
        "win_rate_pct": metrics.get("win_rate_pct"),
        "sharpe_like": metrics.get("sharpe_like"),
        "buy_hold_return_pct": metrics["buy_hold_return_pct"],
        "excess_vs_buy_hold_pct": metrics["excess_vs_buy_hold_pct"],
        "final_equity": metrics["final_equity"],
        "total_fees": metrics["total_fees"],
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    tab = payload["tabpfn"]
    sma = payload.get("sma_crossover")
    wf = payload["walk_forward"]
    lines = [
        "# TabPFN paper backtest (walk-forward)",
        "",
        f"Generated: {payload['generated_at_local']} (Europe/Berlin)",
        "Mode: `paper` — no live orders. Prior Labs TabPFN cloud client only.",
        "",
        f"- Pair / TF: `{payload['pair']}` `{payload['timeframe']}`",
        f"- Candles: {payload['candles']} ({payload['start']} → {payload['end']})",
        f"- Fee rate: `{payload['fee_rate']}` ({payload['fee_rate'] * 100:.2f}% per side)",
        f"- Package: `tabpfn-client=={payload['package_version']}`",
        f"- Auth env: `{payload['auth_env_var']}` (value masked, never logged)",
        "",
        "## Method",
        "",
        "- Features: lagged returns (1/2/3/5/10/20), rolling vol, RSI-14, SMA ratios,",
        "  HL range, volume z-score, momentum — all causal (no look-ahead).",
        "- Target: next-bar close direction (up=1 / down=0).",
        f"- Train window: rolling last `{wf['train_window']}` usable rows "
        f"(min `{wf['min_train']}`).",
        f"- Retrain every `{wf['retrain_every']}` bars; batch-predict until next refit.",
        "- Trade rule: long-only when predicted up; otherwise flat (signal −1).",
        "- Fills: prior-bar signal @ next open (same as Orca).",
        f"- API usage this run: {wf['fit_calls']} fit / {wf['predict_calls']} predict "
        f"({wf['predict_rows']} rows), {wf['elapsed_sec']}s.",
        "",
        "## Results",
        "",
        "| Strategy | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def row(name: str, m: dict[str, Any]) -> str:
        wr = m.get("win_rate_pct")
        sh = m.get("sharpe_like")
        wr_s = f"{wr:.1f}" if wr is not None else "n/a"
        sh_s = f"{sh:.2f}" if sh is not None else "n/a"
        return (
            f"| `{name}` | {m['return_pct']:.2f} | {m['max_drawdown_pct']:.2f} | "
            f"{m['n_trades']} | {wr_s} | {sh_s} | {m['buy_hold_return_pct']:.2f} | "
            f"{m['excess_vs_buy_hold_pct']:.2f} |"
        )

    lines.append(row("tabpfn_walkforward", tab))
    if sma:
        lines.append(row("sma_crossover", sma))
    lines.extend(
        [
            "",
            f"Buy & hold (fee on entry): **{tab['buy_hold_return_pct']:.2f}%**.",
            f"TabPFN final equity: **{tab['final_equity']:.2f}** "
            f"(initial {tab['initial_cash']:.2f}); fees paid {tab['total_fees']:.2f}.",
            "",
            "## Caveats",
            "",
            "- Short sample (~720 Kraken public daily candles ≈ 2y).",
            "- Periodic retrain (not every bar) → stale model between refits.",
            "- TabPFN cloud API rate / quota limits; n_estimators kept small.",
            "- Direction classification ≠ calibrated edge; fees (~0.26%/side) hurt churn.",
            "- Paper simulation only — no live trading.",
            "- Past paper performance does not predict live results.",
            "",
            "## Re-run",
            "",
            "```bash",
            "cd /workspace/orca && source .venv/bin/activate",
            "python scripts/run_tabpfn_backtest.py --cache",
            "```",
            "",
        ]
    )
    if wf.get("last_error"):
        lines.insert(-6, f"- Last API error (run continued/skipped blocks): `{wf['last_error']}`")
        lines.insert(-6, "")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper TabPFN walk-forward backtest")
    parser.add_argument("--pair", default="BTC/USD")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--cache", action="store_true", default=True, help="Use data/*.csv (default)")
    parser.add_argument("--refresh", action="store_true", help="Fetch fresh OHLC via ccxt")
    parser.add_argument("--min-train", type=int, default=120)
    parser.add_argument("--train-window", type=int, default=180)
    parser.add_argument("--retrain-every", type=int, default=21)
    parser.add_argument("--n-estimators", type=int, default=4)
    parser.add_argument("--fee", type=float, default=None)
    args = parser.parse_args()

    token = load_tabpfn_token()
    pkg_ver = authenticate_tabpfn(token)
    auth_env = "TABPFN_API_KEY" if os.getenv("TABPFN_API_KEY") else "TABPFN_TOKEN"

    from orca.config import load_config
    from orca.data import fetch_ohlcv, save_ohlcv

    cfg = load_config()
    if cfg.get("mode") != "paper":
        raise SystemExit("Refusing to run: config mode is not paper")
    pair = args.pair
    timeframe = args.timeframe
    port = cfg.get("portfolio", {})
    if args.fee is not None:
        port = {**port, "fee_rate": args.fee}
    fee_rate = float(port.get("fee_rate", 0.0026))

    csv_path = ROOT / "data" / f"{pair.replace('/', '_')}_{timeframe}.csv"
    if args.refresh or not csv_path.exists():
        df = fetch_ohlcv(pair=pair, timeframe=timeframe, limit=int(cfg.get("backtest", {}).get("limit", 720)))
        save_ohlcv(df, csv_path)
    else:
        logger.info("Loading cached OHLCV %s", csv_path)
        df = load_ohlcv(csv_path)

    print(
        f"Mode: paper | pair={pair} | strategy=tabpfn_walkforward | "
        f"fee={fee_rate} | tabpfn-client={pkg_ver}"
    )
    print(f"Auth: {auth_env} present {_mask_token(token)} — value not printed further.\n")

    signals, wf_meta = walk_forward_signals(
        df,
        min_train=args.min_train,
        train_window=args.train_window,
        retrain_every=args.retrain_every,
        n_estimators=args.n_estimators,
    )

    if wf_meta["fit_calls"] == 0:
        raise SystemExit(
            "No successful TabPFN fits — aborting without writing a fake report. "
            f"last_error={wf_meta.get('last_error')}"
        )

    portfolio, equity = simulate_portfolio(
        signals,
        initial_cash=float(port.get("initial_cash", 10_000)),
        fee_rate=fee_rate,
        position_size=float(port.get("position_size", 0.95)),
    )
    tab_metrics = compute_metrics(portfolio, equity, signals["close"], periods_per_year=365.0)
    sma_metrics = run_sma_benchmark(df, port)

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)

    # Berlin-local timestamp for the report
    try:
        from zoneinfo import ZoneInfo

        local_now = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:  # noqa: BLE001
        local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload: dict[str, Any] = {
        "mode": "paper",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_at_local": local_now,
        "pair": pair,
        "timeframe": timeframe,
        "candles": len(df),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "fee_rate": fee_rate,
        "package": "tabpfn-client",
        "package_version": pkg_ver,
        "auth_env_var": auth_env,
        "ohlcv_path": str(csv_path),
        "walk_forward": wf_meta,
        "tabpfn": tab_metrics,
        "sma_crossover": sma_metrics,
    }

    json_path = logs / "tabpfn_backtest.json"
    md_path = logs / "tabpfn_backtest.md"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    write_markdown(md_path, payload)

    fills_path = logs / "tabpfn_fills.csv"
    eq_path = logs / "tabpfn_equity.csv"
    portfolio.fills_frame().to_csv(fills_path, index=False)
    equity.to_csv(eq_path)

    print("--- Paper TabPFN backtest summary ---")
    print(f"Return:       {tab_metrics['return_pct']:.2f}%")
    print(f"Max drawdown: {tab_metrics['max_drawdown_pct']:.2f}%")
    print(f"Trades:       {tab_metrics['n_trades']}")
    print(f"Buy & hold:   {tab_metrics['buy_hold_return_pct']:.2f}%")
    print(f"Excess vs BH: {tab_metrics['excess_vs_buy_hold_pct']:.2f}%")
    print(f"SMA xover:    {sma_metrics['return_pct']:.2f}% (excess {sma_metrics['excess_vs_buy_hold_pct']:.2f}%)")
    print(f"Fits/preds:   {wf_meta['fit_calls']} / {wf_meta['predict_calls']} ({wf_meta['elapsed_sec']}s)")
    print(f"JSON:         {json_path}")
    print(f"Markdown:     {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

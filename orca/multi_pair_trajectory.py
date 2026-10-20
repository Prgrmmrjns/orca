"""Paper-only multi-pair short-horizon trajectory model (sklearn, no GPU).

Jointly predicts next 1–5 day returns for BTC/USD, SOL/USD, XRP/USD from a
shared causal feature matrix (lags, cross-asset returns, rolling correlations).
Walk-forward expanding window; long/flat or relative allocation. No live trades.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.multioutput import MultiOutputRegressor

from orca.logging_setup import setup_logging
from orca.metrics import buy_and_hold_return, max_drawdown, sharpe_like
from orca.portfolio import PaperPortfolio
from orca.strategy import rsi

logger = setup_logging()

DEFAULT_PAIRS = ("BTC/USD", "SOL/USD", "XRP/USD")
DEFAULT_HORIZONS = (1, 2, 3, 5)
LAGS = (1, 2, 3, 5, 10)


def _slug(pair: str) -> str:
    return pair.replace("/", "_")


def align_closes(
    ohlcv_by_pair: dict[str, pd.DataFrame],
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
) -> pd.DataFrame:
    """Inner-join close/open/high/low/volume across pairs on UTC timestamps."""
    frames: list[pd.DataFrame] = []
    for pair in pairs:
        df = ohlcv_by_pair[pair].copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        slug = _slug(pair)
        part = pd.DataFrame(
            {
                f"{slug}_open": df["open"].astype(float),
                f"{slug}_high": df["high"].astype(float),
                f"{slug}_low": df["low"].astype(float),
                f"{slug}_close": df["close"].astype(float),
                f"{slug}_volume": df["volume"].astype(float),
            },
            index=df.index,
        )
        frames.append(part)
    aligned = frames[0]
    for part in frames[1:]:
        aligned = aligned.join(part, how="inner")
    aligned = aligned.sort_index()
    aligned = aligned[~aligned.index.duplicated(keep="last")]
    logger.info(
        "Aligned %d bars across %s (%s → %s)",
        len(aligned),
        list(pairs),
        aligned.index[0] if len(aligned) else None,
        aligned.index[-1] if len(aligned) else None,
    )
    return aligned


def build_shared_features(
    aligned: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    corr_window: int = 20,
) -> pd.DataFrame:
    """Causal shared feature matrix — all columns use only data <= t."""
    feat = pd.DataFrame(index=aligned.index)
    daily_rets: dict[str, pd.Series] = {}

    for pair in pairs:
        slug = _slug(pair)
        close = aligned[f"{slug}_close"]
        volume = aligned[f"{slug}_volume"]
        high = aligned[f"{slug}_high"]
        low = aligned[f"{slug}_low"]
        ret = close.pct_change()
        daily_rets[slug] = ret

        for lag in LAGS:
            feat[f"{slug}_ret_{lag}"] = close.pct_change(lag)
        feat[f"{slug}_vol_10"] = ret.rolling(10, min_periods=10).std()
        feat[f"{slug}_vol_20"] = ret.rolling(20, min_periods=20).std()
        feat[f"{slug}_rsi_14"] = rsi(close, 14)
        feat[f"{slug}_mom_5"] = close / close.shift(5) - 1.0
        feat[f"{slug}_mom_10"] = close / close.shift(10) - 1.0
        feat[f"{slug}_hl_range"] = (high - low) / close.replace(0, np.nan)
        vol_sma = volume.rolling(20, min_periods=20).mean()
        feat[f"{slug}_vol_z"] = volume / vol_sma - 1.0

    slugs = [_slug(p) for p in pairs]
    for i, a in enumerate(slugs):
        for b in slugs[i + 1 :]:
            feat[f"corr_{a}_{b}_{corr_window}"] = (
                daily_rets[a].rolling(corr_window, min_periods=corr_window).corr(daily_rets[b])
            )
            feat[f"spread_{a}_{b}_1"] = daily_rets[a] - daily_rets[b]

    return feat


def build_trajectory_targets(
    aligned: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    horizons: tuple[int, ...] | list[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Multi-horizon forward returns: (close[t+h] / close[t]) - 1 per pair.

    NaN on the last `max(horizons)` bars (unknown future).
    """
    cols: dict[str, pd.Series] = {}
    for pair in pairs:
        slug = _slug(pair)
        close = aligned[f"{slug}_close"]
        for h in horizons:
            cols[f"{slug}_fwd_{h}"] = close.shift(-h) / close - 1.0
    return pd.DataFrame(cols, index=aligned.index)


def make_model(model_name: str = "ridge", alpha: float = 1.0, random_state: int = 42):
    """CPU-only multi-output regressor. TabPFN is intentionally not used here."""
    name = model_name.lower()
    if name == "ridge":
        base = Ridge(alpha=alpha, random_state=random_state)
    elif name in ("gbr", "gradient_boosting"):
        from sklearn.ensemble import GradientBoostingRegressor

        base = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            random_state=random_state,
        )
    else:
        raise ValueError(f"Unknown model_name={model_name!r}; use ridge|gbr")
    return MultiOutputRegressor(base)


def walk_forward_predict(
    feat: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    min_train: int = 120,
    retrain_every: int = 21,
    expanding: bool = True,
    train_window: int = 0,
    model_name: str = "ridge",
    alpha: float = 1.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Expanding (or rolling) walk-forward multi-output predictions.

    At retrain index i: fit on rows with known targets strictly before i;
    predict features for [i, i+retrain_every).
    """
    feature_cols = list(feat.columns)
    target_cols = list(targets.columns)
    n = len(feat)
    preds = pd.DataFrame(np.nan, index=feat.index, columns=target_cols)

    usable = feat.notna().all(axis=1) & targets.notna().all(axis=1)
    usable_idx = np.where(usable.to_numpy())[0]
    if len(usable_idx) < min_train + 1:
        raise RuntimeError(
            f"Not enough usable rows for walk-forward "
            f"(need >={min_train}+1, got {len(usable_idx)})"
        )

    start_i = None
    for i in range(n):
        if int(usable.iloc[:i].sum()) >= min_train:
            start_i = i
            break
    if start_i is None:
        raise RuntimeError("Could not find walk-forward start index")

    fit_calls = 0
    predict_rows = 0
    i = start_i
    while i < n:
        train_mask = usable & (np.arange(n) < i)
        train_pos = np.where(train_mask.to_numpy())[0]
        if len(train_pos) < min_train:
            i += 1
            continue
        if not expanding and train_window > 0 and len(train_pos) > train_window:
            train_pos = train_pos[-train_window:]
        elif expanding and train_window > 0 and len(train_pos) > train_window:
            # optional cap even in expanding mode
            train_pos = train_pos[-train_window:]

        end_pred = min(i + retrain_every, n)
        pred_ok = [j for j in range(i, end_pred) if bool(feat.iloc[j].notna().all())]
        if not pred_ok:
            i = end_pred
            continue

        X_train = feat.iloc[train_pos][feature_cols].to_numpy()
        y_train = targets.iloc[train_pos][target_cols].to_numpy()
        X_pred = feat.iloc[pred_ok][feature_cols].to_numpy()

        model = make_model(model_name=model_name, alpha=alpha)
        model.fit(X_train, y_train)
        fit_calls += 1
        yhat = model.predict(X_pred)
        predict_rows += len(pred_ok)
        for row_i, j in enumerate(pred_ok):
            preds.iloc[j] = yhat[row_i]

        logger.info(
            "WF fit#%d train=%d predict=%d bars [%s→%s]",
            fit_calls,
            len(train_pos),
            len(pred_ok),
            feat.index[pred_ok[0]].date(),
            feat.index[pred_ok[-1]].date(),
        )
        i = end_pred

    meta = {
        "min_train": min_train,
        "retrain_every": retrain_every,
        "expanding": expanding,
        "train_window": train_window,
        "model_name": model_name,
        "alpha": alpha,
        "feature_cols": feature_cols,
        "target_cols": target_cols,
        "fit_calls": fit_calls,
        "predict_rows": predict_rows,
        "start_predict": str(feat.index[start_i]),
        "n_features": len(feature_cols),
        "n_targets": len(target_cols),
    }
    return preds, meta


def trajectory_score(
    preds: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    horizons: tuple[int, ...] | list[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Per-pair score = mean predicted forward return over horizons (trajectory)."""
    scores = pd.DataFrame(index=preds.index)
    for pair in pairs:
        slug = _slug(pair)
        cols = [f"{slug}_fwd_{h}" for h in horizons if f"{slug}_fwd_{h}" in preds.columns]
        scores[slug] = preds[cols].mean(axis=1)
        # Also expose 1d predicted return for simple long/flat
        h1 = f"{slug}_fwd_1"
        if h1 in preds.columns:
            scores[f"{slug}_1d"] = preds[h1]
    return scores


def scores_to_long_flat_signals(
    scores: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    use_1d: bool = True,
    threshold: float = 0.0,
) -> pd.DataFrame:
    """+1 long if predicted score > threshold else -1 (flat)."""
    sig = pd.DataFrame(index=scores.index)
    for pair in pairs:
        slug = _slug(pair)
        col = f"{slug}_1d" if use_1d and f"{slug}_1d" in scores.columns else slug
        s = scores[col]
        out = pd.Series(0, index=scores.index, dtype=int)
        out[s.notna() & (s > threshold)] = 1
        out[s.notna() & (s <= threshold)] = -1
        sig[slug] = out
    return sig


def scores_to_relative_weights(
    scores: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    use_1d: bool = True,
) -> pd.DataFrame:
    """Long-only relative weights: max(score,0) / sum; all-flat if none positive."""
    slugs = [_slug(p) for p in pairs]
    cols = []
    for slug in slugs:
        col = f"{slug}_1d" if use_1d and f"{slug}_1d" in scores.columns else slug
        cols.append(scores[col].clip(lower=0.0))
    pos = pd.concat(cols, axis=1)
    pos.columns = slugs
    total = pos.sum(axis=1)
    weights = pos.div(total.replace(0, np.nan), axis=0).fillna(0.0)
    return weights


def simulate_per_pair_long_flat(
    aligned: pd.DataFrame,
    signals: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.0026,
    position_size: float = 0.95,
) -> dict[str, Any]:
    """Equal cash split; each pair runs independent long/flat paper portfolio."""
    n = len(pairs)
    cash_each = initial_cash / n
    results: dict[str, Any] = {}
    equity_frames: list[pd.Series] = []

    for pair in pairs:
        slug = _slug(pair)
        sig = signals[slug]
        opens = aligned[f"{slug}_open"]
        closes = aligned[f"{slug}_close"]
        # Build signal frame compatible with simulate_portfolio logic
        frame = pd.DataFrame(
            {"open": opens, "close": closes, "signal": sig},
            index=aligned.index,
        )
        port = PaperPortfolio(
            initial_cash=cash_each, fee_rate=fee_rate, position_size=position_size
        )
        rows: list[dict[str, Any]] = []
        for i in range(1, len(frame)):
            prev = int(frame["signal"].iloc[i - 1])
            ts = frame.index[i]
            price = float(frame["open"].iloc[i])
            if prev == 1 and port.base == 0:
                port.buy(ts, price)
            elif prev == -1 and port.base > 0:
                port.sell(ts, price)
            mark = float(frame["close"].iloc[i])
            rows.append({"timestamp": ts, "equity": port.equity(mark)})
        eq = (
            pd.DataFrame(rows).set_index("timestamp")["equity"]
            if rows
            else pd.Series(dtype=float)
        )
        last_px = float(closes.iloc[-1])
        summary = port.summary(last_px)
        bh = buy_and_hold_return(closes, cash_each, fee_rate, position_size)
        mdd = max_drawdown(eq) * 100.0 if len(eq) else 0.0
        sh = sharpe_like(eq, 365.0)
        results[pair] = {
            "initial_cash": cash_each,
            "final_equity": summary["final_equity"],
            "return_pct": summary["return_pct"],
            "max_drawdown_pct": mdd,
            "sharpe_like": sh,
            "n_trades": summary["n_trades"],
            "total_fees": summary["total_fees"],
            "buy_hold_return_pct": bh["return_pct"],
            "excess_vs_buy_hold_pct": summary["return_pct"] - bh["return_pct"],
            "n_long_bars": int((sig == 1).sum()),
            "n_flat_bars": int((sig == -1).sum()),
        }
        equity_frames.append(eq.rename(slug))

    # Portfolio equity = sum of per-pair equities (aligned)
    if equity_frames:
        eq_df = pd.concat(equity_frames, axis=1).dropna(how="all").ffill()
        port_eq = eq_df.sum(axis=1)
    else:
        port_eq = pd.Series(dtype=float)

    port_final = float(port_eq.iloc[-1]) if len(port_eq) else initial_cash
    port_ret = (port_final / initial_cash - 1.0) * 100.0
    results["portfolio"] = {
        "initial_cash": initial_cash,
        "final_equity": port_final,
        "return_pct": port_ret,
        "max_drawdown_pct": max_drawdown(port_eq) * 100.0 if len(port_eq) else 0.0,
        "sharpe_like": sharpe_like(port_eq, 365.0),
        "n_trades": sum(results[p]["n_trades"] for p in pairs),
        "total_fees": sum(results[p]["total_fees"] for p in pairs),
        "equity_curve": port_eq,
    }
    return results


def simulate_relative_allocation(
    aligned: pd.DataFrame,
    weights: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.0026,
    rebalance_threshold: float = 0.20,
    min_hold_bars: int = 5,
) -> dict[str, Any]:
    """Daily mark-to-market with target weights from prior bar; fee on turnover.

    Uses prior-bar weights at current open (no look-ahead). Positions held in
    base units; rebalance when L1 weight distance exceeds threshold and at least
    min_hold_bars have passed since the last rebalance (cuts fee churn).
    """
    slugs = [_slug(p) for p in pairs]
    cash = float(initial_cash)
    holdings = {s: 0.0 for s in slugs}  # base units
    rows: list[dict[str, Any]] = []
    total_fees = 0.0
    n_rebals = 0
    last_rebal_i = -10_000

    for i in range(1, len(aligned)):
        ts = aligned.index[i]
        # Target from prior bar
        w_prev = weights.iloc[i - 1]
        opens = {s: float(aligned[f"{s}_open"].iloc[i]) for s in slugs}
        closes = {s: float(aligned[f"{s}_close"].iloc[i]) for s in slugs}

        # Current equity at open
        eq_open = cash + sum(holdings[s] * opens[s] for s in slugs)
        if eq_open <= 0:
            rows.append({"timestamp": ts, "equity": 0.0})
            continue

        current_w = {
            s: (holdings[s] * opens[s] / eq_open) if eq_open > 0 else 0.0 for s in slugs
        }
        target_w = {s: float(w_prev.get(s, 0.0)) for s in slugs}
        # Residual cash weight
        w_sum = sum(max(target_w[s], 0.0) for s in slugs)
        if w_sum > 1.0:
            for s in slugs:
                target_w[s] = max(target_w[s], 0.0) / w_sum
            w_sum = 1.0
        for s in slugs:
            target_w[s] = max(target_w[s], 0.0)

        l1 = sum(abs(target_w[s] - current_w[s]) for s in slugs)
        held_long_enough = (i - last_rebal_i) >= min_hold_bars
        flat_exit = w_sum == 0.0 and sum(holdings.values()) > 0
        if held_long_enough and (l1 >= rebalance_threshold or flat_exit):
            last_rebal_i = i
            # Trade toward targets at open
            for s in slugs:
                target_val = eq_open * target_w[s]
                cur_val = holdings[s] * opens[s]
                delta = target_val - cur_val
                if abs(delta) < 1e-9 or opens[s] <= 0:
                    continue
                fee = abs(delta) * fee_rate
                total_fees += fee
                if delta > 0:
                    # buy
                    spend = delta + fee
                    if spend > cash:
                        # scale down
                        spend = cash
                        delta = spend / (1.0 + fee_rate)
                        fee = spend - delta
                        total_fees += fee - abs(target_val - cur_val) * fee_rate
                    amount = delta / opens[s]
                    holdings[s] += amount
                    cash -= delta + fee
                else:
                    # sell
                    sell_val = min(-delta, holdings[s] * opens[s])
                    amount = sell_val / opens[s]
                    holdings[s] -= amount
                    cash += sell_val - fee
            n_rebals += 1

        eq_close = cash + sum(holdings[s] * closes[s] for s in slugs)
        rows.append({"timestamp": ts, "equity": eq_close})

    eq = (
        pd.DataFrame(rows).set_index("timestamp")["equity"]
        if rows
        else pd.Series(dtype=float)
    )
    final = float(eq.iloc[-1]) if len(eq) else initial_cash
    return {
        "initial_cash": initial_cash,
        "final_equity": final,
        "return_pct": (final / initial_cash - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown(eq) * 100.0 if len(eq) else 0.0,
        "sharpe_like": sharpe_like(eq, 365.0),
        "n_rebalances": n_rebals,
        "total_fees": total_fees,
        "equity_curve": eq,
    }


def equal_weight_buy_hold(
    aligned: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.0026,
    position_size: float = 0.95,
) -> dict[str, Any]:
    """Buy equal notional of each pair on first bar, hold to end (entry fees only)."""
    n = len(pairs)
    cash_each = (initial_cash * position_size) / n
    cash_left = initial_cash - initial_cash * position_size
    final = cash_left
    per_pair: dict[str, float] = {}
    for pair in pairs:
        slug = _slug(pair)
        close = aligned[f"{slug}_close"]
        entry = float(close.iloc[0])
        exit_p = float(close.iloc[-1])
        fee = cash_each * fee_rate
        amount = (cash_each - fee) / entry
        value = amount * exit_p
        per_pair[pair] = (value / (initial_cash / n) - 1.0) * 100.0  # vs equal slice
        final += value
    eq_points = []
    # Approximate equity curve: mark holdings daily
    amounts = {}
    for pair in pairs:
        slug = _slug(pair)
        entry = float(aligned[f"{slug}_close"].iloc[0])
        fee = cash_each * fee_rate
        amounts[slug] = (cash_each - fee) / entry
    for ts, row in aligned.iterrows():
        eq = cash_left + sum(amounts[_slug(p)] * float(row[f"{_slug(p)}_close"]) for p in pairs)
        eq_points.append(eq)
    eq = pd.Series(eq_points, index=aligned.index)
    return {
        "initial_cash": initial_cash,
        "final_equity": final,
        "return_pct": (final / initial_cash - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown(eq) * 100.0,
        "sharpe_like": sharpe_like(eq, 365.0),
        "per_pair_approx_return_pct": per_pair,
        "total_fees": cash_each * fee_rate * n,
        "equity_curve": eq,
    }


def single_asset_buy_hold(
    aligned: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
    *,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.0026,
    position_size: float = 0.95,
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for pair in pairs:
        slug = _slug(pair)
        close = aligned[f"{slug}_close"]
        out[pair] = buy_and_hold_return(close, initial_cash, fee_rate, position_size)
    return out


def prediction_diagnostics(
    preds: pd.DataFrame,
    targets: pd.DataFrame,
    pairs: tuple[str, ...] | list[str] = DEFAULT_PAIRS,
) -> dict[str, Any]:
    """Simple OOS correlation / direction accuracy on overlapping finite rows."""
    diag: dict[str, Any] = {}
    for pair in pairs:
        slug = _slug(pair)
        col = f"{slug}_fwd_1"
        if col not in preds.columns:
            continue
        mask = preds[col].notna() & targets[col].notna()
        if mask.sum() < 10:
            continue
        y = targets.loc[mask, col]
        yhat = preds.loc[mask, col]
        corr = float(np.corrcoef(y, yhat)[0, 1]) if y.std() > 0 and yhat.std() > 0 else None
        dir_acc = float(((y > 0) == (yhat > 0)).mean())
        diag[pair] = {
            "n_oos": int(mask.sum()),
            "corr_1d": corr,
            "dir_acc_1d": dir_acc,
            "pred_mean_1d": float(yhat.mean()),
            "realized_mean_1d": float(y.mean()),
        }
    return diag

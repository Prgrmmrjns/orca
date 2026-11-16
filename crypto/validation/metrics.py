"""Sharpe, drawdown, deflated Sharpe, and probability of backtest overfitting.

Sharpe is the annualised mean/std of net log-returns (the training objective).
Max drawdown is the minimum of equity/peak - 1, so less-severe drawdowns are
greater (closer to zero).
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np


def sharpe_ratio(log_returns: np.ndarray, periods_per_year: float) -> float:
    r = np.asarray(log_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return float("nan")
    return mu / sd * math.sqrt(float(periods_per_year))


def max_drawdown(simple_returns: np.ndarray) -> float:
    """Most severe peak-to-trough, as a negative fraction (0 if none)."""
    r = np.asarray(simple_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(dd.min())


def simulate_net_simple(
    weights: np.ndarray,
    asset_returns: np.ndarray,
    cost_rate: float,
) -> np.ndarray:
    """Net simple returns with a strict one-bar delay.

    ``weights[t]`` is chosen at the close of bar t (using information <= t)
    and earns ``asset_returns[t + 1]``. Turnover ``|w_t - w_{t-1}|`` is charged
    once per side at that fill. ``weights`` is the risky sleeve only, shape
    ``(T, A)``. The path starts from cash, so the first decision pays for the
    whole entry.
    """
    w = np.asarray(weights, dtype=float)
    rets = np.asarray(asset_returns, dtype=float)
    if w.ndim != 2 or rets.shape != w.shape:
        raise ValueError(f"weights and returns must share shape (T, A); got {w.shape} {rets.shape}")
    if len(w) < 2:
        return np.zeros(0, dtype=float)
    earned = w[:-1]
    prev = np.zeros_like(earned)
    prev[1:] = w[:-2]
    gross = (earned * rets[1:]).sum(axis=1)
    turnover = np.abs(earned - prev).sum(axis=1)
    return gross - turnover * float(cost_rate)


def to_log(simple: np.ndarray) -> np.ndarray:
    r = np.asarray(simple, dtype=float)
    return np.log1p(np.clip(r, -0.999999, None))


def equity_curve(simple: np.ndarray) -> np.ndarray:
    r = np.asarray(simple, dtype=float)
    if len(r) == 0:
        return np.ones(1, dtype=float)
    return np.concatenate([[1.0], np.cumprod(1.0 + r)])


def _moments(log_returns: np.ndarray) -> tuple[float, float]:
    x = np.asarray(log_returns, dtype=float)
    x = x[np.isfinite(x)]
    sd = float(x.std(ddof=1))
    if len(x) < 3 or sd <= 0:
        return 0.0, 3.0
    z = (x - x.mean()) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))  # not excess; Gaussian is 3
    return skew, kurt


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q) + c[5]
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
        return num / den
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q) + c[5]
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
        return -(num / den)
    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r) + 1.0
    return num / den


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Probability the Sharpe beats the expected maximum under ``n_trials``.

    Bailey & Lopez de Prado. ``kurt`` is the raw 4th moment (Gaussian = 3).
    Returns NaN when the sample is too short or the Sharpe is not finite.
    """
    if n_obs < 3 or not math.isfinite(sharpe):
        return float("nan")
    n_trials = max(int(n_trials), 1)
    if n_trials == 1:
        sr0 = 0.0
    else:
        euler = 0.5772156649015329
        z1 = norm_ppf(1.0 - 1.0 / n_trials)
        z2 = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
        sr0 = math.sqrt(1.0 / (n_obs - 1)) * ((1.0 - euler) * z1 + euler * z2)
    var_term = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * sharpe * sharpe
    var_term = max(var_term, 1e-12)
    se = math.sqrt(var_term / (n_obs - 1))
    return norm_cdf((sharpe - sr0) / se)


def deflated_from_returns(
    log_returns: np.ndarray,
    periods_per_year: float,
    n_trials: int,
) -> dict:
    r = np.asarray(log_returns, dtype=float)
    r = r[np.isfinite(r)]
    sr = sharpe_ratio(r, periods_per_year)
    # DSR is defined on the per-period Sharpe, not the annualised one.
    per_period = sr / math.sqrt(periods_per_year) if math.isfinite(sr) else float("nan")
    skew, kurt = _moments(r)
    dsr = deflated_sharpe_ratio(per_period, len(r), n_trials, skew, kurt)
    return {
        "sharpe_annual": None if not math.isfinite(sr) else sr,
        "sharpe_per_period": None if not math.isfinite(per_period) else per_period,
        "skew": skew,
        "kurtosis": kurt,
        "n_obs": int(len(r)),
        "n_trials": int(n_trials),
        "dsr": None if not math.isfinite(dsr) else dsr,
    }


def probability_of_backtest_overfitting(
    returns: np.ndarray,
    *,
    n_groups: int = 8,
    periods_per_year: float = 1.0,
) -> float:
    """CSCV probability of backtest overfitting (Bailey, Borwein, Lopez de Prado).

    ``returns`` has shape ``(T, N)``: one column per strategy trial, rows are
    aligned per-period simple or log returns. PBO is the fraction of
    train/test combinations whose in-sample winner lands below the
    out-of-sample median. NaN when N < 2 or T is too short.
    """
    m = np.asarray(returns, dtype=float)
    if m.ndim != 2:
        raise ValueError("returns must have shape (T, N)")
    t, n = m.shape
    if n < 2 or t < n_groups * 2:
        return float("nan")
    groups = n_groups if n_groups % 2 == 0 else n_groups - 1
    if groups < 2:
        return float("nan")
    bounds = np.linspace(0, t, groups + 1, dtype=int)
    slices = [np.arange(bounds[i], bounds[i + 1]) for i in range(groups)]
    if any(len(s) < 2 for s in slices):
        return float("nan")
    half = groups // 2
    worse = 0
    total = 0
    for comb in combinations(range(groups), half):
        test_idx = np.concatenate([slices[i] for i in comb])
        train_idx = np.concatenate([slices[i] for i in range(groups) if i not in comb])
        is_sr = np.array([sharpe_ratio(m[train_idx, j], periods_per_year) for j in range(n)])
        oos_sr = np.array([sharpe_ratio(m[test_idx, j], periods_per_year) for j in range(n)])
        if not np.isfinite(is_sr).all() or not np.isfinite(oos_sr).all():
            continue
        winner = int(np.argmax(is_sr))
        # Relative rank in (0, 1]: 1 means best out of sample.
        rank = int(np.sum(oos_sr <= oos_sr[winner]))
        rel = rank / n
        worse += int(rel <= 0.5)
        total += 1
    if total == 0:
        return float("nan")
    return worse / total


def buy_and_hold_simple(asset_returns: np.ndarray, cost_rate: float) -> np.ndarray:
    """Fully invested path. The first bar pays a one-way entry cost."""
    r = np.asarray(asset_returns, dtype=float).copy()
    if len(r) == 0:
        return r
    r[0] = r[0] - float(cost_rate)
    return r

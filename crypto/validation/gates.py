"""Promotion gate. A checkpoint passes only when every check is true.

Required:
- net Sharpe of the stitched out-of-sample path is positive
- max drawdown is strictly better (less severe) than BTC buy-and-hold
  on the same bars
- multi-seed: at least two seeds, every seed Sharpe positive
"""

from __future__ import annotations

import math


def evaluate_gate(
    net_sharpe: float,
    strategy_max_dd: float,
    btc_max_dd: float,
    seed_sharpes: list[float],
) -> dict:
    seeds = [float(s) for s in seed_sharpes]
    finite_seeds = [s for s in seeds if math.isfinite(s)]
    positive = math.isfinite(net_sharpe) and net_sharpe > 0.0
    # Drawdowns are negative. -0.20 is better than -0.50.
    dd_better = (
        math.isfinite(strategy_max_dd)
        and math.isfinite(btc_max_dd)
        and strategy_max_dd > btc_max_dd
    )
    stable = len(finite_seeds) >= 2 and all(s > 0.0 for s in finite_seeds)
    reasons: list[str] = []
    if not positive:
        reasons.append(f"net Sharpe {net_sharpe} is not positive")
    if not dd_better:
        reasons.append(
            f"max drawdown {strategy_max_dd} is not better than BTC buy-and-hold {btc_max_dd}"
        )
    if len(finite_seeds) < 2:
        reasons.append("need at least two finite seed Sharpes")
    elif not stable:
        reasons.append(f"seed Sharpes are not all positive: {finite_seeds}")
    return {
        "passed": bool(positive and dd_better and stable),
        "positive_net_sharpe": bool(positive),
        "drawdown_better_than_btc": bool(dd_better),
        "multi_seed_stable": bool(stable),
        "net_sharpe": None if not math.isfinite(net_sharpe) else float(net_sharpe),
        "strategy_max_dd": None if not math.isfinite(strategy_max_dd) else float(strategy_max_dd),
        "btc_max_dd": None if not math.isfinite(btc_max_dd) else float(btc_max_dd),
        "seed_sharpes": finite_seeds,
        "reasons": reasons,
    }

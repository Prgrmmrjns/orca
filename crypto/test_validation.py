"""Purged splitters, Sharpe, DSR, PBO, and the promotion gate. Synthetic only."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from crypto.validation.gates import evaluate_gate
from crypto.validation.metrics import (
    deflated_sharpe_ratio,
    max_drawdown,
    norm_ppf,
    probability_of_backtest_overfitting,
    sharpe_ratio,
    simulate_net_simple,
)
from crypto.validation.splits import combinatorial_purged_splits, purged_walk_forward


def test_one_bar_delay_and_cost():
    # The signal at t earns bar t+1, never bar t. The -50% bar is not collected.
    # The exit to zero is charged on the following bar.
    weights = np.array([[1.0], [1.0], [0.0], [0.0]])
    returns = np.array([[-0.50], [0.10], [0.02], [0.05]])
    net = simulate_net_simple(weights, returns, cost_rate=0.0)
    # Cash after the exit earns none of the last asset return.
    assert net.tolist() == pytest.approx([0.10, 0.02, 0.0])
    charged = simulate_net_simple(weights, returns, cost_rate=0.10)
    assert charged[0] == pytest.approx(0.0)        # entry cost
    assert charged[1] == pytest.approx(0.02)       # unchanged weight
    assert charged[2] == pytest.approx(-0.10)      # exit cost, flat cash return


def test_sharpe_matches_hand_calculation():
    r = np.array([0.01, -0.02, 0.015, 0.005, 0.0])
    expected = r.mean() / r.std(ddof=1) * math.sqrt(365)
    assert sharpe_ratio(r, 365) == pytest.approx(expected)


def test_max_drawdown_sign():
    # +10% then -18.18% from the peak: 1.1 -> 0.9.
    dd = max_drawdown(np.array([0.10, -0.1818181818, 0.05]))
    assert dd == pytest.approx(0.9 / 1.1 - 1.0, rel=1e-4)
    assert dd < 0


def test_purged_walk_forward_embargo_and_horizon():
    index = pd.date_range("2018-01-01", "2020-12-31", freq="D", tz="UTC")
    folds = purged_walk_forward(
        index,
        embargo=pd.Timedelta(days=7),
        horizon_bars=1,
        min_train_years=2,
    )
    assert [f["test_year"] for f in folds] == [2020]
    fold = folds[0]
    train_idx = set(fold["train"].tolist())
    test_idx = set(fold["test"].tolist())
    assert train_idx.isdisjoint(test_idx)
    test_start = index[min(test_idx)]
    assert index[max(train_idx)] <= test_start - pd.Timedelta(days=7)
    # Horizon purge: the bar immediately before the test year is not train.
    before = int(np.flatnonzero(index < test_start)[-1])
    assert before not in train_idx
    assert index.year[fold["train"]].min() == 2018


def test_combinatorial_purged_cv_gap():
    splits = combinatorial_purged_splits(120, n_groups=6, n_test_groups=2, embargo_bars=3, horizon_bars=1)
    assert len(splits) == 15  # C(6, 2)
    for train, test in splits:
        assert set(train).isdisjoint(set(test))
        pad = 4
        banned = set()
        for t in test:
            banned.update(range(max(0, int(t) - pad), min(120, int(t) + pad + 1)))
        assert set(train).isdisjoint(banned)


def test_dsr_falls_as_trials_grow():
    assert norm_ppf(0.975) == pytest.approx(1.95996398454, rel=1e-6)
    few = deflated_sharpe_ratio(0.15, n_obs=500, n_trials=1, skew=0.0, kurt=3.0)
    many = deflated_sharpe_ratio(0.15, n_obs=500, n_trials=100, skew=0.0, kurt=3.0)
    assert few > many


def test_pbo_detects_overfit():
    rng = np.random.default_rng(1)
    t = 400
    # Two strategies that swap rank between the halves of the sample.
    signal = np.concatenate([np.ones(t // 2), -np.ones(t // 2)])
    noise = rng.normal(0, 0.1, size=(t, 2))
    returns = np.column_stack([signal, -signal]) + noise
    pbo = probability_of_backtest_overfitting(returns, n_groups=8, periods_per_year=1.0)
    assert pbo > 0.5


def test_gate_requires_all_three():
    good = evaluate_gate(0.8, -0.2, -0.5, [0.4, 0.9])
    assert good["passed"] is True
    bad = evaluate_gate(-0.1, -0.2, -0.5, [0.4, 0.9])
    assert bad["passed"] is False
    tied = evaluate_gate(0.8, -0.5, -0.5, [0.4, 0.9])
    assert tied["drawdown_better_than_btc"] is False
    unstable = evaluate_gate(0.8, -0.2, -0.5, [0.4, -0.1])
    assert unstable["multi_seed_stable"] is False

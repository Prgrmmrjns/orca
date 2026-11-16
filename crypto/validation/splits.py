"""Purged walk-forward and combinatorial purged cross-validation.

Expanding yearly folds drop the forward label horizon that would overlap the
test year, then apply an embargo (default 7 days) so train labels do not sit
on the test boundary. Combinatorial splits do the same purge around every
test group.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from crypto.spec import EMBARGO_DAYS, HORIZON_BARS, MIN_TRAIN_YEARS


def _utc(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(index)
    if index.tz is None:
        return index.tz_localize("UTC")
    return index.tz_convert("UTC")


def purged_walk_forward(
    index: pd.DatetimeIndex,
    *,
    embargo: pd.Timedelta | None = None,
    horizon_bars: int = HORIZON_BARS,
    min_train_years: int = MIN_TRAIN_YEARS,
) -> list[dict]:
    """Expanding yearly folds.

    Train is every bar strictly before the test year, minus the last
    ``horizon_bars`` (their forward return lands in the test) and minus any
    bar later than ``test_start - embargo``.
    """
    index = _utc(index)
    if embargo is None:
        embargo = pd.Timedelta(days=EMBARGO_DAYS)
    if horizon_bars < 0:
        raise ValueError("horizon_bars must be >= 0")
    years = sorted(set(int(y) for y in index.year))
    if not years:
        return []
    first = years[0]
    folds: list[dict] = []
    for year in years:
        if year - first < min_train_years:
            continue
        test = np.flatnonzero(index.year == year)
        if len(test) == 0:
            continue
        test_start = index[int(test[0])]
        prior = np.flatnonzero(index < test_start)
        if horizon_bars > 0:
            prior = prior[:-horizon_bars] if len(prior) > horizon_bars else np.array([], dtype=int)
        if len(prior) == 0:
            continue
        cutoff = test_start - embargo
        train = np.array([int(i) for i in prior if index[int(i)] <= cutoff], dtype=int)
        if len(train) == 0:
            continue
        folds.append({"test_year": int(year), "train": train, "test": test.astype(int)})
    return folds


def combinatorial_purged_splits(
    n: int,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo_bars: int = 0,
    horizon_bars: int = HORIZON_BARS,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Combinatorial purged CV over contiguous groups.

    Returns ``(train_idx, test_idx)`` pairs. Train bars within
    ``embargo_bars + horizon_bars`` of a test group are removed on both sides.
    """
    if n_groups < 2 or not (1 <= n_test_groups < n_groups):
        raise ValueError("need 1 <= n_test_groups < n_groups and n_groups >= 2")
    if n < n_groups:
        raise ValueError(f"need at least {n_groups} samples, got {n}")
    if embargo_bars < 0 or horizon_bars < 0:
        raise ValueError("embargo_bars and horizon_bars must be >= 0")
    bounds = np.linspace(0, n, n_groups + 1, dtype=int)
    groups = [np.arange(bounds[i], bounds[i + 1]) for i in range(n_groups)]
    pad = int(embargo_bars) + int(horizon_bars)
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for comb in combinations(range(n_groups), n_test_groups):
        test = np.concatenate([groups[i] for i in sorted(comb)])
        banned = set(int(i) for i in test)
        if pad:
            for i in comb:
                lo = int(groups[i][0])
                hi = int(groups[i][-1])
                for j in range(max(0, lo - pad), min(n, hi + pad + 1)):
                    banned.add(j)
        train = np.array([j for j in range(n) if j not in banned], dtype=int)
        out.append((train, np.sort(test.astype(int))))
    return out

"""Objective, combinatorial purged CV, deflated Sharpe. python -m kagent.cpcv

Objective (per unit of capital, daily net returns r):
    J = 365 * mean(log(1 + r))  -  LAMBDA * 365 * mean(min(r, 0)^2)
i.e. annualised net log growth (Kelly: already penalises variance once) plus an extra penalty on downside variance only.
LAMBDA = 3 makes a -20% drawdown-ish strategy pay roughly 2-4 points a year more than its upside twin. Upside
volatility is not punished, which matches "max profit, minimal risk" better than Sharpe.

CPCV: the evaluation window (2024-11-26 .. FINAL) is cut into NB contiguous blocks; each split tests on KT blocks
chosen at random and trains on the rest, minus PURGE days before and EMBARGO days after each test block.
Everything after FINAL is never used until the very end (untouched slice).
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from scipy import optimize, stats

LAMBDA = 3.0
NB, KT, NSPLIT, PURGE, EMBARGO = 20, 4, 300, 5, 10
FINAL = "2026-10-15"


def J(r):
    r = np.asarray(r)
    return 365 * np.mean(np.log1p(r), axis=0) - LAMBDA * 365 * np.mean(np.minimum(r, 0) ** 2, axis=0)


def sharpe(r):
    r = np.asarray(r)
    return np.mean(r, axis=0) / np.std(r, axis=0) * np.sqrt(365)


def mdd(r):
    e = np.cumprod(1 + np.asarray(r))
    return (e / np.maximum.accumulate(e) - 1).min()


def splits(n, seed=0):
    edges = np.linspace(0, n, NB + 1).astype(int)
    blocks = [np.arange(edges[i], edges[i + 1]) for i in range(NB)]
    combos = list(itertools.combinations(range(NB), KT))
    rng = np.random.default_rng(seed)
    for ci in rng.choice(len(combos), size=min(NSPLIT, len(combos)), replace=False):
        test_b = combos[ci]
        test = np.concatenate([blocks[b] for b in test_b])
        ban = np.zeros(n, bool)
        for b in test_b:
            ban[max(0, edges[b] - PURGE): min(n, edges[b + 1] + EMBARGO)] = True
        yield np.where(~ban)[0], test


# ---------------------------------------------------------------- selectors (train returns -> weights over configs)
def sel_best(Rtr):
    w = np.zeros(Rtr.shape[1]); w[np.argmax(J(Rtr))] = 1; return w


def sel_topk(Rtr, k=10):
    w = np.zeros(Rtr.shape[1]); w[np.argsort(J(Rtr))[-k:]] = 1 / k; return w


def sel_objective(Rtr, pre=40, max_lev=2.0, shrink=0.5):
    """Maximise J of the mixed book over w >= 0, sum(w) <= max_lev, on the top-`pre` configs by J,
    with an L2 pull toward equal weights (shrink) to fight estimation error."""
    j = J(Rtr); idx = np.argsort(j)[-pre:]
    X = Rtr[:, idx]
    w0 = np.full(pre, 1.0 / pre)

    def f(w):
        r = X @ w
        return -(J(r) - shrink * np.sum((w - w0) ** 2))
    res = optimize.minimize(f, w0, method="SLSQP", bounds=[(0, 1)] * pre,
                            constraints=[{"type": "ineq", "fun": lambda w: max_lev - w.sum()}], options={"maxiter": 200})
    w = np.zeros(Rtr.shape[1]); w[idx] = res.x; return w


def sel_all(Rtr):
    return np.full(Rtr.shape[1], 1 / Rtr.shape[1])


SELECTORS = {"best_single": sel_best, "top10_equal": sel_topk, "objective_opt": sel_objective, "all_equal": sel_all}


def run_cpcv(Z: pd.DataFrame, selectors=SELECTORS):
    Zd = Z.loc[:FINAL].fillna(0)
    X = Zd.values
    rows, picks = [], {k: pd.Series(0.0, index=Z.columns) for k in selectors}
    for s, (tr, te) in enumerate(splits(len(X))):
        for name, fn in selectors.items():
            w = fn(X[tr])
            r = X[te] @ w
            picks[name] += w / NSPLIT
            rows.append(dict(split=s, selector=name, J=J(r), sharpe=sharpe(r), ann=np.expm1(365 * np.mean(np.log1p(r))),
                             mdd=mdd(r), lev=w.sum()))
    return pd.DataFrame(rows), pd.DataFrame(picks)


def deflated_sharpe(r, sr_trials, n_trials):
    """Bailey & Lopez de Prado (2014). r: daily returns of the chosen strategy; sr_trials: daily Sharpe of every trial."""
    r = np.asarray(r); T = len(r)
    sr = r.mean() / r.std()
    g3, g4 = stats.skew(r), stats.kurtosis(r, fisher=False)
    v = np.var(sr_trials)
    em = 0.5772156649
    sr0 = np.sqrt(v) * ((1 - em) * stats.norm.ppf(1 - 1 / n_trials) + em * stats.norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return float(stats.norm.cdf(z)), float(sr0 * np.sqrt(365))


def summary(res):
    q = lambda p: (lambda x: x.quantile(p))
    g = res.groupby("selector")
    return pd.concat({
        "sharpe_p10": g.sharpe.quantile(.1), "sharpe_med": g.sharpe.median(), "sharpe_p90": g.sharpe.quantile(.9),
        "ann_med": g.ann.median() * 100, "ann_p10": g.ann.quantile(.1) * 100, "J_med": g.J.median() * 100,
        "mdd_med": g.mdd.median() * 100, "P(ann>0)": g.ann.apply(lambda x: (x > 0).mean()), "lev": g.lev.mean()}, axis=1).round(2)


if __name__ == "__main__":
    Z = pd.read_csv("research/kagent_report/zoo_returns.csv.gz", index_col=0, parse_dates=True)
    res, picks = run_cpcv(Z)
    print(summary(res).to_string())
    for k in picks:
        print(k, picks[k].sort_values().tail(8).round(3).to_dict())
    full = Z.loc[:FINAL].fillna(0)
    sr_d = (full.mean() / full.std()).values
    best = full.columns[np.argmax(J(full.values))]
    print("best full-period config:", best, "DSR prob:", deflated_sharpe(full[best], sr_d, full.shape[1]))
    res.to_csv("research/kagent_report/cpcv_results.csv", index=False)

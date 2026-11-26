"""ML policies trained to maximise the objective directly, inside purged CPCV folds. python -m kagent.policy

Panel: 20 coin perps (long/short). Weekly rebalance (every 7th day). Features per asset (cross-sectionally z-scored):
  mom7, mom30, mom90, rev1, rev3, vol30, max30, runup30, sma50, sma200, funding7, beta60, volume_z
plus a market-timing block (BTC sma50 / sma200 sign, breadth, BTC mom30) that sets net exposure.

Policies:
  linear_mv : w_it = theta . x_it / N  (+ timing).  Portfolio return is linear in theta: r_t = theta . g_t, with
              g_kt = mean_i x_ikt R_i,t->t+7. Maximising mean - (gamma/2) var with a ridge term gives the closed form
              theta = (Cov(g) + rho I)^-1 mean(g) / gamma  -> the second-order approximation of J (log growth).
  lgbm_rank : LightGBM regressor on next-7d cross-sectional return rank; long top 5 / short bottom 5.
Costs: perp fee + slippage on turnover, real funding (Kraken, Binance before). Positions scaled to 15% vol ex-ante.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from kagent import zoo
from kagent.cpcv import J, mdd, sharpe

warnings.filterwarnings("ignore")
H = 7


def features(C, fund):
    R = C.pct_change(fill_method=None)
    rk = lambda X: X.sub(X.mean(axis=1), axis=0).div(X.std(axis=1), axis=0)
    beta = R.rolling(60).cov(R["BTC"]).div(R["BTC"].rolling(60).var(), axis=0)
    F = {"mom7": C.pct_change(7), "mom30": C.pct_change(30), "mom90": C.pct_change(90), "rev1": -R, "rev3": -C.pct_change(3),
         "vol30": R.rolling(30).std(), "max30": R.rolling(30).max(), "runup30": C / C.rolling(30).min() - 1,
         "sma50": C / C.rolling(50).mean() - 1, "sma200": C / C.rolling(200).mean() - 1,
         "fund7": fund.rolling(7).mean(), "beta60": beta}
    X = {k: rk(v).clip(-3, 3) for k, v in F.items()}
    btc = C.BTC
    T = pd.DataFrame({"btc_sma50": np.sign(btc / btc.rolling(50).mean() - 1), "btc_sma200": np.sign(btc / btc.rolling(200).mean() - 1),
                      "breadth50": (C > C.rolling(50).mean()).mean(axis=1) * 2 - 1, "btc_mom30": np.sign(btc.pct_change(30))})
    return X, T, R


def weights_linear(X, T, theta_x, theta_t, valid):
    W = sum(theta_x[k] * X[k].fillna(0) for k in X).where(valid, 0)
    W = W.div(valid.sum(axis=1), axis=0)
    net = (T.fillna(0) @ theta_t) / valid.sum(axis=1)
    return W.add(net, axis=0).where(valid, 0)


def fit_linear(X, T, R7, valid, tr_idx, gamma=1.0, rho=1e-4):
    n = valid.sum(axis=1)
    G = pd.DataFrame({k: (X[k].fillna(0) * R7).where(valid).sum(axis=1) / n for k in X})
    Gt = pd.DataFrame({k: T[k] * R7.where(valid).sum(axis=1) / n for k in T})
    Gall = pd.concat([G, Gt], axis=1).iloc[tr_idx].dropna()
    Gall = Gall.iloc[::H]  # non-overlapping weekly samples
    mu, S = Gall.mean().values, np.cov(Gall.values.T)
    th = np.linalg.solve(S + rho * np.trace(S) / len(mu) * np.eye(len(mu)) * 10, mu) / gamma
    th = pd.Series(th, index=Gall.columns)
    return th[list(X)], th[list(T)]


def realise(W, C, fund, every=H):
    R = C.pct_change(fill_method=None)
    cc = pd.Series({c: 0.0005 + (0.0005 if c in zoo.MAJORS else 0.0015) for c in C.columns})
    return zoo.net(zoo.weekly(W, every), R, cc, fund, fund)


def run(start_eval="2019-09-01", final=None):
    import lightgbm as lgb
    from kagent.cpcv import splits
    zoo.START = "2018-06-01"
    C, S, SP, fund = zoo.panel()
    C = C.loc[:final] if final else C
    X, T, R = features(C, fund)
    valid = C.notna() & C.shift(200).notna()
    R7 = C.shift(-H) / C - 1
    R7 = R7.sub(R7.where(valid).mean(axis=1), axis=0)  # cross-sectional (market-neutral) target for features
    R7m = C.shift(-H) / C - 1
    idx = C.loc[start_eval:].index
    pos0 = C.index.get_loc(idx[0])
    rows = []
    # global-in-time CPCV over the whole evaluation range
    for s, (tr, te) in enumerate(splits(len(idx))):
        trg, teg = tr + pos0, te + pos0
        # purge: training labels look H days ahead -> drop the H days before each test block (PURGE in cpcv >= 5; add H)
        ban = np.zeros(len(C), bool)
        for t0 in teg:
            ban[max(0, t0 - H):t0] = True
        trg = np.array([t for t in trg if not ban[t]])
        # linear, objective-maximising
        tx, tt = fit_linear(X, T, R7m.where(valid), valid, trg)
        W = weights_linear(X, T, tx, tt, valid)
        r = realise(W, C, fund).reindex(C.index).fillna(0).values
        # lgbm rank model
        if s < 60:  # LightGBM is slower: first 60 splits
            panel = pd.concat({k: v.stack() for k, v in X.items()}, axis=1)
            y = R7.stack().rank(pct=True) if False else R7.rank(axis=1, pct=True).stack()
            dates = panel.index.get_level_values(0)
            pos = C.index.get_indexer(dates)
            trm = np.isin(pos, trg[::3]) & y.reindex(panel.index).notna().values
            m = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=200,
                                  subsample=0.7, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
            m.fit(panel[trm].values, y.reindex(panel.index)[trm].values)
            pred = pd.Series(m.predict(panel.values), index=panel.index).unstack().reindex(columns=C.columns)
            Wl = zoo.xs_book(pred.where(valid), R, 5)
            rl = realise(Wl, C, fund).reindex(C.index).fillna(0).values
        else:
            rl = None
        for name, rr in (("linear_mv", r), ("lgbm_rank", rl)):
            if rr is None:
                continue
            x = rr[teg]
            rows.append(dict(split=s, model=name, J=J(x), sharpe=sharpe(x), ann=np.expm1(365 * np.mean(np.log1p(x))), mdd=mdd(x),
                             in_last2y=np.mean(C.index[teg] >= "2024-11-26")))
        if s % 20 == 0:
            print(s, dict(tx.round(4)), dict(tt.round(4)), flush=True)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    res = run()
    res.to_csv("research/kagent_report/policy_cpcv.csv", index=False)
    g = res.groupby("model")
    print(pd.concat({"sh_p10": g.sharpe.quantile(.1), "sh_med": g.sharpe.median(), "sh_p90": g.sharpe.quantile(.9),
                     "ann_med": g.ann.median() * 100, "P(ann>0)": g.ann.apply(lambda x: (x > 0).mean()), "mdd_med": g.mdd.median() * 100}, axis=1).round(2))

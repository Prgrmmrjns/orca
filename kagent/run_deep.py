"""Deep round driver: zoo on 2019-09..now (longer history for selection), CPCV with random blocks,
results reported on all test blocks and on the test days inside the last 2 years. python -m kagent.run_deep"""
import numpy as np, pandas as pd
from kagent import zoo, cpcv

zoo.START, zoo.EVAL = "2018-06-01", "2019-09-01"
Z, bench = zoo.build_zoo()
Z.to_csv("research/kagent_report/zoo_returns_long.csv.gz"); bench.to_csv("research/kagent_report/zoo_bench_long.csv.gz")
X = Z.loc[:cpcv.FINAL].fillna(0)
idx = X.index
rows = []
for s, (tr, te) in enumerate(cpcv.splits(len(X))):
    te2 = te[idx[te] >= "2024-11-26"]
    for name, fn in cpcv.SELECTORS.items():
        w = fn(X.values[tr])
        for lab, t in (("all", te), ("last2y", te2)):
            if len(t) < 30:
                continue
            r = X.values[t] @ w
            rows.append(dict(split=s, selector=name, window=lab, sharpe=cpcv.sharpe(r), ann=np.expm1(365 * np.mean(np.log1p(r))),
                             J=cpcv.J(r), mdd=cpcv.mdd(r)))
res = pd.DataFrame(rows)
res.to_csv("research/kagent_report/cpcv_long.csv", index=False)
g = res.groupby(["window", "selector"])
print(pd.concat({"sh_p10": g.sharpe.quantile(.1), "sh_med": g.sharpe.median(), "sh_p90": g.sharpe.quantile(.9),
                 "ann_med": g.ann.median() * 100, "P(ann>0)": g.ann.apply(lambda x: (x > 0).mean()), "mdd_med": g.mdd.median() * 100}, axis=1).round(2).to_string())
# probability of backtest overfitting (Bailey et al.): IS-best config's OOS rank below median
pbo = []
for tr, te in cpcv.splits(len(X)):
    jtr, jte = cpcv.J(X.values[tr]), cpcv.J(X.values[te])
    pbo.append(pd.Series(jte).rank(pct=True).iloc[int(np.argmax(jtr))] < 0.5)
print("PBO:", np.mean(pbo))
full = X; srd = (full.mean() / full.std()).values; best = full.columns[np.argmax(cpcv.J(full.values))]
print("best:", best, "DSR:", cpcv.deflated_sharpe(full[best], srd, full.shape[1]))

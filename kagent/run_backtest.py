"""Reproduce the full backtest report: python -m kagent.run_backtest  (writes research/kagent_report/)."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from kagent import agent, backtest as B, ml, strategies as S
from kagent.data import CRYPTO, load

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent.parent / "research" / "kagent_report"
DEV = ("2020-01-01", "2024-11-30")
HOLD = ("2024-12-01", None)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    C = load("close").ffill(limit=3).loc["2018-01-01":]
    H, L, V = (load(n).reindex(C.index) for n in ("high", "low", "qvol"))
    top8 = agent.UNIVERSE
    strat = {}
    one = lambda a: pd.DataFrame({a: 1.0}, index=C.index)
    strat["BTC buy & hold (perp)"] = (one("BTC"), {})
    ew = C[top8].notna().astype(float)
    strat["Equal-weight top-8 buy & hold, monthly"] = (ew.div(ew.sum(axis=1), axis=0).where(pd.Series(C.index.is_month_start, index=C.index), axis=0).ffill().fillna(0), {"band": 0.05})
    strat["Old Orca v2 on spot (0.8% taker)"] = (S.v2_long_only(C), {"venue": "spot", "band": 0.05})
    strat["Old Orca v2 on perps"] = (S.v2_long_only(C), {"band": 0.05})
    strat["Trend long/short, 20 coins"] = (S.weekly(S.trend(C, list(CRYPTO))), {})
    X = ml.features(C, H, L, V, list(CRYPTO))
    pr = ml.walk_forward(X, model="ridge")
    pl = ml.walk_forward(X, model="lgbm")
    strat["ML ranker (ridge) long/short"] = (S.weekly(ml.xs_portfolio(C, pr)), {})
    strat["ML ranker (LightGBM) long/short"] = (S.weekly(ml.xs_portfolio(C, pl)), {})
    w = agent.targets(C)
    strat["Agent, no brake"] = (w, {"band": agent.BAND})
    shadow = B.simulate(C, w, band=agent.BAND).equity
    strat["AGENT (chosen, with DD brake)"] = (S.dd_brake(shadow, w, agent.BRAKE), {"band": agent.BAND})

    report, curves = {}, {}
    for name, (wt, kw) in strat.items():
        res = B.simulate(C, wt, **kw)
        curves[name] = res.equity
        report[name] = {}
        for lab, (s, e) in {"dev_2020_2024": DEV, "holdout_2024-12_now": HOLD, "full_2020_now": (DEV[0], None)}.items():
            eq = res.equity.loc[s:e]
            d = B.stats(eq, res)
            d["sharpe_90ci"] = B.boot_sharpe(eq)
            report[name][lab] = {k: (float(v) if isinstance(v, (np.floating, float, int)) else v) for k, v in d.items()}
        report[name]["yearly_%"] = {int(k): float(v) for k, v in B.yearly(res.equity.loc[DEV[0]:]).items()}
    y = X["y"].unstack()
    for nm, p in (("ridge", pr), ("lgbm", pl)):
        ic = p.corrwith(y.reindex(p.index)[p.columns], axis=1, method="spearman")
        report[f"ML {nm} rank IC"] = {"mean": round(float(ic.mean()), 4), "by_year": ic.groupby(ic.index.year).mean().round(3).to_dict()}
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
    pd.DataFrame(curves).loc[DEV[0]:].to_csv(OUT / "equity_curves.csv.gz")
    # agent's current target book
    print(json.dumps({k: v.get("holdout_2024-12_now", v) for k, v in report.items()}, indent=1, default=str))


if __name__ == "__main__":
    main()

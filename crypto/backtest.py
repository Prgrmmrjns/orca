"""Walk-forward backtest of strategy v3 on Binance daily history.

  python -m crypto.backtest            # walk-forward report -> crypto/reports/backtest.json
  python -m crypto.backtest --export   # also train the final model -> crypto/model/gbm.json

The ML sleeve is retrained each 1 Jan on data strictly before that date (minus
the 7d label horizon), so every forecast used from 2021 on is out-of-sample.
Costs: 0% commission (user's Kraken assumption) + `--cost` per side for
spread/slippage (default 0.10%; live Kraken EUR spreads are ~0.01-0.07%/side).
"""
from __future__ import annotations
import argparse, json, warnings
from pathlib import Path
import numpy as np, pandas as pd
from . import strategy as S, model as Mdl
from .research_lib import load_panel, backtest, stats, by_year

warnings.filterwarnings("ignore")
REPORT = Path(__file__).parent / "reports" / "backtest.json"

def panels():
    return tuple(load_panel(field=f) for f in ("close", "high", "low", "qvol"))

def walk_forward_preds(close, high, low, qvol, first_year=2021, export=False):
    p = S.PARAMS
    un = S.universe(close, qvol, p["universe_n"], p["min_history"])
    X = S.feature_frame(close, high, low, qvol, un)
    fwd = (close.shift(-p["horizon"]) / close - 1).stack(future_stack=True).reindex(X.index)
    d = X.index.get_level_values(0); out = []; ics = {}
    for yr in range(first_year, close.index[-1].year + 1):
        t0, t1 = pd.Timestamp(f"{yr}-01-01", tz="UTC"), pd.Timestamp(f"{yr+1}-01-01", tz="UTC")
        tr = (d < t0 - pd.Timedelta(days=p["horizon"] + 1)) & fwd.notna().values
        te = (d >= t0) & (d < t1)
        ms = Mdl.fit(X[tr], fwd[tr])
        out.append(pd.Series(Mdl.predict_lgb(ms, X[te]), index=X.index[te]))
    pred = pd.concat(out).unstack().reindex(columns=close.columns)
    ic = pred.corrwith((close.shift(-p["horizon"]) / close - 1).reindex(pred.index), axis=1, method="spearman")
    ics = ic.groupby(ic.index.year).mean().round(3).to_dict()
    if export:
        ok = fwd.notna().values
        ms = Mdl.fit(X[ok], fwd[ok])
        Mdl.export(ms, X.columns, meta=dict(trained_through=str(d[ok].max().date()), rows=int(ok.sum()),
                                            target=f"fwd_{p['horizon']}d_return", source="binance spot USDT daily"))
        jm = Mdl.JsonModel()
        diff = float(np.abs(jm.predict(X[te]) - Mdl.predict_lgb(ms, X[te])).max())
        assert diff < 1e-9, diff
        print("exported final model; numpy-vs-lightgbm max diff", diff)
    return pred, ics

def run(cost=0.001, export=False, start="2021-01-01"):
    close, high, low, qvol = panels()
    ret = close.pct_change()
    pred, ics = walk_forward_preds(close, high, low, qvol, export=export)
    w, gate = S.target_weights(close, qvol, pred)
    w = S.apply_band(w, S.PARAMS["band"])
    res = {}
    def add(name, weights, c=cost):
        r, t = backtest(weights, ret, cost=c); r = r[start:]
        res[name] = dict(full=stats(r), since_2024=stats(r["2024":]), last_12m=stats(r.iloc[-365:]),
                         by_year=by_year(r), turnover_per_year=round(float(t[start:].mean() * 365), 1))
        return r
    r = add("orca_v3", w)
    add("orca_v3_cost_0.30pct", w, 0.003)
    add("orca_v3_trade_1day_late", w.shift(1))
    btc = pd.DataFrame(0.0, index=close.index, columns=close.columns); btc["BTC"] = 1.0
    add("btc_buy_hold", btc, 0)
    un = S.universe(close, qvol)
    add("equal_weight_top20", un.div(un.sum(axis=1), axis=0).astype(float))
    m = (1 + r).resample("ME").prod() - 1
    res["orca_v3"].update(monthly_win_rate=round(float((m > 0).mean()), 2), worst_month=round(float(m.min() * 100), 1),
                          best_month=round(float(m.max() * 100), 1),
                          time_invested=round(float((w.sum(axis=1)[start:] > 0).mean()), 2),
                          eur_1000_becomes=round(float(1000 * (1 + r).prod())))
    out = dict(period=[start, str(close.index[-1].date())], cost_per_side=cost, params={k: (list(v) if isinstance(v, tuple) else v) for k, v in S.PARAMS.items()},
               ml_rank_ic_by_year=ics, results=res,
               caveats=["universe = coins listed on Binance AND Kraken today (survivorship bias; dead coins like LUNA/FTT absent)",
                        "2021 return is dominated by the alt bull market and will not repeat on demand",
                        "rule set chosen on this same history; only the ML forecasts are strictly walk-forward"])
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(out, indent=1, default=float))
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--cost", type=float, default=0.001); ap.add_argument("--export", action="store_true")
    a = ap.parse_args(); o = run(a.cost, a.export)
    print("ML rank IC:", o["ml_rank_ic_by_year"])
    for k, v in o["results"].items():
        print(f"{k:26s} full {v['full']} | 2024+ {v['since_2024']} | last12m {v['last_12m']}\n{'':26s} {v['by_year']} turn/yr {v['turnover_per_year']}")
    print({k: o["results"]["orca_v3"][k] for k in ("monthly_win_rate", "worst_month", "best_month", "time_invested", "eur_1000_becomes")})

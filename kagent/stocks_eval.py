"""Do Kraken-listed stocks (xStocks / equity perps) help? Last 2 years, year A / year B. python -m kagent.stocks_eval

Stock costs (perp): 0.05% taker + 0.05% slippage per side; funding assumed 5%/yr paid by longs, 0 for shorts
(Kraken equity perps are too new for a funding history). Prices: underlying US close (xStocks track it).
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

from kagent import lowrisk as L, round3, agent, backtest as BT, strategies as S
from kagent.data import load
from kagent.discovery import build_features, A, B
from kagent.stocks_data import load_calendar, STOCKS, PERP

OUT = "research/kagent_report/"
COST = 0.0010
FUND_LONG = 0.05 / 365


def stock_trend(P, gate="SPY", windows=(20, 50, 100, 200), tv=0.15, cap=0.25):
    """Same rule as the coin agent: hold names above their SMA while SPY is above its SMA; inverse-vol, vol target."""
    vol = P.pct_change(fill_method=None).rolling(60).std() * np.sqrt(252 / 7 * 5 * 7 / 5)  # calendar-day series
    subs = []
    for N in windows:
        on = (P > P.rolling(N).mean()).mul(P[gate] > P[gate].rolling(N).mean(), axis=0)
        iv = (1 / vol).where(on)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
        pv = (w * vol).sum(axis=1) * 0.85
        subs.append(w.mul((tv / pv).clip(upper=1).fillna(0), axis=0).clip(upper=cap))
    return S.weekly(sum(subs) / len(subs))


def run_weights(P, W, long_only_spot=False):
    R = P.pct_change(fill_method=None).fillna(0)
    H = W.shift(1).fillna(0)
    c = 0.0008 + 0.0005 if long_only_spot else COST
    tc = (H.diff().abs() * c).sum(axis=1)
    fd = 0 if long_only_spot else H.clip(lower=0).sum(axis=1) * FUND_LONG
    return (H * R).sum(axis=1) - tc - fd


def st(x):
    e = (1 + x).cumprod()
    return dict(ann=round((e.iloc[-1] ** (365 / len(x)) - 1) * 100, 1), sharpe=round(x.mean() / x.std() * np.sqrt(365), 2),
                mdd=round((e / e.cummax() - 1).min() * 100, 1))


def main():
    F, C20 = build_features()
    idx = C20.index
    P = load_calendar(idx)[STOCKS]
    # coin sleeves (as in REPORT section 12)
    fund = L.kraken_funding_daily(idx)
    coin_lr = L.net_returns(C20, L.targets(C20, L.score(F)), fund)
    C = load("close").ffill(limit=3).loc["2018-01-01":]
    comb, *_ = round3.combined_targets(C)
    coin_ta = BT.simulate(C, comb, band=agent.BAND).equity.pct_change().fillna(0).reindex(idx)
    # stock sleeves
    st_trend = run_weights(P, stock_trend(P))
    st_trend_perp = run_weights(P[PERP], stock_trend(P[PERP]))
    st_trend_spot = run_weights(P, stock_trend(P), long_only_spot=True)
    st_lr = L.net_returns(P, L.targets(P, L.price_score(P), k=5, bench="SPY"), pd.DataFrame(0, index=idx, columns=P.columns),
                          cost=pd.Series(COST, index=P.columns))
    # shorts on perps pay 0, longs pay 5%/yr
    H = L.targets(P, L.price_score(P), k=5, bench="SPY").shift(1).fillna(0)
    st_lr = st_lr - H.clip(lower=0).sum(axis=1) * FUND_LONG
    spy = P.SPY.pct_change().fillna(0); btc = C20.BTC.pct_change().fillna(0)
    D = pd.DataFrame(dict(coin_trend_alt=coin_ta, coin_lowrisk=coin_lr, stock_trend=st_trend, stock_trend_perp_only=st_trend_perp,
                          stock_trend_xstock_spot=st_trend_spot, stock_lowrisk=st_lr, spy=spy, btc=btc)).loc[A[0]:].fillna(0)
    mixes = {
        "coins only (50/50, section 12)": .5 * D.coin_trend_alt + .5 * D.coin_lowrisk,
        "stocks only (trend + low-risk)": .5 * D.stock_trend + .5 * D.stock_lowrisk,
        "coins + stocks, 4 sleeves equal": (D.coin_trend_alt + D.coin_lowrisk + D.stock_trend + D.stock_lowrisk) / 4,
        "coins + stock trend (3 equal)": (D.coin_trend_alt + D.coin_lowrisk + D.stock_trend) / 3,
    }
    rows = []
    for k, x in {**{c: D[c] for c in D}, **mixes}.items():
        for lab, (a, b) in (("A", A), ("B", B)):
            rows.append(dict(name=k, period=lab, **st(x.loc[a:b])))
    out = pd.DataFrame(rows)
    q = pd.DataFrame({k: (1 + x).groupby(x.index.to_period("Q")).prod() - 1 for k, x in mixes.items()}).mul(100).round(1)
    print(D.corr().round(2).to_string()); print(out.to_string()); print(q.to_string())
    out.to_csv(OUT + "stocks_2y.csv", index=False); q.to_csv(OUT + "stocks_2y_quarterly.csv")
    D.to_csv(OUT + "stocks_sleeve_returns_2y.csv.gz")


if __name__ == "__main__":
    main()

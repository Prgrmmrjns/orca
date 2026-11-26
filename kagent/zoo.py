"""Strategy zoo over everything Kraken lets us trade, long and short. python -m kagent.zoo

Every config maps data known at close t to target weights, held over day t+1 (net of costs). Each config is
scaled ex-ante to 15% annual vol (w' S w with the 60d covariance of its assets, capped at 3x leverage).

Venues and cost model (per side; daily funding where relevant):
  coin perps (20)        : 0.05% taker + 0.05% (BTC/ETH/SOL/XRP) or 0.15% slippage; funding = Kraken hourly
                           history where it exists (~1y), Binance 8h history before that; longs pay positive.
  stock perps (14)       : 0.05% + 0.05%; longs pay 5%/yr, shorts 0 (assumption, no history yet).
  xStocks spot           : 0.08% + 0.05%, long only, no funding. Universe = ~50 largest US stocks as of 2024-11
                           (hindsight-free) + SPY/QQQ/GLD; perps = the 14 names Kraken has perps for.
  coin spot / margin     : 0.80% taker (+ rollover for shorts) -> dominated by perps, not used.
"""
from __future__ import annotations

import itertools
import warnings

import numpy as np
import pandas as pd

from kagent.data import CRYPTO, DATA, load
from kagent.stocks_data import PERP, SP50_2024, load_calendar

warnings.filterwarnings("ignore")
START = "2023-06-01"          # warm-up; evaluation uses 2024-11-26 onward
EVAL = "2024-11-26"
MAJORS = {"BTC", "ETH", "SOL", "XRP"}
TV, LEV_CAP = 0.15, 3.0


def panel():
    C = load("close")[list(CRYPTO)].loc[START:].ffill(limit=3)
    idx = C.index
    S = load_calendar(idx)
    from kagent.lowrisk import kraken_funding_daily
    kf = kraken_funding_daily(idx)
    kf = kf.loc[kf.fillna(0).ne(0).any(axis=1).idxmax():]  # Kraken history starts ~1y ago
    try:
        bf = pd.read_csv(DATA / "binance_funding_daily.csv.gz", index_col=0, parse_dates=True).reindex(idx)
    except FileNotFoundError:
        bf = pd.DataFrame(0.10 / 365, index=idx, columns=C.columns)
    fund = kf.reindex(idx).combine_first(bf).reindex(columns=C.columns).fillna(0.10 / 365)
    return C, S[SP50_2024], S[PERP], fund


def _ivol(R, w=30):
    return 1 / R.rolling(w).std().replace(0, np.nan)


def xs_book(score, R, k, long_only=False):
    """Long top-k / short bottom-k by score, inverse-vol, 50% gross a side (100% long if long_only)."""
    iv = _ivol(R)
    rk = score.rank(axis=1, ascending=False)
    n = score.notna().sum(axis=1)
    top = rk.le(k)
    bot = rk.gt(n - k, axis=0) & score.notna()
    wl = (iv.where(top)).div(iv.where(top).sum(axis=1), axis=0).fillna(0)
    ws = (iv.where(bot)).div(iv.where(bot).sum(axis=1), axis=0).fillna(0)
    return wl if long_only else 0.5 * wl - 0.5 * ws


def ts_book(sig, R):
    """Time-series book: sig in {-1,0,1} per asset, inverse-vol, sum |w| = 1."""
    iv = _ivol(R).where(sig != 0)
    return (sig * iv).div(iv.sum(axis=1), axis=0).fillna(0)


def weekly(W, every):
    if every == 1:
        return W
    keep = np.arange(len(W)) % every == 0
    return W.where(pd.Series(keep, index=W.index), np.nan).ffill().fillna(0)


_COV = {}


def _cov(R):
    """Rolling 60d covariance (annualised), cached per universe: array [T, N, N]."""
    key = (tuple(R.columns), len(R))
    if key not in _COV:
        X = R.fillna(0).values
        T, N = X.shape
        out = np.full((T, N, N), np.nan)
        for t in range(60, T):
            out[t] = np.cov(X[t - 60:t].T) * 365
        _COV[key] = out
    return _COV[key]


def net(W, R, cost, fund_long, fund_short=None):
    """Scale ex-ante to TV with the 60d covariance (w' S w), then daily net return. W decided at close t, earns R[t+1]."""
    S = _cov(R)
    w = W.reindex(columns=R.columns).fillna(0).values
    pv = np.sqrt(np.maximum(np.einsum("ti,tij,tj->t", w, S, w), 0))
    k = pd.Series(np.where(pv > 1e-6, TV / pv, 0), index=W.index).clip(upper=LEV_CAP).fillna(0)
    H = W.mul(k, axis=0).shift(1).fillna(0)
    gross = (H * R.fillna(0)).sum(axis=1)
    tc = (H.diff().abs() * cost).sum(axis=1)
    fl = (H.clip(lower=0) * fund_long).sum(axis=1)
    fs = (H.clip(upper=0) * (fund_short if fund_short is not None else 0)).sum(axis=1)  # shorts receive positive funding
    return gross - tc - fl - fs


def build_zoo():
    C, S, SP, fund = panel()
    Rc = C.pct_change(fill_method=None)
    Rs, Rsp = S.pct_change(fill_method=None), SP.pct_change(fill_method=None)
    cc = pd.Series({c: 0.0005 + (0.0005 if c in MAJORS else 0.0015) for c in C.columns})
    sc_perp, sc_spot = 0.0010, 0.0013
    sfund = 0.05 / 365
    out = {}

    def coin(name, W):
        out[name] = net(W, Rc, cc, fund, fund)     # short pays -f (receives positive funding)

    def stk_perp(name, W):
        out[name] = net(W, Rsp, sc_perp, sfund)

    def stk_spot(name, W):
        out[name] = net(W.clip(lower=0), Rs, sc_spot, 0.0)

    for ev in (1, 7):
        # ---- coins: time-series trend (long/short and long-only), BTC-only trend
        for L in (7, 14, 30, 60, 90, 180):
            sig = np.sign(C / C.shift(L) - 1)
            coin(f"c_tsmom_ls_L{L}_e{ev}", weekly(ts_book(sig, Rc), ev))
            coin(f"c_tsmom_lo_L{L}_e{ev}", weekly(ts_book(sig.clip(lower=0), Rc), ev))
            coin(f"c_tsmom_so_L{L}_e{ev}", weekly(ts_book(sig.clip(upper=0), Rc), ev))   # short-only
        for N in (20, 50, 100, 200):
            sig = np.sign(C - C.rolling(N).mean())
            coin(f"c_sma_ls_N{N}_e{ev}", weekly(ts_book(sig, Rc), ev))
            coin(f"c_sma_lo_N{N}_e{ev}", weekly(ts_book(sig.clip(lower=0), Rc), ev))
            b = np.sign(C[["BTC"]] - C[["BTC"]].rolling(N).mean())
            coin(f"c_btc_sma_ls_N{N}_e{ev}", weekly(b.reindex(columns=C.columns).fillna(0), ev))
        # ---- coins: cross-sectional
        for k in (3, 5):
            for L in (3, 7, 14, 30, 60, 90):
                coin(f"c_xsmom_L{L}_k{k}_e{ev}", weekly(xs_book(C.pct_change(L), Rc, k), ev))
            for L in (1, 2, 3):
                coin(f"c_xsrev_L{L}_k{k}_e{ev}", weekly(xs_book(-C.pct_change(L), Rc, k), ev))
            rk = lambda X: X.rank(axis=1, pct=True)
            lr = -(rk(Rc.rolling(30).std()) + rk(Rc.rolling(30).max()) + rk(C / C.rolling(30).min() - 1))
            coin(f"c_lowrisk_k{k}_e{ev}", weekly(xs_book(lr, Rc, k), ev))
            coin(f"c_lowvol_k{k}_e{ev}", weekly(xs_book(-Rc.rolling(30).std(), Rc, k), ev))
            coin(f"c_lowmax_k{k}_e{ev}", weekly(xs_book(-Rc.rolling(30).max(), Rc, k), ev))
            for F in (7, 30):
                coin(f"c_carry_F{F}_k{k}_e{ev}", weekly(xs_book(-fund.rolling(F).mean(), Rc, k), ev))
            beta = Rc.rolling(60).cov(Rc["BTC"]).div(Rc["BTC"].rolling(60).var(), axis=0)
            resid = Rc - beta.mul(Rc["BTC"], axis=0)
            for L in (3, 7):
                coin(f"c_residrev_L{L}_k{k}_e{ev}", weekly(xs_book(-resid.rolling(L).sum(), Rc, k), ev))
        # ---- coins: risk-off overlay from stocks (short coins when SPY under its 100/200d SMA and BTC too)
        for N in (100, 200):
            off = (S.SPY < S.SPY.rolling(N).mean()) & (C.BTC < C.BTC.rolling(N).mean())
            sig = pd.DataFrame(np.repeat(np.where(off.values, -1.0, 0.0)[:, None], C.shape[1], axis=1), index=C.index, columns=C.columns)
            coin(f"c_riskoff_short_N{N}_e{ev}", weekly(ts_book(sig, Rc), ev))
        # ---- stocks
        for N in (20, 50, 100, 200):
            sig = np.sign(S - S.rolling(N).mean())
            stk_spot(f"s_sma_lo_spot_N{N}_e{ev}", weekly(ts_book(sig.clip(lower=0), Rs), ev))
            sigp = np.sign(SP - SP.rolling(N).mean())
            stk_perp(f"s_sma_ls_perp_N{N}_e{ev}", weekly(ts_book(sigp, Rsp), ev))
            g = (S.SPY > S.SPY.rolling(N).mean())
            stk_spot(f"s_spygate_lo_spot_N{N}_e{ev}", weekly(ts_book(sig.clip(lower=0).mul(g, axis=0), Rs), ev))
        for k in (3, 5):
            for L in (7, 30, 90, 180):
                stk_perp(f"s_xsmom_perp_L{L}_k{k}_e{ev}", weekly(xs_book(SP.pct_change(L), Rsp, k), ev))
                stk_spot(f"s_xsmom_lo_spot_L{L}_k{k}_e{ev}", weekly(xs_book(S.pct_change(L), Rs, k, long_only=True), ev))
            for L in (3, 7):
                stk_perp(f"s_xsrev_perp_L{L}_k{k}_e{ev}", weekly(xs_book(-SP.pct_change(L), Rsp, k), ev))
            stk_perp(f"s_lowvol_perp_k{k}_e{ev}", weekly(xs_book(-Rsp.rolling(60).std(), Rsp, k), ev))
            stk_spot(f"s_lowvol_lo_spot_k{k}_e{ev}", weekly(xs_book(-Rs.rolling(60).std(), Rs, k, long_only=True), ev))
    Z = pd.DataFrame(out).loc[EVAL:]
    bench = pd.DataFrame({"BTC": Rc.BTC, "SPY": Rs.SPY}).loc[EVAL:]
    return Z, bench


if __name__ == "__main__":
    Z, bench = build_zoo()
    Z.to_csv("research/kagent_report/zoo_returns.csv.gz")
    bench.to_csv("research/kagent_report/zoo_bench.csv.gz")
    print(Z.shape)
    s = (Z.mean() / Z.std() * np.sqrt(365)).sort_values()
    print(s.tail(25).round(2).to_string()); print(s.head(10).round(2).to_string())

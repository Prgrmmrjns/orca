"""Research helpers: panel loading + vectorised portfolio backtest."""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd

CACHE = Path(__file__).parent / "data_cache"
STABLE = {"USDC", "DAI", "PAXG"}

def load_panel(freq="1D", field="close"):
    cols = {}
    for p in sorted(CACHE.glob("*USDT_1h.parquet")):
        sym = p.name.split("USDT")[0]
        df = pd.read_parquet(p)
        if field == "close": s = df["close"].resample(freq).last()
        elif field == "qvol": s = df["qvol"].resample(freq).sum()
        elif field == "high": s = df["high"].resample(freq).max()
        elif field == "low": s = df["low"].resample(freq).min()
        cols[sym] = s
    px = pd.DataFrame(cols)
    if "MATIC" in px and "POL" in px:  # MATIC migrated to POL in 2024-09
        if field == "qvol": px["POL"] = px["POL"].fillna(0) + px["MATIC"].fillna(0)
        else: px["POL"] = px["POL"].combine_first(px["MATIC"])
        px = px.drop(columns="MATIC")
    return px.iloc[:-1] if field == "close" else px.iloc[:-1]

def backtest(w: pd.DataFrame, ret: pd.DataFrame, cost=0.001, borrow_daily=0.0012, periods=365):
    """w decided at bar close t (uses info <= t), held over bar t+1.
    cost = fraction of traded notional per side; borrow on short notional per day."""
    w = w.reindex(ret.index).fillna(0.0)
    held = w.shift(1).fillna(0.0)
    gross = (held * ret.fillna(0.0)).sum(axis=1)
    turn = (w - held).abs().sum(axis=1).shift(1).fillna(0.0)  # trade at close t -> charge on t+1
    short = held.clip(upper=0).abs().sum(axis=1)
    net = gross - turn * cost - short * borrow_daily * (365 / periods)
    return net, turn

def stats(r: pd.Series, periods=365):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / periods
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    sh = r.mean() / r.std() * np.sqrt(periods) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return dict(cagr=round(float(cagr) * 100, 1), sharpe=round(float(sh), 2), mdd=round(float(dd) * 100, 1), vol=round(float(r.std() * np.sqrt(periods)) * 100, 1))

def by_year(r):
    return ((1 + r).groupby(r.index.year).prod() - 1).mul(100).round(1).to_dict()

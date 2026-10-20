"""Backtest performance metrics (paper / research only)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from orca.portfolio import Fill, PaperPortfolio


def max_drawdown(equity: pd.Series) -> float:
    """Max peak-to-trough drawdown as a negative fraction (e.g. -0.25 = -25%)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min()) if len(dd) else 0.0


def sharpe_like(equity: pd.Series, periods_per_year: float = 365.0) -> float | None:
    """Annualized Sharpe-like ratio of simple returns (rf=0). None if undefined."""
    if equity is None or len(equity) < 3:
        return None
    rets = equity.pct_change().dropna()
    if rets.empty or float(rets.std()) == 0.0:
        return None
    return float(rets.mean() / rets.std() * np.sqrt(periods_per_year))


def buy_and_hold_return(
    prices: pd.Series,
    initial_cash: float,
    fee_rate: float,
    position_size: float = 0.95,
) -> dict[str, float]:
    """Buy once at first open-equivalent price, hold to last close (fee on entry only)."""
    if prices.empty or initial_cash <= 0:
        return {"return_pct": 0.0, "final_equity": initial_cash}
    entry = float(prices.iloc[0])
    exit_p = float(prices.iloc[-1])
    spend = initial_cash * position_size
    fee = spend * fee_rate
    amount = (spend - fee) / entry
    cash_left = initial_cash - spend
    final = cash_left + amount * exit_p
    return {
        "return_pct": (final / initial_cash - 1.0) * 100.0,
        "final_equity": final,
    }


def round_trip_win_rate(fills: list[Fill]) -> dict[str, Any]:
    """Pair consecutive buy->sell fills; win if sell notional net of fees > buy cost."""
    wins = 0
    losses = 0
    completed = 0
    i = 0
    while i < len(fills) - 1:
        a, b = fills[i], fills[i + 1]
        if a.side == "buy" and b.side == "sell":
            buy_cost = a.amount * a.price + a.fee
            sell_net = b.amount * b.price - b.fee
            if sell_net > buy_cost:
                wins += 1
            else:
                losses += 1
            completed += 1
            i += 2
        else:
            i += 1
    rate = (wins / completed * 100.0) if completed else None
    return {
        "round_trips": completed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": rate,
    }


def compute_metrics(
    portfolio: PaperPortfolio,
    equity_curve: pd.DataFrame,
    prices: pd.Series,
    periods_per_year: float = 365.0,
) -> dict[str, Any]:
    """Aggregate summary from a completed paper backtest."""
    last_price = float(prices.iloc[-1]) if len(prices) else 0.0
    summary = portfolio.summary(last_price)
    eq = equity_curve["equity"] if "equity" in equity_curve.columns else pd.Series(dtype=float)
    mdd = max_drawdown(eq)
    sharpe = sharpe_like(eq, periods_per_year=periods_per_year)
    wr = round_trip_win_rate(portfolio.fills)
    bh = buy_and_hold_return(
        prices,
        initial_cash=portfolio.initial_cash,
        fee_rate=portfolio.fee_rate,
        position_size=portfolio.position_size,
    )
    return {
        **summary,
        "max_drawdown_pct": mdd * 100.0,
        "sharpe_like": sharpe,
        "win_rate_pct": wr["win_rate_pct"],
        "round_trips": wr["round_trips"],
        "buy_hold_return_pct": bh["return_pct"],
        "excess_vs_buy_hold_pct": summary["return_pct"] - bh["return_pct"],
    }

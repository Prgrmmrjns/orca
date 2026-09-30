"""Paper portfolio simulator (spot long-only, fee-aware)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Fill:
    timestamp: Any
    side: str  # buy | sell
    price: float
    amount: float  # base units
    fee: float  # quote currency
    cash_after: float
    base_after: float


@dataclass
class PaperPortfolio:
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0026
    position_size: float = 0.95
    cash: float = field(init=False)
    base: float = field(init=False, default=0.0)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)
        self.base = 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.base * price

    def buy(self, timestamp: Any, price: float) -> Fill | None:
        if self.cash <= 0 or price <= 0:
            return None
        spend = self.cash * self.position_size
        fee = spend * self.fee_rate
        notional = spend - fee
        amount = notional / price
        if amount <= 0:
            return None
        self.cash -= spend
        self.base += amount
        fill = Fill(timestamp, "buy", price, amount, fee, self.cash, self.base)
        self.fills.append(fill)
        return fill

    def sell(self, timestamp: Any, price: float) -> Fill | None:
        if self.base <= 0 or price <= 0:
            return None
        proceeds = self.base * price
        fee = proceeds * self.fee_rate
        amount = self.base
        self.cash += proceeds - fee
        self.base = 0.0
        fill = Fill(timestamp, "sell", price, amount, fee, self.cash, self.base)
        self.fills.append(fill)
        return fill

    def summary(self, last_price: float) -> dict[str, float]:
        eq = self.equity(last_price)
        ret = (eq / self.initial_cash) - 1.0
        fees = sum(f.fee for f in self.fills)
        return {
            "initial_cash": self.initial_cash,
            "final_equity": eq,
            "return_pct": ret * 100,
            "cash": self.cash,
            "base": self.base,
            "n_trades": len(self.fills),
            "total_fees": fees,
        }

    def fills_frame(self) -> pd.DataFrame:
        if not self.fills:
            return pd.DataFrame(
                columns=["timestamp", "side", "price", "amount", "fee", "cash_after", "base_after"]
            )
        return pd.DataFrame([f.__dict__ for f in self.fills])

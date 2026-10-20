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
    pair: str = ""


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
                columns=[
                    "timestamp",
                    "side",
                    "price",
                    "amount",
                    "fee",
                    "cash_after",
                    "base_after",
                    "pair",
                ]
            )
        return pd.DataFrame([f.__dict__ for f in self.fills])


@dataclass
class MultiPaperPortfolio:
    """One shared cash account, one long-or-flat position per pair."""

    pairs: tuple[str, ...]
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0026
    position_size: float = 0.95
    spread_rate: float = 0.0
    fee_free_volume: float | None = None
    subscription: float = 0.0
    cash: float = field(init=False)
    base: dict[str, float] = field(init=False)
    volume_used: float = field(init=False, default=0.0)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash) - float(self.subscription)
        self.base = {pair: 0.0 for pair in self.pairs}

    def quote_fee(self, quote: float) -> float:
        """Spread on every fill. Commission is waived until `fee_free_volume` of notional."""
        spread = float(quote) * float(self.spread_rate)
        if self.fee_free_volume is None:
            commission = float(quote) * self.fee_rate
        else:
            room = max(float(self.fee_free_volume) - self.volume_used, 0.0)
            over = max(float(quote) - room, 0.0)
            commission = over * self.fee_rate
        self.volume_used += float(quote)
        return commission + spread

    def equity(self, marks: dict[str, float]) -> float:
        total = self.cash
        for pair, amount in self.base.items():
            if amount == 0:
                continue
            total += amount * float(marks[pair])
        return total

    def buy_pair(
        self,
        timestamp: Any,
        price: float,
        pair: str,
        quote_spend: float,
    ) -> Fill | None:
        if pair not in self.base or price <= 0:
            return None
        spend = min(self.cash, float(quote_spend))
        if spend <= 0:
            return None
        fee = self.quote_fee(spend)
        amount = (spend - fee) / price
        if amount <= 0:
            return None
        self.cash -= spend
        self.base[pair] += amount
        fill = Fill(timestamp, "buy", price, amount, fee, self.cash, self.base[pair], pair)
        self.fills.append(fill)
        return fill

    def sell_pair(self, timestamp: Any, price: float, pair: str) -> Fill | None:
        if pair not in self.base or self.base[pair] <= 0 or price <= 0:
            return None
        return self.sell_pair_amount(timestamp, price, pair, self.base[pair])

    def sell_pair_amount(
        self,
        timestamp: Any,
        price: float,
        pair: str,
        amount: float,
    ) -> Fill | None:
        if pair not in self.base or price <= 0:
            return None
        amount = min(float(amount), self.base[pair])
        if amount <= 0:
            return None
        proceeds = amount * price
        fee = self.quote_fee(proceeds)
        self.cash += proceeds - fee
        self.base[pair] -= amount
        if self.base[pair] < 1e-12:
            self.base[pair] = 0.0
        fill = Fill(timestamp, "sell", price, amount, fee, self.cash, self.base[pair], pair)
        self.fills.append(fill)
        return fill

    def apply_target_weights(
        self,
        timestamp: Any,
        prices: dict[str, float],
        targets: dict[str, float],
        *,
        tol: float = 0.005,
    ) -> list[Fill]:
        """Move toward target equity weights. Sell first, then buy. Long only."""
        done: list[Fill] = []
        marks = {pair: float(prices[pair]) for pair in self.pairs if pair in prices and prices[pair]}
        if not marks:
            return done
        equity = self.equity(marks)
        if equity <= 0:
            return done
        clean: dict[str, float] = {}
        for pair, weight in targets.items():
            if pair not in self.base:
                continue
            w = float(weight)
            if w > 0 and pair in marks:
                clean[pair] = w
        total = sum(clean.values())
        if total > 1.0 + 1e-9:
            clean = {pair: w / total for pair, w in clean.items()}

        for pair in list(self.base):
            px = marks.get(pair)
            if px is None or self.base[pair] <= 0:
                continue
            want = clean.get(pair, 0.0) * equity
            have = self.base[pair] * px
            if pair not in clean:
                fill = self.sell_pair(timestamp, px, pair)
                if fill:
                    done.append(fill)
            elif have > want * (1.0 + tol) and have - want > equity * tol:
                sell_quote = have - want
                fill = self.sell_pair_amount(timestamp, px, pair, sell_quote / px)
                if fill:
                    done.append(fill)

        equity = self.equity(marks)
        for pair, weight in sorted(clean.items(), key=lambda item: -item[1]):
            px = marks.get(pair)
            if px is None:
                continue
            want = weight * equity
            have = self.base[pair] * px
            gap = want - have
            if gap > equity * tol and gap > 1.0:
                fill = self.buy_pair(timestamp, px, pair, gap)
                if fill:
                    done.append(fill)
        return done

    def fills_frame(self) -> pd.DataFrame:
        if not self.fills:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "pair",
                    "side",
                    "price",
                    "amount",
                    "fee",
                    "cash_after",
                    "base_after",
                ]
            )
        return pd.DataFrame([f.__dict__ for f in self.fills])

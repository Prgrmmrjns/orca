"""Static configuration. Everything here is research output from kagent/ (see research/kagent_report/REPORT.md)."""
from __future__ import annotations

import os

PAPER_ONLY = True  # there is no order-placing code anywhere in this package

# 20 liquid Kraken perps with long histories (research universe, kagent/data.py)
PERPS = {
    "BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD", "XRP": "PF_XRPUSD",
    "ADA": "PF_ADAUSD", "DOGE": "PF_DOGEUSD", "LINK": "PF_LINKUSD", "AVAX": "PF_AVAXUSD",
    "LTC": "PF_LTCUSD", "DOT": "PF_DOTUSD", "BCH": "PF_BCHUSD", "XLM": "PF_XLMUSD",
    "UNI": "PF_UNIUSD", "ATOM": "PF_ATOMUSD", "NEAR": "PF_NEARUSD", "AAVE": "PF_AAVEUSD",
    "FIL": "PF_FILUSD", "ETC": "PF_ETCUSD", "TRX": "PF_TRXUSD", "INJ": "PF_INJUSD",
}
SYMS = list(PERPS)
TREND_UNI = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX"]
MAJORS = {"BTC", "ETH", "SOL", "XRP"}

# Kraken Pro perps tier 1: 0.02% maker / 0.05% taker; slippage assumption from the backtests
TAKER_FEE = 0.0005


def slippage(sym: str) -> float:
    return 0.0005 if sym in MAJORS else 0.0015


START_CAPITAL_EUR = float(os.environ.get("ORCA_CAPITAL_EUR", "1000"))  # Jonas, 2026-11-26
# Kraken perps settle in USD: the EUR budget is converted at the live EUR/USD perp price when the books are created.
MAX_LEVERAGE = 2.0  # conservative EU-retail assumption: total perp notional <= 2x equity (we keep a 10% buffer)
BOOKS = {
    "trend_alt": dict(name="Trend + alt-data", alloc=0.35,
                      description="Frozen BTC-gated SMA 20/50/100/200 trend on 8 perps (long only) plus a long/short "
                                  "sleeve driven by a walk-forward alt-data composite (funding, on-chain, Fear & Greed, "
                                  "stablecoins, Fed net liquidity). Weekly. Report sections 4 and 9."),
    "lowrisk": dict(name="Low-risk long/short", alloc=0.35,
                    description="Weekly beta-neutral long/short on 20 perps: long the 4 lowest-risk coins (vol, max "
                                "daily jump, run-up), short the 4 highest. Report section 11."),
    "policy": dict(name="Objective-max ML policy", alloc=0.20,
                   description="Linear policy whose weights maximise net log growth minus a variance penalty "
                               "(closed form), refit weekly on all data since 2019; 15% vol target. Report section 14."),
    "events": dict(name="News / event trades", alloc=0.10,
                   description="Fast event trades from scraped news: Binance listings/delistings and Haiku-rated "
                               "high-impact coin news. Each event type is tracked and switched off if it loses."),
}

# Risk limits (hard; the self-review can never loosen these)
RISK = dict(
    max_gross_total=2.0,        # sum |notional| / total equity
    max_coin_weight=0.5,        # per coin per book (fraction of book capital)
    dd_half=0.15, dd_quarter=0.25,   # total-equity drawdown brake
    book_kill_dd=0.30,          # a book is disabled at -30% from its high-water mark
    event_max_weight=0.25,      # per event trade, fraction of the events book
    event_stop=0.06, event_max_hold_h=48,
    alloc_min=0.5, alloc_max=1.6,  # meta-allocator bounds relative to the prior allocation
)

# Anthropic usage (Haiku). Hard caps in USD.
HAIKU = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.10 / 1e6, 0.50 / 1e6
LLM_CAP_TOTAL = float(os.environ.get("ORCA_LLM_CAP_TOTAL", "30"))      # whole deployment
LLM_CAP_DAILY = float(os.environ.get("ORCA_LLM_CAP_DAILY", "0.60"))
SCRAPER_CAP_TOTAL = float(os.environ.get("ORCA_SCRAPER_CAP_TOTAL", "20"))  # classification only

UA = "Mozilla/5.0 (compatible; OrcaPaperBot/1.0)"

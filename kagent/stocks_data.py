"""Daily closes for stocks/ETFs Kraken lists as xStocks spot and/or equity perps (Yahoo, adjusted).
Kraken's equity perps mostly launched 2026-02..09 and xStocks spot in mid-2025, so 2-year tests use the
underlying US close as the price proxy (xStocks track it 1:1). python -m kagent.stocks_data"""
from __future__ import annotations

import pandas as pd

from kagent.data import DATA

# perps on Kraken Futures (2026): SPY QQQ GLD TSLA NVDA MSTR HOOD GOOGL CRCL AAPL AMZN COIN META MU INTC SNDK SOXL
PERP = ["SPY", "QQQ", "GLD", "TSLA", "NVDA", "MSTR", "HOOD", "GOOGL", "AAPL", "AMZN", "COIN", "META", "MU", "INTC"]
XSTOCK_ONLY = ["MSFT", "AVGO", "AMD", "PLTR", "NFLX", "JPM", "ORCL", "LLY", "V", "COST", "WMT", "XOM", "UNH", "SLV"]
STOCKS = PERP + XSTOCK_ONLY


def build() -> None:
    import yfinance as yf
    df = yf.download(STOCKS, start="2022-01-01", progress=False, auto_adjust=True)["Close"]
    df.to_csv(DATA / "stocks_close.csv.gz")
    print(df.shape, df.index[0].date(), df.index[-1].date(), df.isna().sum().to_dict())


def load_calendar(index=None) -> pd.DataFrame:
    """Business-day closes forward-filled onto calendar days (weekends carry Friday's price)."""
    s = pd.read_csv(DATA / "stocks_close.csv.gz", index_col=0, parse_dates=True)
    s = s.asfreq("D").ffill(limit=4)
    return s if index is None else s.reindex(index).ffill(limit=4)


if __name__ == "__main__":
    build()

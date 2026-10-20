#!/usr/bin/env python3
"""Paper book: one coin, the 14-day leader, checked once a week.

The coin stays until it falls out of the top 3 names with a positive 14-day
return. Those settings were frozen on monthly results from Aug 2025 through
Aug 2026. September is only a test. Spread is 0.5% per side. No LLM.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.config import load_config  # noqa: E402
from orca.portfolio import MultiPaperPortfolio  # noqa: E402

PAIRS = (
    "AAVE/USD", "ADA/USD", "AERO/USD", "ALGO/USD", "APT/USD", "ARB/USD",
    "ASTER/USD", "AVAX/USD", "BCH/USD", "BNB/USD", "BONK/USD", "BTC/USD",
    "CRV/USD", "DASH/USD", "DOGE/USD", "DOT/USD", "ENA/USD", "ETH/USD",
    "FET/USD", "FIL/USD", "HBAR/USD", "ICP/USD", "INJ/USD", "JUP/USD",
    "LINK/USD", "LTC/USD", "MINA/USD", "NEAR/USD", "ONDO/USD", "PAXG/USD",
    "PENGU/USD", "PEPE/USD", "POL/USD", "PUMP/USD", "RAY/USD", "RENDER/USD",
    "SOL/USD", "SUI/USD", "TAO/USD", "TRUMP/USD", "TRX/USD", "UNI/USD",
    "WLD/USD", "XLM/USD", "XPL/USD", "XRP/USD", "ZEC/USD",
)

LOOKBACK = 14 * 24
EVERY = 7 * 24
SLOTS = 1
STICKY = 3
SPREAD = 0.005
TEST_START = pd.Timestamp("2026-09-07 14:00", tz="UTC")
TEST_END = pd.Timestamp("2026-09-30 14:00", tz="UTC")


def load_pair(pair: str) -> pd.DataFrame:
    base = pair.split("/")[0]
    frames = []
    for path in (
        ROOT / "data" / f"binance_{base}_USDT_1h.csv",
        ROOT / "data" / f"{base}_USD_1h_recent.csv",
    ):
        if not path.exists():
            continue
        frame = pd.read_csv(path, usecols=["timestamp", "open", "close"], parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frames.append(frame)
    if not frames:
        raise SystemExit(f"missing candles for {pair}")
    out = pd.concat(frames).drop_duplicates("timestamp", keep="last")
    return out.set_index("timestamp").sort_index()


def leaders(closes: pd.DataFrame, prev: pd.Timestamp) -> list[str]:
    """Names with a positive 14-day return, strongest first. Uses the prior close."""
    if prev not in closes.index:
        return []
    loc = closes.index.get_loc(prev)
    if isinstance(loc, slice) or loc < LOOKBACK:
        return []
    now = closes.iloc[loc]
    base = closes.iloc[loc - LOOKBACK]
    trail = now / base - 1.0
    trail = trail.replace([pd.NA], pd.NA).dropna()
    trail = trail[trail > 0].sort_values(ascending=False)
    return [str(pair) for pair in trail.index]


def main() -> int:
    cfg = load_config()
    if cfg.get("mode") != "paper":
        raise SystemExit("Refusing to run: config mode is not paper")

    import ccxt

    eur_usd = float(ccxt.kraken().fetch_ticker("EUR/USD")["last"])
    budget_eur = 1000.0
    initial_cash = budget_eur * eur_usd
    subscription = 4.99 * eur_usd

    books = {pair: load_pair(pair) for pair in PAIRS}
    index = books["BTC/USD"].index
    index = index[(index >= TEST_START - pd.Timedelta(hours=LOOKBACK + 2)) & (index <= TEST_END)]
    closes = pd.DataFrame({pair: books[pair]["close"].reindex(index) for pair in PAIRS})
    opens = pd.DataFrame({pair: books[pair]["open"].reindex(index) for pair in PAIRS})
    start_i = int(index.searchsorted(TEST_START))
    end_i = min(int(index.searchsorted(TEST_END)), len(index) - 1)

    port = MultiPaperPortfolio(
        pairs=PAIRS,
        initial_cash=initial_cash,
        fee_rate=0.0,
        position_size=0.95,
        spread_rate=SPREAD,
        fee_free_volume=None,
        subscription=subscription,
    )
    equity_rows: list[dict] = []
    held: list[str] = []
    last_i = -10**9
    for i in range(start_i, end_i + 1):
        ts = index[i]
        prev = index[i - 1]
        if i - last_i >= EVERY or last_i < 0:
            ranked = leaders(closes, prev)
            pool = set(ranked[: SLOTS * STICKY])
            top = ranked[:SLOTS]
            keep = [pair for pair in held if pair in pool]
            chosen = list(keep)
            for pair in top:
                if pair in chosen or len(chosen) >= SLOTS:
                    continue
                chosen.append(pair)
            if set(chosen) != set(held):
                px = {pair: float(opens.at[ts, pair]) for pair in PAIRS if pd.notna(opens.at[ts, pair])}
                for pair in list(held):
                    if pair not in chosen and pair in px:
                        port.sell_pair(ts, px[pair], pair)
                equity = port.equity(px)
                for pair in chosen:
                    if pair not in px or port.base.get(pair, 0) > 0:
                        continue
                    spend = 0.95 * equity
                    if spend > 1:
                        port.buy_pair(ts, px[pair], pair, spend)
                held = [pair for pair, amount in port.base.items() if amount > 0]
            last_i = i
        marks = {
            pair: float(closes.at[ts, pair])
            for pair in held
            if pd.notna(closes.at[ts, pair])
        }
        equity_rows.append({"timestamp": ts, "equity": port.equity(marks) if marks else port.cash})

    final = float(equity_rows[-1]["equity"])
    peak = 0.0
    mdd = 0.0
    for row in equity_rows:
        eq = float(row["equity"])
        peak = max(peak, eq)
        if peak:
            mdd = min(mdd, eq / peak - 1.0)
    ret = (final / initial_cash - 1.0) * 100.0
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    pd.DataFrame(equity_rows).to_csv(logs / "trend_equity.csv", index=False)
    port.fills_frame().to_csv(logs / "trend_fills.csv", index=False)
    report = {
        "generated_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
        "test_start": str(index[start_i]),
        "test_end": str(index[end_i]),
        "hours_done": end_i - start_i + 1,
        "hours_total": end_i - start_i + 1,
        "start_eur": budget_eur,
        "eur_usd": eur_usd,
        "final_equity": final,
        "final_eur": final / eur_usd,
        "return_pct": ret,
        "max_drawdown_pct": mdd * 100.0,
        "n_trades": len(port.fills),
        "total_fees": float(sum(f.fee for f in port.fills)),
        "status": "finished",
        "rule": "14-day leader, one name, keep while it stays in the top 3, check every 7 days",
    }
    (logs / "trend_backtest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{ret:+.2f}% €{final / eur_usd:.0f} trades {len(port.fills)} dd {mdd * 100:.1f}%")
    if ret < 20:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

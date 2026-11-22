# Orca crypto v3 — gated momentum + ML blend (paper)

**PAPER ONLY.** Public Kraken market data, simulated fills, no API keys, no orders.
Started 2026-11-22 with **€1,000**. Ledger: `state/crypto/ledger.json`.

## Rules (frozen 2026-11-22)
| | |
|---|---|
| Universe | Any Kraken EUR spot pair (stablecoins/fiat/wrapped excluded) with ≥210 daily bars; top 20 by 30-day median EUR volume |
| Regime gate | Invested only while BTC/EUR daily close > its 100-day SMA. Otherwise 100% cash |
| Sleeve A (50%) | Momentum: top 5 by average cross-sectional rank of 7/14/30-day return, only if 14-day return > 0 |
| Sleeve B (50%) | ML: top 5 by LightGBM forecast of next-7-day return, only if forecast > 0 |
| Sizing | 10% of equity per pick per sleeve (max 20% in a coin); scaled down so estimated portfolio vol ≤ 50%/yr |
| Trading | Once per completed UTC day, right after 00:00 UTC. Trade only when a target moves > 3% of equity; exits always |
| Fills | Buy at Kraken ask, sell at bid, 0% commission (`ORCA_CRYPTO_FEE` to change) |
| Shorts | None (see below) |
| News | Risk-reduction only: `state/crypto/overrides.json` can cap a coin (hack, depeg, insolvency, delisting). Never adds risk |

The ML model (`crypto/model/gbm.json`, 900 small trees, CPU-trained in ~1 min, evaluated with numpy only)
uses 34 price/volume features: multi-horizon returns, volatility, distance from 90-day high/low, SMA distances,
beta, skew, their cross-sectional ranks, and BTC/market regime features.

## Backtest (`python -m crypto.backtest`, report in `crypto/reports/backtest.json`)
Binance daily data for 52 coins, 2021-01-01 → 2026-10-29, 0.10% per side for spread (live Kraken EUR spreads
measured at 0.01–0.07% per side). ML forecasts are walk-forward: retrained each 1 January on prior data only.

| | CAGR | Sharpe | Max DD | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| **Orca v3** | 50.0% | 1.44 | −34.2% | +276 | −15 | +63 | +71 | −6 | +27 |
| v3, 0.30%/side cost | 32.8% | 1.06 | −44.4% | +240 | −17 | +33 | +46 | −18 | +17 |
| v3, traded one day late | 38.9% | 1.20 | −30.6% | +251 | −17 | +67 | +36 | −16 | +22 |
| BTC buy & hold | 18.4% | 0.58 | −76.6% | +60 | −64 | +156 | +121 | −6 | −12 |
| Equal-weight top 20 | 17.4% | 0.60 | −82.4% | +522 | −78 | +120 | +64 | −47 | −4 |

Since 2024: CAGR 28.9%, Sharpe 0.93, max DD −24.1%. Last 12 months: +26.8% (BTC −28.6%).
Monthly win rate 44%, worst month −14.4%, in the market 53% of days. ML rank IC 0.04–0.11 every test year.

**Read this honestly**
- Survivorship bias: the coin list is what is listed today. Dead coins (LUNA, FTT) are missing, which flatters results.
- 2021 will not repeat on demand. The since-2024 numbers are the fairer guide: Sharpe ≈ 0.5–0.9.
- The rules were chosen on this same history. Only the ML forecasts are strictly out-of-sample.
- Two calendar years of six lost money. Expect long flat stretches in cash and −25% to −35% drawdowns.
- Speed matters: trading a day late costs about a quarter of the return, so the tick should run soon after 00:00 UTC.

## What was tested and rejected
- **Short selling.** Mechanically possible on Kraken (67 EUR pairs allow margin shorts; EEA perpetuals exist), but
  not worth it. Spot margin costs 0.02% to open plus 0.02% every 4 hours, about 44%/yr. Every short sleeve tested
  lost money even with zero carry: short BTC below its 100d SMA −6%/yr, short the 5 weakest alts −13%/yr,
  market-neutral long/short momentum −1% to −20%/yr after borrow.
- Cross-sectional reversal (1–7 days): −55% to −60%/yr.
- Ungated momentum: −80% drawdown. The BTC gate is the main risk control.
- ML alone: lower return than the blend; momentum alone: deeper drawdowns and a −21% 2025.

## Fee assumption
The book assumes 0% commission as instructed. Kraken's published schedule does not currently offer that on
Kraken Pro: spot is 0.40% maker / 0.80% taker at the entry tier, and Kraken+ zero-fee covers app buy/sell only.
At 0.30% per side the backtest still returns 32.8%/yr, but at 0.80% taker and ~61× yearly turnover it would not
be worth running. Check the real fee tier before going live.

## Operations
```bash
python -m crypto.live tick      # mark; rebalance once per completed UTC day (idempotent)
python -m crypto.live status
python -m pytest crypto/test_crypto.py
python -m crypto.fetch_binance && python -m crypto.backtest --export   # refresh data, backtest, retrain
```
- Daily run: a scheduled Claude session ticks just after 00:00 UTC, commits `state/crypto/`, and reports the balance.
- `.github/workflows/crypto-paper.yml` does the same hourly without an agent once it is on `main`.
- Retrain at most monthly, and only by re-running the commands above; record changes in this file.
- Flag to Jonas if equity falls 25% below its high-water mark. No automatic override.

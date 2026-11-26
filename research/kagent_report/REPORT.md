# Kraken agent: fees, universe, design and backtest (2026-11-25)

**Paper only. Nothing here places orders or uses private keys.** Reproduce with:

```bash
pip install -r requirements.txt
python -m kagent.data           # refresh market data into research/kagent_data/
python -m kagent.run_backtest   # writes research/kagent_report/report.json + equity_curves.csv.gz
```

## TL;DR

- **The rule-based agent kept its risk promise but did not make money out of sample.** Over the untouched holdout
  (2024-12-01 → 2026-11-24) it returned **-4.9%/yr, max drawdown -20%**, while BTC fell **-29%/yr with a -58% drawdown**
  and an equal-weight basket of the same 8 coins lost -47%/yr. On 2020-2024 (the design period) it made 54%/yr at Sharpe 1.9.
- **ML on price data has no edge.** Walk-forward LightGBM and ridge models had rank-IC ≈ 0.01 (noise) and lost money
  after costs in both periods. I did not ship them. ML should come back once the news/filings history from the
  Haiku pipeline is long enough to test.
- **Trade perps, not spot.** At Kraken Pro tier 1 spot taker is **0.80%** vs **0.05%** on perps. The same strategy pays
  ~9-15%/yr in spot fees vs ~0.7%/yr on perps (plus ~2.7%/yr funding).
- **Today the agent is 100% cash**: BTC (60.2k) is below its 20/50/100/200-day averages.

## 1. Kraken Pro fees (kraken.com/pro/fees, read 2026-11-25)

Fee tier = best of 30-day spot volume, 30-day futures volume, or assets on platform (since 2026-07-09).

| Tier (30d spot vol) | Spot maker | Spot taker | Futures/perps maker | Futures/perps taker |
|---|---|---|---|---|
| 1 ($0+) | 0.40% | **0.80%** | 0.02% | **0.05%** |
| 2 ($2.5K+) | 0.30% | 0.60% | 0.0175% | 0.045% |
| 3 ($10K+ or $20K assets) | 0.22% | 0.38% | 0.015% | 0.04% |
| 5 ($50K+) | 0.15% | 0.30% | 0.01% | 0.03% |

- **xStocks (tokenized stocks, spot):** 0.00% maker / 0.08% taker at tier 1.
- **Stablecoin/FX pairs:** 0.20% / 0.20% at tier 1.
- **Spot margin:** opening fee + rollover **per 4 hours**: BTC 0.01%, most alts 0.02%, some 0.04%. That is
  ~22-44%/yr on the borrowed amount, on top of trading fees. 2% liquidation fee. Margin shorts are expensive.
- **Perp funding:** hourly; longs pay shorts when positive. Kraken's own last 12 months: BTC +3.7%/yr, ETH +3.6%/yr,
  most alts slightly negative (shorts paid). In 2021-style bull markets funding ran 20-50%/yr.

Note: `strategy/STRATEGY.md` says Jonas has a 0%-fee plan. That is most likely Kraken+ (waives the *app* instant-buy
fee), which does not apply to Kraken Pro order-book trading. Worth confirming in the account.

### Cost model used in every backtest
- Perps: 0.05% taker + slippage per side (0.05% BTC/ETH/SOL/XRP, 0.15% other alts, 0.05% traditional).
- Funding: crypto longs pay 10%/yr (about 3x the recent realised rate), shorts earn nothing; RWA perps 5%/yr either side.
- Spot comparison: 0.80% taker + same slippage; margin rollover 0.12%/day on borrowed amounts.
- Execution one full day after the signal (signal at close t, trade at close t+1). Cash earns 0.

## 2. What's tradable through the Kraken API (public endpoints, 2026-11-25)

| Market | Count | Notes |
|---|---|---|
| Spot pairs | 1,436 | `api.kraken.com/0/public/AssetPairs` |
| Spot pairs with margin | 267 (251 also shortable) | BTC up to 10x; position limits per pair |
| Tokenized stocks (xStocks) spot | 356 pairs | e.g. AAPLx/USD; 0.08% taker |
| Perpetual futures (`PF_*`) | 196 | `futures.kraken.com/derivatives/api/v3/instruments` |
| … of which crypto | ~150 | BTC $629M/day, ETH $136M, SOL $57M; most alts < $5M/day |
| … equities perps | 13 | NVDA-like semis, SOXL/SOXS, AMD, PLTR, MU, Samsung, SK Hynix |
| … xStocks perps | 15 | SPY, QQQ, GLD, TSLA, NVDA, AAPL, GOOGL, META, AMZN, COIN, MSTR, HOOD, CRCL, … |
| … commodities | 6 | gold, silver, WTI, Brent, natgas, copper |
| … FX / index / pre-IPO | 3 / 1 / 3 | EUR, GBP, CHF / US100 / OpenAI, … |

Perps allow long and short on the same instrument, so they replace margin for shorts. They are MTF-listed with an ISIN
(Kraken's EU entity); Jonas should confirm derivatives are enabled for his German account before paper → real.

## 3. Data

- Crypto daily OHLCV 2017+ for 20 liquid coins from Binance's public data mirror (Kraken's public OHLC only gives
  720 bars). Checked against Kraken perp closes since 2022: daily return correlation 0.995-0.9997, median basis 5-13 bps.
- Gold, silver, WTI, Nasdaq-100, S&P 500, EURUSD from Yahoo as stand-ins for Kraken's RWA perps.
- Kraken hourly funding (last 12 months) for all 20 coins.
- **Caveat: survivorship bias.** The universe is today's surviving large coins. Results before ~2021 are flattered.

## 4. Method

- **Design period 2020-01-01 → 2024-11-30.** All choices were made here.
- **Holdout 2024-12-01 → 2026-11-24, run once** after the rule was frozen (`kagent/agent.py`). Not tuned afterwards.
- Walk-forward for ML: retrain every 28 days on all data ending 8 days before (7-day label + 1-day embargo).
- Robustness: 144-config grid (universe × SMA windows × vol target × BTC gate × shorts), cost ×3, funding 25%/yr,
  2-day lag, block-bootstrap 90% Sharpe intervals.

### What was tried

| Idea | Result on design period | Kept? |
|---|---|---|
| Regime-gated trend (hold coins above SMA while BTC above SMA) | all 144 variants Sharpe > 1; median 1.7 | **Yes** |
| BTC regime gate on vs off | median Sharpe 1.84 vs 1.50, max DD -38% vs -49% | Yes |
| Shorting coins in bear regime (perps) | no improvement (1.69 vs 1.64) | No |
| Time-series momentum long/short, 20 coins | Sharpe 0.65, DD -41% | No |
| Adding gold/silver/oil/Nasdaq/S&P/EUR trend sleeve | Sharpe 0.21 alone after carry; drags combined | No |
| ML cross-sectional ranker (LightGBM, ridge), 26 features | rank-IC 0.006-0.013, loses after costs | No |
| ML time-series classifier (next-7d up/down) | AUC 0.497; top quintile had the *worst* returns | No |
| Weekly vs daily rebalance | same Sharpe, half the turnover | Weekly |
| Drawdown brake (½ size at -15%, ¼ at -25%) | Sharpe 1.98 → 1.89, DD -26% → -21% | Included, see below |

### Frozen agent rule (`kagent/agent.py`)
1. Universe: BTC, ETH, SOL, XRP, ADA, DOGE, LINK, AVAX perps (the 8 most liquid with long history).
2. For each SMA window N ∈ {20, 50, 100, 200}: if BTC > SMA(N), hold each coin above its own SMA(N),
   inverse-vol weighted, scaled to 30% annual vol, max 50% per coin. Target = average of the 4 sub-books.
3. Weekly rebalance (Monday), skip trades < 5% of equity, always fully exit. Long only, no leverage (gross ≤ 1).
   Average gross exposure ≈ 0.28, i.e. mostly cash.
4. Drawdown brake on its own equity: ½ size at -15% from high, ¼ at -25%.

## 5. Results (all after fees, slippage, funding)

| Strategy | Design 2020-24 CAGR | Sharpe | Max DD | **Holdout 2024-12→now CAGR** | **Sharpe** | **Max DD** |
|---|---|---|---|---|---|---|
| BTC buy & hold (perp) | 53.3% | 0.99 | -78.9% | **-29.0%** | -0.57 | -57.9% |
| Equal-weight 8 coins buy & hold | 136.1% | 1.49 | -80.5% | **-46.7%** | -0.66 | -74.6% |
| Old Orca v2 on spot fees | 65.4% | 1.67 | -40.9% | **-17.5%** | -0.61 | -41.5% |
| Old Orca v2 on perps | 79.0% | 1.91 | -36.6% | **-9.9%** | -0.27 | -34.4% |
| Trend long/short, 20 coins | 13.9% | 0.65 | -40.8% | **-6.6%** | -0.18 | -29.9% |
| ML ranker ridge L/S | 5.0% | 0.33 | -58.4% | **-42.0%** | -2.00 | -67.3% |
| ML ranker LightGBM L/S | -25.7% | -1.15 | -82.2% | **-5.8%** | -0.19 | -24.9% |
| Agent without brake | 60.6% | 1.98 | -26.4% | **-2.1%** | 0.00 | -19.4% |
| **Agent (frozen, with brake)** | **53.5%** | **1.89** | **-21.4%** | **-4.9%** | **-0.14** | **-20.3%** |

Agent by calendar year: 2020 +96%, 2021 +119%, 2022 -7%, 2023 +51%, 2024 +34%, 2025 +3%, 2026 YTD -9%.
Bootstrap 90% Sharpe interval: design [0.95, 2.62], holdout [-1.23, 0.85].

## 6. Where it fails (honest read)

- **No profit in the last two years.** The edge is avoiding crashes, not making money in chop. 2025-26 was a slow
  bleed with failed rallies; every rally above the averages reversed, so the agent bought high and sold low in small size.
- **Design-period numbers are flattered** by survivorship and by 2020-21, the strongest crypto bull run on record.
  Expect real-world Sharpe well below 1.9; the holdout says somewhere around 0, with drawdowns around -20%.
- **The drawdown brake cost return in both periods** and cut drawdown only a little. I'd drop it and replace it with a
  hard stop: halt and ask Jonas at -25% from the high.
- **No short side.** Shorting in bear regimes did not help on the design period, so it wasn't added. It might have
  helped in the holdout, but adding it now would be tuning on the holdout.
- Cash earns 0 here; USD/USDC held as collateral could earn a few % a year, which would help a mostly-cash strategy.

## 7. Next steps (not started; need Jonas's go-ahead)

1. Paper-trade the frozen rule on live Kraken perp prices (weekly), next to the existing books.
2. When the Haiku news/filings history has a few months of data, test news features as ML inputs with the same
   walk-forward + holdout discipline. Funding rate and open interest (now being collected) are the other candidates.
3. Equity/commodity perps (13 + 15 + 6 markets) only have weeks-to-months of Kraken history; revisit once longer.

## 8. Round 2 (2026-11-26): trying to make the holdout positive without fitting to it

Jonas asked for more research before paper trading. The trap is to keep trying ideas until one looks good on
2024-12 → now, which turns the holdout into training data. So every new idea was first judged on 2020-24 only.

| New idea (judged on 2020-24) | Sharpe | Max DD | Verdict |
|---|---|---|---|
| Frozen agent (reference) | 1.98 | -26% | |
| + short coins in bear regime (½ size / full size) | 1.79 / 1.52 | -32% / -37% | worse |
| Alt-vs-BTC relative trend spread | -0.57 | -64% | fails |
| Cross-sectional momentum L/S (30/60/90d) | 0.68 / 0.22 / 0.30 | -28% to -43% | fails |
| Short-term mean reversion on BTC/ETH/SOL/XRP (9 variants) | -0.9 to -1.6 | -33% to -72% | fails |
| Daily exits (weekly entries) | 1.88 | -24% | similar |
| Entry filters: price ≥ 2-5% above SMA and/or SMA rising | 1.7-1.9 | **-14% to -24%** | same Sharpe, lower drawdown |

**Walk-forward selection (honest out-of-sample for the whole period).** A family of 56 variants (filters × universe
BTC/4/8 coins × 20-30% vol target, plus the bear-short versions). Every 6 months, the variant with the best trailing
2-3 year Sharpe (or Calmar) is picked using only past data and traded for the next 6 months.

| Selection rule | 2021-24 CAGR / Sharpe / DD | **Holdout 2024-12 → now CAGR / Sharpe / DD** |
|---|---|---|
| Best trailing 2y Sharpe | 25.5% / 1.43 / -19.7% | **-1.0% / -0.05 / -9.7%** |
| Best trailing 3y Sharpe | 24.8% / 1.22 / -23.4% | **-0.2% / 0.01 / -7.4%** |
| Best trailing 2y Calmar | 24.4% / 1.61 / -16.5% | **-2.7% / -0.23 / -10.2%** |
| Best trailing 3y Calmar | 15.2% / 1.06 / -20.1% | **-0.2% / 0.01 / -7.4%** |

Since mid-2025 every selector picks the same variant: 8 coins, price ≥ 5% above SMA, SMA rising over 10 days, 20% vol target.

**Holdout across all 56 variants:** only 3 of 56 were positive; median -5%/yr. The best (+8.8%/yr) is "full-size shorts in
bear regime", which was one of the *worst* on 2020-24 (Sharpe 1.52 vs 1.98). Picking it now would be choosing by
hindsight. Rank correlation between 2020-24 Sharpe and holdout Sharpe is only 0.21.

**Honest conclusion:** with price data alone, I can't find a rule that is both justified by 2020-24 and positive on the
last two years. What *is* robust is the drawdown control: the walk-forward version lost ~0-1%/yr with a -7% to -10% max
drawdown while BTC lost 29%/yr with a -58% drawdown. A positive return in this market would need a new source of
information (news, funding, on-chain), not more tuning of price rules.

## 9. Round 3 (2026-11-26): new data sources

New inputs (`kagent/altdata.py`, all public, multi-year, lagged to when they were known; FRED +7 days, others +1 day):
Deribit BTC/ETH perp funding (2019+), Coin Metrics on-chain (MVRV, active addresses, exchange flows), Fear & Greed
(2018+), total stablecoin supply (DefiLlama), Fed net liquidity (balance sheet − reverse repo − TGA), broad dollar,
10y real yield.

**News history:** GDELT's API only serves a rolling ~3-month window, and there's no Anthropic key in the cloud
environment, so there is no multi-year news history to backtest yet. The Haiku scraper's stored output becomes
testable once a few months have built up.

**Alt-data score (`kagent/round3.py`)**, fully walk-forward: every month, keep the inputs whose past correlation with BTC's
forward 7d / 28d return was ≥ 0.03 with the same sign in both halves of the past. Then average their 365-day
z-scores. Go long or short the 8 coins when the score is beyond ±0.25, at a 25% vol target. Recent picks:
on-chain valuation momentum (MVRV), exchange outflows, Fear & Greed, stablecoin growth, Fed liquidity, and BTC trend.
Funding flipped sign between periods, so the selector usually dropped it.

| Year | Trend (frozen) | Alt-data score L/S | **Combined (trend + score)** |
|---|---|---|---|
| 2020 | +113% | -19% | +97% |
| 2021 | +119% | -10% | +119% |
| 2022 | -7% | -3% | **-19%** |
| 2023 | +61% | +8% | +79% |
| 2024 | +45% | +25% | +68% |
| 2025 | +3% | +9% | +21% |
| 2026 YTD | -4% | +4% | **-0.1%** |
| Holdout 2024-12→now CAGR / max DD | -2.1% / -19% | +6.5% / -9% | **+6.3% / -22%** |
| 2020-24 Sharpe / max DD | 1.98 / -26% | 0.01 / -39% | 1.66 / -37% |

Daily return correlation between the two sleeves: 0.18.

**Read:** the alt-data score is the first input that made money in the holdout years without being picked with
hindsight. It is weak in 2020-21 because the selector had only about a year of history then. Combining it with trend
gives a positive holdout (+6%/yr), but it is **not profitable in every year**: 2022 lost 19% and 2026 is flat, and the
combined drawdown (-37% on 2020-24) is worse than trend alone. Across rounds 1-3 about 250 variants were tried, so
expect live results below these numbers.

## 10. Fast event layer (2026-11-26)

### How fast can we be?
| Piece | Measured / expected delay |
|---|---|
| Kraken WebSocket trades (cloud test) | **60-120 ms** behind the exchange timestamp |
| Fed press releases (federalreserve.gov RSS, 5 s polling) | ~1-5 s after publication |
| BLS CPI RSS | release at 08:30 ET; RSS ~seconds |
| SEC EDGAR 8-K live feed | ~10 s to a few minutes |
| PR Newswire / BusinessWire RSS | measured 44 s for a fresh item in a 2-minute test |
| Haiku classification of one headline | ~1-2 s (≈ $0.0001 per headline at Haiku 5.5 prices) |
| X/Twitter, Bloomberg, Dow Jones, Benzinga Pro | fastest for scandals; paid (≈$100 to thousands per month) |

Professional firms trade Fed and CPI headlines in microseconds, so the first move is gone before any of these feeds deliver.

### Event backtest (`kagent/events.py`, 1-minute BTC & ETH, 70 FOMC decisions + 105 CPI releases, 2018-2026)
Rule: look at the move from the release to entry time (+1, +5 or +15 min); **follow** it or **fade** it; hold 15 min - 1 day.
Costs: 0.30% round trip (0.05% perp taker + 0.10% event slippage per side). Design period 2018-2023, test 2024-2026.

- Median BTC move in the first minute: 25 bps; first 15 minutes: 42 bps.
- **Every short hold (15-60 min) loses after costs in both periods.** Trying to scalp the reaction doesn't pay at our speed.
- **Following the first 5-minute move and holding 1 day** was positive in both periods: +17 bps/event on 2018-23 (t = 0.5),
  +64 bps/event on 2024-26 (t = 2.0). Driven by CPI (design +50 bps, test +36 bps); FOMC is inconsistent (design -33, test +105).
- By year (net bps per event): 2020 +116, 2021 +88, 2022 +32, **2023 -51**, 2024 +77, 2025 +71, 2026 +44.
- At ~40 events a year this is worth a few % a year at most. It's a small add-on, not a money machine, and statistically weak.

Single-stock news (earnings, CEO scandals) can't be backtested at 1-minute resolution with free data. Kraken's stock perps
only date from 2026, so that layer has to be judged forward in paper mode.

### Live paper listener (`kagent/live/`)
`python -m kagent.live.run --minutes 60` subscribes to Kraken perp tickers over WebSocket, polls Fed / BLS / SEC / PR Newswire /
BusinessWire, sends each new headline to Haiku (structured output: assets, long/short/none, event type, surprise, confidence),
and opens **paper** positions (5% each, 1-hour exit, fees + slippage) for confident signals. Logs to `research/kagent_live/`.
It has no order code at all. Needs `ANTHROPIC_API_KEY` (on the Mac's `.env`); without it, it only logs feed delays.

## 11. Signal discovery, last 2 years only (2026-11-26)

New data: Kraken Futures' own public analytics for 30 perps, daily, and hourly for BTC/ETH/SOL (`kagent/kraken_analytics.py`).
It covers open interest, long/short ratio, liquidations, taker buy/sell imbalance, top-20% trader positioning, trade
count/size, volatility and basis. Combined with price/volume, on-chain, sentiment, macro and cross-asset features, that is
333 daily tests (`kagent/discovery.py`) and 75 hourly tests (`kagent/discovery_hourly.py`).

**Method:** year A (2024-11-26 → 2025-11-25) finds signals; year B (2025-11-26 → now) checks them. "Robust" means
|t| > 2 in A and the same sign with |t| > 1.5 in B. With 333 tests, about 15 would pass |t| > 2 in A by luck alone.

### What predicts moves (robust in both years)
**Only one family: risk / lottery-ness, cross-sectionally.** Coins that have been the most volatile, had the biggest single-day
jump in the last 30 days, ran furthest off their 30-day low, or saw a spike in trade count **underperform the other coins
over the next 1-7 days**. Rank-IC is -0.05 to -0.13, with t -2.3 to -4.5 in both years: 19 of 19 robust results are this
family (volatility 7d/30d, Kraken rolling volatility, max daily return 30d, daily range, run-up from the 30d low,
trade-count spike, volume spike).
This matches the "low-volatility / MAX" effect known from stocks: gamblers overpay for lottery-like coins.

### What does not predict moves (honest negatives)
- **Market timing (whether all of crypto goes up or down):** none of the ~40 market-level features (Fear & Greed,
  stablecoins, Fed liquidity, dollar, real yields, MVRV, exchange flows, Deribit funding, BTC trend, breadth, market-wide
  liquidations, Nasdaq/S&P/gold/oil) was robust over these two years.
- **Kraken positioning (open interest, long/short ratio, taker imbalance, liquidations, top traders):** nothing robust
  on daily. Hourly, the crowd long/short ratio leans contrarian (more retail longs → weaker next 1-24h, t ≈ -1.1 to -1.9),
  which is too weak to trade.
- **Short-term reversal:** 1-3 day reversal was strong in year A (t -4) and **vanished in year B**. Hourly reversal is
  statistically real (t -2.4 to -3.4 in both years), but it's worth < 1 bp per hour against ~15 bps of costs.
  Fading big hourly moves (|z| > 2-3) loses on BTC and ETH.

### Is the robust signal tradable?
Market-neutral perps book: each week, long the 4 lowest-risk coins and short the 4 highest-risk coins of 20. Inverse-vol
weights, beta-neutral to BTC, 0.05% taker + 0.05-0.15% slippage per side, actual Kraken funding in year B.

| Variant | Year A return / Sharpe / max DD | Year B return / Sharpe / max DD |
|---|---|---|
| Low-risk composite (vol + max-jump + run-up), weekly | **+13.7% / 1.20 / -8%** | **+7.5% / 0.53 / -16%** |
| Same, one extra day of delay | +16.4% / 1.43 / -7% | +5.6% / 0.42 / -15% |
| Same, rebalanced daily | -9.1% (costs 14%/yr) | -17.4% |
| Low-vol only, weekly | +9.8% / 0.79 | -2.1% / -0.08 |
| Low-MAX only, weekly | +7.3% / 0.62 | +4.8% / 0.40 |

Correlation to BTC is about 0.1, so it can sit next to the trend sleeve. It's positive in both years but modest. The edge
is real in rank terms, while costs and the short side's squeezes eat a large share of it. Weekly is the only workable
cadence.

## 12. Combined book, last 2 years (2026-11-26)

Three sleeves, mixed with fixed weights chosen in advance (nothing fitted to the result):
trend (frozen agent), alt-data composite (round 3), and the weekly beta-neutral low-risk long/short sleeve (`kagent/lowrisk.py`).
All net of Kraken perp fees, slippage and funding. Year A = 2024-11-26..2025-11-25, year B = 2025-11-26..now.
The low-risk signal was *found* on year A, so only year B is a clean check for it.

| Mix | A ann | A Sharpe | A maxDD | B ann | B Sharpe | B maxDD |
|---|---|---|---|---|---|---|
| trend + alt score | +24.2% | 0.82 | -21.9% | +1.1% | 0.17 | -19.5% |
| low-risk L/S | +13.7% | 1.20 | -8.0% | +7.4% | 0.52 | -15.7% |
| **50/50 of the two** | **+20.7%** | **1.17** | **-10.3%** | **+5.6%** | **0.45** | **-15.8%** |
| 1/3 each (trend, alt, low-risk) | +11.6% | 1.10 | -5.9% | +3.3% | 0.40 | -10.8% |
| BTC buy & hold | -6.1% | 0.06 | -32.0% | -31.1% | -0.62 | -39.5% |

The low-risk sleeve is uncorrelated with the others (corr -0.07 to trend, +0.09 to BTC), so mixing halves drawdown.
Both years are positive for every mix, but not every quarter: 2026Q2 and 2026Q4 (to date) are negative (-6% / -8% for 50/50).
Block-bootstrap Sharpe 90% CI for the 50/50 mix: year A -0.5..2.9, year B -1.7..2.3 (P(Sharpe>0) 0.65 in B).
Honest read: positive, beats BTC by a wide margin with a third of its drawdown, but the edge in year B is not statistically
distinguishable from zero. Files: `combined_2y.csv`, `combined_2y_quarterly.csv`, `sleeve_returns_2y.csv.gz`.

## 13. Adding stocks (xStocks / equity perps), last 2 years (2026-11-26)

Kraken's equity perps launched Feb-Sep 2026 and xStocks spot in mid-2025, so the 2-year test uses the underlying US
close as the price (xStocks track it 1:1). Universe: 28 names Kraken lists (14 with perps: SPY QQQ GLD TSLA NVDA MSTR
HOOD GOOGL AAPL AMZN COIN META MU INTC; 14 xStocks only: MSFT AVGO AMD PLTR NFLX JPM ORCL LLY V COST WMT XOM UNH SLV).
Costs: perp 0.05% fee + 0.05% slippage per side, longs pay 5%/yr funding (assumed; no history yet). xStocks spot:
0.08% fee + 0.05% slippage, no funding, long only. Code: `kagent/stocks_data.py`, `kagent/stocks_eval.py`.

| Sleeve / mix | A ann | A Sharpe | A maxDD | B ann | B Sharpe | B maxDD |
|---|---|---|---|---|---|---|
| Stock trend (SPY-gated SMA, 15% vol target), perps | +13.2% | 1.57 | -4.1% | +4.4% | 0.57 | -5.2% |
| Same, on xStocks spot (no funding) | +15.5% | 1.81 | -4.0% | +6.8% | 0.86 | -5.0% |
| Stock low-risk L/S, 28 names | -0.9% | -0.10 | -5.7% | +3.8% | 0.46 | -7.7% |
| Stock low-risk L/S, 14 perp names only | -13.1% | -1.75 | -16.1% | -2.2% | -0.18 | -12.2% |
| Coins only (section 12) | +20.7% | 1.17 | -10.3% | +5.6% | 0.46 | -15.8% |
| **Coins + stock trend (3 equal sleeves)** | **+18.5%** | **1.44** | **-7.2%** | **+5.5%** | **0.57** | **-11.6%** |
| Coins + stock trend + stock low-risk (4 equal) | +13.6% | 1.44 | -5.1% | +5.3% | 0.71 | -8.7% |
| SPY buy & hold | +14.3% | 0.78 | -18.8% | +7.3% | 0.55 | -11.5% |

Stock trend is only 0.2 correlated with the coin book, so adding it keeps return and cuts drawdown by about a quarter.
The low-risk effect that works in coins does not work in stocks here (negative on the shortable perp names), so it is dropped.
Caveat: the stock list is what Kraken lists *today*, which favours past winners (PLTR, HOOD, MSTR); that flatters the trend sleeve.

## 14. Deep round (2026-11-26): everything tradable, an explicit objective, purged CPCV, ML policy, minute-level news

**Objective.** J = 365·mean(log(1+r)) − 3·365·mean(min(r,0)²) on daily net returns: annualised net log growth
(Kelly already charges variance once) plus an extra charge on downside variance only. Upside volatility is free.

**Validation.** Purged combinatorial CV (`kagent/cpcv.py`): the window is cut into 20 contiguous blocks, each split
tests on 4 random blocks (300 random splits of C(20,4)), trains on the rest minus 5 days before and 10 days after
each test block (plus the label horizon for ML). Reported as a distribution over splits. Also: deflated Sharpe
(Bailey & López de Prado) and probability of backtest overfitting (PBO). Data after 2026-10-15 is kept untouched.

**Universe and costs** (`kagent/zoo.py`): 20 coin perps (fee + slippage, real Kraken funding, Binance funding before
Kraken's history starts), 14 stock perps (0.10%/side, longs pay 5%/yr), xStocks spot on the ~50 largest US stocks
as of 2024-11 (0.13%/side, long only). Coin spot and margin are dominated by perps (0.80% taker) and not used.

**Hindsight check.** With the stock list Kraken offers *today* (full of 2025 winners: PLTR, HOOD, MSTR...) stock
long-only looked great. With the 2024-11 top-50 list most of that edge disappears. Section 13's stock numbers are biased.

**Strategy zoo: 200 configs** (TS trend/momentum long-short, long-only and short-only; BTC trend; XS momentum,
reversal, low-risk, low-vol, low-MAX, funding carry, residual reversal; SPY-gated stock trend; stock XS momentum,
reversal, low-vol; risk-off coin shorts), daily or weekly rebalanced, each scaled ex-ante to 15% vol.

| Selection inside CPCV | Window | Sharpe p10 / median / p90 | median ann. | P(ann>0) |
|---|---|---|---|---|
| best single config, 2y data only | 2y | -2.5 / -0.9 / 0.9 | -13.7% | 0.23 |
| top-10 equal, 2y only | 2y | -2.8 / -1.1 / 0.5 | -9.8% | 0.21 |
| best single, 2019+ data | all blocks | -0.6 / 0.2 / 1.1 | +2.1% | 0.61 |
| objective-optimised mix (≤2x), 2019+ | all blocks | -0.1 / 0.9 / 1.9 | +15.7% | 0.83 |
| same | test days in last 2y | -1.7 / 0.2 / 1.4 | +1.3% | 0.55 |

PBO (2019+) = 0.29. Deflated-Sharpe probability of the full-period winner ≈ 0 (200 trials): not significant.
Two years cannot separate 200 similar strategies; choosing among them on 2 years is overfitting.

**ML policy that maximises the objective** (`kagent/policy.py`, coins, weekly). Weights are linear in 16 features
(cross-sectional z-scores + market-timing block); the portfolio return is θ·g_t, so the 2nd-order approximation of J
has the closed form θ = (Cov(g) + ρI)⁻¹ mean(g). Fit inside each CPCV fold (2019+), purged by the 7-day label horizon.

| Model | Sharpe p10 / median / p90 | median ann. | P(ann>0) | median maxDD |
|---|---|---|---|---|
| linear objective-max policy (300 splits) | -0.42 / 0.51 / 1.50 | +6.7% | 0.72 | -18% |
| same, splits mostly in the last 2y (40) | median 0.43 | +6% | | -22% |
| LightGBM rank model (60 splits) | -1.61 / -0.54 / 0.57 | -10.3% | 0.28 | -26% |

The policy loads mostly on trend (price above 50/200-day SMA, positive) and against 30-day winners. Its
coefficients move a lot between folds, a sign the signal is weak.

**Falling prices.** In BTC's 8 down months of the last 2 years, short-capable trend books (BTC SMA20/50 long-short,
short-only TS momentum, funding carry) made +3% to +4.6% per month, and gave most of it back in up months.

**Minute-level news (data in `research/kagent_data/news/`, 1m prices in `m1/`, not committed).**
- Binance listing announcements → buy the coin on Kraken perps 1-5 min later: +4-5% average after 0.7% costs over
  1-4 h, but only 15 events matched a Kraken perp and fewer than half won (`events_binance.csv`). Not proven.
- FinancialJuice posts every data release as "ACTUAL x (FORECAST y)" (history since 2025-03, 4,212 releases). Trading
  BTC/ETH on the learned surprise sign, CPCV over time blocks: about 0 bp gross, −20 bp net per trade. BTC's typical
  5-minute move on a US release is only 8-11 bp, below the 20 bp round trip. No edge on crypto from macro squawks.

**Which free source is first** (`kagent/news_latency.py`, second-stamped history; live poller `kagent/newslat.py`):

| Event | Official source | FinancialJuice TG | unfolded TG | WatcherGuru TG | Wu Blockchain TG |
|---|---|---|---|---|---|
| US macro release (CPI, PPI, PCE, claims, ISM, retail) | agency site at :00 | median 31 s (p10 1 s, p90 50 s, n=123) | 14 s (n=10) | 25 s (n=14) | 459 s |
| Binance listing / delisting | Binance CMS API (t=0) | – | – | rarely | 537 s (n=52) |

Live RSS lags (seen time − item time, 10 h poll): FinancialJuice TG ~30 s, PR Newswire ~2 min, Seeking Alpha ~2.5 min,
Investing.com ~11 min. BTC does about half of its 15-minute reaction within the first 2 minutes after a release, so
a 30 s squawk still catches most of it, but the move is too small to pay perp costs.
Paid feeds publish no measured latency (see `news_feeds_paid.md`): Tree News $500-2,500/mo, Benzinga Pro $37-197,
Newsquawk $199-399, Bloomberg custom. For crypto the fastest free source is the exchange's own announcement API.

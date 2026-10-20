# Orca strategy comparison (paper)

Generated: 2026-09-30T11:56:43Z (UTC)
Mode: `paper` — no live orders.
Timeframe: `1d` | fee_rate: `0.0026` (0.26% per side) | initial_cash: `10000.0`

## Ranking (all pair×strategy rows)

| Rank | Strategy | Pair | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `sma_crossover` | ETH/USD | 52.07 | -45.22 | 25 | 41.7 | 0.73 | 9.76 | 42.31 |
| 2 | `macd` | BTC/EUR | 6.18 | -24.57 | 47 | 26.1 | 0.25 | 27.49 | -21.30 |
| 3 | `rsi_mean_reversion` | BTC/EUR | -1.87 | -25.26 | 6 | 66.7 | 0.12 | 27.49 | -29.36 |
| 4 | `macd` | ETH/USD | -21.84 | -54.81 | 50 | 28.0 | -0.06 | 9.76 | -31.60 |
| 5 | `rsi_mean_reversion` | ETH/USD | -31.44 | -55.91 | 4 | 0.0 | -0.17 | 9.76 | -41.20 |
| 6 | `dual_ma_volume` | ETH/USD | -33.42 | -43.76 | 15 | 14.3 | -0.80 | 9.76 | -43.18 |
| 7 | `bollinger_mean_reversion` | BTC/USD | -12.94 | -37.85 | 24 | 66.7 | -0.18 | 32.04 | -44.98 |
| 8 | `bollinger_mean_reversion` | BTC/EUR | -19.63 | -42.85 | 24 | 66.7 | -0.32 | 27.49 | -47.12 |
| 9 | `bollinger_mean_reversion` | ETH/USD | -37.76 | -57.29 | 22 | 54.5 | -0.43 | 9.76 | -47.52 |
| 10 | `rsi_mean_reversion` | BTC/USD | -17.56 | -37.72 | 4 | 50.0 | -0.16 | 32.04 | -49.59 |
| 11 | `sma_crossover` | BTC/EUR | -22.52 | -37.46 | 31 | 26.7 | -0.50 | 27.49 | -50.01 |
| 12 | `dual_ma_volume` | BTC/EUR | -25.52 | -37.57 | 16 | 25.0 | -0.75 | 27.49 | -53.01 |
| 13 | `sma_crossover` | BTC/USD | -21.03 | -37.92 | 29 | 28.6 | -0.43 | 32.04 | -53.07 |
| 14 | `macd` | BTC/USD | -22.41 | -32.32 | 58 | 27.6 | -0.37 | 32.04 | -54.45 |
| 15 | `dual_ma_volume` | BTC/USD | -31.50 | -33.91 | 17 | 25.0 | -1.04 | 32.04 | -63.54 |

## Best candidate (paper constraints)

**`sma_crossover`** on **ETH/USD** — return 52.07%, excess vs B&H 42.31%, max DD -45.22%, trades 25.

Selection rule: maximize excess return vs buy-and-hold, then total return, then shallower drawdown. Prefer strategies with ≥1 round trip when tied on zeros.


## Fee sensitivity (0.80% taker)

Same candles with `fee_rate=0.008`. Best remains **`sma_crossover`** on **ETH/USD** (return 33.67%, excess 24.47%, max DD -50.06%). MACD on BTC/EUR flips from +6% at 0.26% fees to negative at 0.80% — high trade count strategies are fee-fragile.

## Caveats

- Short history: Kraken public OHLC caps at ~720 daily candles (~2 years).
- In-sample only; no walk-forward / out-of-sample — high overfitting risk.
- Fees applied per fill; default is configurable (Tier-1 taker is 0.80%).
- Long-only spot; signals execute on next bar open (no same-bar lookahead).
- Sharpe~ uses rf=0 and calendar-day annualization; treat as descriptive.
- Past paper performance does not predict live results.

## Re-run

```bash
cd /workspace/orca && source .venv/bin/activate
python scripts/compare_strategies.py
python scripts/compare_strategies.py --fee 0.008 --pairs BTC/USD,BTC/EUR
python scripts/compare_strategies.py --refresh  # re-fetch OHLCV
```


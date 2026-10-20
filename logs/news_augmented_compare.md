# Orca news-augmented strategy comparison (paper)

Generated: 2026-09-30T12:56:06Z (UTC)
Mode: `paper` — no live orders. Pair: **BTC/USD** only.
Timeframe: `1d` | fee_rate: `0.0026` (0.26% per side) | initial_cash: `10000.0`
News articles cached: **2484** (sources: coindesk, cointelegraph, decrypt, google_btc, google_btc_hist)

## Ranking

| Rank | Label | Filter | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `rsi_mean_reversion_news_gate` | long_gate | 11.83 | -18.20 | 4 | 100.0 | 0.38 | 32.04 | -20.21 |
| 2 | `rsi_mean_reversion_news_strict` | long_gate | 8.34 | -18.20 | 2 | 100.0 | 0.32 | 32.04 | -23.69 |
| 3 | `rsi_mean_reversion_news_size` | sentiment_size | 8.34 | -18.20 | 2 | 100.0 | 0.32 | 32.04 | -23.69 |
| 4 | `macd_news_strict` | long_gate | -4.85 | -14.35 | 16 | 25.0 | -0.15 | 32.04 | -36.89 |
| 5 | `macd_news_size` | sentiment_size | -4.85 | -14.35 | 16 | 25.0 | -0.15 | 32.04 | -36.89 |
| 6 | `rsi_mean_reversion_baseline` | none | -17.56 | -37.72 | 4 | 50.0 | -0.16 | 32.04 | -49.59 |
| 7 | `sma_crossover_baseline` | none | -21.03 | -37.92 | 29 | 28.6 | -0.43 | 32.04 | -53.07 |
| 8 | `macd_baseline` | none | -22.41 | -32.32 | 58 | 27.6 | -0.37 | 32.04 | -54.45 |
| 9 | `sma_crossover_news_gate` | long_gate | -24.65 | -31.19 | 21 | 30.0 | -0.65 | 32.04 | -56.69 |
| 10 | `sma_crossover_news_strict` | long_gate | -26.40 | -31.19 | 17 | 25.0 | -0.84 | 32.04 | -58.44 |
| 11 | `sma_crossover_news_size` | sentiment_size | -26.40 | -31.19 | 17 | 25.0 | -0.84 | 32.04 | -58.44 |
| 12 | `macd_news_gate` | long_gate | -30.21 | -37.27 | 32 | 25.0 | -1.06 | 32.04 | -62.25 |

## Baseline vs news (same base rule)

### `rsi_mean_reversion`

Baseline return **-17.56%**, excess **-49.59%**, trades 4.
- `rsi_mean_reversion_news_gate` (long_gate): return 11.83% (+29.38 pp vs baseline), max DD -18.20%, trades 4
- `rsi_mean_reversion_news_strict` (long_gate): return 8.34% (+25.90 pp vs baseline), max DD -18.20%, trades 2
- `rsi_mean_reversion_news_size` (sentiment_size): return 8.34% (+25.90 pp vs baseline), max DD -18.20%, trades 2

### `macd`

Baseline return **-22.41%**, excess **-54.45%**, trades 58.
- `macd_news_strict` (long_gate): return -4.85% (+17.56 pp vs baseline), max DD -14.35%, trades 16
- `macd_news_size` (sentiment_size): return -4.85% (+17.56 pp vs baseline), max DD -14.35%, trades 16
- `macd_news_gate` (long_gate): return -30.21% (-7.80 pp vs baseline), max DD -37.27%, trades 32

### `sma_crossover`

Baseline return **-21.03%**, excess **-53.07%**, trades 29.
- `sma_crossover_news_gate` (long_gate): return -24.65% (-3.62 pp vs baseline), max DD -31.19%, trades 21
- `sma_crossover_news_strict` (long_gate): return -26.40% (-5.37 pp vs baseline), max DD -31.19%, trades 17
- `sma_crossover_news_size` (sentiment_size): return -26.40% (-5.37 pp vs baseline), max DD -31.19%, trades 17

## Does news help?

**Yes (in-sample):** best news variant `rsi_mean_reversion_news_gate` beats its baseline on the ranking key (excess -20.21% vs baseline excess -49.59%).

## Look-ahead check

- OK: **True** (bars checked: 17)
- Rule: For bar timestamp T (00:00 UTC), features use articles with published calendar day <= T-1 day (publish day shifted +1 before align).
- Sampled bars: news features match rebuild using only articles with published_at < bar timestamp on the full calendar index (no look-ahead).

## Caveats

- **Scraping fragility:** RSS / Google News HTML-free feeds can change, rate-limit, or return partial history; cache under `data/news/`.
- **Sentiment quality:** keyword lists + VADER on headlines are crude; sarcasm, tickers, and publisher suffixes add noise.
- **Coverage gaps:** Google News monthly RSS is capped (~100/query); not a complete news universe.
- **Look-ahead:** features use articles with `published_at < bar_ts` (daily bars at 00:00 UTC ⇒ through prior calendar day).
- **In-sample only:** no walk-forward; news thresholds can overfit.
- Kraken public OHLC ~720 daily candles; fees ~0.26% per side.
- Paper simulation only — not predictive of live trading.

## Re-run

```bash
cd /workspace/orca && source .venv/bin/activate
python scripts/compare_news_strategies.py
python scripts/compare_news_strategies.py --refresh-news
```


# TabPFN paper backtest (walk-forward)

Generated: 2026-09-30 14:43:54 CEST (Europe/Berlin)
Mode: `paper` — no live orders. Prior Labs TabPFN cloud client only.

- Pair / TF: `BTC/USD` `1d`
- Candles: 720 (2024-10-11 00:00:00+00:00 → 2026-09-30 00:00:00+00:00)
- Fee rate: `0.0026` (0.26% per side)
- Package: `tabpfn-client==0.6.1`
- Auth env: `TABPFN_API_KEY` (value masked, never logged)

## Method

- Features: lagged returns (1/2/3/5/10/20), rolling vol, RSI-14, SMA ratios,
  HL range, volume z-score, momentum — all causal (no look-ahead).
- Target: next-bar close direction (up=1 / down=0).
- Train window: rolling last `180` usable rows (min `120`).
- Retrain every `21` bars; batch-predict until next refit.
- Trade rule: long-only when predicted up; otherwise flat (signal −1).
- Fills: prior-bar signal @ next open (same as Orca).
- API usage this run: 28 fit / 28 predict (571 rows), 2452.4s.

## Results

| Strategy | Return % | Max DD % | Trades | Win % | Sharpe~ | B&H % | Excess % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tabpfn_walkforward` | -60.05 | -69.16 | 137 | 41.2 | -1.77 | 32.04 | -92.09 |
| `sma_crossover` | -21.03 | -37.92 | 29 | 28.6 | -0.43 | 32.04 | -53.07 |

Buy & hold (fee on entry): **32.04%**.
TabPFN final equity: **3995.05** (initial 10000.00); fees paid 2508.75.

## Caveats

- Short sample (~720 Kraken public daily candles ≈ 2y).
- Periodic retrain (not every bar) → stale model between refits.
- TabPFN cloud API rate / quota limits; n_estimators kept small.
- Direction classification ≠ calibrated edge; fees (~0.26%/side) hurt churn.
- Paper simulation only — no live trading.
- Past paper performance does not predict live results.

## Re-run

```bash
cd /workspace/orca && source .venv/bin/activate
python scripts/run_tabpfn_backtest.py --cache
```

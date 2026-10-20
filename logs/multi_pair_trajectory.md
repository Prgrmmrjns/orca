# Multi-pair trajectory model (paper)

Generated: 2026-09-30 15:04:41 CEST (Europe/Berlin)
Mode: `paper` — no live orders. sklearn MultiOutputRegressor only (no GPU / no TabPFN).

- Pairs: `BTC/USD`, `SOL/USD`, `XRP/USD`
- Timeframe: `1d` | aligned candles: 720 (2024-10-11 00:00:00+00:00 → 2026-09-30 00:00:00+00:00)
- OOS eval window: `2025-02-28 00:00:00+00:00` → `2026-09-30 00:00:00+00:00` (580 bars); B&H benchmarks use this window.
- Fee rate: `0.0026` (0.26% per side)
- Initial cash: 10000

## Method

- Shared causal features: per-pair lagged returns (1/2/3/5/10), rolling vol,
  RSI-14, momentum, HL range, volume z-score; pairwise rolling correlations
  and 1d return spreads (cross-asset).
- Targets (trajectory): forward close-to-close returns at horizons [1, 2, 3, 5] days × 3 pairs (12 outputs).
- Model: `MultiOutputRegressor(ridge)` (alpha=1.0) — CPU only.
- Walk-forward: expanding window, min_train=120, retrain_every=21 bars; 28 fits / 580 predicted rows.
- Signal score: mean predicted trajectory return (also expose 1d).
- **Long/flat**: equal cash split; long if predicted 1d return > 0 else flat.
- **Relative alloc**: long-only weights ∝ max(pred_1d, 0); rebalance when L1 weight drift ≥ 20% and ≥5 bars since last rebalance; fees on turnover.
- Fills / marks: prior-bar signal @ current open (no same-bar look-ahead).

## Benchmarks (buy & hold)

| Benchmark | Return % | Max DD % | Sharpe~ |
| --- | ---: | ---: | ---: |
| B&H `BTC/USD` | 0.75 | n/a | n/a |
| B&H `SOL/USD` | -16.94 | n/a | n/a |
| B&H `XRP/USD` | -27.25 | n/a | n/a |
| Equal-weight B&H | -14.48 | -61.13 | 0.11 |

## Long/flat (equal split, trajectory 1d sign)

| Sleeve | Return % | Max DD % | Trades | Sharpe~ | B&H % | Excess % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `BTC/USD` | -46.33 | -62.44 | 135 | -0.91 | 0.75 | -47.08 |
| `SOL/USD` | -49.08 | -69.34 | 121 | -0.38 | -16.94 | -32.14 |
| `XRP/USD` | -65.44 | -70.52 | 159 | -0.88 | -27.25 | -38.19 |
| **Portfolio** | **-53.62** | **-63.86** | **415** | **-0.85** | -14.48 (EW) | -39.13 |

## Relative allocation (trajectory scores)

- Final equity: **4344.97** | return **-56.55%** | max DD **-72.54%**
- Sharpe~: -0.64 | rebalances: 113 | fees: 1327.65
- Excess vs equal-weight B&H: **-42.07%**

## OOS prediction diagnostics (1d)

| Pair | N | Corr | Dir acc | Pred mean | Realized mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| `BTC/USD` | 579 | -0.013 | 48.9% | 0.242% | 0.028% |
| `SOL/USD` | 579 | -0.069 | 49.7% | 0.299% | 0.044% |
| `XRP/USD` | 579 | -0.150 | 45.1% | 0.289% | 0.017% |

## Caveats

- Kraken public OHLC ≈ 720 daily bars (~2y); short sample, regime-sensitive.
- Inner-join alignment drops days missing any pair.
- Linear multi-output Ridge is weak on noisy crypto returns; direction accuracy
  near coin-flip is expected — treat results as research, not an edge claim.
- Fees (~0.26%/side) punish frequent long/flat flips and relative rebalances.
- Expanding walk-forward still reuses sequential OOS for reporting (no nested CV).
- B&H benchmarks are computed on the same OOS window as the strategy (after min_train).
- Spot long-only; no shorts, funding, or borrow.
- Paper simulation only — no live trading. Past paper ≠ future live.

## Re-run

```bash
cd /workspace/orca && source .venv/bin/activate
python scripts/run_multi_pair_trajectory.py --cache
# or: python scripts/run_multi_pair_trajectory.py --refresh
```

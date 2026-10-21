# Orca strategy v2 — trend-filtered, vol-sized core basket

**Status:** PAPER ONLY (Kraken public data, simulated fills). Started 2026-10-20 with €1,000.
Replaces the September 2026 single-coin momentum + headline rule (archived in `archive/september_rule/`).

## Why this strategy (research summary, `research/`)
Data: Binance public daily closes 2017-08 → 2026-10-19 for 15 large coins (Kraken's public OHLC only gives 720 days, enough for live signals but not for research). Costs modelled at **0.5% per side** (0.4% Kraken taker + ~0.1% spread).

- **Short-term mean reversion, not tradable:** 1-day return autocorrelation is slightly negative (BTC −0.05, ETH −0.06) — far too small to beat 1% round-trip costs.
- **Medium-term trend persists:** pooled across coins, next-7-day return after a positive 30-day return averaged **+2.4%** vs **+0.1%** after a negative one (similar for 14–120 day lookbacks).
- **BTC regime drives everything:** BTC forward 30d return +5.6% when above its 200d SMA vs +2.4% below; alts forward 30d **+9.3%** when BTC is above vs **+1.7%** below. Alt correlation to BTC since 2023: ETH 0.81, SOL 0.73, XRP 0.57 — diversification is limited and vanishes in crashes.
- **Drawdowns are brutal:** every coin had −83% to −99% max drawdown. Risk control matters more than coin picking.
- **Single-coin cross-sectional momentum (the old rule's family) fails after costs:** top-1 by 14d return, weekly, 15 coins: CAGR 33%, Sharpe 0.79, **max DD −91%**, ~52× turnover/yr.
- **Wider alt universes don't help** once survivorship is considered (the 15-coin set is today's survivors and still underperformed the 4-coin core out-of-sample).

### Out-of-sample results (2019-01-01 → 2026-10-19, fees in)
| Strategy | CAGR | Sharpe | Max DD |
|---|---|---|---|
| BTC buy-and-hold | 48.2% | 0.95 | −76.6% |
| Equal-weight BTC/ETH/SOL/XRP buy-and-hold | 66.0% | 1.06 | −82.1% |
| Equal-weight 15 coins, monthly rebalance | 52.0% | 0.94 | −79.2% |
| Trend core, params re-picked each year walk-forward | 44.8% | 1.19 | −53.6% |
| **v2: SMA 50/100/200 ensemble, 40% vol target (chosen)** | **46.9%** | **1.32** | **−44.7%** |
| v2 variant, 60% vol target | 59.6% | 1.32 | −55.6% |
| Old-style top-1 momentum | 33.3% | 0.79 | −91.0% |

v2 by year: 2019 +28.5, 2020 +128.5, 2021 +142.5, **2022 −10.9** (BTC −64), 2023 +72.3, 2024 +83.2, 2025 −0.9, 2026 YTD +1.4. Turnover ~6.5×/yr.

**Honest read:** the edge is *risk*, not return. v2 roughly matches BTC's return with about half the drawdown. It lags hard in V-shaped recoveries (2019, 2023 early) and is flat in choppy years (2025–26). The ensemble was chosen after seeing that single-window walk-forward picks jumped around; averaging all windows is the no-tuning alternative, but it's still a mild look-ahead choice — the forward paper test is the real check.

## Rules
1. **Universe:** BTC, ETH, SOL, XRP vs USD on Kraken. Everything else is USD cash (no yield modelled).
2. **Three sub-books**, one per SMA window N ∈ {50, 100, 200} days, on daily closes (last completed UTC bar).
   - Sub-book is invested only if **BTC close > BTC SMA(N)**.
   - Holds each coin whose close > its own SMA(N).
   - Weights ∝ 1 / 30-day realised vol; scaled so estimated portfolio vol (Σ wᵢσᵢ × 0.85) ≤ **40%/yr**; each coin ≤ **50%**.
3. **Target = average of the three sub-books.** Long only, no leverage, no shorting.
4. **Rebalance weekly** (every 7 days from the last rebalance; first was Tue 2026-10-20). Skip a coin's trade if the change is < 5% of equity, except full exits.
5. **Fills (paper):** buys at Kraken ask, sells at bid, 0% fee (Jonas has a 0%-fee Kraken plan, so only spread applies; since v2.1, 2026-10-21).

## Risk limits
- Max 50% in any coin; max gross 100%; cash floor implicitly from vol target.
- If paper equity falls 25% below its high-water mark, flag it to Jonas (no automatic override — the backtest had a −45% DD and that's expected behaviour, not a bug).
- No real orders, no private API keys, never a withdraw-capable key. Paper until the 2027-01-15 go/no-go.

## How news factors in
History (`research/events.txt`) says news is mostly *already in price* and "buy the news" doesn't work:
- Scheduled positive events sold off: BTC spot ETF approval (2024-01-10) BTC −8% in 7d; ETH ETF launch (2024-07-23) ETH −25% in 30d.
- Exogenous macro shocks mean-reverted: COVID (+43% BTC 30d later), SVB (+41%), yen unwind (+7%), tariffs (+23%).
- **Crypto-native failures kept falling:** LUNA (−23% BTC 90d), 3AC/Celsius (−10% 30d), FTX (−7% 30d), Bybit hack (−11% 30d).

So news may **only reduce** exposure, via `strategy/news_overrides.json`:
`{"SOL": {"max_weight": 0.0, "until": "2026-11-03", "why": "..."}}`. Valid triggers: hack/exploit of the coin's own chain or a top custodian/exchange, issuer/foundation insolvency, stablecoin depeg > 3%, regulatory action against the asset itself. Never add risk on bullish headlines; never override for macro scares.

## Daily procedure (Chewbacca)
1. Pull news: CoinDesk + Cointelegraph RSS, X `search_news` ("bitcoin crypto market"), web search for hacks/regulatory items on BTC/ETH/SOL/XRP.
2. Decide whether any override is warranted (rarely). Edit/expire `strategy/news_overrides.json`.
3. `python scripts/orca_daily.py --apply` — rebalances only when due (weekly) or if an override cut a held coin (`--force`). Always marks to market and appends `logs/metrics.jsonl`.
4. Record any rule/parameter change in `strategy/CHANGELOG.md` with the reason. Parameters change **at most monthly** and only with walk-forward evidence (re-run `research/walkforward.py`) — "update the strategy daily" means a daily review, not daily re-tuning.
5. Commit `state/ledger.json`, `demo/state/ledger.json`, `logs/metrics.jsonl` via a cloud agent PR so Vercel shows the new book. Report to Jonas.

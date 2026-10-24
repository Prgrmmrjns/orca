# Strategy changelog

## v2.1 — 2026-10-21
- Cost-model fix only, no signal/parameter change: paper fills now use **0% fee** (Jonas's Kraken plan has 0% trading fees; only bid/ask spread applies). Was 0.4% taker. The 2026-10-20 rebalance stays as booked ($3.74 fees). Research tables still use 0.5%/side, so they're conservative.
- 2026-10-21 news review: BTC ~$83K after US–Iran talks progress, US equities up on earnings, FINMA approved first Swiss retail BTC ETF, Solana Alpenglow upgrade live today, Ledger tampered-device supply-chain theft (~$90M, not a protocol/custodian hack), France stablecoin tax vote, Kazakhstan court curbs on Coinbase in AIFC → **no override**.

## v2.0 — 2026-10-20
- Dropped the September 2026 rule (top-1 coin by 14d return + headline early switch) and its baseline/ledger. Archived in `archive/september_rule/`.
- New: BTC/ETH/SOL/XRP trend-filtered, inverse-vol, 40% vol-target basket; SMA 50/100/200 ensemble; BTC regime gate; weekly rebalance with 5% band.
- Research: 2017–2026 daily data, walk-forward OOS 2019–2026 with 0.5%/side costs (see STRATEGY.md).
- News: risk-reduction-only overrides file. 2026-10-20 review: BTC dominance 60.7% (14-mo high), alts weak, AI/equity selloff, Brent >$110, ETF inflows $527M on 10-19, Drift exploit (Solana DeFi app, not SOL itself) → **no override**.
- New paper ledger: start 2026-10-20, €1,000 at EUR/USD 1.12231. First rebalance: BTC 37%, ETH 19%, SOL 14%, XRP 13%, cash 17%.

## 2026-10-24 review (v2.1, no change)
- Hold, mark to market. BTC ~$81.1K, still below SMA50 (above 100/200); ETH/SOL/XRP also below SMA50. Target cash 33%; trim happens at the scheduled 10-27 rebalance (no off-cycle trades).
- News: BTC spot ETFs -$636M outflows (10-23), Brent at six-month high, AI-driven equity weakness, Ethereum delays Glamsterdam testnet fork over gas security flaws (schedule, not exploit), Franklin Templeton puts $150M fund shares on XRP Ledger, Initia chain exploit (~$2.6M, not in universe), Senate probe into Tether payments → **no override**.

# Balance reward model — last month

Generated: 2026-10-01 10:27:48 CEST
Mode: `paper`. One reward model for every pair. Actions: keep, buy, sell.

- Pairs: AAVE/USD, ADA/USD, AERO/USD, ALGO/USD, APT/USD, ARB/USD, ASTER/USD, AVAX/USD, BCH/USD, BNB/USD, BONK/USD, BTC/USD, CRV/USD, DASH/USD, DOGE/USD, DOT/USD, ENA/USD, ETH/USD, FET/USD, FIL/USD, HBAR/USD, ICP/USD, INJ/USD, JUP/USD, LINK/USD, LTC/USD, MINA/USD, NEAR/USD, ONDO/USD, PAXG/USD, PENGU/USD, PEPE/USD, POL/USD, PUMP/USD, RAY/USD, RENDER/USD, SOL/USD, SUI/USD, TAO/USD, TRUMP/USD, TRX/USD, UNI/USD, WLD/USD, XLM/USD, XPL/USD, XRP/USD, ZEC/USD
- Bars: `1h`
- Train: 2024-10-11 00:00:00+00:00 → 2026-08-31 13:00:00+00:00 (16550 bars)
- Test: 2026-09-07 14:00:00+00:00 → 2026-09-30 14:00:00+00:00 (553 bars)
- Train rows: 178932 (reward is 5 days = 120 bars, and those bars end before the test window)
- Mean reward by position/action: {'pos0_buy': -0.00047, 'pos0_keep': 0.0, 'pos0_sell': 0.0, 'pos1_buy': 0.00013, 'pos1_keep': 0.00013, 'pos1_sell': -0.00059}
- Fee: 0.0100 per side. Sleeve: 0.95 / 8 slots.

Reward is the change in account balance 5 days after the action, after fees. The book takes the legal action with the higher predicted balance. Scanned 47 Kraken USD pairs (stables and fiat excluded). Traded 47 whose median hour does at least $20,000 and that have Binance hourly history from 11 Oct 2024. Holds at most 8 names. The learned book scores a 5-day balance change and may exit early on an 8% loss or a 12% drop from the peak. Rotation keeps the strongest 7-day uptrends, holds at least 3 days, same stops. News: 17663 headlines from CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine, CryptoSlate, The Block, and Google News. Each bar only sees articles published before that bar. The test is each pair's latest Kraken hourly bars. Start: €1,000 ($1,130 at 1.1305). Kraken+: commission waived on the first €10,000 ($11,305) of notional, then 1%. Spread 0.5% per side. Subscription $5.64. August, trained only on data before 1 Aug: 5-day model 1.2%, rotation 5.9%.

| Strategy | Return % | Max DD % | Trades | Fees | Final equity |
| --- | ---: | ---: | ---: | ---: | ---: |
| `reward_5d` | 12.64 | -11.86 | 78 | 54.56 | 1273.35 |
| `rotation` | -5.61 | -18.54 | 170 | 144.59 | 1067.10 |
| `sma_crossover` | -3.69 | -12.04 | 1000 | 227.31 | 1088.79 |
| `buy_hold` | 21.05 | -11.99 | 47 | 5.34 | 1368.46 |

## Actions taken

Watched 47 pairs. Opened or closed 28.

| Pair | keep | buy | sell |
| --- | ---: | ---: | ---: |
| `AERO/USD` | 552 | 1 | 0 |
| `ALGO/USD` | 550 | 2 | 1 |
| `ARB/USD` | 551 | 1 | 1 |
| `ASTER/USD` | 551 | 1 | 1 |
| `AVAX/USD` | 547 | 3 | 3 |
| `BCH/USD` | 551 | 1 | 1 |
| `BONK/USD` | 551 | 1 | 1 |
| `BTC/USD` | 551 | 1 | 1 |
| `CRV/USD` | 552 | 1 | 0 |
| `DOGE/USD` | 551 | 1 | 1 |
| `DOT/USD` | 551 | 1 | 1 |
| `ENA/USD` | 549 | 2 | 2 |
| `ETH/USD` | 550 | 2 | 1 |
| `HBAR/USD` | 549 | 2 | 2 |
| `ICP/USD` | 550 | 2 | 1 |
| `LTC/USD` | 549 | 2 | 2 |
| `MINA/USD` | 549 | 2 | 2 |
| `NEAR/USD` | 551 | 1 | 1 |
| `ONDO/USD` | 550 | 2 | 1 |
| `PAXG/USD` | 548 | 3 | 2 |
| `PENGU/USD` | 551 | 1 | 1 |
| `PUMP/USD` | 550 | 2 | 1 |
| `RAY/USD` | 549 | 2 | 2 |
| `SUI/USD` | 551 | 1 | 1 |
| `TRX/USD` | 551 | 1 | 1 |
| `XLM/USD` | 549 | 2 | 2 |
| `XPL/USD` | 551 | 1 | 1 |
| `XRP/USD` | 551 | 1 | 1 |

# bigmove_unified_v1 sanity backtest (2026-11-02 → 2026-11-12)

- Trades: **37**
- Net P&L: **€-33.38**
- End equity: **€966.62** (start €1000)
- Entries by session: `{'after': 3, 'open': 30, 'pre': 4}`

| Ticker | Side | Rule | Entry | Exit | Sess | Gap% | P&L € |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| AAPL | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | after→after | -4.5 | -4.31 |
| AMD | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.3 | +2.37 |
| AMZN | short | pr_pop_fade_short | 2026-11-02 | 2026-11-09 | open→pre | +4.2 | -3.29 |
| PLTR | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | after→pre | -4.5 | +3.16 |
| SOFI | short | pr_pop_fade_short | 2026-11-02 | 2026-11-09 | open→after | +4.0 | +1.50 |
| RIVN | short | pr_pop_fade_short | 2026-11-02 | 2026-11-09 | pre→pre | +4.3 | -6.20 |
| MARA | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.1 | +2.01 |
| COIN | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.1 | +2.13 |
| SMCI | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.0 | +7.60 |
| ARM | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.2 | +8.88 |
| MU | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.1 | +14.83 |
| INTC | short | offering_gapdown_short | 2026-11-02 | 2026-11-09 | open→pre | -4.4 | +8.84 |
| SNAP | short | pr_pop_fade_short | 2026-11-02 | 2026-11-09 | open→pre | +4.2 | +8.79 |
| DKNG | short | pr_pop_fade_short | 2026-11-02 | 2026-11-09 | open→pre | +4.7 | +14.56 |
| NVDA | short | pr_pop_fade_short | 2026-11-03 | 2026-11-10 | after→pre | +4.2 | +12.03 |
| CRM | short | offering_gapdown_short | 2026-11-03 | 2026-11-10 | open→pre | -4.5 | -1.13 |
| AVGO | short | pr_pop_fade_short | 2026-11-03 | 2026-11-10 | open→after | +4.1 | -0.51 |
| RBLX | short | pr_pop_fade_short | 2026-11-03 | 2026-11-10 | open→pre | +4.6 | -19.11 |
| MSFT | short | pr_pop_fade_short | 2026-11-04 | 2026-11-11 | pre→after | +4.2 | +2.50 |
| TSLA | short | offering_gapdown_short | 2026-11-04 | 2026-11-11 | pre→after | -4.9 | -6.53 |
| AMD | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | pre→after | +4.2 | -23.57 |
| META | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.5 | +0.67 |
| AMZN | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +5.4 | +0.67 |
| PLTR | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.6 | -3.79 |
| SOFI | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.2 | +6.29 |
| MARA | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.1 | -11.56 |
| HOOD | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.2 | +2.41 |
| SMCI | short | offering_gapdown_short | 2026-11-09 | 2026-11-12 | open→after | -4.2 | +0.65 |
| ARM | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +5.1 | -13.24 |
| MU | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.2 | -16.91 |
| INTC | short | pr_pop_fade_short | 2026-11-09 | 2026-11-12 | open→after | +4.1 | -8.39 |
| NVDA | short | offering_gapdown_short | 2026-11-10 | 2026-11-12 | open→after | -4.3 | +1.74 |
| SHOP | short | offering_gapdown_short | 2026-11-10 | 2026-11-12 | open→after | -4.3 | -3.18 |
| COIN | short | offering_gapdown_short | 2026-11-10 | 2026-11-12 | open→after | -4.1 | -2.18 |
| RIVN | short | offering_gapdown_short | 2026-11-12 | 2026-11-12 | open→after | -4.8 | -1.01 |
| SNAP | short | offering_gapdown_short | 2026-11-12 | 2026-11-12 | open→after | -4.5 | -5.41 |
| DKNG | short | offering_gapdown_short | 2026-11-12 | 2026-11-12 | open→after | -4.3 | -4.69 |

Sanity backtest with proxy categories (no full news archive). Extended-hours entries via yfinance prepost=True 5m when available. Cost 0.5%/fill + 0.5% outside RTH. No earnings/guidance chase.

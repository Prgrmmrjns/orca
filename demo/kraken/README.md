# Orca Kraken agent (paper)

Live: https://orca-paper.vercel.app/kraken. **Paper only:** no code path sends an order to Kraken.

## How it runs
- **Scheduler:** Supabase `pg_cron` + `pg_net` (project `orca-agent`) call `GET /api/kraken/cron/tick` every minute and
  `/api/kraken/cron/daily` at 00:07 UTC, with `Authorization: Bearer $CRON_SECRET`. The tick also runs the daily job if it was missed.
- **Storage:** Supabase Postgres via PostgREST. Row-level security only lets requests with the `x-orca-key` header through
  (`ORCA_DB_KEY`). Vercel env: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `ORCA_DB_KEY`, `CRON_SECRET`, `ANTHROPIC_API_KEY`.
- **History seed:** `python scripts/seed_kraken_db.py` (prices 2017+, funding, alt data from `research/kagent_data`).

## What it does
| Job | Cadence | What |
|---|---|---|
| scrape | every minute (fast) / 5 min (rest) | 25 sources: Binance CMS listings/delistings, Telegram (FinancialJuice, WatcherGuru, unfolded, db_news, Binance, Wu, Whale Alert), Fed, SEC, BLS, PR Newswire, Business Wire, CoinDesk, Cointelegraph, The Block, Decrypt, CryptoSlate, Bitcoin Magazine, Kraken blog/status, MarketWatch, Investing, Seeking Alpha, Benzinga |
| triage | every minute | Haiku (`claude-haiku-5-5`) rates each new item: category, impact 1-5, assets, direction |
| events book | every minute | Binance listing → long the Kraken perp 4 h; delisting → short 24 h; impact ≥ 4 coin news → Haiku trader decides; stops and timed exits |
| mark | every minute | marks all positions at Kraken mark prices; equity snapshot every 10 min |
| data | daily | Kraken daily closes and funding, Deribit funding, Coin Metrics, Fear & Greed, stablecoins, FRED |
| funding | daily | charges Kraken's realised funding on open positions |
| rebalance | Mondays | trend+alt, low-risk L/S, objective-max policy (code ported from `kagent/`) |
| learn | Mondays | policy refit (blended 50/50 with last week), capital re-allocation by each book's own objective |
| review | daily | Haiku self-review: daily report, lessons (fed back into the triage and trader prompts), bounded knob changes |

Risk limits (`config.py`, the review can't change them): drawdown brake 15% / 25% on total equity, book kill switch at -30%,
event size ≤ 25% of the events book, event stop ≤ 6%, hold ≤ 48 h, allocation within 0.5x-1.6x of the prior.
A losing event type (n ≥ 8, mean < 0, t < -1) is switched off automatically.
Haiku spend caps: $30 total, $0.60/day, $20 for scraping (env `ORCA_LLM_CAP_*`).

# newsperp — news collector + paper perp book

**PAPER ONLY.** Public data, simulated fills on Kraken perpetual futures, no exchange keys, no orders.
Started 2026-11-25 with **€1,000**. State in `state/newsperp/`; rules in `config/newsperp/book.json`.

## What it does
1. **Polls** 23 public sources (`config/newsperp/sources.json`):
   - company newsrooms and IR feeds: NVIDIA, Apple, Amazon, Alphabet, Meta, Coinbase, Circle, AMD, Microsoft
   - SEC 8-K filings for 12 companies (round-robin, one request every 12 s)
   - PR Newswire, White House, a Truth Social mirror, Fed, BLS, BEA, Federal Register (BIS export rules)
   - CoinDesk, MarketWatch, Benzinga, Seeking Alpha
   Conditional GETs (ETag / If-Modified-Since) make an unchanged poll nearly free; NVIDIA's feed ignores them,
   so it gets a HEAD check every 5 s instead.
2. **Logs** every new item with our own first-seen time, the publisher's timestamp, the lag between them, and
   live mid prices of all 14 perps (`state/newsperp/events/YYYY-MM-DD.jsonl`). The first poll of a source only
   records existing items; nothing old is ever traded.
3. **Classifies** new items. With `ANTHROPIC_API_KEY` set: Claude (`claude-opus-5-5`, effort `low`, structured
   output, server-side refusal fallback) returns event type, and per symbol direction, confidence, expected move
   and horizon. Without a key: a small keyword rule set (CEO resignation/reinstatement, guidance raise/cut,
   offerings, investigations, take-overs).
4. **Paper-trades** Kraken perps: NVDA, TSLA, AAPL, AMZN, GOOGL, META, MSTR, COIN, HOOD, CRCL, SPY, QQQ, BTC, ETH.

## Book rules v0 (frozen 2026-11-25)
| | |
|---|---|
| Entry | confidence ≥ 0.65, expected move ≥ 1%, item seen within 15 min of publication |
| Fill | fresh Kraken quote *after* classification: buy at ask, sell at bid, + 0.10% slippage, + 0.05% taker fee |
| Size | risk 1.5% of equity to the stop; max 60% of equity per position, 3× gross, 4 positions |
| Stop | 3% (SPY 1.5%, QQQ 2%, BTC 2.5%), checked every 10 s |
| Exit | the classifier's horizon (15 min – 24 h, default 2 h), stop, or a reversing signal |
| Funding | Kraken hourly funding accrued continuously |
| Kill switch | −4% on the UTC day: flatten and no new entries until tomorrow |

## Evidence so far (`python -m newsperp study --sec`, report in `reports/newsperp/sec_backfill.json`)
130 SEC 8-Ks on the 10 stock perps, Feb–Nov 2026, 1-minute Kraken perp candles:
- Earnings 8-Ks (29): median move 2.8% from filing to +2 h; 2.5% still left when entering 5 min late.
  Prices had already moved 1.3% on average before the filing (company feeds publish first).
- The direction of the first 5 minutes matched the next 2 hours only 63% of the time (earnings), 53% overall,
  so the edge must come from reading the release, not chasing the first move.
- No control group yet: ordinary 2-hour volatility is part of these numbers.
- Two case studies (NVIDIA CEO resignation 2026-11-12, reinstatement 2026-11-23) are in the session notes:
  the reinstatement perp move was +8% in the first minute, +24% at 2 h, +7.6% two days later.

## Backtest (`python -m newsperp backtest`, report in `reports/newsperp/backtest.json`)
Feb 7 – Nov 25 2026, 214 official events (SEC 8-Ks of the 10 perp stocks + IR press releases, merged within 20 min),
1-minute Kraken perp bars, 20 bp per side (half-spread + slippage + fee), book sizing and stops as above,
entry 45 s after publication unless stated.

| Signal | Trades | Win rate | Avg / trade | Total | Max DD |
|---|---|---|---|---|---|
| follow (price-confirmed, ≥1% move by entry) | 16 | 75% | +1.70% | +14.2% | −1.6% |
| follow at 40 bp per side | 16 | 75% | +1.35% | +11.1% | −1.7% |
| follow, entry after 10 s / 2 min / 5 min | 14 / 20 / 25 | 71 / 55 / 52% | | +13.9 / +4.6 / +1.5% | |
| fade (opposite of follow) | 16 | 19% | −1.69% | −12.9% | −13.2% |
| random direction on every event (20 seeds) | 167 | 29% | −0.26% | −19.5% | −26.7% |
| keyword rules (live filters) | 1 | – | – | – | – |
| Claude classifier | not run: no API key in this environment | | | | |

Read with care:
- 16 trades over ten months is a small sample; three trades (Alphabet Q3, a Circle charter, NVIDIA's CEO
  reinstatement) make most of the profit.
- The 1% trigger and 2 h hold were picked after looking at the same period, and the 8% bad-print cap was added
  after one Amazon bar printed −10.7% in a minute (without the cap: 17 trades, +12.4%).
- The keyword rules' single trade is the NVIDIA reinstatement, and the "reinstated" rule was written after that
  event, so it proves nothing.
- The edge fades with delay (+14% at 45 s, +1.5% at 5 min): polling speed matters, which is why SEC filings are
  now polled one company every 2 s (each company every ~24 s).
- No real order book: thin perps can fill much worse at the moment news breaks.

## Known gaps
- Tesla and Robinhood IR sites return 403 to this container's IP; they are covered via SEC 8-Ks.
  The GitHub runner IP may or may not be blocked; see `state/newsperp/health.json`.
- Kraken stock perps other than NVDA are thin ($0.1–2.6M/day); real fills in the first seconds would slip more
  than the 0.10% modelled.
- Without analyst consensus the classifier cannot judge earnings against expectations; it is told to keep
  confidence low unless guidance changes clearly.
- X is not polled (scraping breaks its terms; the paid API costs about $0.005 per post).
- Kalshi/Polymarket are read-only research sources at most: trading them from Germany is not permitted.

## Commands
```bash
python -m newsperp probe                       # poll every source once
python -m newsperp run --minutes 110           # collector + book (add --commit-every 15 on a server)
python -m newsperp status                      # book summary
python -m newsperp study                       # forward returns after our first-seen times
python -m newsperp study --sec                 # 8-K backfill
python -m pytest newsperp/test_newsperp.py -q
```
Settings for the GitHub Action: secret `ANTHROPIC_API_KEY` (optional, enables the Claude classifier;
roughly $0.005–0.01 per classified item) and variable `ORCA_CONTACT_UA` = `"<name> <email>"`
(SEC and BLS reject clients without a contact address).

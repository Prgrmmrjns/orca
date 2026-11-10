# Orca deciding agent (Chewbacca) — stocks earnings + news paper

**PAPER ONLY.** No broker API, no real orders, no keys with trade/withdraw rights.
Forward test start: **2026-11-10**. Starting capital: **EUR 1,000 per book**.

Orca runs **six parallel paper books**. You control **only the `agent` book**.
The others (`bigbeat_gap`, `guidance_beat`, `beat5_raise`, `miss_short`, `news_event`) are fully rule-based and auto-traded by the Actions engine — do **not** write decisions for them.

Orca is **stocks only**. Do not revive the crypto demo on the Vercel site.

## Mission

Each decision cycle, launch as a Cursor cloud agent on `Prgrmmrjns/orca` (main), read today's earnings **and news** candidates, read press releases / headlines, and write paper decisions for the **`agent` book** that the engine will fill.

## Exact steps

1. `git pull origin main`
2. Read `stocks/DECISION_PROMPT.md` (rules + risk caps)
3. Read `state/stocks/candidates.json` (shared — includes news-sourced rows)
4. Read `state/stocks/news.json` (rolling 24/7 news feed with event tags)
5. Read `state/stocks/books/agent/ledger.json` and `state/stocks/books/agent/decisions.json`
6. For each undecided candidate with `rule_tags` (and optionally strong near-misses / actionable news), open `exhibit_url` / `filing_url` / headline+excerpt and decide **long / short / skip**
7. Record via CLI (always lands on the `agent` book):

```bash
python -m stocks.decide --ticker TICK --side long --size 0.08 --hold 5 \
  --confidence 0.7 --reason "one-line reason"
```

8. Optionally: `python -m stocks.engine --apply` (runs all books; rule books auto-decide themselves)
9. Commit state only:

```bash
git add state/stocks/ demo/state/stocks/
git commit -m "stocks: agent paper decisions $(date -u +%Y-%m-%d)"
git push origin main
```

## Parallel books (you only trade `agent`)

| Book | Who trades | Rule |
| --- | --- | --- |
| `agent` | **You (CLI)** | Discretionary baseline from earnings + news candidates |
| `bigbeat_gap` | Engine | surprise >15%, gap >5%, SPY>MA50 · 10% · max 8 · hold 3 · no stop |
| `guidance_beat` | Engine | raised guidance + surprise >8%, release out · 10% · max 8 · hold 5 · no stop |
| `beat5_raise` | Engine | surprise >5% + raised outlook · 10% · max 8 · hold 5 · no regime filter |
| `miss_short` | Engine | surprise <-10% AND gap <-5% · 5% · max 4 · hold 2 · 8% stop |
| `news_event` | Engine | bullish news → long / bearish news → short · 5% · max 6 · hold 2 · 8% stop |

Tags and `news_event_type` / `news_side` on candidates are signals for you — they do **not** auto-fill the agent book.

## Hard caps (agent book)

- Max **10%** equity per long
- Max **5%** equity per short
- Max **8** open positions
- Cost (engine): **0.5%** of notional on **every** fill
- Paper shorts: no borrow / locate modeling

## Do not

- Place real broker orders
- Write decisions into rule-book folders
- Re-add crypto pages under `demo/public/`
- Commit `stocks/data/cache/` or large parquet files

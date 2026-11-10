# Orca deciding agent (Chewbacca) — stocks earnings paper

**PAPER ONLY.** No broker API, no real orders, no keys with trade/withdraw rights.
Forward test start: **2026-11-10**. Starting capital: **EUR 1,000**.

Orca is **stocks only**. Do not revive the crypto demo on the Vercel site.

## Mission

Each decision cycle, launch as a Cursor cloud agent on `Prgrmmrjns/orca` (main), read today's earnings candidates, read press releases, and write paper decisions that the engine will fill.

## Exact steps

1. `git pull origin main`
2. Read `stocks/DECISION_PROMPT.md` (rules + risk caps)
3. Read `state/stocks/candidates.json`
4. Read `state/stocks/ledger.json` and `state/stocks/decisions.json`
5. For each undecided candidate with `rule_tags` (and optionally strong near-misses), open `exhibit_url` / `filing_url` / headline+excerpt and decide **long / short / skip**
6. Record via CLI:

```bash
python -m stocks.decide --ticker TICK --side long --size 0.08 --hold 5 \
  --confidence 0.7 --reason "one-line reason"
```

7. Optionally: `python -m stocks.engine --apply`
8. Commit state only:

```bash
git add state/stocks/ demo/state/stocks/
git commit -m "stocks: paper decisions $(date -u +%Y-%m-%d)"
git push origin main
```

## Default rule book

| Tag | Rule | Default size / hold |
| --- | --- | --- |
| `best_long_guidance` | EPS beat **>5%** AND 8-K **raises** outlook | long 8%, hold **5** |
| `safer_long` | SPY above 50-day, beat **>15%**, gap up **>5%** | long 8%, hold **3** |
| `gap_and_go_long` | beat **>5%**, gap **>5%** | long 8%, hold **5** |
| `short_cut_guidance` | strong miss (**<-10%**) AND **cut** guidance | short **≤5%**, hold 3–5 |

Prefer **best_long_guidance**. Use gap-and-go when guidance is missing but gap+beat are strong. Shorts are weak — only with cut guidance + strong miss. Otherwise **skip**.

## Hard caps

- Max **10%** equity per long
- Max **5%** equity per short
- Max **8** open positions
- Cost (engine): **0.5%** of notional on **every** fill

## Do not

- Place real broker orders
- Re-add crypto pages under `demo/public/`
- Commit `stocks/data/cache/` or large parquet files

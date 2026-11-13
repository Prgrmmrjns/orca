# Orca — multi-book US earnings paper forward test

**Paper-only** side-by-side forward test of **14** strategies (12 since **2026-11-10**, plus `bigmove_unified_v1` and `bigmove_eu_v1` from **2026-11-13**; EUR **1,000 each**). Live: [orca-paper.vercel.app](https://orca-paper.vercel.app).

> **Safety:** `mode: paper` only. No broker API, no real orders, no keys with trade/withdraw rights. `ORCA_MODE` must stay `paper`.

## Books

| Book | Trader | Rule |
| --- | --- | --- |
| `agent` | Cursor agent (`stocks.decide`) | Discretionary baseline (earnings + news) |
| `bigbeat_gap` | Engine | surprise >15%, gap >5%, SPY>MA50 · 10% · max 8 · hold 3 · no stop |
| `guidance_beat` | Engine | raised guidance + surprise >8%, release out · 10% · max 8 · hold 5 |
| `beat5_raise` | Engine | surprise >5% + raised outlook · 10% · max 8 · hold 5 |
| `miss_short` | Engine | surprise <-10% AND gap <-5% · 5% · max 4 · hold 2 · 8% stop |
| `news_event` | Engine | config-driven (`config/strategies/news_event.yaml`) · defaults: bullish/bearish · 5% · max 6 · hold 2 · 8% stop |
| `guidance_raise_tuned` / `dilution_shorts_tuned` / `quality_longs_tuned` / `guidance_raise_score` / `gap_filtered_guidance` / `wide_capacity` | Engine | tuned news configs from box backtest · each €1,000 paper · next_open · mostly no stop |
| `bigmove_unified_v1` | Engine + bigmove loop | Fade offering / PR-pop / M&A gaps + selective gap-down fades across **pre / regular / after** · 5% · max 20 · hold 5 · 0.5%/fill **+ 0.5% ext slippage** · mega-cap shocks **alert only** · no earnings/guidance chase · start 2026-11-13 |
| `bigmove_eu_v1` | Engine + bigmove-eu loop | EU liquid large/mid caps: fade **≥10% gap-downs** (earnings / other-PR / no-news) on **primary hours 09:00–17:30 Berlin** · 5% · max 20 · hold 5 · 0.5%/fill (+0.5% outside primary) · STOXX/DAX regime · alert-only mega-caps · no gap-up shorts (EU event study) · start 2026-11-13 |

Shared candidates + `state/stocks/news.json`; independent ledgers. Cost **0.5%**/fill (bigmove adds configurable **0.5%** outside primary/RTH); EUR sizing via **EURUSD=X** (EU quotes converted when needed). Paper shorts have **no borrow model**.

## Commands

```bash
python -m stocks scan                  # earnings calendar candidates
python -m stocks news                  # 24/7 news → news.json + candidate merge
python -m stocks bigmove --once        # EDGAR 8-K/424B/SC TO + move triggers (bigmove book)
python -m stocks bigmove --loop        # ~22 min poll every ~2.5 min + engine ticks
python -m stocks bigmove-eu --once     # EQS/DGAP aggregators + EU movers (bigmove_eu book)
python -m stocks bigmove-eu --loop     # ~22 min poll every ~2.5 min + engine ticks (Berlin primary)
python -m stocks.decide --ticker TICK --side long --size 0.08 --hold 5 --reason "..."  # agent book only
python -m stocks engine --apply        # all books: auto-decide rules + fill/exit/mark
python -m stocks engine --apply --book bigmove_unified_v1
python -m stocks engine --apply --book bigmove_eu_v1
python -m stocks init                  # ensure all books exist (€1,000; does not wipe existing)
python -m stocks replay --live         # rule dry-run over sample + live candidates
```

See `AGENT.md` and `stocks/DECISION_PROMPT.md` for the deciding-agent loop (**agent book only**).

## Listeners (GitHub Actions)

| Workflow | When | Hours covered |
| --- | --- | --- |
| `stocks-news` | ~every 15 min, **all days** (`5,20,35,50 * * * *`) | 24/7 news ingest |
| `stocks-scanner` | ~every 30 min weekdays UTC (`7,37 * * * 1-5`) | earnings calendar |
| `stocks-engine` | every 15 min during US RTH window + after close | regular session fills/marks for all books |
| `stocks-bigmove` | every ~30 min **08:00–00:00 UTC weekdays**, each run loops **~22 min** polling every **~2.5 min** | **US pre 04:00–09:30 ET**, **regular 09:30–16:00 ET**, **after 16:00–20:00 ET** (weekdays). Concurrency group `stocks-bigmove`; rebase/retry on push. |
| `stocks-bigmove-eu` | every ~30 min **08:00–16:59 UTC weekdays**, each run loops **~22 min** polling every **~2.5 min** | **EU primary ≈09:00–17:30 Berlin** (no free Tradegate/L&S). Skips EU holidays. Concurrency group `stocks-bigmove-eu`; rebase/retry on push. |
| `stocks-decide` | `workflow_dispatch` | manual agent helper |

### Bigmove hours & cadence

- **Intended coverage:** weekdays **04:00–20:00 America/New_York** (pre + regular + after).
- **UTC schedules:** crons at `:05` and `:35` for hours `8–23` UTC Mon–Fri, plus early `0` UTC Tue–Sat to catch the last after-hours slot under EST/EDT.
- **Intra-run loop:** each workflow invocation polls EDGAR + movers for ~22 minutes every ~150s and runs the `bigmove_unified_v1` engine each cycle, so coverage does not depend on cron firing exactly on the minute.
- **Overlap safety:** `concurrency.group: stocks-bigmove` with `cancel-in-progress: false`; commits rebase/retry to avoid ledger write conflicts.
- **Actions minutes:** ~16 scheduled fires/weekday × ~25 min ≈ **~6–7 runner-hours/weekday** for this workflow alone — dense but within a typical public-repo free budget when combined with the lighter news/scanner/engine jobs.

### If schedules never run (Jonas)

Cloud agents cannot flip repo Actions settings or `workflow_dispatch`. Do this once:

1. Open [Settings → Actions → General](https://github.com/Prgrmmrjns/orca/settings/actions)
2. **Actions permissions** → *Allow all actions and reusable workflows* → Save
3. **Workflow permissions** → *Read and write permissions* (required so the bot can commit state) → Save
4. Open [Actions](https://github.com/Prgrmmrjns/orca/actions) → **stocks-scanner** → **Run workflow** (branch `main`)
5. Same for **stocks-engine** / **stocks-bigmove**

After one successful run, weekday schedules should keep firing. Pushing the workflow YAML to `main` also triggers a one-shot run via the `paths` filter.

**Fallback if `schedule` never fires:** workflows also listen for `repository_dispatch` types `orca-stocks-tick` / `orca-stocks-news` / `orca-stocks-engine` / `orca-stocks-bigmove`. Point an external cron (e.g. cron-job.org) at:

```bash
curl -X POST -H "Authorization: Bearer $GH_PAT" -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/Prgrmmrjns/orca/dispatches \
  -d '{"event_type":"orca-stocks-tick"}'
```

(`GH_PAT` needs `actions:write`. Cloud agent tokens cannot dispatch.)

## Layout

```
demo/                      # Vercel root — multi-book comparison dashboard
stocks/                    # scanner, engine, decide CLI, rule books, bigmove
config/strategies/         # frozen YAML for config-driven books
state/stocks/candidates.json
state/stocks/news.json
state/stocks/alerts.json   # mega-cap shock alerts (alert_only)
state/stocks/books/<id>/   # ledger.json + decisions.json per book
```

## Disclaimer

Paper results are not predictive. Educational software only.

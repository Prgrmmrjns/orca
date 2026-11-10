# Orca — multi-book US earnings paper forward test

**Paper-only** side-by-side forward test of **five** earnings strategies since **2026-11-10** (EUR **1,000 each**). Live: [orca-paper.vercel.app](https://orca-paper.vercel.app).

> **Safety:** `mode: paper` only. No broker API, no real orders, no keys with trade/withdraw rights. `ORCA_MODE` must stay `paper`.

## Books

| Book | Trader | Rule |
| --- | --- | --- |
| `agent` | Cursor agent (`stocks.decide`) | Discretionary baseline |
| `bigbeat_gap` | Engine | surprise >15%, gap >5%, SPY>MA50 · 10% · max 8 · hold 3 · no stop |
| `guidance_beat` | Engine | raised guidance + surprise >8%, release out · 10% · max 8 · hold 5 |
| `beat5_raise` | Engine | surprise >5% + raised outlook · 10% · max 8 · hold 5 |
| `miss_short` | Engine | surprise <-10% AND gap <-5% · 5% · max 4 · hold 2 · 8% stop |

Shared candidates; independent ledgers. Cost **0.5%**/fill; EUR sizing via **EURUSD=X**.

## Commands

```bash
python -m stocks scan                  # refresh shared candidates
python -m stocks.decide --ticker TICK --side long --size 0.08 --hold 5 --reason "..."  # agent book only
python -m stocks engine --apply        # all books: auto-decide rules + fill/exit/mark
python -m stocks init                  # reset all five books to €1,000
python -m stocks replay --live         # rule dry-run over sample + live candidates
```

See `AGENT.md` and `stocks/DECISION_PROMPT.md` for the deciding-agent loop (**agent book only**).

## Listeners (GitHub Actions)

| Workflow | When |
| --- | --- |
| `stocks-scanner` | ~every 30 min weekdays UTC (`7,37 * * * 1-5`) |
| `stocks-engine` | ~US open / midday / after close |
| `stocks-decide` | `workflow_dispatch` |

### If schedules never run (Jonas)

Cloud agents cannot flip repo Actions settings or `workflow_dispatch`. Do this once:

1. Open [Settings → Actions → General](https://github.com/Prgrmmrjns/orca/settings/actions)
2. **Actions permissions** → *Allow all actions and reusable workflows* → Save
3. **Workflow permissions** → *Read and write permissions* (required so the bot can commit state) → Save
4. Open [Actions](https://github.com/Prgrmmrjns/orca/actions) → **stocks-scanner** → **Run workflow** (branch `main`)
5. Same for **stocks-engine**

After one successful run, weekday schedules should keep firing. Pushing the workflow YAML to `main` also triggers a one-shot run via the `paths` filter.

## Layout

```
demo/                      # Vercel root — multi-book comparison dashboard
stocks/                    # scanner, engine, decide CLI, rule books
state/stocks/candidates.json
state/stocks/books/<id>/   # ledger.json + decisions.json per book
```

## Disclaimer

Paper results are not predictive. Educational software only.

# Orca — US stock earnings paper agent

**Paper-only** forward test of an agentic US stock **earnings** strategy since **2026-11-10** (EUR 1,000). Live: [orca-paper.vercel.app](https://orca-paper.vercel.app).

> **Safety:** `mode: paper` only. No broker API, no real orders, no keys with trade/withdraw rights. `ORCA_MODE` must stay `paper`.

## What it does

- **Scanner** (`stocks/`): Nasdaq earnings calendar + yfinance EPS/gap + SEC EDGAR 8-K Item 2.02 guidance keywords → `state/stocks/candidates.json`
- **Decider** (Chewbacca / Cursor agent): reads candidates + press releases, writes decisions via `python -m stocks.decide` (risk caps enforced)
- **Engine**: fills at next regular open (or session price if open), time exits, optional stop/target, marks to market; **0.5%** cost on every fill
- **Dashboard** (`demo/`): equity vs SPY (EUR), open positions, today's candidates/decisions, closed trades

## Commands

```bash
python -m stocks scan                  # refresh candidates
python -m stocks.decide --ticker TICK --side long --size 0.08 --hold 5 --reason "..."
python -m stocks engine --apply        # fills / exits / marks
python -m stocks init                  # reset paper ledger (careful)
```

See `AGENT.md` and `stocks/DECISION_PROMPT.md` for the deciding-agent loop.

## Risk caps

| Cap | Value |
| --- | --- |
| Start | EUR 1,000 on 2026-11-10 |
| Long size | ≤ 10% of equity |
| Short size | ≤ 5% of equity |
| Max open | 8 |
| Cost | 0.5% of notional per fill (entry and exit) |

## Default rules (from backtest)

- **Best:** EPS beat >5% AND 8-K raises outlook → long, hold 5
- **Safer:** SPY above 50-day, beat >15%, gap >5% → long, hold 3
- **Gap-and-go (active alternative):** beat >5%, gap >5% → long, hold 5
- **Shorts (weak):** strong miss + cut guidance only, small size

## Listeners (GitHub Actions)

| Workflow | When |
| --- | --- |
| `stocks-scanner` | every 15 min, weekdays |
| `stocks-engine` | ~US open / midday / after close |
| `stocks-decide` | `workflow_dispatch` |

## Layout

```
demo/                 # Vercel root — stocks dashboard only
stocks/               # scanner, engine, decide CLI
state/stocks/         # candidates, decisions, ledger (mirrored to demo/state/stocks/)
archive/crypto-demo/  # old Kraken demo pages (not served)
orca/, scripts/       # legacy crypto research code (not the live product)
```

## Disclaimer

Paper results are not predictive. Educational software only.

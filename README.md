# Orca — paper earnings agent (US stocks)

**Active product:** paper forward test of an agentic US stock **earnings** strategy since **2026-11-10** (EUR 1,000). Live at [orca-paper.vercel.app](https://orca-paper.vercel.app).

> **Safety:** `mode: paper` only. No broker API, no real orders, no keys with trade/withdraw rights. `ORCA_MODE` must stay `paper`.

Crypto Kraken paper code remains in the repo (`orca/`, `scripts/orca_daily.py`, `state/ledger.json`) but the daily crypto strategy is **paused** and no longer the homepage ([/crypto.html](https://orca-paper.vercel.app/crypto.html)).

## Stocks paper book (demo)

- **Package:** `stocks/` — scanner (Nasdaq calendar + yfinance EPS + EDGAR 8-K Item 2.02), engine (fills/exits/marks), `python -m stocks.decide` CLI
- **State:** `state/stocks/{candidates,decisions,ledger}.json` (mirrored to `demo/state/stocks/` for Vercel)
- **Costs:** 0.5% of notional on every fill; max 10% equity / long, 5% / short, max 8 open
- **Listeners:** GitHub Actions `stocks-scanner` (every 15m weekdays), `stocks-engine` (US open / midday / after close), `stocks-decide` (workflow_dispatch)
- **Agent:** see `AGENT.md` and `stocks/DECISION_PROMPT.md` (Chewbacca)

## Crypto paper book (paused)

The Vercel `/crypto.html` page still marks the crypto ledger with public Kraken prices. No private/order APIs.

- **Start:** 2026-10-13, capital €1000.
- **Daily job (paused):** `python scripts/orca_daily.py --apply`
- **APIs:** `/api/today` (crypto), `/api/stocks` (earnings homepage)

---

## Research summary (Sep 2026)

### Markets (stocks vs crypto)

| Product | What it is | Orca focus |
| --- | --- | --- |
| **Spot crypto** | 1,400+ pairs (BTC, ETH, SOL, XRP, … vs USD/EUR/USDT/USDC). You own the asset. | **Yes — this project** |
| Spot margin | Leverage up to ~20× on eligible pairs; borrow fees + liquidation risk | Out of scope |
| Futures / perps | Derivatives; separate fee schedule & wallet | Out of scope |
| Stocks / ETFs | ~11k US stocks/ETFs (US clients); **not** the same as spot crypto pairs | Not traded by Orca |
| xStocks | Tokenized stock exposure on Kraken (geo-limited) | Out of scope |

**Clarification:** Traditional equities on Kraken (where available) are a separate product from crypto spot. Orca only targets **crypto spot pairs** such as `BTC/USD` and `BTC/EUR`.

Sources: [Kraken markets](https://support.kraken.com/articles/kraken-markets), [Kraken Pro spot](https://pro.kraken.com/spot), [fee schedule](https://www.kraken.com/features/fee-schedule).

### Spot trading fees (Kraken Pro)

Tier is the **best of** 30-day spot volume **or** Assets on Platform (AoP). Base (Tier 1) rates:

| Tier | Spot 30d vol / AoP | Maker | Taker |
| --- | --- | --- | --- |
| 1 | $0+ | **0.40%** | **0.80%** |
| 2 | $2.5K+ | 0.30% | 0.60% |
| 3 | $10K+ / AoP 20k | 0.22% | 0.38% |
| … | … | … | … |
| 12 | $10M+ / AoP 10m | 0.00% | 0.10% |
| Pro 5 | $500M+ / AoP 100m | 0.00% | 0.05% |

- Fees are per fill on quote (or selectable fee currency). Instant Buy volume does **not** count toward Pro tiers.
- Stablecoin/FX books use a separate flatter schedule; margin adds opening + 4h rollover fees on top of spot fees.
- Paper config defaults to a conservative ~0.26% blended fee; switch to `0.008` (0.80% taker) for stricter Tier-1 realism.

Source: [Kraken fee schedule](https://www.kraken.com/features/fee-schedule) (cross-platform tiers).

### Promising liquid pairs (beginner)

High 24h volume / tight spreads on Kraken (order of magnitude; varies daily):

1. **BTC/USD** — deepest book (~$100M+/day typical)
2. **ETH/USD**, **SOL/USD**, **XRP/USD**
3. **BTC/EUR**, **ETH/EUR** — strong for EUR-based accounts (Jonas / Europe)
4. Avoid thin alts for a first strategy (wide spreads kill SMA/RSI edges)

Default config: `BTC/USD` daily candles. Override with `--pair BTC/EUR`.

### How open-source bots are structured

| Stack | Pattern | Notes |
| --- | --- | --- |
| **ccxt** | Thin exchange I/O library | Used by Orca for public OHLCV |
| **Freqtrade** | Config + strategy class + dry-run + Docker + Telegram/UI | Production-grade; dry-run = paper |
| **Jesse** | Research/backtest first; live/paper often licensed plugin | Strong research API |
| **Custom** | `data → strategy → portfolio → runner` | Orca’s layout |

Common modules: market data, signal generation, portfolio/risk, execution (paper vs live), persistence, logging, kill switch / dry-run flag.

### Deploy & assistant supervision (paper first)

1. **Paper / backtest locally** (this repo) — no keys.
2. **Docker** — pin deps, restart policy, mount `logs/` + `config/`.
3. **VPS** — small Linux box; run under systemd or Docker Compose; never commit `.env`.
4. **Logging** — structured logs + fill CSV + equity curve (Orca writes `logs/`).
5. **Kill switches** — `ORCA_MODE=paper` hard-fail on non-paper; process stop; future: max daily loss, max position, Telegram pause (Freqtrade-style).
6. **Assistant supervision** — read logs / last_backtest.json; Query-only API later for balances & open orders (no Trade permission).

### Historical + live data (Kraken)

| Source | Use | Limit |
| --- | --- | --- |
| REST `GET /0/public/OHLC` (via **ccxt** `fetch_ohlcv`) | Recent candles | **Max ~720** candles; older not available via OHLC |
| REST `GET /0/public/Trades` | Full history | Paginate with `since`; aggregate to OHLC yourself |
| WebSocket `wss://ws.kraken.com` (v2 `ohlc`) | Live candles | Current only, not historical replay |
| Public datasets / CSV cache | Offline backtests | Orca caches under `data/` |

No API key needed for public market data.

---

## Project layout

```
orca/
├── README.md
├── requirements.txt
├── .env.example          # document keys; do not commit .env
├── .gitignore
├── config/
│   └── config.example.yaml
├── orca/
│   ├── config.py         # load YAML + .env; reject non-paper mode
│   ├── data.py           # ccxt Kraken OHLCV
│   ├── strategy.py       # SMA, RSI, Bollinger, MACD, dual-MA+volume
│   ├── news.py           # RSS news fetch/cache + daily features (no look-ahead)
│   ├── news_strategy.py  # news-gated wrappers around rule strategies
│   ├── metrics.py        # return, MDD, Sharpe~, win rate, vs B&H
│   ├── portfolio.py      # paper fills + fees
│   ├── backtest.py       # glue
│   ├── multi_pair_trajectory.py  # joint BTC/SOL/XRP trajectory (sklearn)
│   └── logging_setup.py
├── scripts/
│   ├── run_backtest.py
│   ├── compare_strategies.py
│   └── compare_news_strategies.py
├── data/                 # cached OHLCV CSVs + data/news/ (gitignored)
└── logs/                 # orca.log, fills, equity, compare reports (gitignored)
```

---

## Multi-pair trajectory (paper)

Joint short-horizon return trajectories for `BTC/USD`, `SOL/USD`, `XRP/USD`
(shared lags + cross-asset features → `MultiOutputRegressor(Ridge)`), walk-forward,
long/flat equal-split and relative allocation vs buy-and-hold. **No live trades;
no TabPFN** in this path; fees ~0.26%.

```bash
cd /workspace/orca && source .venv/bin/activate
python scripts/run_multi_pair_trajectory.py --cache   # or --refresh
# writes logs/multi_pair_trajectory.md + .json
```

---

## Quick start (paper backtest)

```bash
cd /workspace/orca
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Uses config.example.yaml if config.yaml is missing
python scripts/run_backtest.py

# EUR pair + RSI
python scripts/run_backtest.py --pair BTC/EUR --strategy rsi
```

Optional: `cp config/config.example.yaml config/config.yaml` and edit.

Outputs: console summary, `data/BTC_USD_1d.csv`, `logs/last_fills.csv`, `logs/last_equity.csv`, `logs/last_backtest.json`.

### Compare strategies

```bash
python scripts/compare_strategies.py
# Stricter Tier-1 taker fee:
python scripts/compare_strategies.py --fee 0.008
```

Writes `logs/strategy_compare.json` and `logs/strategy_compare.md` (ranking + caveats).

### News-augmented compare (BTC/USD)

```bash
python scripts/compare_news_strategies.py
python scripts/compare_news_strategies.py --refresh-news  # re-fetch RSS
```

Uses CoinDesk / CoinTelegraph / Decrypt / Google News RSS (monthly Google queries for history).
Caches under `data/news/`. Writes `logs/news_augmented_compare.json` and `.md`.
Paper only; no TabPFN; BTC/USD daily.

---

## API keys (future — do not create in this task)

1. First key: **Query-only** (funds + open/closed orders) for monitoring.
2. Never enable **Withdraw Funds** on a bot key.
3. Add Trade permissions only after explicit approval for live mode.
4. IP whitelist when possible; store secrets in `.env` only.

---

## Disclaimer

Crypto trading is risky. Paper results are not predictive. Orca is educational software; you are responsible for any future live use.

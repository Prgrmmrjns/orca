## 2026-10-16
* **Update**: The live paper book now fills. It replayed the September rule from 13 Oct: one coin, weekly check, sticky top 3, and a same-day product headline can switch it. Spread 0.5%. No live orders.

## 2026-10-13
* **Update**: Paper demo is live at https://orca-paper.vercel.app. It reads Kraken public prices and headlines each day and holds the 14-day leader unless a product event hits another top-5 name. No orders. September book stays +75%.

## 2026-10-04
* **Update**: September’s big up days line up with a product launch or listing for NEAR, RAY, ENA, ONDO, and HBAR. AERO and SUI had none. The mid-month jump was the whole alt market.
* **Update**: Dropped the LLM book. A 14-day trend rule, frozen before September, finished the month at €1,751 (+75%) in three trades.

## 2026-10-01
* **Update**: Daily LLM book on the checked September signals finished at €868 (−13.2%), 29 trades, drawdown −28.8%.
* **Update**: Replaced the agent’s Google News dump with 12 September signals read from CoinDesk, Bitwise, Hashgraph, and Mina. Silence means no checked event. The daily book was restarted on that feed.
* **Update**: LLM book is one decision a day, at most two names, with its own trade memory and resting buy/sell levels. ML and headlines are built once. Showcase is the LLM book only, and a click shows that hour’s reasoning, ML, and headlines.
* **Update**: Showcase dropped the 5-day trades table, polls the LLM book every 4 seconds, and uses a navy Orca theme.
* **Update**: LLM agent now decides every 6 hours, holds at most 4 names for 24 hours, and ranks by 7-day strength. The hourly run was stopped at −12.6% after 229 trades and $124 of spread.
* **Update**: Added an hourly Grok 4.7 Medium agent backtest (Cursor SDK): news + reward-model scores, €1000, 0.5% spread, checkpointed decisions; showcase charts it against the 5-day book.
* **Update**: September news check: 687 cached headlines. HBAR’s Sep 28 jump and ENA’s Sep 25 jump match same-day stories. NEAR’s rally, and all of MINA and RAY, do not.
* **Update**: Hourly hindsight on the showcase pays only the 0.5% spread. That ceiling is about €90M versus the model’s €1,126.
* **Update**: Showcase chart now compares the 5-day model with an hourly hindsight book (one coin, whole month known, 1.5% per fill). That ceiling ends near €1.33M versus the model’s €1,126.
* **Update**: Added a FastAPI showcase at port 8765 for the paper backtest: equity versus buy-and-hold and the hindsight baskets, plus the model’s trades.
* **Update**: Added a 5-day reward book with stops and a 3-day momentum rotation. From €1,000, the 5-day book made +12.64% and rotation −5.61%, versus buy-and-hold +21.05%.
* **Update**: Same Kraken+ crypto book started from €1,000. Last month +12.10% (€1,121) vs buy-and-hold +21.05%. Turnover stayed inside the €10k waiver.
* **Update**: Reran the 47-pair crypto book with Kraken+ costs (0.5% spread per side, commission waived on the first €10k). Last month +10.89% vs buy-and-hold +21.59%. Turnover went past the waiver.

## 2026-09-30
* **Update**: Reward model now also scores 17,660 crypto headlines (RSS + Google News, no look-ahead). Last-month test +13.19% vs +17.09% without news, buy-and-hold +21.94%.
* **Update**: Universe is now the fixed 47 liquid USD pairs (hourly history and at least $20k median hour).
* **Update**: Reward model now scans all 609 Kraken USD pairs and trades the 47 with real hourly volume. Last-month test +17.09% (22 trades) vs buy-and-hold +21.94%.
* **Update**: Hourly reward model now trains on Oct 2024–Aug 2026 and holds a buy for 21 days. Last-month test +8.79% (10 trades) vs buy-and-hold +9.27%.
* **Update**: Retrained the shared reward model on hourly candles (Jan–Jun 2026). Last-month test stayed in cash, +0.00%, while buy-and-hold made +12.64%.
* **Update**: Shared keep/buy/sell model now scores actions by the 21-day change in account balance. Last-month paper result: +5.10% vs buy-and-hold +13.15%.

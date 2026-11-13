# bigmove_eu_v1 event study (pre-Nov 2026)

Window: **2024-11-01 → 2026-10-31** · |move|≥10% · cost 0.5%/fill round-trip · hold 5d  
Index regime: `^STOXX` vs 50d MA · liquid EU seed (DAX/MDAX/Euro Stoxx 50/large STOXX 600)  
Events classified: **123** (after earnings_chase drop)

## Category fade stats (fade the move, 5d net)

| Category | N | mean 5d net | hit |
|---|---:|---:|---:|
| analyst | 3 | +0.016 | 0.67 |
| earnings_nosurprise | 24 | +0.003 | 0.54 |
| other_8k | 96 | −0.008 | 0.42 |

Direction split (same sample):

| Slice | N | mean 5d net | hit |
|---|---:|---:|---:|
| UP fade short (all cats) | 66 | −0.015 | 0.42 |
| DOWN fade long (all cats) | 57 | **+0.0065** | 0.47 |
| earnings DOWN long | 15 | **+0.0086** | 0.53 |
| other_8k DOWN long | 41 | **+0.0073** | 0.46 |
| UP short when STOXX>MA50 | 59 | −0.014 | 0.44 |

## US rule validation → keep / drop

| Rule | Result | Decision |
|---|---|---|
| offering_gapdown_short | n=0 labeled offering events | **drop** (insufficient EU evidence) |
| pr_pop_fade_short | n=57 mean −1.4% | **drop** (unprofitable) |
| mna_gapdown_short | n=0 labeled M&A events | **drop** (insufficient EU evidence) |
| analyst_gapdown_fade_long | n=1 mean −5.5% | **drop** (too few / negative) |
| capitulation_fade_long (US: no_news + spy_below) | n=0 with Yahoo always returning a headline; spy_below hurt | **retune** → any regime; keep category |

## Kept (frozen in `config/strategies/bigmove_eu_v1.yaml`)

| Rule | min gap | side | regime | N | mean 5d net | hit |
|---|---:|---|---|---:|---:|---:|
| earnings_gapdown_fade_long | 10% | long | any | 15 | +0.86% | 53% |
| other_gapdown_fade_long | 10% | long | any | 41 | +0.73% | 46% |
| capitulation_fade_long | 10% | long | any | (live no_news) | (see other DOWN) | — |

News sources for live book: Google News RSS (EQS/DGAP ad-hoc keywords), wallstreet-online, finanzen.net, Yahoo Finance DE ticker RSS.  
Hours: **primary only 09:00–17:30 Berlin** (no reliable free Tradegate/L&S extended quotes).

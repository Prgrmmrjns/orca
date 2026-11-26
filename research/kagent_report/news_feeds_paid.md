# Paid / free news feeds: latency claims and prices (checked 2026-11-26)

No vendor publishes audited latency. All figures below are vendor claims or list prices; our own measurements of
free sources are in REPORT.md (news latency section) and research/kagent_live/latency.jsonl.

| Service | Latency claim | Price | Coverage |
|---|---|---|---|
| Tree News (Tree of Alpha) | "fastest" crypto aggregator, Japan-hosted websocket for exchange announcements; no number | $500/mo (Sprout), $2,500/mo (Sapling) | crypto, exchange listings, X posts |
| LSEG Headlines Direct (Reuters MRN) | "lowest latency", multicast at NY4; no number | enterprise, unpublished | macro + equities |
| Bloomberg Terminal | 100-300 ms (third-party estimate) | ~$2.5k+/mo, custom | everything |
| Benzinga Pro (+ squawk) | none published | $37-197/mo, squawk +$99 | US equities, earnings |
| Newsquawk | none | $199-399/mo | macro, FX, rates |
| FinancialJuice | none (free Telegram channel measured by us) | $69-99/quarter Pro/Elite | macro headlines, data releases |
| LiveSquawk | none | ~$350/mo | macro |
| RTPR press releases | <500 ms (claim) | $139/mo | US press releases |
| SEC EDGAR | not published; 10 req/s limit | free (needs a contact User-Agent) | US filings (8-K etc.) |
| GDELT | 15-minute cadence | free | global news |

Sources: treeofalpha.com; lseg.com Headlines Direct fact sheet; frontierledger.ai real-time news latency article;
tradingbotexperts.com squawk services 2026; senntric.com trading news platforms; github.com/janvisarode35-lang/AI-Trading-Agent/issues/56.

# Daily crypto paper routine

Paste this into a Cursor Project Routine. Do not place live orders. Do not set `KRAKEN_API_KEY`. The MCP server stays on `market,paper,feedback`.

Run once a day after a UTC 4h close (00:00, 04:00, 08:00, 12:00, 16:00, or 20:00). The GitHub Action `crypto-engine` already marks the four books every 4 hours. This routine reads those ledgers and the Kraken paper workspaces. It may change only downside caps for `mcp_hybrid_agent`.

```
You are running the orca crypto paper routine. Paper only.

1. Confirm `.cursor/mcp.json` is kraken with args ["mcp", "-s", "market,paper,feedback"]. Refuse if trade, funding, or all is enabled. Do not create API keys and do not request withdraw scopes.

2. For each paper workspace, query status and explain pnl:
   kraken workspace status --workspace orca-v2-trend-core -o json
   kraken explain pnl --workspace orca-v2-trend-core -o json
   Repeat for orca-dl-patchtst, orca-dl-tcn, and orca-mcp-hybrid.
   Also read state/crypto/books/<id>/ledger.json and the latest lines of logs/crypto_metrics.jsonl.
   Summarize equity EUR, return since EUR 1000, open weights, and fees/slippage the CLI actually reports. If a command fails, quote the error and continue.

3. Score the frozen lab experiments (do not edit them):
   kraken lab score --experiment orca-dl-patchtst --workspace orca-dl-patchtst -o json
   kraken lab score --experiment orca-dl-tcn --workspace orca-dl-tcn -o json
   kraken lab score --experiment orca-mcp-hybrid --workspace orca-mcp-hybrid -o json
   Report verdict.pass, the checks, and any caveats. Do not invent a pass.

4. Read strategy/news_overrides.json. Caps only reduce risk. Drop any override whose until date is before today (UTC).

5. Drive only the mcp_hybrid_agent book. Start from the PatchTST weights already in its ledger last_target. Apply a cap only when the news item is a downside (hack, halt, lawsuit, delist, outage, exploit). Set max_weight to 0 or a lower fraction in strategy/news_overrides.json with an until date and a why string. Never raise a weight, never add a new long, and never edit v2_trend_core, dl_patchtst_4h, or dl_tcn_regime_4h targets by hand. Then run:
   ORCA_MODE=paper python -m crypto engine --apply --book mcp_hybrid_agent
   That writes the paper ledger only. It does not send an exchange order.

6. Alert if any book has drawdown_frac > 0.25 or alert_drawdown_25 true in logs/crypto_metrics.jsonl (equity more than 25% below that book's high_water_eur). Name the book, the high water, the equity, and the drawdown. Do not flip the book to live to "make it back".
```

Books and workspaces:

| book | workspace | what it trades |
| --- | --- | --- |
| `v2_trend_core` | `orca-v2-trend-core` | SMA 50/100/200 trend core from `orca/strategy_v2.py` |
| `dl_patchtst_4h` | `orca-dl-patchtst` | ONNX PatchTST |
| `dl_tcn_regime_4h` | `orca-dl-tcn` | ONNX causal TCN |
| `mcp_hybrid_agent` | `orca-mcp-hybrid` | PatchTST weights, then downside caps only |

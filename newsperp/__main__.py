"""newsperp CLI.

  python -m newsperp run --minutes 110 [--commit-every 15]   # collector + paper book
  python -m newsperp probe                                    # poll every source once, print health
  python -m newsperp status                                   # book summary
  python -m newsperp study [--sec]                            # event study
  python -m newsperp backtest [--latency 45] [--cost-bp 20]   # historical backtest (Feb 2026+)
"""
from __future__ import annotations

import argparse
import json

def main():
    ap = argparse.ArgumentParser(prog="newsperp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--minutes", type=float, default=110); r.add_argument("--commit-every", type=float, default=0)
    sub.add_parser("probe"); sub.add_parser("status")
    s = sub.add_parser("study"); s.add_argument("--sec", action="store_true")
    b = sub.add_parser("backtest"); b.add_argument("--latency", type=int, default=45); b.add_argument("--cost-bp", type=float, default=20); b.add_argument("--crypto", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        from .collector import Collector
        Collector().run(a.minutes, a.commit_every)
    elif a.cmd == "probe":
        import time
        from .collector import CFG, load_json
        from .sources import Poller
        cfg = load_json(CFG / "sources.json", {})
        for src in cfg["sources"]:
            p = Poller(src, cfg["universe"]); t = time.time(); items = p.poll()
            newest = max((i.get("published") or "" for i in items), default="")
            print(f"{src['id']:15s} {'OK ' if not p.errors else 'ERR'} {len(items):3d} items  newest={newest or '-':20s} "
                  f"{p.stats['bytes']/1024:6.0f}KB {time.time()-t:4.1f}s {p.stats['last_error'] or ''}")
    elif a.cmd == "status":
        from .collector import STATE, load_json
        L = load_json(STATE / "ledger.json", None)
        if not L: print("no ledger yet"); return
        h = L["history"]; closes = [t for t in L["trades"] if t["action"] == "close"]
        eq = h[-1]["equity_eur"] if h else L["cash_eur"]
        peak = max([x["equity_eur"] for x in h] or [eq])
        print(json.dumps(dict(start=L["start"], equity_eur=eq, pnl_pct=round((eq / L["start_equity_eur"] - 1) * 100, 2),
                              drawdown_from_peak_pct=round((eq / peak - 1) * 100, 2), open=L["positions"],
                              closed_trades=len(closes), wins=sum(t["pnl_eur"] > 0 for t in closes),
                              realized_eur=round(sum(t["pnl_eur"] for t in closes), 2), last=h[-1] if h else None),
                         indent=1, default=str))
    elif a.cmd == "backtest":
        from .backtest import run
        r = run(a.latency, a.cost_bp, dataset="crypto" if a.crypto else "stocks")
        print(json.dumps({k: v for k, v in r.items() if k != "trades"}, indent=1))
    elif a.cmd == "study":
        from .study import study_events, study_sec
        if a.sec: print(json.dumps(study_sec(), indent=1))
        else:
            summ, n = study_events(); print(n, "rows"); print(json.dumps({k: v for k, v in summ.items() if k.startswith(("all", "direction"))}, indent=1))

if __name__ == "__main__":
    main()

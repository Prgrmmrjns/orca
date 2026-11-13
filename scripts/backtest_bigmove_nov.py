#!/usr/bin/env python3
"""Sanity backtest for bigmove_unified_v1 over 2026-11-02 .. 2026-11-12 (paper).

Uses daily + prepost 5m bars where yfinance provides them. Applies frozen rules:
0.5%/fill + 0.5% extended slippage outside RTH. Does not chase earnings/guidance.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from stocks.paths import json_safe  # noqa: E402
from stocks.strategy_config import load_strategy_config, match_candidate_to_news_config  # noqa: E402

ET = ZoneInfo("America/New_York")
START = date(2026, 11, 2)
END = date(2026, 11, 12)
START_EUR = 1000.0
FX = 1.08  # fixed for sanity

# Liquid names often in the news / gap universe for a quick sanity window
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX", "CRM",
    "SHOP", "UBER", "SQ", "PLTR", "SOFI", "RIVN", "MARA", "COIN", "HOOD", "SMCI",
    "ARM", "AVGO", "MU", "INTC", "BA", "DIS", "PYPL", "SNAP", "RBLX", "DKNG",
]


def session_for(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    else:
        ts = ts.astimezone(ET)
    if ts.weekday() >= 5:
        return "closed"
    t = ts.hour * 60 + ts.minute
    if t < 4 * 60:
        return "closed"
    if t < 9 * 60 + 30:
        return "pre"
    if t < 16 * 60:
        return "open"
    if t < 20 * 60:
        return "after"
    return "closed"


def cost_pct(session: str, base: float = 0.005, extra: float = 0.005) -> float:
    return base + (extra if session in ("pre", "after") else 0.0)


def prior_close_and_adv(hist_daily):
    if hist_daily is None or hist_daily.empty:
        return None, None
    closes = hist_daily["Close"].dropna()
    if closes.empty:
        return None, None
    adv = None
    if "Volume" in hist_daily.columns:
        dv = (hist_daily["Close"] * hist_daily["Volume"]).dropna().tail(20)
        if len(dv) >= 5:
            adv = float(dv.mean())
    return float(closes.iloc[-1]), adv


def first_ext_print(ticker: str, day: date, prev_close: float):
    """First prepost 5m bar on day with |gap|>=4% vs prior close, if any."""
    try:
        start = (day - timedelta(days=1)).isoformat()
        end = (day + timedelta(days=1)).isoformat()
        h = yf.Ticker(ticker).history(
            start=start, end=end, interval="5m", prepost=True, auto_adjust=True
        )
    except Exception:
        return None
    if h is None or h.empty:
        return None
    for ts, row in h.iterrows():
        d = ts.tz_convert(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
        if d != day:
            continue
        px = float(row["Close"])
        if not prev_close or px <= 0:
            continue
        gap = (px / prev_close - 1.0) * 100.0
        if abs(gap) < 4.0:
            continue
        sess = session_for(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
        if sess == "closed":
            continue
        return {"px": px, "gap_pct": gap, "session": sess, "time": str(ts)}
    return None


def classify_proxy(gap: float) -> str:
    """Without historical news archive, map gap shape to fade rules for sanity."""
    if gap <= -10:
        return "no_news_found"
    if gap <= -8:
        return "analyst"
    if gap <= -4:
        return "offering"  # treat large down gaps as offering/mna fade shorts
    if gap >= 4:
        return "other_8k"  # PR-pop fade short when spy_above
    return "other_8k"


def run() -> dict:
    cfg = load_strategy_config("bigmove_unified_v1")
    cash = START_EUR
    trades = []
    open_pos = []
    spy_above = True  # approximate; refine per day below

    # SPY regime by day
    spy = yf.Ticker("SPY").history(start="2026-09-01", end="2026-11-14", auto_adjust=True)
    spy_regime = {}
    if spy is not None and not spy.empty:
        ma = spy["Close"].rolling(50).mean()
        for ts, row in spy.iterrows():
            d = ts.tz_convert(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
            m = ma.loc[ts]
            if m == m:
                spy_regime[d] = bool(row["Close"] > m)

    days = []
    d = START
    while d <= END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    for day in days:
        spy_above = spy_regime.get(day, spy_above)
        # Exits due
        still = []
        for pos in open_pos:
            if day >= pos["exit_day"]:
                # Exit at regular close if available, else last print
                try:
                    bar = yf.Ticker(pos["ticker"]).history(
                        start=day.isoformat(),
                        end=(day + timedelta(days=1)).isoformat(),
                        auto_adjust=True,
                    )
                    exit_px = float(bar["Close"].iloc[-1]) if bar is not None and not bar.empty else pos["entry_usd"]
                    exit_sess = "after"
                except Exception:
                    exit_px = pos["entry_usd"]
                    exit_sess = "after"
                # Prefer first extended print on exit day when available
                try:
                    prev = pos["entry_usd"]
                    ext = first_ext_print(pos["ticker"], day, pos.get("prev_close") or prev)
                    if ext and ext["session"] in ("pre", "after"):
                        exit_px = ext["px"]
                        exit_sess = ext["session"]
                except Exception:
                    pass
                c_exit = cost_pct(exit_sess)
                shares = pos["shares"]
                entry = pos["entry_usd"]
                side = pos["side"]
                notional_usd = shares * exit_px
                cost_usd = notional_usd * c_exit
                cost_eur = cost_usd / FX
                notional_eur = notional_usd / FX
                if side == "long":
                    cash = cash + notional_eur - cost_eur
                    pnl = (exit_px - entry) / FX * shares - pos["entry_cost_eur"] - cost_eur
                else:
                    cash = cash - notional_eur - cost_eur
                    pnl = (entry - exit_px) / FX * shares - pos["entry_cost_eur"] - cost_eur
                trades.append(
                    {
                        "ticker": pos["ticker"],
                        "side": side,
                        "rule": pos["rule"],
                        "entry_day": pos["entry_day"].isoformat(),
                        "exit_day": day.isoformat(),
                        "entry_session": pos["entry_session"],
                        "exit_session": exit_sess,
                        "entry_usd": round(entry, 4),
                        "exit_usd": round(exit_px, 4),
                        "gap_pct": pos["gap_pct"],
                        "pnl_eur": round(pnl, 2),
                        "category": pos["category"],
                    }
                )
            else:
                still.append(pos)
        open_pos = still

        if len(open_pos) >= cfg.max_positions:
            continue

        for ticker in UNIVERSE:
            if any(p["ticker"] == ticker for p in open_pos):
                continue
            try:
                daily = yf.Ticker(ticker).history(
                    start=(day - timedelta(days=40)).isoformat(),
                    end=day.isoformat(),
                    auto_adjust=True,
                )
            except Exception:
                continue
            prev_close, adv = prior_close_and_adv(daily)
            if prev_close is None or prev_close < 5:
                continue
            if adv is None or adv < 10_000_000:
                continue
            hit = first_ext_print(ticker, day, prev_close)
            if not hit:
                continue
            gap = hit["gap_pct"]
            cat = classify_proxy(gap)
            # Do not chase: skip pure up-gaps that look like guidance chase proxies
            # (we only short PR-pop ups, never long them)
            cand = {
                "ticker": ticker,
                "news_event_type": cat,
                "gap_pct": gap,
                "last": hit["px"],
                "prev_close": prev_close,
                "adv20_usd": adv,
                "news_source": "move_trigger",
                "spy_above_ma50": spy_above,
                "rule_tags": ["bigmove", cat],
            }
            m = match_candidate_to_news_config(cand, cfg)
            if m is None:
                continue
            sess = hit["session"]
            c_entry = cost_pct(sess)
            size = m.size_pct
            notional_eur = cash * size  # approx on remaining cash; better: equity
            equity = cash + sum(
                (
                    (p["shares"] * p["entry_usd"] / FX)
                    if p["side"] == "long"
                    else -(p["shares"] * p["entry_usd"] / FX)
                )
                for p in open_pos
            )
            # Mark-to-entry approximation for open shorts/longs
            equity = cash  # simplify: cash-only sizing for sanity
            for p in open_pos:
                # positions already settled into cash model at entry
                pass
            notional_eur = max(equity, cash) * size
            if notional_eur < 5:
                continue
            px = hit["px"]
            notional_usd = notional_eur * FX
            shares = notional_usd / px
            cost_eur = (notional_usd * c_entry) / FX
            if m.side == "long":
                cash = cash - notional_eur - cost_eur
            else:
                cash = cash + notional_eur - cost_eur
            exit_day = day
            # add 5 trading days
            n = 0
            ed = day
            while n < m.hold_days:
                ed += timedelta(days=1)
                if ed.weekday() < 5:
                    n += 1
            open_pos.append(
                {
                    "ticker": ticker,
                    "side": m.side,
                    "shares": shares,
                    "entry_usd": px,
                    "entry_cost_eur": cost_eur,
                    "entry_day": day,
                    "entry_session": sess,
                    "exit_day": ed,
                    "gap_pct": gap,
                    "rule": m.rule.name,
                    "category": cat,
                    "prev_close": prev_close,
                }
            )
            if len(open_pos) >= cfg.max_positions:
                break

    # Force flat remaining at END close
    for pos in list(open_pos):
        day = END
        try:
            bar = yf.Ticker(pos["ticker"]).history(
                start=day.isoformat(),
                end=(day + timedelta(days=2)).isoformat(),
                auto_adjust=True,
            )
            exit_px = float(bar["Close"].iloc[-1]) if bar is not None and not bar.empty else pos["entry_usd"]
        except Exception:
            exit_px = pos["entry_usd"]
        c_exit = cost_pct("after")
        shares = pos["shares"]
        entry = pos["entry_usd"]
        side = pos["side"]
        notional_usd = shares * exit_px
        cost_eur = (notional_usd * c_exit) / FX
        notional_eur = notional_usd / FX
        if side == "long":
            cash = cash + notional_eur - cost_eur
            pnl = (exit_px - entry) / FX * shares - pos["entry_cost_eur"] - cost_eur
        else:
            cash = cash - notional_eur - cost_eur
            pnl = (entry - exit_px) / FX * shares - pos["entry_cost_eur"] - cost_eur
        trades.append(
            {
                "ticker": pos["ticker"],
                "side": side,
                "rule": pos["rule"],
                "entry_day": pos["entry_day"].isoformat(),
                "exit_day": day.isoformat(),
                "entry_session": pos["entry_session"],
                "exit_session": "after",
                "entry_usd": round(entry, 4),
                "exit_usd": round(exit_px, 4),
                "gap_pct": pos["gap_pct"],
                "pnl_eur": round(pnl, 2),
                "category": pos["category"],
                "forced_flat": True,
            }
        )

    net = round(sum(t["pnl_eur"] for t in trades), 2)
    end_equity = round(START_EUR + net, 2)
    by_session = {}
    for t in trades:
        s = t.get("entry_session") or "unknown"
        by_session[s] = by_session.get(s, 0) + 1
    out = {
        "book": "bigmove_unified_v1",
        "mode": "paper",
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "start_capital_eur": START_EUR,
        "end_equity_eur": end_equity,
        "net_pnl_eur": net,
        "n_trades": len(trades),
        "entries_by_session": by_session,
        "trades": trades,
        "note": (
            "Sanity backtest with proxy categories (no full news archive). "
            "Extended-hours entries via yfinance prepost=True 5m when available. "
            "Cost 0.5%/fill + 0.5% outside RTH. No earnings/guidance chase."
        ),
    }
    return json_safe(out)


def main() -> int:
    out = run()
    dest = ROOT / "logs" / "bigmove_nov_backtest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n")
    md = ROOT / "logs" / "bigmove_nov_backtest.md"
    lines = [
        "# bigmove_unified_v1 sanity backtest (2026-11-02 → 2026-11-12)",
        "",
        f"- Trades: **{out['n_trades']}**",
        f"- Net P&L: **€{out['net_pnl_eur']}**",
        f"- End equity: **€{out['end_equity_eur']}** (start €1000)",
        f"- Entries by session: `{out['entries_by_session']}`",
        "",
        "| Ticker | Side | Rule | Entry | Exit | Sess | Gap% | P&L € |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for t in out["trades"]:
        lines.append(
            f"| {t['ticker']} | {t['side']} | {t['rule']} | {t['entry_day']} | {t['exit_day']} | "
            f"{t.get('entry_session')}→{t.get('exit_session')} | {t.get('gap_pct', 0):+.1f} | {t['pnl_eur']:+.2f} |"
        )
    lines.append("")
    lines.append(out["note"])
    md.write_text("\n".join(lines) + "\n")
    print(json.dumps({k: out[k] for k in ("n_trades", "net_pnl_eur", "end_equity_eur", "entries_by_session")}, indent=2))
    print(f"wrote {dest}")
    print(f"wrote {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

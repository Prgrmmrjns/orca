#!/usr/bin/env python3
"""Out-of-sample backtest for bigmove_eu_v1 over 2026-11-02 .. 2026-11-12 (paper).

Applies frozen EU rules (gap-down fade longs only) with 0.5%/fill on primary
session. Outside primary would add 0.5% slippage (not used — no free ext data).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from stocks.eu_news import classify_eu_news  # noqa: E402
from stocks.eu_universe import EU_UNIVERSE_SEED  # noqa: E402
from stocks.market import quote_to_eur_divisor  # noqa: E402
from stocks.paths import json_safe  # noqa: E402
from stocks.strategy_config import load_strategy_config, match_candidate_to_news_config  # noqa: E402

START = date(2026, 11, 2)
END = date(2026, 11, 12)
START_EUR = 1000.0
COST = 0.005
HOLD = 5


def main() -> int:
    cfg = load_strategy_config("bigmove_eu_v1")
    # STOXX regime series
    idx_h = yf.Ticker("^STOXX").history(start="2026-08-01", end="2026-11-15", auto_adjust=True)
    ma = idx_h["Close"].rolling(50).mean()
    idx_above = {}
    for ts, px in idx_h["Close"].items():
        m = ma.loc[ts]
        if m == m:
            idx_above[ts.date()] = bool(px > m)

    cash = START_EUR
    equity = START_EUR
    positions = []  # open
    closed = []
    day = START
    while day <= END:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        # exits
        still = []
        for p in positions:
            if day >= p["exit_day"]:
                try:
                    h = yf.Ticker(p["ticker"]).history(
                        start=day.isoformat(),
                        end=(day + timedelta(days=3)).isoformat(),
                        auto_adjust=True,
                    )
                    px = float(h["Close"].iloc[0]) if h is not None and not h.empty else p["entry_px"]
                except Exception:
                    px = p["entry_px"]
                qfx = p["qfx"]
                shares = p["shares"]
                notional = shares * px / qfx
                cost = notional * COST
                if p["side"] == "long":
                    cash += notional - cost
                    pnl = (px - p["entry_px"]) / qfx * shares - p["entry_cost"] - cost
                else:
                    cash -= notional + cost
                    pnl = (p["entry_px"] - px) / qfx * shares - p["entry_cost"] - cost
                closed.append(
                    {
                        **{k: p[k] for k in ("ticker", "side", "rule_name", "category", "gap_pct", "entry_day")},
                        "exit_day": day.isoformat(),
                        "entry_session": "open",
                        "exit_session": "open",
                        "pnl_eur": round(pnl, 2),
                    }
                )
            else:
                still.append(p)
        positions = still

        if len(positions) >= cfg.max_positions:
            day += timedelta(days=1)
            continue

        # scan universe for gap-downs
        for ticker in EU_UNIVERSE_SEED:
            if any(p["ticker"] == ticker for p in positions):
                continue
            try:
                h = yf.Ticker(ticker).history(
                    start=(day - timedelta(days=40)).isoformat(),
                    end=(day + timedelta(days=1)).isoformat(),
                    auto_adjust=True,
                )
            except Exception:
                continue
            if h is None or len(h) < 22:
                continue
            # find day bar
            row = None
            prev = None
            for j, ts in enumerate(h.index):
                d = ts.date()
                if d == day and j >= 1:
                    row = h.iloc[j]
                    prev = float(h["Close"].iloc[j - 1])
                    break
            if row is None or not prev:
                continue
            px = float(row["Close"])
            gap = px / prev - 1.0
            if gap > -0.10:
                continue
            try:
                div = float(quote_to_eur_divisor(ticker) or 1.0)
            except Exception:
                div = 1.0
            if prev / div < 5:
                continue
            window = h.iloc[-21:-1]
            dv = (window["Close"] * window["Volume"]).dropna()
            if len(dv) < 5 or float(dv.mean()) / div < 10_000_000:
                continue
            # classify
            title = ""
            try:
                for it in (yf.Ticker(ticker).news or [])[:8]:
                    t = it.get("title") or (it.get("content") or {}).get("title") or ""
                    if t:
                        title = t
                        break
            except Exception:
                pass
            if title:
                tags = classify_eu_news(title, "")
                cat = tags.get("eu_category") or "other_8k"
            else:
                cat = "no_news_found"
            if cat == "earnings_chase":
                continue
            cand = {
                "ticker": ticker,
                "news_event_type": cat,
                "gap_pct": gap * 100,
                "last": px,
                "prev_close": prev,
                "adv20_usd": float(dv.mean()) / div,
                "news_source": "yahoo_eu",
                "spy_above_ma50": idx_above.get(day),
                "rule_tags": [],
            }
            m = match_candidate_to_news_config(cand, cfg)
            if m is None:
                continue
            # mark-to-market equity for sizing
            eq = cash
            for p in positions:
                try:
                    mh = yf.Ticker(p["ticker"]).history(
                        start=day.isoformat(), end=(day + timedelta(days=2)).isoformat(), auto_adjust=True
                    )
                    mpx = float(mh["Close"].iloc[0]) if mh is not None and not mh.empty else p["entry_px"]
                except Exception:
                    mpx = p["entry_px"]
                if p["side"] == "long":
                    eq += p["shares"] * mpx / p["qfx"]
                else:
                    eq -= p["shares"] * mpx / p["qfx"]
            size = float(m.size_pct or 0.05)
            notional = eq * size
            if notional < 5:
                continue
            shares = notional / (px / div)
            cost = notional * COST
            cash -= notional + cost
            exit_day = day
            # +5 weekdays
            add = 0
            while add < HOLD:
                exit_day += timedelta(days=1)
                if exit_day.weekday() < 5:
                    add += 1
            positions.append(
                {
                    "ticker": ticker,
                    "side": m.side,
                    "shares": shares,
                    "entry_px": px,
                    "qfx": div,
                    "entry_cost": cost,
                    "entry_day": day.isoformat(),
                    "exit_day": exit_day,
                    "rule_name": m.rule.name,
                    "category": cat,
                    "gap_pct": round(gap * 100, 2),
                }
            )
            if len(positions) >= cfg.max_positions:
                break
        # end-of-day equity mark
        equity = cash
        for p in positions:
            try:
                mh = yf.Ticker(p["ticker"]).history(
                    start=day.isoformat(), end=(day + timedelta(days=2)).isoformat(), auto_adjust=True
                )
                mpx = float(mh["Close"].iloc[0]) if mh is not None and not mh.empty else p["entry_px"]
            except Exception:
                mpx = p["entry_px"]
            if p["side"] == "long":
                equity += p["shares"] * mpx / p["qfx"]
            else:
                equity -= p["shares"] * mpx / p["qfx"]
        day += timedelta(days=1)

    # force close remaining at END
    for p in list(positions):
        try:
            h = yf.Ticker(p["ticker"]).history(
                start=END.isoformat(), end=(END + timedelta(days=5)).isoformat(), auto_adjust=True
            )
            px = float(h["Close"].iloc[-1]) if h is not None and not h.empty else p["entry_px"]
        except Exception:
            px = p["entry_px"]
        notional = p["shares"] * px / p["qfx"]
        cost = notional * COST
        if p["side"] == "long":
            cash += notional - cost
            pnl = (px - p["entry_px"]) / p["qfx"] * p["shares"] - p["entry_cost"] - cost
        else:
            cash -= notional + cost
            pnl = (p["entry_px"] - px) / p["qfx"] * p["shares"] - p["entry_cost"] - cost
        closed.append(
            {
                "ticker": p["ticker"],
                "side": p["side"],
                "rule_name": p["rule_name"],
                "category": p["category"],
                "gap_pct": p["gap_pct"],
                "entry_day": p["entry_day"],
                "exit_day": END.isoformat(),
                "entry_session": "open",
                "exit_session": "open",
                "pnl_eur": round(pnl, 2),
                "forced": True,
            }
        )
    positions = []
    equity = cash
    net = round(equity - START_EUR, 2)
    out = {
        "book_id": "bigmove_eu_v1",
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "start_eur": START_EUR,
        "end_eur": round(equity, 2),
        "net_pnl_eur": net,
        "n_trades": len(closed),
        "trades": closed,
        "enabled_rules": [r.name for r in cfg.rules if r.enabled],
        "hours": "primary 09:00-17:30 Berlin only",
    }
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "bigmove_eu_nov_backtest.json").write_text(json.dumps(json_safe(out), indent=2))
    lines = [
        "# bigmove_eu_v1 Nov 2–12 2026 OOS backtest",
        "",
        f"Trades: **{len(closed)}** · Net P&L: **€{net:+.2f}** · End equity: **€{equity:.2f}**",
        f"Enabled rules: {', '.join(out['enabled_rules'])}",
        "Sessions: all fills `open` (primary Berlin).",
        "",
        "| Ticker | Side | Rule | Gap | Entry | Exit | PnL € |",
        "|---|---|---|---:|---|---|---:|",
    ]
    for t in closed:
        lines.append(
            f"| {t['ticker']} | {t['side']} | {t.get('rule_name')} | {t.get('gap_pct')}% | "
            f"{t.get('entry_day')} | {t.get('exit_day')} | {t.get('pnl_eur')} |"
        )
    (logs / "bigmove_eu_nov_backtest.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

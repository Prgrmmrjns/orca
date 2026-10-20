#!/usr/bin/env python3
"""Daily paper backtest. One LLM decision a day, ML and headlines built once."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orca.action_model import build_books  # noqa: E402
from orca.config import load_config  # noqa: E402
from orca.data import fetch_binance_hourly, fetch_ohlcv, load_ohlcv, save_ohlcv  # noqa: E402
from orca.llm_agent import (  # noqa: E402
    build_prompt,
    decide_weights,
    headlines_before,
    load_api_key,
    ml_snapshot,
    resolve_grok_medium,
    stabilize_weights,
    stamp_held,
)
from orca.logging_setup import setup_logging  # noqa: E402
from orca.news import collect_reward_news, hourly_news_features  # noqa: E402
from orca.portfolio import MultiPaperPortfolio  # noqa: E402

logger = setup_logging()

DEFAULT_PAIRS = (
    "AAVE/USD",
    "ADA/USD",
    "AERO/USD",
    "ALGO/USD",
    "APT/USD",
    "ARB/USD",
    "ASTER/USD",
    "AVAX/USD",
    "BCH/USD",
    "BNB/USD",
    "BONK/USD",
    "BTC/USD",
    "CRV/USD",
    "DASH/USD",
    "DOGE/USD",
    "DOT/USD",
    "ENA/USD",
    "ETH/USD",
    "FET/USD",
    "FIL/USD",
    "HBAR/USD",
    "ICP/USD",
    "INJ/USD",
    "JUP/USD",
    "LINK/USD",
    "LTC/USD",
    "MINA/USD",
    "NEAR/USD",
    "ONDO/USD",
    "PAXG/USD",
    "PENGU/USD",
    "PEPE/USD",
    "POL/USD",
    "PUMP/USD",
    "RAY/USD",
    "RENDER/USD",
    "SOL/USD",
    "SUI/USD",
    "TAO/USD",
    "TRUMP/USD",
    "TRX/USD",
    "UNI/USD",
    "WLD/USD",
    "XLM/USD",
    "XPL/USD",
    "XRP/USD",
    "ZEC/USD",
)

CHECKPOINT = ROOT / "logs" / "llm_agent"
EQUITY_PATH = ROOT / "logs" / "llm_equity.csv"
DECISIONS_PATH = ROOT / "logs" / "llm_decisions.csv"
FILLS_PATH = ROOT / "logs" / "llm_fills.csv"
REPORT_PATH = ROOT / "logs" / "llm_backtest.json"
DETAIL_PATH = ROOT / "logs" / "llm_detail.json"


def load_recent_hourly(cfg: dict[str, Any], pairs: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        path = ROOT / "data" / f"{pair.replace('/', '_')}_1h_recent.csv"
        if path.exists():
            cached = load_ohlcv(path)
            if len(cached) >= 24 * 7:
                out[pair] = cached
                continue
        try:
            df = fetch_ohlcv(pair=pair, timeframe="1h", limit=720, exchange_cfg=cfg.get("exchange"))
        except Exception as exc:  # noqa: BLE001
            logger.info("Skip %s: %s", pair, exc)
            continue
        if len(df) >= 24 * 7:
            save_ohlcv(df, path)
            out[pair] = df
    return out


def checkpoint_path(ts: pd.Timestamp) -> Path:
    return CHECKPOINT / f"{ts.strftime('%Y%m%dT%H%M%S')}.json"


def load_checkpoint(ts: pd.Timestamp) -> dict[str, Any] | None:
    path = checkpoint_path(ts)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(ts: pd.Timestamp, payload: dict[str, Any]) -> None:
    CHECKPOINT.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(ts)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def current_weights(port: MultiPaperPortfolio, marks: dict[str, float]) -> dict[str, float]:
    equity = port.equity(marks)
    if equity <= 0:
        return {}
    out: dict[str, float] = {}
    for pair, amount in port.base.items():
        if amount <= 0 or pair not in marks:
            continue
        out[pair] = amount * marks[pair] / equity
    return out


def write_outputs(
    equity_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    port: MultiPaperPortfolio,
    meta: dict[str, Any],
) -> None:
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    pd.DataFrame(equity_rows).to_csv(EQUITY_PATH, index=False)
    pd.DataFrame(decision_rows).to_csv(DECISIONS_PATH, index=False)
    DETAIL_PATH.write_text(json.dumps(detail_rows), encoding="utf-8")
    port.fills_frame().to_csv(FILLS_PATH, index=False)
    REPORT_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def clean_orders(
    orders: list[dict[str, Any]],
    held: dict[str, float],
    marks: dict[str, float],
    *,
    max_names: int = 2,
) -> list[dict[str, Any]]:
    """Drop levels far from the mark, sells of coins we don't hold, and extra names."""
    out: list[dict[str, Any]] = []
    held_n = sum(1 for weight in held.values() if weight > 0)
    for order in orders:
        pair = order["pair"]
        mark = marks.get(pair)
        if not mark or order["below"] <= 0:
            continue
        if abs(order["below"] / mark - 1.0) > 0.15:
            continue
        if order["side"] == "sell" and held.get(pair, 0.0) <= 0:
            continue
        if order["side"] == "buy" and held.get(pair, 0.0) <= 0 and held_n >= max_names:
            continue
        out.append(order)
    return out[:4]


def trigger_price(open_: float | None, low: float | None, high: float | None, below: float) -> float | None:
    """Fill a resting 'at or below' order if this bar traded there."""
    if open_ is None or low is None or low > below:
        return None
    if open_ <= below:
        return open_
    if high is None or high < below:
        return None
    return below


def apply_resting(
    port: MultiPaperPortfolio,
    ts: pd.Timestamp,
    opens: dict[str, float],
    lows: dict[str, float],
    highs: dict[str, float],
    orders: list[dict[str, Any]],
    entry_px: dict[str, float],
    opened_at: dict[str, pd.Timestamp],
    *,
    max_names: int = 2,
) -> tuple[list[Any], list[dict[str, Any]]]:
    fills: list[Any] = []
    still: list[dict[str, Any]] = []
    for order in orders:
        pair = order["pair"]
        px = trigger_price(opens.get(pair), lows.get(pair), highs.get(pair), float(order["below"]))
        if px is None:
            still.append(order)
            continue
        if order["side"] == "sell":
            fill = port.sell_pair(ts, px, pair)
            if fill:
                fills.append(fill)
            entry_px.pop(pair, None)
            opened_at.pop(pair, None)
            continue
        held_n = sum(1 for amount in port.base.values() if amount > 0)
        if port.base.get(pair, 0.0) <= 0 and held_n >= max_names:
            still.append(order)
            continue
        marks = dict(opens)
        marks[pair] = px
        equity = port.equity(marks)
        want = float(order.get("weight") or 0.0) * equity
        have = port.base.get(pair, 0.0) * px
        gap = want - have
        if gap > 1.0:
            fill = port.buy_pair(ts, px, pair, gap)
            if fill:
                fills.append(fill)
                if pair not in entry_px:
                    entry_px[pair] = px
                    opened_at[pair] = ts
    return fills, still


def entry_stats(
    port: MultiPaperPortfolio,
    marks: dict[str, float],
    entry_px: dict[str, float],
    opened_at: dict[str, pd.Timestamp],
    now: pd.Timestamp,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair, amount in port.base.items():
        if amount <= 0 or pair not in marks or pair not in entry_px or entry_px[pair] <= 0:
            continue
        since = opened_at.get(pair)
        out[pair] = {
            "pnl": round(marks[pair] / entry_px[pair] - 1.0, 4),
            "days": int((now - since).total_seconds() // 86400) if since is not None else 0,
        }
    return out


def main() -> int:
    cfg = load_config()
    if cfg.get("mode") != "paper":
        raise SystemExit("Refusing to run: config mode is not paper")

    api_key = load_api_key()
    model_sel = resolve_grok_medium(api_key)
    logger.info("Model %s", model_sel.to_json())

    action_cfg = cfg.get("action") or {}
    pairs = list(cfg.get("pairs") or DEFAULT_PAIRS)
    reward_days = int(action_cfg.get("reward_days", 5))
    min_qv = float(action_cfg.get("min_hourly_quote_volume", 20_000))
    port_cfg = cfg.get("portfolio") or {}
    spread_rate = float(port_cfg.get("spread_rate", 0.005))
    budget_eur = float(port_cfg.get("initial_cash_eur", 1000))
    subscription_eur = float(port_cfg.get("subscription_eur", 4.99))

    import ccxt

    eur_usd = float(ccxt.kraken().fetch_ticker("EUR/USD")["last"])
    initial_cash = budget_eur * eur_usd
    subscription = subscription_eur * eur_usd
    reward_bars = reward_days * 24

    test_ohlcv = load_recent_hourly(cfg, pairs)
    if "BTC/USD" not in test_ohlcv:
        raise SystemExit("BTC/USD hourly book missing")
    cutoff = test_ohlcv["BTC/USD"].index[0]
    liquid = {
        pair: df
        for pair, df in test_ohlcv.items()
        if float((df["volume"] * df["close"]).median()) >= min_qv
    }
    history = fetch_binance_hourly(list(liquid), until_ms=int(cutoff.timestamp() * 1000))
    history = {
        pair: df[df.index < cutoff]
        for pair, df in history.items()
        if len(df[df.index < cutoff]) >= reward_bars + 24 * 7
    }
    train_pairs = list(history)
    news_through = test_ohlcv["BTC/USD"].index[-1]
    articles = collect_reward_news(
        train_pairs,
        start=datetime(2024, 9, 1, tzinfo=timezone.utc),
        end=news_through.to_pydatetime(),
    )
    news = hourly_news_features(articles, train_pairs, through=news_through)
    logger.info("News loaded once: %d headlines", len(articles))

    pairs = list(history)
    stitched: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        live = test_ohlcv[pair]
        live = live[live.index >= cutoff]
        both = pd.concat([history[pair], live])
        both = both[~both.index.duplicated(keep="last")].sort_index()
        stitched[pair] = both
    panel = build_books(stitched, news)
    calendar = pd.DatetimeIndex(sorted(panel.index.unique()))
    # Same September window as the saved action backtest when present.
    report_path = ROOT / "logs" / "action_backtest.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        start = pd.Timestamp(report["test_start"])
        end = pd.Timestamp(report["test_end"])
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        start_i = int(calendar.searchsorted(start))
        end_i = int(calendar.searchsorted(end, side="right")) - 1
    else:
        start_i = max(int(calendar.searchsorted(calendar[0] + pd.Timedelta(days=7))), 1)
        end_i = len(calendar) - 1
    start_i = max(start_i, 1)
    end_i = min(end_i, len(calendar) - 1)
    step_hours = 24
    decision_idx = [i for i in range(start_i, end_i + 1) if (i - start_i) % step_hours == 0]
    boards: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    heads_by_day: dict[pd.Timestamp, list[dict[str, str]]] = {}
    for i in decision_idx:
        ts = calendar[i]
        boards[ts] = ml_snapshot(panel, pairs, calendar[i - 1], {}, top_n=6)
        heads_by_day[ts] = headlines_before(articles, pairs, before=ts, limit=8)
    logger.info(
        "LLM book %s → %s (%d hours, %d daily decisions). ML and headlines built once.",
        calendar[start_i],
        calendar[end_i],
        end_i - start_i + 1,
        len(decision_idx),
    )

    port = MultiPaperPortfolio(
        pairs=tuple(pairs),
        initial_cash=initial_cash,
        fee_rate=0.0,
        position_size=1.0,
        spread_rate=spread_rate,
        fee_free_volume=None,
        subscription=subscription,
    )
    lookup = panel.reset_index().set_index(["timestamp", "pair"])
    known = set(zip(lookup.index.get_level_values(0), lookup.index.get_level_values(1)))

    def px(ts: pd.Timestamp, pair: str, col: str) -> float | None:
        key = (ts, pair)
        if key not in known:
            return None
        val = lookup.loc[key, col]
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        if pd.isna(val):
            return None
        return float(val)

    equity_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    hours_done = 0
    hours_total = end_i - start_i + 1
    opened_at: dict[str, pd.Timestamp] = {}
    entry_px: dict[str, float] = {}
    targets: dict[str, float] = {}
    orders: list[dict[str, Any]] = []
    memory: list[dict[str, Any]] = []
    reason = ""
    reasoning = ""

    def meta(equity: float, status: str, end_ts: pd.Timestamp) -> dict[str, Any]:
        peak = 0.0
        mdd = 0.0
        for row in equity_rows:
            eq = float(row["equity"])
            peak = max(peak, eq)
            if peak > 0:
                mdd = min(mdd, eq / peak - 1.0)
        return {
            "generated_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
            "model": model_sel.to_json(),
            "test_start": str(calendar[start_i]),
            "test_end": str(end_ts),
            "hours_done": hours_done,
            "hours_total": hours_total,
            "start_eur": budget_eur,
            "eur_usd": eur_usd,
            "final_equity": equity,
            "final_eur": equity / eur_usd,
            "return_pct": (equity / initial_cash - 1.0) * 100.0,
            "max_drawdown_pct": mdd * 100.0,
            "n_trades": len(port.fills),
            "total_fees": float(sum(f.fee for f in port.fills)),
            "status": status,
        }

    for i in range(start_i, end_i + 1):
        ts = calendar[i]
        opens = {pair: v for pair in pairs if (v := px(ts, pair, "open")) is not None}
        lows: dict[str, float] = {}
        highs: dict[str, float] = {}
        for pair, frame in stitched.items():
            if ts not in frame.index:
                continue
            bar = frame.loc[ts]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]
            if pd.notna(bar["low"]):
                lows[pair] = float(bar["low"])
            if pd.notna(bar["high"]):
                highs[pair] = float(bar["high"])
        closes: dict[str, float] = {}
        for pair in pairs:
            mark = px(ts, pair, "close")
            if mark is None:
                mark = last_close.get(pair)
            else:
                last_close[pair] = mark
            if mark is not None:
                closes[pair] = mark
        marks_open = opens or closes
        if not marks_open:
            continue

        decide_now = (i - start_i) % step_hours == 0
        held = current_weights(port, marks_open)
        fills: list[Any] = []
        ml_rows: list[dict[str, Any]] = []
        heads: list[dict[str, str]] = []
        if decide_now:
            ml_rows = stamp_held(boards[ts], held)
            heads = heads_by_day[ts]
            ck = load_checkpoint(ts)
            if ck is not None:
                targets = {k: float(v) for k, v in (ck.get("weights") or {}).items()}
                orders = list(ck.get("orders") or [])
                reason = str(ck.get("reason") or "checkpoint")
                reasoning = str(ck.get("reasoning") or reason)
                ml_rows = list(ck.get("ml") or ml_rows)
                heads = list(ck.get("headlines") or heads)
            else:
                prompt = build_prompt(
                    hour=ts,
                    cash=port.cash,
                    equity=port.equity(marks_open),
                    weights=held,
                    closes=marks_open,
                    ml_rows=ml_rows,
                    headlines=heads,
                    start_eur=budget_eur,
                    eur_usd=eur_usd,
                    pairs=pairs,
                    memory=memory,
                    since_entry=entry_stats(port, marks_open, entry_px, opened_at, ts),
                )
                try:
                    decision = decide_weights(
                        api_key=api_key,
                        model=model_sel,
                        prompt=prompt,
                        pairs=pairs,
                    )
                    targets = stabilize_weights(decision["weights"], held, opened_at, ts, max_names=2)
                    orders = clean_orders(decision["orders"], targets, marks_open)
                    reason = decision["reason"]
                    reasoning = decision["reasoning"]
                    raw = decision["raw"]
                except Exception as exc:  # noqa: BLE001 — leave book unchanged
                    logger.warning("Day %s keep unchanged: %s", ts, exc)
                    targets = held
                    orders = []
                    reason = f"unchanged after error: {exc}"
                    reasoning = reason
                    raw = ""
                save_checkpoint(
                    ts,
                    {
                        "timestamp": str(ts),
                        "weights": targets,
                        "orders": orders,
                        "reason": reason,
                        "reasoning": reasoning,
                        "ml": ml_rows,
                        "headlines": heads,
                        "raw": raw,
                    },
                )
            before_amt = {pair: amount for pair, amount in port.base.items()}
            fills = port.apply_target_weights(ts, marks_open, targets)
            for pair, amount in port.base.items():
                if amount > 0 and before_amt.get(pair, 0.0) <= 0:
                    opened_at[pair] = ts
                    if pair in marks_open:
                        entry_px[pair] = marks_open[pair]
                elif amount <= 0:
                    opened_at.pop(pair, None)
                    entry_px.pop(pair, None)
        else:
            fills, orders = apply_resting(
                port, ts, opens, lows, highs, orders, entry_px, opened_at
            )

        equity = port.equity(closes if closes else marks_open)
        equity_rows.append({"timestamp": ts, "equity": equity, "cash": port.cash})
        if decide_now:
            weights_out = {k: round(v, 4) for k, v in targets.items()}
            decision_rows.append(
                {
                    "timestamp": ts,
                    "weights": json.dumps(weights_out),
                    "reason": reason,
                    "n_fills": len(fills),
                    "equity": equity,
                }
            )
            detail_rows.append(
                {
                    "timestamp": str(ts),
                    "weights": weights_out,
                    "orders": orders,
                    "reason": reason,
                    "reasoning": reasoning,
                    "ml": ml_rows,
                    "headlines": heads,
                    "equity": equity,
                }
            )
            memory.append(
                {
                    "day": ts.strftime("%Y-%m-%d %H:%M"),
                    "weights": weights_out,
                    "orders": orders,
                    "reason": reason,
                    "equity_eur": round(equity / eur_usd, 2),
                }
            )
            memory = memory[-8:]
        hours_done += 1
        if decide_now or fills:
            write_outputs(
                equity_rows,
                decision_rows,
                detail_rows,
                port,
                meta(equity, "running" if i < end_i else "finished", ts),
            )
            logger.info(
                "LLM %d/%d equity €%.0f trades=%d",
                hours_done,
                hours_total,
                equity / eur_usd,
                len(port.fills),
            )

    final = equity_rows[-1]["equity"] if equity_rows else port.cash
    write_outputs(
        equity_rows,
        decision_rows,
        detail_rows,
        port,
        meta(final, "finished", calendar[end_i]),
    )
    logger.info("Done. Final €%.2f (%+.2f%%)", final / eur_usd, (final / initial_cash - 1) * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

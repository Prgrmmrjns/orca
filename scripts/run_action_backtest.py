#!/usr/bin/env python3
"""Paper backtest: one balance reward model, all pairs, last month only."""

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

from orca.action_model import (  # noqa: E402
    build_books,
    build_panel,
    fit_reward_model,
    market_columns,
    model_decider,
    momentum_decider,
    simulate,
    sma_decider,
    summarize,
    test_start_index,
    training_frame,
)
from orca.config import load_config  # noqa: E402
from orca.data import (  # noqa: E402
    fetch_binance_hourly,
    fetch_ohlcv,
    kraken_usd_pairs,
    load_ohlcv,
    save_ohlcv,
)
from orca.logging_setup import setup_logging  # noqa: E402
from orca.multi_pair_trajectory import align_closes  # noqa: E402
from orca.news import collect_reward_news, hourly_news_features  # noqa: E402

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


def load_pair_ohlcv(cfg: dict[str, Any], pairs: list[str], timeframe: str) -> dict[str, pd.DataFrame]:
    limit = int(cfg.get("backtest", {}).get("limit", 720))
    out: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        path = ROOT / "data" / f"{pair.replace('/', '_')}_{timeframe}.csv"
        if path.exists():
            logger.info("Loading cached OHLCV %s", path)
            out[pair] = load_ohlcv(path)
            continue
        df = fetch_ohlcv(
            pair=pair,
            timeframe=timeframe,
            limit=limit,
            exchange_cfg=cfg.get("exchange"),
        )
        save_ohlcv(df, path)
        out[pair] = df
    return out


def bars_per_day(timeframe: str) -> int:
    if timeframe == "1h":
        return 24
    if timeframe == "1d":
        return 1
    raise SystemExit(f"Unsupported timeframe {timeframe}")


def first_tradable_index(
    panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    pairs: list[str],
) -> int:
    """First bar whose previous candle has a full feature row for every pair."""
    cols = market_columns(pairs)
    ready: set[pd.Timestamp] = set()
    for ts, grp in panel.reset_index().groupby("timestamp"):
        if len(grp) == len(pairs) and bool(grp[cols].notna().all().all()):
            ready.add(ts)
    for i in range(1, len(calendar)):
        if calendar[i - 1] in ready:
            return i
    raise SystemExit("No hourly bar with complete features")


def load_recent_hourly(cfg: dict[str, Any], pairs: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    min_bars = 24 * 7
    for i, pair in enumerate(pairs, start=1):
        path = ROOT / "data" / f"{pair.replace('/', '_')}_1h_recent.csv"
        if path.exists():
            cached = load_ohlcv(path)
            if len(cached) >= min_bars:
                out[pair] = cached
                continue
        try:
            df = fetch_ohlcv(
                pair=pair,
                timeframe="1h",
                limit=720,
                exchange_cfg=cfg.get("exchange"),
            )
        except Exception as exc:  # noqa: BLE001 — one thin book should not stop the rest
            logger.info("Skip Kraken %s: %s", pair, exc)
            continue
        if len(df) < min_bars:
            continue
        save_ohlcv(df, path)
        out[pair] = df
        if i % 40 == 0:
            logger.info("Kraken hourly %d/%d kept %d", i, len(pairs), len(out))
    logger.info("Kraken hourly books: %d", len(out))
    return out


def buy_hold_decider(pairs: list[str]):
    def decide(prev: pd.Timestamp, state: dict[str, int]) -> dict[str, str]:
        return {pair: "buy" if state[pair] == 0 else "keep" for pair in pairs}

    return decide


def write_report(path: Path, payload: dict[str, Any]) -> None:
    rows = payload["results"]
    lines = [
        "# Balance reward model — last month",
        "",
        f"Generated: {payload['generated_at']}",
        "Mode: `paper`. One reward model for every pair. Actions: keep, buy, sell.",
        "",
        f"- Pairs: {', '.join(payload['pairs'])}",
        f"- Bars: `{payload['timeframe']}`",
        f"- Train: {payload['train_start']} → {payload['train_end']} ({payload['train_bars']} bars)",
        f"- Test: {payload['test_start']} → {payload['test_end']} ({payload['test_bars']} bars)",
        f"- Train rows: {payload['n_train']} (reward is {payload['reward_days']} days = {payload['reward_bars']} bars, and those bars end before the test window)",
        f"- Mean reward by position/action: {payload['reward_means']}",
        f"- Fee: {payload['fee_rate']:.4f} per side. Sleeve: {payload['position_size']:.2f} / {payload.get('slots', len(payload['pairs']))} slots.",
        "",
        f"Reward is the change in account balance {payload['reward_days']} days after the action, after fees. The book takes the legal action with the higher predicted balance. {payload.get('note', '')}",
        "",
        "| Strategy | Return % | Max DD % | Trades | Fees | Final equity |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['strategy']}` | {row['return_pct']:.2f} | {row['max_drawdown_pct']:.2f} | "
            f"{row['n_trades']} | {row['total_fees']:.2f} | {row['final_equity']:.2f} |"
        )
    lines.extend(["", "## Actions taken", ""])
    counts = payload["action_counts"]
    traded = {
        pair: c
        for pair, c in counts.items()
        if c.get("buy", 0) or c.get("sell", 0)
    }
    lines.append(f"Watched {len(counts)} pairs. Opened or closed {len(traded)}.")
    lines.append("")
    lines.append("| Pair | keep | buy | sell |")
    lines.append("| --- | ---: | ---: | ---: |")
    for pair, c in sorted(traded.items()):
        lines.append(f"| `{pair}` | {c.get('keep', 0)} | {c.get('buy', 0)} | {c.get('sell', 0)} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    if cfg.get("mode") != "paper":
        raise SystemExit("Refusing to run: config mode is not paper")

    action_cfg = cfg.get("action") or {}
    raw_pairs = cfg.get("pairs") or list(DEFAULT_PAIRS)
    pairs = kraken_usd_pairs() if raw_pairs == "all" else list(raw_pairs)
    timeframe = cfg.get("timeframe", "1d")
    reward_days = int(action_cfg.get("reward_days", action_cfg.get("horizon", 21)))
    test_days = int(action_cfg.get("test_days", 30))
    max_positions = int(action_cfg.get("max_positions", 8))
    port = cfg.get("portfolio") or {}
    fee_rate = float(port.get("fee_rate", 0.0026))
    spread_rate = float(port.get("spread_rate", 0.0))
    position_size = float(port.get("position_size", 0.95))
    fee_free_eur = port.get("fee_free_volume_eur")
    budget_eur = port.get("initial_cash_eur")
    eur_usd = 1.0
    fee_free_volume = None
    subscription = 0.0
    if fee_free_eur is not None or budget_eur is not None:
        import ccxt

        ticker = ccxt.kraken().fetch_ticker("EUR/USD")
        eur_usd = float(ticker["last"])
    if budget_eur is not None:
        initial_cash = float(budget_eur) * eur_usd
    else:
        initial_cash = float(port.get("initial_cash", 10_000))
    if fee_free_eur is not None:
        fee_free_volume = float(fee_free_eur) * eur_usd
        subscription = float(port.get("subscription_eur", 0.0)) * eur_usd
        # Under the cap the only cost is the spread. The 1% commission applies after it.
        train_fee = spread_rate
    else:
        train_fee = fee_rate

    reward_bars = reward_days * bars_per_day(timeframe)
    note = ""
    if timeframe == "1h":
        # One shared model watches every Kraken USD pair. Training uses Binance
        # hourly history where Kraken only keeps ~30 days. Capital goes to the
        # slots with the highest predicted balance change, not an equal slice of every name.
        test_ohlcv = load_recent_hourly(cfg, pairs)
        if "BTC/USD" not in test_ohlcv:
            raise SystemExit("BTC/USD hourly book missing")
        cutoff = test_ohlcv["BTC/USD"].index[0]
        min_qv = float(action_cfg.get("min_hourly_quote_volume", 20_000))
        liquid = {
            pair: df
            for pair, df in test_ohlcv.items()
            if float((df["volume"] * df["close"]).median()) >= min_qv
        }
        scanned = len(test_ohlcv)
        history = fetch_binance_hourly(list(liquid), until_ms=int(cutoff.timestamp() * 1000))
        history = {
            pair: df[df.index < cutoff]
            for pair, df in history.items()
            if len(df[df.index < cutoff]) >= reward_bars + 24 * 7
        }
        if len(history) < 4:
            raise SystemExit(f"Only {len(history)} pairs have enough hourly history to train")
        train_pairs = list(history)
        news_through = test_ohlcv["BTC/USD"].index[-1]
        articles = collect_reward_news(
            train_pairs,
            start=datetime(2024, 9, 1, tzinfo=timezone.utc),
            end=news_through.to_pydatetime(),
        )
        if len(articles) < 50:
            raise SystemExit(f"Only {len(articles)} headlines; refusing to train without news")
        news = hourly_news_features(articles, train_pairs, through=news_through)
        logger.info("News headlines=%d", len(articles))
        train_panel = build_books(history, news)
        train_calendar = pd.DatetimeIndex(sorted(train_panel.index.unique()))
        costs = dict(
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            position_size=position_size,
            spread_rate=spread_rate,
            fee_free_volume=fee_free_volume,
            subscription=subscription,
            stop_from_entry=0.08,
            trail_from_peak=0.12,
        )
        val_i = int(train_calendar.searchsorted(pd.Timestamp("2026-08-01", tz="UTC")))
        val_note = ""
        if reward_bars + 24 * 7 < val_i < len(train_calendar) - 24 * 7:
            val_df = training_frame(
                train_panel,
                train_calendar,
                train_pairs,
                val_i,
                reward_bars,
                train_fee,
                position_size,
                stride=24,
                n_slots=max_positions,
                step=pd.Timedelta(hours=1),
                horizon=pd.Timedelta(days=reward_days),
                with_pair=False,
            )
            val_model = fit_reward_model(val_df, train_pairs, with_pair=False)
            _, val_model_eq, _ = simulate(
                train_panel,
                train_calendar,
                train_pairs,
                val_i,
                model_decider(val_model, train_panel, train_pairs, with_pair=False, max_positions=max_positions),
                min_hold_bars=reward_bars,
                n_slots=max_positions,
                **costs,
            )
            _, val_rot_eq, _ = simulate(
                train_panel,
                train_calendar,
                train_pairs,
                val_i,
                momentum_decider(train_panel, train_pairs, max_positions=max_positions),
                min_hold_bars=72,
                n_slots=max_positions,
                **costs,
            )
            val_note = (
                f" August, trained only on data before 1 Aug: "
                f"5-day model {(float(val_model_eq['equity'].iloc[-1]) / initial_cash - 1) * 100:.1f}%, "
                f"rotation {(float(val_rot_eq['equity'].iloc[-1]) / initial_cash - 1) * 100:.1f}%."
            )
        train_df = training_frame(
            train_panel,
            train_calendar,
            train_pairs,
            len(train_calendar),
            reward_bars,
            train_fee,
            position_size,
            stride=24,
            n_slots=max_positions,
            step=pd.Timedelta(hours=1),
            horizon=pd.Timedelta(days=reward_days),
            with_pair=False,
        )
        pairs = list(history)
        ohlcv = {pair: test_ohlcv[pair] for pair in pairs}
        panel = build_books(ohlcv, news)
        calendar = pd.DatetimeIndex(sorted(panel.index.unique()))
        start_i = max(int(calendar.searchsorted(calendar[0] + pd.Timedelta(days=7))), 1)
        note = (
            f"Scanned {scanned} Kraken USD pairs (stables and fiat excluded). "
            f"Traded {len(pairs)} whose median hour does at least ${min_qv:,.0f} and that have Binance hourly history from 11 Oct 2024. "
            f"Holds at most {max_positions} names. The learned book scores a {reward_days}-day balance change "
            f"and may exit early on an 8% loss or a 12% drop from the peak. "
            f"Rotation keeps the strongest 7-day uptrends, holds at least 3 days, same stops. "
            f"News: {len(articles)} headlines from CoinDesk, CoinTelegraph, Decrypt, "
            "Bitcoin Magazine, CryptoSlate, The Block, and Google News. "
            "Each bar only sees articles published before that bar. "
            "The test is each pair's latest Kraken hourly bars. "
            f"Start: €{float(budget_eur):,.0f} (${initial_cash:,.0f} at {eur_usd:.4f}). "
            f"Kraken+: commission waived on the first €{float(fee_free_eur):,.0f} "
            f"(${fee_free_volume:,.0f}) of notional, then {fee_rate:.0%}. "
            f"Spread {spread_rate:.1%} per side. Subscription ${subscription:.2f}.{val_note}"
        )
    else:
        ohlcv = load_pair_ohlcv(cfg, pairs, timeframe)
        aligned = align_closes(ohlcv, pairs)
        calendar = aligned.index
        train_calendar = calendar
        start_i = test_start_index(calendar, test_days)
        if start_i < reward_bars + 30:
            raise SystemExit(f"Not enough history before the test window (start_i={start_i})")
        panel = build_panel(aligned, pairs)
        train_df = training_frame(
            panel, calendar, pairs, start_i, reward_bars, train_fee, position_size
        )
    reward_means = (
        train_df.groupby(["in_position", "action"])["reward"].mean().round(5).to_dict()
    )
    logger.info("Train rows=%d mean reward=%s", len(train_df), reward_means)
    model = fit_reward_model(train_df, pairs, with_pair=timeframe != "1h")

    common = dict(
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
        spread_rate=spread_rate,
        fee_free_volume=fee_free_volume,
        subscription=subscription,
    )
    risk = dict(stop_from_entry=0.08, trail_from_peak=0.12) if timeframe == "1h" else {}
    action_port, action_eq, decisions = simulate(
        panel,
        calendar,
        pairs,
        start_i,
        model_decider(
            model,
            panel,
            pairs,
            with_pair=timeframe != "1h",
            max_positions=max_positions if timeframe == "1h" else None,
        ),
        min_hold_bars=reward_bars,
        n_slots=max_positions if timeframe == "1h" else None,
        **common,
        **risk,
    )
    rot_port, rot_eq, _ = simulate(
        panel,
        calendar,
        pairs,
        start_i,
        momentum_decider(panel, pairs, max_positions=max_positions if timeframe == "1h" else len(pairs)),
        min_hold_bars=72 if timeframe == "1h" else 3,
        n_slots=max_positions if timeframe == "1h" else None,
        **common,
        **risk,
    )
    sma_port, sma_eq, _ = simulate(
        panel,
        calendar,
        pairs,
        start_i,
        sma_decider(ohlcv, pairs),
        **common,
    )
    bh_port, bh_eq, _ = simulate(
        panel,
        calendar,
        pairs,
        start_i,
        buy_hold_decider(pairs),
        **common,
    )

    results = [
        summarize("reward_5d", action_port, action_eq, initial_cash),
        summarize("rotation", rot_port, rot_eq, initial_cash),
        summarize("sma_crossover", sma_port, sma_eq, initial_cash),
        summarize("buy_hold", bh_port, bh_eq, initial_cash),
    ]
    action_counts = {
        pair: decisions.loc[decisions["pair"] == pair, "action"].value_counts().to_dict()
        for pair in pairs
    }

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    action_port.fills_frame().to_csv(logs / "action_fills.csv", index=False)
    action_eq.to_csv(logs / "action_equity.csv")
    decisions.to_csv(logs / "action_decisions.csv", index=False)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "pairs": pairs,
        "timeframe": timeframe,
        "reward_days": reward_days,
        "reward_bars": reward_bars,
        "note": note,
        "test_days": test_days,
        "train_bars": int(len(train_calendar)),
        "train_start": str(train_calendar[0]),
        "train_end": str(train_calendar[-1]),
        "test_bars": len(calendar) - start_i,
        "test_start": str(calendar[start_i]),
        "test_end": str(calendar[-1]),
        "n_train": int(len(train_df)),
        "reward_means": {f"pos{k[0]}_{k[1]}": float(v) for k, v in reward_means.items()},
        "fee_rate": fee_rate,
        "position_size": position_size,
        "slots": max_positions if timeframe == "1h" else len(pairs),
        "results": results,
        "action_counts": action_counts,
    }
    json_path = logs / "action_backtest.json"
    md_path = logs / "action_backtest.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(md_path, payload)

    print(f"Window {payload['test_start']} → {payload['test_end']} ({payload['test_bars']} bars)")
    print(f"Train rows {payload['n_train']} mean reward {payload['reward_means']}")
    for row in results:
        print(
            f"{row['strategy']:16} return {row['return_pct']:7.2f}%  "
            f"mdd {row['max_drawdown_pct']:7.2f}%  trades {row['n_trades']:3d}  "
            f"equity {row['final_equity']:.2f}"
        )
    print(f"Report {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

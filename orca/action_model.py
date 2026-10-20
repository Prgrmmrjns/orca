"""One shared reward model for every spot pair.

The model scores keep, buy, and sell by the change in account balance those
actions would have produced. Reward is mark-to-market balance over `reward_days`,
after fees. It is not a forecast of the next price. One regressor is fit on all
pairs stacked together. At decision time the book takes the legal action with
the higher predicted balance.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from orca.metrics import max_drawdown
from orca.portfolio import MultiPaperPortfolio
from orca.strategy import rsi, sma

ACTIONS = ("keep", "buy", "sell")

_BASE_FEATURES = (
    "ret_1",
    "ret_6",
    "ret_1d",
    "ret_3d",
    "ret_7d",
    "vol_1d",
    "vol_7d",
    "rsi_7d",
    "dist_sma_1d",
    "dist_sma_7d",
    "hl_range",
    "vol_z",
    "btc_ret_1d",
    "btc_ret_7d",
    "xs_ret_1d",
    "rank_3d",
    "rank_7d",
    "news_sent_1d",
    "news_n_1d",
    "news_sent_7d",
    "news_pair_1d",
    "news_pair_n_1d",
)

_NEWS_FEATURES = (
    "news_sent_1d",
    "news_n_1d",
    "news_sent_7d",
    "news_pair_1d",
    "news_pair_n_1d",
)


def slug(pair: str) -> str:
    return pair.replace("/", "_")


def feature_columns(
    pairs: list[str] | tuple[str, ...],
    *,
    with_pair: bool = True,
) -> list[str]:
    """Market features plus position and the action being scored."""
    market = list(_BASE_FEATURES)
    if with_pair:
        market += [f"pair_{slug(p)}" for p in pairs]
    return market + ["in_position", "action_keep", "action_buy", "action_sell"]


def market_columns(
    pairs: list[str] | tuple[str, ...],
    *,
    with_pair: bool = True,
) -> list[str]:
    skip = {"in_position", "action_keep", "action_buy", "action_sell"}
    return [c for c in feature_columns(pairs, with_pair=with_pair) if c not in skip]


def test_start_index(calendar: pd.DatetimeIndex, test_days: int) -> int:
    """First bar of the last `test_days` calendar days. Always leaves a prior bar."""
    cutoff = calendar[-1] - pd.Timedelta(days=int(test_days))
    start = int(calendar.searchsorted(cutoff))
    return min(max(start, 1), len(calendar) - 1)


def _bars_for(index: pd.DatetimeIndex, hours: int) -> int:
    gap_h = index.to_series().diff().median().total_seconds() / 3600.0
    return max(int(round(hours / max(gap_h, 1.0))), 1)


def build_panel(
    aligned: pd.DataFrame,
    pairs: list[str] | tuple[str, ...],
) -> pd.DataFrame:
    """Long table: one row per (timestamp, pair). Features use only data <= t.

    Lookbacks are calendar time (1d, 7d), so an hourly bar and a daily bar
    describe the same horizon.
    """
    h1 = _bars_for(aligned.index, 1)
    d1 = _bars_for(aligned.index, 24)
    d3 = _bars_for(aligned.index, 72)
    d7 = _bars_for(aligned.index, 24 * 7)
    market = "BTC/USD" if "BTC/USD" in pairs else pairs[0]
    market_close = aligned[f"{slug(market)}_close"].astype(float)
    btc_ret_1d = market_close.pct_change(d1)
    btc_ret_7d = market_close.pct_change(d7)

    ret1d_by_pair = {
        pair: aligned[f"{slug(pair)}_close"].astype(float).pct_change(d1) for pair in pairs
    }
    xs_mean = pd.concat(ret1d_by_pair, axis=1).mean(axis=1)
    ret3_by_pair = {
        pair: aligned[f"{slug(pair)}_close"].astype(float).pct_change(d3) for pair in pairs
    }
    ret7_by_pair = {
        pair: aligned[f"{slug(pair)}_close"].astype(float).pct_change(d7) for pair in pairs
    }
    rank3 = pd.concat(ret3_by_pair, axis=1).rank(axis=1, pct=True)
    rank7 = pd.concat(ret7_by_pair, axis=1).rank(axis=1, pct=True)

    frames: list[pd.DataFrame] = []
    for pair in pairs:
        s = slug(pair)
        close = aligned[f"{s}_close"].astype(float)
        high = aligned[f"{s}_high"].astype(float)
        low = aligned[f"{s}_low"].astype(float)
        volume = aligned[f"{s}_volume"].astype(float)
        ret1 = close.pct_change(h1)
        feat = pd.DataFrame(index=aligned.index)
        feat["ret_1"] = ret1
        feat["ret_6"] = close.pct_change(max(h1 * 6, 1))
        feat["ret_1d"] = ret1d_by_pair[pair]
        feat["ret_3d"] = close.pct_change(d3)
        feat["ret_7d"] = close.pct_change(d7)
        feat["vol_1d"] = ret1.rolling(d1, min_periods=d1).std()
        feat["vol_7d"] = ret1.rolling(d7, min_periods=d7).std()
        feat["rsi_7d"] = rsi(close, d7) / 100.0
        feat["dist_sma_1d"] = close / sma(close, d1) - 1.0
        feat["dist_sma_7d"] = close / sma(close, d7) - 1.0
        feat["hl_range"] = (high - low) / close.replace(0, np.nan)
        vol_sma = sma(volume, d7)
        feat["vol_z"] = volume / vol_sma - 1.0
        feat["btc_ret_1d"] = btc_ret_1d
        feat["btc_ret_7d"] = btc_ret_7d
        feat["xs_ret_1d"] = ret1d_by_pair[pair] - xs_mean
        feat["rank_3d"] = rank3[pair]
        feat["rank_7d"] = rank7[pair]
        for col in _NEWS_FEATURES:
            feat[col] = 0.0
        for other in pairs:
            feat[f"pair_{slug(other)}"] = 1.0 if other == pair else 0.0
        feat["pair"] = pair
        feat["open"] = aligned[f"{s}_open"].astype(float)
        feat["close"] = close
        frames.append(feat)

    panel = pd.concat(frames)
    panel.index.name = "timestamp"
    return panel


def _join_news(
    feat: pd.DataFrame,
    pair: str,
    news: tuple[pd.DataFrame, dict[str, pd.DataFrame]] | None,
) -> None:
    """Copy news columns onto this pair. Missing hours stay 0, never a future headline."""
    if not news:
        for col in _NEWS_FEATURES:
            feat[col] = 0.0
        return
    market, by_pair = news
    if market is not None and len(market):
        aligned = market.sort_index()
        joined = aligned.reindex(feat.index.union(aligned.index)).sort_index().ffill().reindex(feat.index)
        for col in ("news_sent_1d", "news_n_1d", "news_sent_7d"):
            feat[col] = joined[col] if col in joined.columns else 0.0
    else:
        for col in ("news_sent_1d", "news_n_1d", "news_sent_7d"):
            feat[col] = 0.0
    pair_frame = by_pair.get(pair) if by_pair else None
    if pair_frame is not None and len(pair_frame):
        aligned = pair_frame.sort_index()
        joined = aligned.reindex(feat.index.union(aligned.index)).sort_index().ffill().reindex(feat.index)
        feat["news_pair_1d"] = joined["news_pair_1d"]
        feat["news_pair_n_1d"] = joined["news_pair_n_1d"]
    else:
        feat["news_pair_1d"] = 0.0
        feat["news_pair_n_1d"] = 0.0
    for col in _NEWS_FEATURES:
        feat[col] = feat[col].fillna(0.0)


def build_books(
    frames: dict[str, pd.DataFrame],
    news: tuple[pd.DataFrame, dict[str, pd.DataFrame]] | None = None,
) -> pd.DataFrame:
    """Feature rows for books that do not share every hour. No pair identity column.

    One shared policy scores every USD pair from the same return features.
    Missing hours stay missing; nothing is filled forward.
    """
    if not frames:
        raise ValueError("No books to build")
    d1, d3, d7 = 24, 72, 24 * 7
    anchor = "BTC/USD" if "BTC/USD" in frames else next(iter(frames))
    btc_close = frames[anchor]["close"].astype(float)
    btc_ret_1d = btc_close.pct_change(d1)
    btc_ret_7d = btc_close.pct_change(d7)
    ret1d = {
        pair: df["close"].astype(float).pct_change(d1) for pair, df in frames.items()
    }
    xs = pd.concat(ret1d, axis=1).mean(axis=1, skipna=True)
    ret3 = {pair: df["close"].astype(float).pct_change(d3) for pair, df in frames.items()}
    ret7 = {pair: df["close"].astype(float).pct_change(d7) for pair, df in frames.items()}
    rank3 = pd.concat(ret3, axis=1).rank(axis=1, pct=True)
    rank7 = pd.concat(ret7, axis=1).rank(axis=1, pct=True)

    frames_out: list[pd.DataFrame] = []
    for pair, df in frames.items():
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)
        ret1 = close.pct_change(1)
        feat = pd.DataFrame(index=df.index)
        feat["ret_1"] = ret1
        feat["ret_6"] = close.pct_change(6)
        feat["ret_1d"] = ret1d[pair]
        feat["ret_3d"] = close.pct_change(d3)
        feat["ret_7d"] = close.pct_change(d7)
        feat["vol_1d"] = ret1.rolling(d1, min_periods=d1).std()
        feat["vol_7d"] = ret1.rolling(d7, min_periods=d7).std()
        feat["rsi_7d"] = rsi(close, d7) / 100.0
        feat["dist_sma_1d"] = close / sma(close, d1) - 1.0
        feat["dist_sma_7d"] = close / sma(close, d7) - 1.0
        feat["hl_range"] = (high - low) / close.replace(0, np.nan)
        vol_sma = sma(volume, d7)
        feat["vol_z"] = volume / vol_sma - 1.0
        feat["btc_ret_1d"] = btc_ret_1d.reindex(df.index)
        feat["btc_ret_7d"] = btc_ret_7d.reindex(df.index)
        feat["xs_ret_1d"] = ret1d[pair] - xs.reindex(df.index)
        feat["rank_3d"] = rank3[pair].reindex(df.index)
        feat["rank_7d"] = rank7[pair].reindex(df.index)
        _join_news(feat, pair, news)
        feat["pair"] = pair
        feat["open"] = df["open"].astype(float)
        feat["close"] = close
        frames_out.append(feat)
    panel = pd.concat(frames_out)
    panel.index.name = "timestamp"
    return panel


def _horizon_rewards(
    close: np.ndarray,
    open_next: np.ndarray,
    close_h: np.ndarray,
    fee_rate: float,
    weight: float,
) -> dict[tuple[int, str], np.ndarray]:
    """Change in account balance if the sleeve does this action and we mark at day h.

    `weight` is the sleeve's share of the account. Buy pays the entry fee.
    Sell pays the exit fee. Keep while flat does not change the balance.
    """
    zero = np.zeros_like(close)
    buy_flat = weight * ((1.0 - fee_rate) * close_h / open_next - 1.0)
    keep_long = weight * (close_h / close - 1.0)
    sell_long = weight * ((1.0 - fee_rate) * open_next / close - 1.0)
    return {
        (0, "keep"): zero,
        (0, "buy"): buy_flat,
        (0, "sell"): zero,
        (1, "keep"): keep_long,
        (1, "buy"): keep_long,
        (1, "sell"): sell_long,
    }


def training_frame(
    panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    pairs: list[str] | tuple[str, ...],
    test_start_i: int,
    reward_bars: int,
    fee_rate: float,
    position_size: float,
    stride: int = 1,
    n_slots: int | None = None,
    step: pd.Timedelta | None = None,
    horizon: pd.Timedelta | None = None,
    with_pair: bool = True,
) -> pd.DataFrame:
    """One row per (pair, bar, position, action). Reward uses only bars before the test month.

    `stride` keeps every Nth bar so a multi-day reward is not copied onto every hour.
    """
    cols = market_columns(pairs, with_pair=with_pair)
    bar_of = {ts: i for i, ts in enumerate(calendar)}
    work = panel.reset_index()
    work["bar_i"] = work["timestamp"].map(bar_of)
    if step is not None and horizon is not None:
        opens = work[["pair", "timestamp", "open"]].rename(
            columns={"timestamp": "next_ts", "open": "open_next"}
        )
        closes = work[["pair", "timestamp", "close"]].rename(
            columns={"timestamp": "future_ts", "close": "close_h"}
        )
        work["next_ts"] = work["timestamp"] + step
        work["future_ts"] = work["timestamp"] + horizon
        work = work.merge(opens, on=["pair", "next_ts"], how="left")
        work = work.merge(closes, on=["pair", "future_ts"], how="left")
        # A reward that would land inside the test window is dropped.
        test_start_ts = calendar[test_start_i] if test_start_i < len(calendar) else None
        if test_start_ts is not None:
            work = work[work["future_ts"] < test_start_ts]
        train = work
    else:
        grouped = work.groupby("pair", sort=False)
        work["open_next"] = grouped["open"].shift(-1)
        work["close_h"] = grouped["close"].shift(-int(reward_bars))
        train = work[work["bar_i"] + int(reward_bars) < test_start_i].copy()
    if int(stride) > 1:
        train = train[train["timestamp"].dt.hour == 0] if int(stride) == 24 else train[train["bar_i"] % int(stride) == 0]
    train = train.dropna(subset=cols + ["open_next", "close_h"])

    slots = int(n_slots) if n_slots else len(pairs)
    weight = float(position_size) / slots
    rewards = _horizon_rewards(
        train["close"].to_numpy(dtype=float),
        train["open_next"].to_numpy(dtype=float),
        train["close_h"].to_numpy(dtype=float),
        fee_rate,
        weight,
    )
    pieces: list[pd.DataFrame] = []
    base_cols = cols + ["pair", "timestamp"]
    for pos, action in rewards:
        part = train[base_cols].copy()
        part["in_position"] = pos
        part["action"] = action
        for name in ACTIONS:
            part[f"action_{name}"] = 1.0 if name == action else 0.0
        part["reward"] = rewards[(pos, action)]
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


def fit_reward_model(
    train_df: pd.DataFrame,
    pairs: list[str] | tuple[str, ...],
    *,
    with_pair: bool = True,
) -> HistGradientBoostingRegressor:
    cols = feature_columns(pairs, with_pair=with_pair)
    model = HistGradientBoostingRegressor(
        max_depth=4,
        learning_rate=0.06,
        max_iter=300,
        min_samples_leaf=15,
        random_state=42,
    )
    model.fit(train_df[cols], train_df["reward"].astype(float))
    return model


def _score_action(
    model: HistGradientBoostingRegressor,
    market: dict[str, float],
    cols: list[str],
    in_position: int,
    action: str,
) -> float:
    row = dict(market)
    row["in_position"] = in_position
    for name in ACTIONS:
        row[f"action_{name}"] = 1.0 if name == action else 0.0
    return float(model.predict(pd.DataFrame([row])[cols])[0])


def simulate(
    panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    pairs: list[str] | tuple[str, ...],
    test_start_i: int,
    decide: Callable[[pd.Timestamp, dict[str, int]], dict[str, str]],
    *,
    initial_cash: float,
    fee_rate: float,
    position_size: float,
    min_hold_bars: int = 1,
    n_slots: int | None = None,
    spread_rate: float = 0.0,
    fee_free_volume: float | None = None,
    subscription: float = 0.0,
    stop_from_entry: float | None = None,
    trail_from_peak: float | None = None,
) -> tuple[MultiPaperPortfolio, pd.DataFrame, pd.DataFrame]:
    """Fills at the bar open from the previous bar's action. Sells before buys.

    A buy is kept for `min_hold_bars` so a multi-day reward is not closed out
    on the next hour.
    """
    port = MultiPaperPortfolio(
        pairs=tuple(pairs),
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        position_size=position_size,
        spread_rate=spread_rate,
        fee_free_volume=fee_free_volume,
        subscription=subscription,
    )
    lookup = panel.reset_index().set_index(["timestamp", "pair"])
    known = set(zip(lookup.index.get_level_values(0), lookup.index.get_level_values(1)))
    state = {pair: 0 for pair in pairs}
    entered_at = {pair: -10**9 for pair in pairs}
    entry_px: dict[str, float] = {}
    peak_px: dict[str, float] = {}
    last_close = {pair: None for pair in pairs}
    equity_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    hold = max(int(min_hold_bars), 1)
    slots = int(n_slots) if n_slots else len(pairs)

    def _px(ts: pd.Timestamp, pair: str, col: str) -> float | None:
        key = (ts, pair)
        if key not in known:
            return None
        val = lookup.loc[key, col]
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        if pd.isna(val):
            return None
        return float(val)

    for i in range(test_start_i, len(calendar)):
        ts = calendar[i]
        prev = calendar[i - 1]
        actions = decide(prev, dict(state))
        known_close = dict(last_close)
        opens = {pair: _px(ts, pair, "open") for pair in pairs}
        closes: dict[str, float] = {}
        for pair in pairs:
            mark = _px(ts, pair, "close")
            if mark is None:
                mark = last_close[pair]
            else:
                last_close[pair] = mark
            if mark is not None:
                closes[pair] = mark

        for pair in pairs:
            action = actions.get(pair, "keep")
            forced = False
            held_px = known_close.get(pair)
            if state[pair] == 1 and held_px and pair in entry_px:
                if stop_from_entry and held_px <= entry_px[pair] * (1.0 - stop_from_entry):
                    action = "sell"
                    forced = True
                elif (
                    trail_from_peak
                    and i - entered_at[pair] >= hold
                    and pair in peak_px
                    and held_px <= peak_px[pair] * (1.0 - trail_from_peak)
                ):
                    action = "sell"
            if action == "sell" and state[pair] == 1 and not forced and i - entered_at[pair] < hold:
                action = "keep"
            decision_rows.append(
                {
                    "timestamp": ts,
                    "pair": pair,
                    "action": action,
                    "in_position": state[pair],
                    "open": opens[pair],
                }
            )
            if action == "sell" and state[pair] == 1 and opens[pair] is not None:
                if port.sell_pair(ts, opens[pair], pair):
                    state[pair] = 0
                    entry_px.pop(pair, None)
                    peak_px.pop(pair, None)

        marks = {pair: opens[pair] if opens[pair] is not None else closes.get(pair) for pair in pairs}
        marks = {pair: px for pair, px in marks.items() if px is not None}
        for pair, amount in port.base.items():
            if amount > 0 and pair not in marks and last_close[pair] is not None:
                marks[pair] = last_close[pair]
        equity_now = port.equity(marks) if marks else port.cash
        sleeve = position_size * equity_now / slots
        for pair in pairs:
            if actions.get(pair) == "buy" and state[pair] == 0 and opens[pair] is not None:
                if port.buy_pair(ts, opens[pair], pair, sleeve):
                    state[pair] = 1
                    entered_at[pair] = i
                    entry_px[pair] = opens[pair]
                    peak_px[pair] = opens[pair]
        for pair in pairs:
            if state[pair] == 1 and pair in closes and pair in peak_px:
                peak_px[pair] = max(peak_px[pair], closes[pair])

        equity_rows.append({"timestamp": ts, "equity": port.equity(closes) if closes else port.cash})

    equity = pd.DataFrame(equity_rows).set_index("timestamp")
    decisions = pd.DataFrame(decision_rows)
    return port, equity, decisions


def model_decider(
    model: HistGradientBoostingRegressor,
    panel: pd.DataFrame,
    pairs: list[str] | tuple[str, ...],
    *,
    with_pair: bool = True,
    max_positions: int | None = None,
) -> Callable[[pd.Timestamp, dict[str, int]], dict[str, str]]:
    """Score every pair in one batch. Open at most `max_positions` names, best reward first."""
    market_cols = market_columns(pairs, with_pair=with_pair)
    cols = feature_columns(pairs, with_pair=with_pair)
    by_pair = {
        pair: frame.drop(columns=["pair"])
        for pair, frame in panel.groupby("pair")
    }
    slots = int(max_positions) if max_positions else len(pairs)

    def decide(prev: pd.Timestamp, state: dict[str, int]) -> dict[str, str]:
        actions = {pair: "keep" for pair in pairs}
        batch: list[dict[str, float]] = []
        owners: list[tuple[str, str]] = []
        for pair in pairs:
            book = by_pair.get(pair)
            if book is None or prev not in book.index:
                continue
            row = book.loc[prev]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            if row[market_cols].isna().any():
                continue
            pos = int(state[pair])
            legal = ("keep", "buy") if pos == 0 else ("keep", "sell")
            market = {c: float(row[c]) for c in market_cols}
            for action in legal:
                scored = dict(market)
                scored["in_position"] = pos
                for name in ACTIONS:
                    scored[f"action_{name}"] = 1.0 if name == action else 0.0
                batch.append(scored)
                owners.append((pair, action))
        if not batch:
            return actions
        pred = model.predict(pd.DataFrame(batch)[cols])
        best: dict[str, tuple[str, float]] = {}
        for (pair, action), reward in zip(owners, pred):
            reward = float(reward)
            current = best.get(pair)
            if current is None or reward > current[1] or (reward == current[1] and action == "keep"):
                best[pair] = (action, reward)
        buys: list[tuple[float, str]] = []
        for pair, (action, reward) in best.items():
            if action == "sell":
                actions[pair] = "sell"
            elif action == "buy":
                buys.append((reward, pair))
        room = max(slots - sum(state.values()), 0)
        buys.sort(reverse=True)
        for _, pair in buys[:room]:
            actions[pair] = "buy"
        return actions

    return decide


def momentum_decider(
    panel: pd.DataFrame,
    pairs: list[str] | tuple[str, ...],
    *,
    max_positions: int = 8,
) -> Callable[[pd.Timestamp, dict[str, int]], dict[str, str]]:
    """Hold the strongest uptrends. Score is 7-day and 3-day return, only above the 7-day average."""
    by_pair = {pair: frame.drop(columns=["pair"]) for pair, frame in panel.groupby("pair")}

    def decide(prev: pd.Timestamp, state: dict[str, int]) -> dict[str, str]:
        scored: list[tuple[float, str]] = []
        for pair in pairs:
            book = by_pair.get(pair)
            if book is None or prev not in book.index:
                continue
            row = book.loc[prev]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            if pd.isna(row["ret_7d"]) or pd.isna(row["ret_3d"]) or pd.isna(row["dist_sma_7d"]):
                continue
            if float(row["ret_7d"]) <= 0 or float(row["dist_sma_7d"]) <= 0:
                continue
            score = 0.7 * float(row["ret_7d"]) + 0.3 * float(row["ret_3d"])
            scored.append((score, pair))
        scored.sort(reverse=True)
        wanted = {pair for _, pair in scored[:max_positions]}
        actions = {pair: "keep" for pair in pairs}
        for pair in pairs:
            if state[pair] == 1 and pair not in wanted:
                actions[pair] = "sell"
        room = max(max_positions - sum(1 for pair in pairs if state[pair] == 1 and actions[pair] != "sell"), 0)
        for _, pair in scored:
            if room <= 0:
                break
            if state[pair] == 0 and pair in wanted:
                actions[pair] = "buy"
                room -= 1
        return actions

    return decide


def sma_decider(
    ohlcv_by_pair: dict[str, pd.DataFrame],
    pairs: list[str] | tuple[str, ...],
) -> Callable[[pd.Timestamp, dict[str, int]], dict[str, str]]:
    """Hold long while fast SMA is above slow SMA; flat otherwise."""
    from orca.strategy import generate_signals

    frames = {
        pair: generate_signals(
            ohlcv_by_pair[pair],
            {"name": "sma_crossover", "fast_period": 10, "slow_period": 30},
        )
        for pair in pairs
    }

    def decide(prev: pd.Timestamp, state: dict[str, int]) -> dict[str, str]:
        actions: dict[str, str] = {}
        for pair in pairs:
            frame = frames[pair]
            if prev not in frame.index:
                actions[pair] = "keep"
                continue
            row = frame.loc[prev]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            if pd.isna(row["sma_fast"]) or pd.isna(row["sma_slow"]):
                actions[pair] = "keep"
                continue
            want_long = bool(row["sma_fast"] > row["sma_slow"])
            if want_long and state[pair] == 0:
                actions[pair] = "buy"
            elif not want_long and state[pair] == 1:
                actions[pair] = "sell"
            else:
                actions[pair] = "keep"
        return actions

    return decide


def summarize(
    name: str,
    port: MultiPaperPortfolio,
    equity: pd.DataFrame,
    initial_cash: float,
) -> dict[str, Any]:
    final = float(equity["equity"].iloc[-1]) if len(equity) else initial_cash
    return {
        "strategy": name,
        "final_equity": final,
        "return_pct": (final / initial_cash - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown(equity["equity"]) * 100.0,
        "n_trades": len(port.fills),
        "total_fees": float(sum(f.fee for f in port.fills)),
        "cash": port.cash,
        "base": dict(port.base),
    }

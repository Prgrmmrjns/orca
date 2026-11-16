#!/usr/bin/env python3
"""Multi-book crypto paper engine.

Fills are simulated at the public ask (buys) and bid (sells). There is no
private API, no API key, and no path that submits a live order. ``--apply``
writes ``state/crypto/books/<id>/`` and appends ``logs/crypto_metrics.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from crypto.books import CryptoBook, load_books
from crypto.data_panel import align_assets, fetch_rest_ohlc, to_grid, wide_close, wide_volume
from crypto.data_panel import _cache_path, _read_cache
from crypto.features import build_features
from crypto.risk import PaperOnlyError, apply_downside_caps, assert_paper, assert_process_is_paper
from crypto.spec import DRAWDOWN_ALERT, INTERVALS, LOOKBACK
from orca.strategy_v2 import (
    UNIVERSE,
    closes,
    eur_usd,
    is_rebalance_day,
    load_overrides,
    mark,
    rebalance,
    signal_from_closes,
    ticker,
)

METRICS_PATH = ROOT / "logs" / "crypto_metrics.jsonl"
SPEC_PATH = ROOT / "crypto" / "checkpoints" / "model_spec.json"
_SESSIONS: dict[str, object] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8") or "{}")


def _write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def empty_ledger(book: CryptoBook, fx: float, day: str) -> dict:
    cash = round(book.capital_eur * fx, 4)
    return {
        "mode": "paper",
        "book_id": book.id,
        "strategy": book.id,
        "strategy_title": book.title,
        "kind": book.kind,
        "workspace": book.workspace,
        "base_currency": "EUR",
        "note": (
            f"Paper only. Book `{book.id}` workspace `{book.workspace}`. "
            "No live orders, no API key."
        ),
        "start_date": day,
        "start_capital_eur": book.capital_eur,
        "eur_usd_start": fx,
        "cash_usd": cash,
        "positions": {},
        "fills": [],
        "high_water_eur": book.capital_eur,
        "last_rebalance": None,
        "last_target": {},
    }


def _ledger_path(book: CryptoBook) -> Path:
    return book.ledger_dir / "ledger.json"


def load_ledger(book: CryptoBook, fx: float, day: str) -> dict:
    path = _ledger_path(book)
    if not path.exists():
        return empty_ledger(book, fx, day)
    led = _read_json(path)
    if led.get("mode") != "paper":
        raise PaperOnlyError(f"ledger {path} mode={led.get('mode')!r} is not paper")
    return led


def _spec() -> dict:
    if SPEC_PATH.exists():
        return json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return {"lookback": LOOKBACK, "interval": "4h", "assets": list(UNIVERSE)}


def _session(path: Path):
    key = str(path)
    if key not in _SESSIONS:
        import onnxruntime as ort

        _SESSIONS[key] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _SESSIONS[key]


def _recent_frames(minutes: int, assets: tuple[str, ...]) -> dict:
    frames = {}
    for asset in assets:
        parts = []
        cached = _read_cache(_cache_path(asset, minutes, "panel"))
        if cached is not None and len(cached):
            parts.append(cached)
        parts.append(fetch_rest_ohlc(asset, minutes))
        import pandas as pd

        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        frames[asset] = to_grid(df, minutes)
    return align_assets(frames)


def infer_book_weights(book: CryptoBook, prev: dict[str, float]) -> tuple[dict[str, float], dict]:
    spec = _spec()
    assets = tuple(spec.get("assets") or book.universe)
    lookback = int(spec.get("lookback") or LOOKBACK)
    interval = "4h" if book.bar == "4h" else book.bar
    minutes = INTERVALS[interval]
    frames = _recent_frames(minutes, assets)
    if len(frames) < len(assets):
        raise RuntimeError(f"{book.id}: missing bars for {set(assets) - set(frames)}")
    built = build_features(wide_close(frames), wide_volume(frames), interval=interval, assets=assets)
    feats = built["features"]
    if len(feats) < lookback:
        raise RuntimeError(f"{book.id}: need {lookback} feature rows, have {len(feats)}")
    window = feats[-lookback:].astype(np.float32)[None, ...]
    vol = built["vol"][-1].astype(np.float32)[None, ...]
    prev_arr = np.array([float(prev.get(a, 0.0)) for a in assets], dtype=np.float32)[None, :]
    ckpt = ROOT / (book.checkpoint or "")
    if not ckpt.exists():
        raise FileNotFoundError(f"{book.id}: checkpoint missing at {ckpt}")
    sess = _session(ckpt)
    out = sess.run(None, {"features": window, "vol": vol, "prev_weights": prev_arr})[0][0]
    weights = {a: float(out[i]) for i, a in enumerate(assets)}
    info = {"cash_weight": float(out[-1]), "bar": str(built["index"][-1]), "checkpoint": str(book.checkpoint)}
    return weights, info


def trend_weights(day) -> tuple[dict[str, float], dict]:
    series = {}
    last = None
    for base, pair in UNIVERSE.items():
        rows = closes(pair)
        series[base] = [px for _, px in rows]
        if rows:
            last = rows[-1][0].isoformat()
    sig = signal_from_closes(series, load_overrides(day))
    sig["bar"] = last
    return dict(sig["weights"]), sig


def _quotes(assets: tuple[str, ...]) -> dict[str, dict]:
    return {base: ticker(UNIVERSE[base]) for base in assets}


def _should_trade_trend(ledger: dict, day, weights: dict[str, float], overrides: dict) -> bool:
    if is_rebalance_day(ledger, day):
        return True
    pos = ledger.get("positions") or {}
    for asset, rule in overrides.items():
        cap = float(rule.get("max_weight", 0.0))
        held = float((pos.get(asset) or {}).get("units") or 0.0) > 0 and cap <= 0
        if held or weights.get(asset, 0.0) < cap:
            # An active cap below the last target is a downside-only exit.
            if float((ledger.get("last_target") or {}).get(asset, 0.0)) > cap:
                return True
    return False


def _drawdown(equity_eur: float, high_water: float) -> tuple[float, float, bool]:
    hw = max(float(high_water or equity_eur), float(equity_eur))
    frac = 0.0 if hw <= 0 else 1.0 - float(equity_eur) / hw
    return hw, frac, frac > DRAWDOWN_ALERT


def run_book(book: CryptoBook, *, apply: bool, fx: float, now: datetime) -> dict:
    assert_paper(book.mode, source=f"book {book.id}")
    day = now.date()
    ledger = load_ledger(book, fx, day.isoformat())
    assert_paper(str(ledger.get("mode")), source=f"ledger {book.id}")
    overrides = load_overrides(day)
    info: dict = {}
    if book.kind == "trend":
        weights, info = trend_weights(day)
        trade = _should_trade_trend(ledger, day, weights, overrides)
    else:
        weights, info = infer_book_weights(book, ledger.get("last_target") or {})
        if book.kind == "hybrid":
            before = dict(weights)
            weights = apply_downside_caps(weights, overrides)
            info["news_caps"] = {k: weights[k] for k in weights if weights[k] < before.get(k, 0.0) - 1e-12}
        trade = True
    assets = tuple(book.universe)
    quotes = _quotes(assets)
    prices = {b: q["last"] for b, q in quotes.items()}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fills: list[dict] = []
    if trade:
        fills = rebalance(ledger, weights, quotes, fx, stamp, reason=book.id)
        if fills:
            ledger["last_rebalance"] = day.isoformat()
        ledger["last_target"] = {k: round(float(v), 6) for k, v in weights.items()}
    marked = mark(ledger, prices, fx)
    hw, dd, alert = _drawdown(marked["equity_eur"], ledger.get("high_water_eur") or marked["equity_eur"])
    ledger["high_water_eur"] = round(hw, 2)
    ledger["equity_eur"] = marked["equity_eur"]
    ledger["equity_usd"] = marked["equity_usd"]
    ledger["mode"] = "paper"
    row = {
        "ts": stamp,
        "book_id": book.id,
        "workspace": book.workspace,
        "kind": book.kind,
        "mode": "paper",
        "equity_eur": marked["equity_eur"],
        "equity_usd": marked["equity_usd"],
        "return_pct": marked["return_pct"],
        "cash_usd": marked["cash_usd"],
        "eur_usd": fx,
        "weights": {k: round(float(v), 6) for k, v in weights.items()},
        "cash_weight": round(1.0 - sum(weights.values()), 6),
        "high_water_eur": round(hw, 2),
        "drawdown_frac": round(dd, 6),
        "alert_drawdown_25": alert,
        "fills": len(fills),
        "bar": info.get("bar") or info.get("bar_date"),
        "checkpoint": info.get("checkpoint"),
    }
    if apply:
        _write_json(_ledger_path(book), ledger)
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with METRICS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return row


def run(*, apply: bool = False, mode: str = "paper", book_id: str | None = None) -> list[dict]:
    assert_paper(mode)
    assert_process_is_paper()
    books = load_books()
    for book in books:
        assert_paper(book.mode, source=book.id)
    if book_id:
        books = [b for b in books if b.id == book_id]
        if not books:
            raise KeyError(book_id)
    now = _now()
    fx = eur_usd()
    rows = []
    for book in books:
        try:
            rows.append(run_book(book, apply=apply, fx=fx, now=now))
        except PaperOnlyError:
            raise
        except Exception as exc:  # one book must not sink the others
            rows.append({
                "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "book_id": book.id,
                "workspace": book.workspace,
                "mode": "paper",
                "error": f"{type(exc).__name__}: {exc}",
                "alert_drawdown_25": False,
            })
            if apply:
                METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
                with METRICS_PATH.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rows[-1]) + "\n")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Crypto multi-book paper engine")
    ap.add_argument("--apply", action="store_true", help="write ledgers and metrics")
    ap.add_argument("--dry-run", action="store_true", help="compute without writing")
    ap.add_argument("--book", default=None, help="single book id")
    ap.add_argument("--mode", default="paper", help="must be paper")
    args = ap.parse_args()
    try:
        rows = run(apply=args.apply and not args.dry_run, mode=args.mode, book_id=args.book)
    except PaperOnlyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

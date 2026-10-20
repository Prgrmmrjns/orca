"""Paper backtest showcase. Reads the last action-model and LLM agent runs."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
DATA = ROOT / "data"
STATIC = Path(__file__).resolve().parent / "static"

EUR_USD = 1.1305
START_EUR = 1000.0

app = FastAPI(title="Orca backtest")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _eur(usd: float, eur_usd: float = EUR_USD) -> float:
    return float(usd) / float(eur_usd)


def _load_report() -> dict:
    return json.loads((LOGS / "action_backtest.json").read_text(encoding="utf-8"))


def _load_llm() -> dict | None:
    path = LOGS / "llm_backtest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _prices(pairs: list[str], start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    opens = {}
    closes = {}
    for pair in pairs:
        path = DATA / f"{pair.replace('/', '_')}_1h_recent.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.set_index("timestamp").sort_index()
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        opens[pair] = frame["open"]
        closes[pair] = frame["close"]
    return pd.DataFrame(opens), pd.DataFrame(closes)


def _stamp(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _bundle() -> dict:
    report = _load_report()
    llm = _load_llm() or {}
    eur_usd = float(llm.get("eur_usd") or EUR_USD)
    start_eur = float(llm.get("start_eur") or START_EUR)
    start = pd.Timestamp(report["test_start"])
    end = pd.Timestamp(report["test_end"])
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    opens, closes = _prices(list(report["pairs"]), start, end)
    month = {}
    for pair in opens.columns:
        o = opens[pair].dropna()
        c = closes[pair].dropna()
        if len(o) and len(c) and float(o.iloc[0]) > 0:
            month[pair] = float(c.iloc[-1]) / float(o.iloc[0]) - 1.0
    ranked = sorted(month, key=month.get, reverse=True)[:10]

    trend_path = LOGS / "trend_backtest.json"
    trend = json.loads(trend_path.read_text(encoding="utf-8")) if trend_path.exists() else None
    if trend:
        llm = trend
        eur_usd = float(trend.get("eur_usd") or eur_usd)

    stamps: list[str] = []
    equity: list[float | None] = []
    equity_name = "trend_equity.csv" if trend else "llm_equity.csv"
    if (LOGS / equity_name).exists():
        frame = pd.read_csv(LOGS / equity_name, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp")
        stamps = [_stamp(ts) for ts in frame["timestamp"]]
        equity = [round(_eur(float(v), eur_usd), 2) for v in frame["equity"]]

    decisions: list[dict] = []
    if trend and (LOGS / "trend_fills.csv").exists():
        fills_frame = pd.read_csv(LOGS / "trend_fills.csv", parse_dates=["timestamp"])
        for row in fills_frame.itertuples(index=False):
            decisions.append(
                {
                    "timestamp": _stamp(row.timestamp),
                    "side": row.side,
                    "pair": row.pair,
                    "price": round(float(row.price), 4),
                    "reason": trend.get("rule") or "",
                }
            )
    detail_path = LOGS / "llm_detail.json"
    if not trend and detail_path.exists():
        for row in json.loads(detail_path.read_text(encoding="utf-8")):
            decisions.append(
                {
                    "timestamp": _stamp(row["timestamp"]),
                    "weights": row.get("weights") or {},
                    "orders": row.get("orders") or [],
                    "reason": row.get("reason") or "",
                    "reasoning": row.get("reasoning") or "",
                    "ml": row.get("ml") or [],
                    "headlines": row.get("headlines") or [],
                    "equity_eur": round(_eur(float(row.get("equity") or 0), eur_usd), 2),
                }
            )

    fills = []
    if (LOGS / "llm_fills.csv").exists():
        frame = pd.read_csv(LOGS / "llm_fills.csv", parse_dates=["timestamp"])
        if len(frame):
            for row in frame.itertuples(index=False):
                fills.append(
                    {
                        "timestamp": _stamp(row.timestamp),
                        "pair": row.pair,
                        "side": row.side,
                        "price": round(float(row.price), 6),
                    }
                )

    return {
        "test_start": str(report["test_start"])[:16],
        "test_end": str(report["test_end"])[:16],
        "start_eur": start_eur,
        "eur_usd": eur_usd,
        "book": "trend" if trend else "llm",
        "rule": (trend or {}).get("rule") or "",
        "status": llm.get("status") or "waiting",
        "return_pct": round(float(llm.get("return_pct", 0.0)), 2) if llm else None,
        "final_eur": round(float(llm.get("final_eur", 0.0)), 2) if llm else None,
        "max_drawdown_pct": round(float(llm.get("max_drawdown_pct", 0.0)), 2) if llm else None,
        "n_trades": int(llm.get("n_trades", 0)),
        "hours_done": llm.get("hours_done"),
        "hours_total": llm.get("hours_total"),
        "timestamps": stamps,
        "equity": equity,
        "decisions": decisions,
        "fills": fills,
        "top10": [
            {"pair": pair, "return_pct": round(month[pair] * 100, 1)}
            for pair in ranked
        ],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/backtest")
def backtest() -> JSONResponse:
    return JSONResponse(_bundle(), headers={"Cache-Control": "no-store"})

"""Kraken paper demo. One process, public prices, no orders."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from live import load_ledger, require_paper, snapshot

app = FastAPI()
PAGE = Path(__file__).resolve().parent / "public" / "index.html"


@app.get("/")
def index():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/today")
def today():
    require_paper()
    return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/daily")
def daily():
    """Read-only status. Paper fills are applied by scripts/orca_daily.py, not this route."""
    require_paper()
    data = snapshot()
    data["cadence"] = "read-only — Chewbacca runs scripts/orca_daily.py"
    data["writable"] = False
    try:
        ledger = load_ledger()
        data["ledger"] = {
            "start_date": ledger.get("start_date"),
            "last_rebalance": ledger.get("last_rebalance"),
            "n_fills": len(ledger.get("fills") or []),
            "positions": sorted((ledger.get("positions") or {}).keys()),
        }
    except Exception as exc:
        data["ledger_error"] = str(exc)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})

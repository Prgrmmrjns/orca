"""Orca paper demo. Stocks earnings homepage; crypto under /crypto.html. No orders."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from live import load_ledger, require_paper, snapshot as crypto_snapshot
from stocks_live import snapshot as stocks_snapshot

app = FastAPI()
PUBLIC = Path(__file__).resolve().parent / "public"
PAGE = PUBLIC / "index.html"
CRYPTO_PAGE = PUBLIC / "crypto.html"


@app.get("/")
def index():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/crypto.html")
def crypto_page():
    return FileResponse(CRYPTO_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/stocks")
def stocks():
    require_paper()
    return JSONResponse(stocks_snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/today")
def today():
    require_paper()
    return JSONResponse(crypto_snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/daily")
def daily():
    """Read-only crypto status. Crypto daily paper is paused."""
    require_paper()
    data = crypto_snapshot()
    data["cadence"] = "paused — stocks earnings forward test is the active product"
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

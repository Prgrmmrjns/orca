"""Kraken paper demo. One process, public prices, no orders."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from live import snapshot

app = FastAPI()
PAGE = Path(__file__).resolve().parent / "public" / "index.html"


@app.get("/")
def index():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/today")
def today():
    return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/daily")
def daily():
    data = snapshot()
    data["cadence"] = "daily 14:00 UTC"
    return JSONResponse(data, headers={"Cache-Control": "no-store"})

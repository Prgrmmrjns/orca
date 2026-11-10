"""Orca paper demo — US stock earnings forward test only. No broker orders."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from stocks_live import require_paper, snapshot as stocks_snapshot

app = FastAPI(title="Orca — earnings paper")
PUBLIC = Path(__file__).resolve().parent / "public"
PAGE = PUBLIC / "index.html"


@app.get("/")
def index():
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/crypto.html")
@app.get("/crypto")
def crypto_gone():
    """Crypto page removed — redirect to stocks homepage."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/api/stocks")
@app.get("/api/today")
def stocks():
    require_paper()
    return JSONResponse(stocks_snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/stocks/books")
def stocks_books():
    require_paper()
    data = stocks_snapshot()
    return JSONResponse(
        {
            "mode": data["mode"],
            "as_of": data["as_of"],
            "since": data["since"],
            "spy_above_ma50": data.get("spy_above_ma50"),
            "books": data.get("books") or [],
            "rules": data.get("rules") or {},
            "news_event_config": data.get("news_event_config"),
        },
        headers={"Cache-Control": "no-store"},
    )

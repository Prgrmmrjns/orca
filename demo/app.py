"""Orca paper demo — US stock earnings forward test only. No broker orders."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from stocks_live import json_safe, require_paper, snapshot as stocks_snapshot

app = FastAPI(title="Orca — earnings paper")
PUBLIC = Path(__file__).resolve().parent / "public"
PAGE = PUBLIC / "index.html"


def _ok(payload: dict) -> JSONResponse:
    return JSONResponse(json_safe(payload), headers={"Cache-Control": "no-store"})


def _books_payload(data: dict) -> dict:
    return {
        "mode": data.get("mode", "paper"),
        "as_of": data.get("as_of"),
        "since": data.get("since"),
        "spy_above_ma50": data.get("spy_above_ma50"),
        "books": data.get("books") or [],
        "rules": data.get("rules") or {},
        "news_event_config": data.get("news_event_config"),
        "strategy_configs": data.get("strategy_configs") or {},
        "book_errors": data.get("book_errors") or [],
    }


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
    try:
        return _ok(stocks_snapshot())
    except Exception as exc:
        # Never 500 the homepage: return empty paper shell
        return _ok(
            {
                "mode": "paper",
                "product": "stocks-earnings-multi-book",
                "orders": "paper",
                "as_of": None,
                "since": "2026-11-10",
                "books": [],
                "candidates": [],
                "news": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


@app.get("/api/stocks/books")
def stocks_books():
    require_paper()
    try:
        return _ok(_books_payload(stocks_snapshot()))
    except Exception as exc:
        return _ok(
            {
                "mode": "paper",
                "as_of": None,
                "since": "2026-11-10",
                "books": [],
                "rules": {},
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

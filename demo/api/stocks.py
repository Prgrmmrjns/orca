"""Stocks paper snapshot for the homepage (multi-book)."""
from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

_DEMO = Path(__file__).resolve().parents[1]
if str(_DEMO) not in sys.path:
    sys.path.insert(0, str(_DEMO))
from stocks_live import json_safe, require_paper, snapshot  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            require_paper()
            path = urlparse(self.path).path.rstrip("/")
            data = snapshot()
            # /api/stocks/books → books array focus; /api/stocks → full snapshot
            if path.endswith("/books"):
                body = {
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
            else:
                body = data
            payload = json.dumps(json_safe(body), allow_nan=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            err = json.dumps(
                {"mode": "paper", "books": [], "error": f"{type(exc).__name__}: {exc}"},
                allow_nan=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(err)

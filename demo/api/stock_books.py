"""Per-book paper snapshot: GET /api/stock_books (also rewritten from /api/stocks/books)."""
from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path

_DEMO = Path(__file__).resolve().parents[1]
if str(_DEMO) not in sys.path:
    sys.path.insert(0, str(_DEMO))
from stocks_live import require_paper, snapshot  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        require_paper()
        data = snapshot()
        body = json.dumps(
            {
                "mode": data["mode"],
                "as_of": data["as_of"],
                "since": data["since"],
                "spy_above_ma50": data.get("spy_above_ma50"),
                "books": data.get("books") or [],
                "rules": data.get("rules") or {},
                "news_event_config": data.get("news_event_config"),
                "strategy_configs": data.get("strategy_configs") or {},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

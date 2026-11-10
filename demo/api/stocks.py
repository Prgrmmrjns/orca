"""Stocks paper snapshot for the homepage."""
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
        body = json.dumps(snapshot()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

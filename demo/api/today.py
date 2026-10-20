"""Live paper snapshot from ledger + public Kraken marks."""

from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import require_paper, snapshot


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        require_paper()
        body = json.dumps(snapshot()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

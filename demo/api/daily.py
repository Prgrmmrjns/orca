"""Daily paper pass at 14:00 UTC. Same book as /api/today. No orders."""

from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import snapshot


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = snapshot()
        data["cadence"] = "daily 14:00 UTC"
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

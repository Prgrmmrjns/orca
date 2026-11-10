"""Read-only paper status. No cron, no fills, no private API."""

from http.server import BaseHTTPRequestHandler
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import load_ledger, require_paper, snapshot


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        require_paper()
        # Read-only: same mark-to-market view as /api/today. Mutations live in scripts/orca_daily.py.
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
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

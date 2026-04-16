"""
Dashboard API — lightweight HTTP server for tender review.
Serves tender list, accept/reject actions, and associate workload.
Runs on Railway alongside PostgreSQL, or locally for development.

Usage: python api.py  (starts on port 8000)
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_loop = None


def _run_async(coro):
    """Run an async function from sync context."""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)


class APIHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for dashboard API routes."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/tenders":
            self._handle_list_tenders(params)
        elif path == "/api/associates":
            self._handle_list_associates()
        elif path.startswith("/api/tenders/") and path.endswith("/detail"):
            tender_id = path.split("/")[3]
            self._handle_tender_detail(tender_id)
        elif path.startswith("/api/associates/") and path.endswith("/tenders"):
            name = path.split("/")[3]
            self._handle_associate_tenders(name, params)
        elif path == "/api/health":
            self._json_response({"status": "ok"})
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "invalid JSON body"}, 400)
            return

        try:
            if path.startswith("/api/tenders/") and path.endswith("/accept"):
                tender_id = int(path.split("/")[3])
                self._handle_accept(tender_id)
            elif path.startswith("/api/tenders/") and path.endswith("/reject"):
                tender_id = int(path.split("/")[3])
                reason = body.get("reason", "")
                self._handle_reject(tender_id, reason)
            elif path == "/api/tenders/bulk-accept":
                ids = body.get("ids", [])
                self._handle_bulk_accept(ids)
            else:
                self._json_response({"error": "not found"}, 404)
        except (ValueError, TypeError, IndexError):
            self._json_response({"error": "invalid request"}, 400)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _handle_list_tenders(self, params):
        status = params.get("status", [""])[0]
        associate = params.get("associate", [""])[0]
        limit = int(params.get("limit", ["100"])[0])
        offset = int(params.get("offset", ["0"])[0])

        tenders = _run_async(db.list_tenders(
            status=status, associate=associate, limit=limit, offset=offset
        ))
        # Convert datetime objects to strings for JSON serialization
        for t in tenders:
            for k, v in t.items():
                if hasattr(v, "isoformat"):
                    t[k] = v.isoformat()
        self._json_response(tenders)

    def _handle_list_associates(self):
        associates = _run_async(db.list_associates())
        for a in associates:
            for k, v in a.items():
                if hasattr(v, "isoformat"):
                    a[k] = v.isoformat()
        self._json_response(associates)

    def _handle_associate_tenders(self, name, params):
        """Get tenders assigned to a specific associate."""
        from urllib.parse import unquote
        name = unquote(name)
        tenders = _run_async(db.list_tenders(associate=name, limit=200))
        for t in tenders:
            for k, v in t.items():
                if hasattr(v, "isoformat"):
                    t[k] = v.isoformat()
        self._json_response(tenders)

    def _handle_tender_detail(self, tender_id):
        try:
            tender = _run_async(db.get_tender(int(tender_id)))
            if tender:
                for k, v in tender.items():
                    if hasattr(v, "isoformat"):
                        tender[k] = v.isoformat()
                self._json_response(tender)
            else:
                self._json_response({"error": "not found"}, 404)
        except (ValueError, TypeError):
            self._json_response({"error": "invalid id"}, 400)

    def _handle_accept(self, tender_id):
        result = _run_async(db.accept_tender(tender_id))
        if result:
            self._json_response({"status": "accepted", "id": tender_id})
        else:
            self._json_response({"error": "not found or not pending"}, 400)

    def _handle_reject(self, tender_id, reason):
        result = _run_async(db.reject_tender(tender_id, reason))
        if result:
            self._json_response({"status": "rejected", "id": tender_id})
        else:
            self._json_response({"error": "not found or not pending"}, 400)

    def _handle_bulk_accept(self, ids):
        accepted = 0
        for tid in ids:
            result = _run_async(db.accept_tender(int(tid)))
            if result:
                accepted += 1
        self._json_response({"accepted": accepted, "total": len(ids)})

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        log.info(format, *args)


async def _init():
    await db.init_schema()


def main():
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", "8000")))
    _run_async(_init())
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    log.info("Dashboard API running on http://0.0.0.0:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        server.server_close()
        _run_async(db.close_pool())


if __name__ == "__main__":
    main()

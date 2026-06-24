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
import po_routes
import vendor_routes

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_loop = None

# Hard upper bound on request body size to prevent a malicious Content-Length
# header from causing an OOM by inflating rfile.read(N). /api/po accepts
# base64-encoded PDFs so it gets a larger cap; everything else stays small.
_LARGE_BODY_PATHS = ("/api/po", "/api/po/extract", "/api/vendors/upload")
_LARGE_BODY_MAX = 30 * 1024 * 1024   # 30 MB — fits two 10MB base64 PDFs + metadata
_SMALL_BODY_MAX = 64 * 1024          # 64 KB — every other JSON POST


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
        elif path.startswith("/api/tenders/") and path.endswith("/csv"):
            tender_id = path.split("/")[3]
            self._handle_csv_download(tender_id)
        elif path == "/api/tenders/count":
            counts = _run_async(db.count_tenders())
            self._json_response(counts)
        elif path == "/api/po":
            po_routes.handle_list(self, _run_async)
        elif path.startswith("/api/po/") and path.endswith("/docx"):
            # /api/po/<uuid>/docx
            parts = path.split("/")
            if len(parts) >= 5:
                po_routes.handle_download(self, _run_async, parts[3])
            else:
                self._json_response({"error": "invalid path"}, 400)
        elif path == "/api/vendors":
            vendor_routes.handle_list(self, _run_async, params)
        elif path.startswith("/api/vendors/"):
            # /api/vendors/<uuid> — exactly 4 segments
            parts = path.split("/")
            if len(parts) == 4 and parts[3]:
                vendor_routes.handle_get(self, _run_async, parts[3])
            else:
                self._json_response({"error": "invalid path"}, 400)
        elif path == "/api/rfp-categories":
            vendor_routes.handle_list_categories(self, _run_async)
        elif path == "/api/health":
            self._json_response({"status": "ok"})
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Enforce a per-path body-size ceiling before reading anything off the
        # socket. Without this, a malicious Content-Length header (e.g. 10 GB)
        # would cause rfile.read() to allocate enough memory to OOM the server.
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            content_length = 0
        body_cap = _LARGE_BODY_MAX if path in _LARGE_BODY_PATHS else _SMALL_BODY_MAX
        if content_length > body_cap:
            mb = content_length / (1024 * 1024)
            cap_mb = body_cap / (1024 * 1024)
            self._json_response(
                {"error": f"request body too large ({mb:.1f} MB; max {cap_mb:.0f} MB)"},
                413,
            )
            return

        # PO extract uses multipart — handle before the JSON-parsing block below.
        if path == "/api/po/extract":
            po_routes.handle_extract(self, _run_async)
            return

        # Vendor upload uses multipart (xlsx file) — handle before JSON parsing.
        if path == "/api/vendors/upload":
            vendor_routes.handle_upload(self, _run_async)
            return

        try:
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
            elif path.startswith("/api/tenders/") and path.endswith("/reassign"):
                tender_id = int(path.split("/")[3])
                new_associate = body.get("associate", "")
                self._handle_reassign(tender_id, new_associate)
            elif path.startswith("/api/tenders/") and path.endswith("/pending"):
                tender_id = int(path.split("/")[3])
                self._handle_revert_pending(tender_id)
            elif path.startswith("/api/tenders/") and path.endswith("/submit"):
                tender_id = int(path.split("/")[3])
                self._handle_submit(tender_id)
            elif path == "/api/tenders/bulk-accept":
                ids = body.get("ids", [])
                self._handle_bulk_accept(ids)
            elif path == "/api/tenders/submit-accepted":
                self._handle_submit_all_accepted()
            elif path == "/api/po":
                po_routes.handle_generate(self, _run_async, body)
            elif path == "/api/vendors":
                vendor_routes.handle_create(self, _run_async, body)
            else:
                self._json_response({"error": "not found"}, 404)
        except (ValueError, TypeError, IndexError):
            self._json_response({"error": "invalid request"}, 400)

    def do_PUT(self):
        """Handle PUT requests. Currently only /api/vendors/<uuid> is supported."""
        parsed = urlparse(self.path)
        path = parsed.path

        # Body-size cap (same scheme as do_POST — vendors PUT is JSON only).
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            content_length = 0
        if content_length > _SMALL_BODY_MAX:
            mb = content_length / (1024 * 1024)
            cap_kb = _SMALL_BODY_MAX // 1024
            self._json_response(
                {"error": f"request body too large ({mb:.2f} MB; max {cap_kb} KB)"},
                413,
            )
            return

        try:
            body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "invalid JSON body"}, 400)
            return

        if path.startswith("/api/vendors/"):
            parts = path.split("/")
            # /api/vendors/<uuid> — exactly 4 segments
            if len(parts) == 4 and parts[3]:
                vendor_routes.handle_update(self, _run_async, parts[3], body)
                return

        self._json_response({"error": "not found"}, 404)

    def do_DELETE(self):
        """Handle DELETE requests. /api/po/<uuid> and /api/vendors/<uuid>."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/po/"):
            parts = path.split("/")
            # /api/po/<uuid> — exactly 4 segments (empty, api, po, uuid)
            if len(parts) == 4 and parts[3]:
                po_routes.handle_delete(self, _run_async, parts[3])
                return

        if path.startswith("/api/vendors/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[3]:
                vendor_routes.handle_delete(self, _run_async, parts[3])
                return

        self._json_response({"error": "not found"}, 404)

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

    def _handle_csv_download(self, tender_id):
        """Serve the requirements CSV for a tender as a downloadable file."""
        try:
            tender = _run_async(db.get_tender(int(tender_id)))
            if not tender:
                self._json_response({"error": "not found"}, 404)
                return
            csv_content = tender.get("requirements_csv", "")
            if not csv_content:
                self._json_response({"error": "no CSV available for this tender"}, 404)
                return
            sol_no = tender.get("solicitation_no", "tender")
            safe_name = "".join(c for c in sol_no if c.isalnum() or c in "-_")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}_requirements.csv"')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(csv_content.encode("utf-8"))
        except (ValueError, TypeError):
            self._json_response({"error": "invalid id"}, 400)

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

    def _handle_reassign(self, tender_id, new_associate):
        if not new_associate:
            self._json_response({"error": "associate name required"}, 400)
            return
        result = _run_async(db.reassign_tender(tender_id, new_associate))
        if result:
            self._json_response({"status": "reassigned", "id": tender_id, "associate": new_associate})
        else:
            self._json_response({"error": "not found or not accepted"}, 400)

    def _handle_revert_pending(self, tender_id):
        result = _run_async(db.revert_to_pending(tender_id))
        if result:
            self._json_response({"status": "pending_review", "id": tender_id})
        else:
            self._json_response({"error": "not found or cannot revert (submitted tenders are final)"}, 400)

    def _handle_bulk_accept(self, ids):
        accepted = 0
        for tid in ids:
            result = _run_async(db.accept_tender(int(tid)))
            if result:
                accepted += 1
        self._json_response({"accepted": accepted, "total": len(ids)})

    def _handle_submit(self, tender_id):
        """Submit a single accepted tender to CFlow."""
        try:
            from submit import submit_tender
            from config import Config
            from cflow_client import CFlowClient
            config = Config.load()
            cflow = CFlowClient(config.cflow)
            success = _run_async(submit_tender(tender_id, cflow))
            if success:
                self._json_response({"status": "submitted", "id": tender_id})
            else:
                self._json_response({"error": "submission failed"}, 500)
        except Exception as exc:
            log.error("Submit failed for %d: %s", tender_id, exc)
            self._json_response({"error": str(exc)}, 500)

    def _handle_submit_all_accepted(self):
        """Submit all accepted tenders to CFlow."""
        try:
            from submit import submit_all_accepted
            from config import Config
            from cflow_client import CFlowClient
            config = Config.load()
            cflow = CFlowClient(config.cflow)
            counts = _run_async(submit_all_accepted(cflow))
            self._json_response(counts)
        except Exception as exc:
            log.error("Submit all failed: %s", exc)
            self._json_response({"error": str(exc)}, 500)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
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

"""
Vendor routes — HTTP handlers for /api/vendors/* and /api/rfp-categories.

Mirrors po_routes.py's shape. Each handler takes the request handler instance
(for response helpers) plus parsed inputs and writes the response. The api.py
dispatcher decides routing.

Endpoints:
  POST   /api/vendors/upload       multipart `file` (xlsx) → 200 {counts, warnings}
  GET    /api/vendors              → 200 [list], optional ?q= ?category=
  GET    /api/vendors/<uuid>       → 200 vendor row with embedded products
  POST   /api/vendors              JSON: vendor payload → 201 (manual create)
  PUT    /api/vendors/<uuid>       JSON: vendor payload → 200 (update; display-only fields ignored)
  DELETE /api/vendors/<uuid>       → 204 (removed) or 404 (not found)
  GET    /api/rfp-categories       → 200 [list]
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import db
import po_routes        # reuse _parse_multipart, MultipartError
import vendor_parser

log = logging.getLogger(__name__)

# Hard ceiling on the xlsx upload body. The 289-row source file is ~230 KB;
# 15 MB is comfortable headroom while still capping a malicious oversize.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


# ── Upload ───────────────────────────────────────────────────────────────────

def handle_upload(handler, _run_async) -> None:
    """POST /api/vendors/upload — multipart with a `file` xlsx field."""
    try:
        content_length = int(handler.headers.get("Content-Length", 0))
    except (TypeError, ValueError):
        handler._json_response({"error": "missing or invalid Content-Length"}, 411)
        return

    if content_length <= 0:
        handler._json_response({"error": "empty request body"}, 400)
        return
    if content_length > MAX_UPLOAD_BYTES:
        handler._json_response(
            {"error": f"upload too large (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)"},
            413,
        )
        return

    body = handler.rfile.read(content_length)
    content_type = handler.headers.get("Content-Type", "")

    try:
        parts = po_routes._parse_multipart(body, content_type)
    except po_routes.MultipartError as exc:
        handler._json_response({"error": str(exc)}, 400)
        return

    file_part = parts.get("file") or parts.get("xlsx") or parts.get("workbook")
    if not file_part:
        handler._json_response(
            {"error": "Multipart field `file` (xlsx) is required."},
            400,
        )
        return

    xlsx_bytes = file_part.get("data") or b""
    filename = file_part.get("filename") or "upload.xlsx"

    # Magic-byte sanity check: xlsx files are ZIP archives, start with "PK".
    if not xlsx_bytes.startswith(b"PK"):
        handler._json_response(
            {"error": "File does not look like an xlsx (expected ZIP/PK header)."},
            400,
        )
        return

    try:
        parsed = vendor_parser.parse_workbook(xlsx_bytes)
    except vendor_parser.VendorParseError as exc:
        log.warning("vendor.upload.parse_error file=%r err=%s", filename, exc)
        handler._json_response({"error": str(exc)}, 400)
        return
    except Exception as exc:  # last-resort safety
        log.exception("vendor.upload.unexpected_parse_error file=%r: %s", filename, exc)
        handler._json_response(
            {"error": "Unexpected error reading workbook. Please check the file."},
            500,
        )
        return

    try:
        counts = _run_async(db.merge_vendor_upload(
            parsed.vendors, parsed.categories, parsed.products,
        ))
    except Exception as exc:
        log.exception("vendor.upload.db_error: %s", exc)
        handler._json_response({"error": f"Could not save: {exc}"}, 500)
        return

    log.info(
        "vendor.upload.ok file=%r vendors=%d categories=%d products=%d warnings=%d",
        filename, len(parsed.vendors), len(parsed.categories),
        len(parsed.products), len(parsed.warnings),
    )

    handler._json_response({
        "filename": filename,
        "counts": counts,
        "warnings": parsed.warnings,
    })


# ── Vendor CRUD ──────────────────────────────────────────────────────────────

def handle_list(handler, _run_async, params: dict) -> None:
    """GET /api/vendors — filterable list."""
    q = params.get("q", [""])[0] or ""
    category = params.get("category", [""])[0] or ""
    try:
        limit = int(params.get("limit", ["200"])[0])
        offset = int(params.get("offset", ["0"])[0])
    except (TypeError, ValueError):
        handler._json_response({"error": "invalid limit/offset"}, 400)
        return

    rows = _run_async(db.list_vendors(
        q=q, category=category, limit=limit, offset=offset,
    ))
    handler._json_response(rows)


def handle_get(handler, _run_async, vendor_uuid: str) -> None:
    """GET /api/vendors/<uuid> — single vendor with embedded products."""
    try:
        vendor = _run_async(db.get_vendor_by_uuid(vendor_uuid))
    except Exception as exc:
        log.warning("vendor.get.bad_uuid uuid=%r err=%s", vendor_uuid, exc)
        handler._json_response({"error": "invalid uuid"}, 400)
        return
    if not vendor:
        handler._json_response({"error": "vendor not found"}, 404)
        return
    handler._json_response(vendor)


def handle_create(handler, _run_async, body: dict) -> None:
    """POST /api/vendors — manual create."""
    company = (body.get("company") or "").strip() if isinstance(body, dict) else ""
    if not company:
        handler._json_response({"error": "company is required"}, 400)
        return

    try:
        row = _run_async(db.insert_vendor(body, source="manual"))
    except Exception as exc:
        # asyncpg UniqueViolationError → 409 conflict on duplicate company.
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            handler._json_response(
                {"error": f"vendor '{company}' already exists"},
                409,
            )
            return
        log.exception("vendor.create.error: %s", exc)
        handler._json_response({"error": f"could not create: {exc}"}, 500)
        return

    handler._json_response(row, 201)


def handle_update(handler, _run_async, vendor_uuid: str, body: dict) -> None:
    """PUT /api/vendors/<uuid> — partial update. Display-only fields ignored."""
    if not isinstance(body, dict):
        handler._json_response({"error": "JSON body required"}, 400)
        return

    try:
        row = _run_async(db.update_vendor(vendor_uuid, body))
    except Exception as exc:
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            handler._json_response(
                {"error": "another vendor with that company name already exists"},
                409,
            )
            return
        log.exception("vendor.update.error: %s", exc)
        handler._json_response({"error": f"could not update: {exc}"}, 500)
        return

    if not row:
        handler._json_response({"error": "vendor not found"}, 404)
        return
    handler._json_response(row)


def handle_delete(handler, _run_async, vendor_uuid: str) -> None:
    """DELETE /api/vendors/<uuid> — cascades to products via FK."""
    try:
        removed = _run_async(db.delete_vendor(vendor_uuid))
    except Exception as exc:
        log.warning("vendor.delete.error uuid=%r: %s", vendor_uuid, exc)
        handler._json_response({"error": "invalid uuid"}, 400)
        return

    if not removed:
        handler._json_response({"error": "vendor not found"}, 404)
        return

    # 204 No Content — mirrors delete_po precedent.
    handler.send_response(204)
    handler._cors_headers()
    handler.end_headers()


# ── RFP Categories ──────────────────────────────────────────────────────────

def handle_list_categories(handler, _run_async) -> None:
    """GET /api/rfp-categories — list (read-only on v1)."""
    rows = _run_async(db.list_rfp_categories())
    handler._json_response(rows)

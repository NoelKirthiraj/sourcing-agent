"""
PO routes — HTTP handlers for /api/po/*.

Designed to be called from api.py's BaseHTTPRequestHandler. Each handler takes
the request handler instance (for response helpers) plus parsed inputs and
writes the response. The api.py dispatcher decides routing.

Endpoints:
  POST   /api/po/extract        multipart: contract, quote → 200 {draft, warnings}
  POST   /api/po                JSON: {draft, contract_pdf_b64?, quote_pdf_b64?, ...}
                                → 201 {id, uuid, po_number, revision, download_url}
  GET    /api/po                → 200 [list of POs]
  GET    /api/po/<uuid>/docx    → 200 DOCX bytes with Content-Disposition
  DELETE /api/po/<uuid>         → 204 (removed) or 404 (not found)
"""
from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import db
import po_extractor
import po_reconciler
import po_renderer

log = logging.getLogger(__name__)

# Multipart cap is a hard ceiling on the whole request body (both files plus
# form fields). Two 10MB PDFs + metadata fits in 25MB.
MAX_REQUEST_BYTES = 25 * 1024 * 1024


class MultipartError(Exception):
    """Raised on malformed multipart upload. Caught and returned as 400."""


# ── Minimal multipart parser ─────────────────────────────────────────────────

def _parse_multipart(body: bytes, content_type: str) -> dict[str, dict[str, Any]]:
    """Parse a multipart/form-data body. Returns {field_name: {filename, content_type, data}}.

    Only supports what we actually need: no nested multiparts, no
    Content-Transfer-Encoding decoding. File-and-text fields only.

    Raises MultipartError on malformed input.
    """
    if not content_type or "multipart/form-data" not in content_type:
        raise MultipartError("Content-Type must be multipart/form-data")

    boundary_marker = "boundary="
    idx = content_type.find(boundary_marker)
    if idx == -1:
        raise MultipartError("Missing multipart boundary")
    boundary = content_type[idx + len(boundary_marker):].strip()
    # Strip optional quotes
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]
    if not boundary:
        raise MultipartError("Empty multipart boundary")

    delim = b"--" + boundary.encode("ascii")
    parts = body.split(delim)
    # The first chunk before the first boundary is preamble, the last after the
    # closing "--" boundary is epilogue. Both are discarded.
    result: dict[str, dict[str, Any]] = {}
    for part in parts[1:-1]:
        # Each part starts with CRLF after the boundary
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        # Split headers from body on the first blank line
        try:
            header_blob, payload = part.split(b"\r\n\r\n", 1)
        except ValueError:
            continue

        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            if b":" not in line:
                continue
            k, _, v = line.decode("utf-8", errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()

        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition:
            continue
        name = _parse_dispo_param(disposition, "name")
        if not name:
            continue
        filename = _parse_dispo_param(disposition, "filename") or ""
        ctype = headers.get("content-type", "")
        result[name] = {
            "filename": filename,
            "content_type": ctype,
            "data": payload,
        }
    return result


def _parse_dispo_param(disposition: str, key: str) -> Optional[str]:
    """Pull a parameter (e.g. `name`, `filename`) out of a Content-Disposition header."""
    for token in disposition.split(";"):
        token = token.strip()
        if token.lower().startswith(key.lower() + "="):
            value = token.split("=", 1)[1].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            return value
    return None


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_extract(handler, _run_async) -> None:
    """POST /api/po/extract — multipart with `contract` and `quote` PDF fields.

    Optional form fields:
      - tender_id  (integer, links the eventual PO to a tender record)
    """
    try:
        content_length = int(handler.headers.get("Content-Length", 0))
    except (TypeError, ValueError):
        handler._json_response({"error": "missing or invalid Content-Length"}, 411)
        return

    if content_length <= 0:
        handler._json_response({"error": "empty request body"}, 400)
        return
    if content_length > MAX_REQUEST_BYTES:
        handler._json_response(
            {"error": f"upload too large (max {MAX_REQUEST_BYTES // (1024*1024)} MB total)"},
            413,
        )
        return

    body = handler.rfile.read(content_length)
    content_type = handler.headers.get("Content-Type", "")

    try:
        parts = _parse_multipart(body, content_type)
    except MultipartError as exc:
        handler._json_response({"error": str(exc)}, 400)
        return

    contract_part = parts.get("contract")
    quote_part = parts.get("quote")
    if not contract_part or not quote_part:
        handler._json_response(
            {"error": "Both `contract` and `quote` PDF files are required."},
            400,
        )
        return

    tender_id: Optional[int] = None
    tender_part = parts.get("tender_id")
    if tender_part:
        try:
            tender_id = int((tender_part.get("data") or b"").decode("utf-8").strip())
            if tender_id <= 0:
                tender_id = None
        except (TypeError, ValueError):
            tender_id = None

    contract_bytes = contract_part["data"]
    quote_bytes = quote_part["data"]
    contract_name = contract_part.get("filename") or "contract.pdf"
    quote_name = quote_part.get("filename") or "quote.pdf"

    # Contract and quote extractions are independent Claude calls that used to
    # run serially (~4.5 min contract + ~1.5 min quote = ~6 min total on real
    # POs). Run them concurrently — the SDK is blocking I/O so threads are
    # sufficient — cutting wall-clock latency to max(contract, quote).
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            contract_future = pool.submit(
                po_extractor.extract_contract, contract_bytes, contract_name,
            )
            quote_future = pool.submit(
                po_extractor.extract_quote, quote_bytes, quote_name,
            )
            contract_result = contract_future.result()
            quote_result = quote_future.result()
    except po_extractor.PoExtractionError as exc:
        log.warning("po.extract.failed: %s", exc.user_message)
        handler._json_response({"error": exc.user_message}, exc.status)
        return
    except Exception as exc:  # last-resort safety net
        log.exception("po.extract.unexpected_error: %s", exc)
        handler._json_response(
            {"error": "Unexpected extraction failure. Please try again."},
            500,
        )
        return

    reconciled = po_reconciler.reconcile(
        contract_result.data, quote_result.data, tender_id=tender_id,
    )

    warnings = (
        list(contract_result.warnings)
        + list(quote_result.warnings)
        + list(reconciled.warnings)
    )

    handler._json_response({
        "draft": reconciled.draft,
        "warnings": warnings,
        "extracted_contract": contract_result.data,
        "extracted_quote": quote_result.data,
        "contract_b64": base64.standard_b64encode(contract_bytes).decode("ascii"),
        "quote_b64": base64.standard_b64encode(quote_bytes).decode("ascii"),
        "contract_filename": contract_name,
        "quote_filename": quote_name,
    })


def handle_generate(handler, _run_async, body: dict[str, Any]) -> None:
    """POST /api/po — render + persist. Expects:

    {
      "draft": {...},                  # required, validated
      "extracted_contract": {...},     # optional, stored for audit
      "extracted_quote": {...},        # optional, stored for audit
      "contract_b64": "...",           # optional source-of-truth bytes
      "quote_b64": "...",
      "contract_filename": "...",
      "quote_filename": "...",
      "warnings": [...]                # warnings the user acknowledged
    }
    """
    draft = body.get("draft")
    if not isinstance(draft, dict):
        handler._json_response({"error": "`draft` (object) is required"}, 400)
        return

    errors = po_reconciler.validate_draft_math(draft)
    if errors:
        handler._json_response({"error": "Draft is invalid", "details": errors}, 400)
        return

    try:
        rendered_docx = po_renderer.render(draft)
    except RuntimeError as exc:
        log.error("po.render.dependency_missing: %s", exc)
        handler._json_response({"error": str(exc)}, 500)
        return
    except Exception as exc:
        log.exception("po.render.unexpected_error: %s", exc)
        handler._json_response(
            {"error": "Rendering failed. Please contact admin."},
            500,
        )
        return

    contract_bytes = _decode_b64(body.get("contract_b64"))
    quote_bytes = _decode_b64(body.get("quote_b64"))

    try:
        row = _run_async(db.insert_po(
            po_number=draft["po_number"],
            draft=draft,
            extracted_contract=body.get("extracted_contract") or {},
            extracted_quote=body.get("extracted_quote") or {},
            contract_pdf=contract_bytes,
            quote_pdf=quote_bytes,
            contract_filename=body.get("contract_filename", ""),
            quote_filename=body.get("quote_filename", ""),
            rendered_docx=rendered_docx,
            warnings=body.get("warnings") or [],
            tender_id=draft.get("tender_id"),
        ))
    except Exception as exc:
        log.exception("po.persist.error: %s", exc)
        handler._json_response(
            {"error": f"Could not save PO: {exc}"},
            500,
        )
        return

    handler._json_response({
        "id": row["id"],
        "uuid": str(row["uuid"]),
        "po_number": row["po_number"],
        "revision": row["revision"],
        "download_url": f"/api/po/{row['uuid']}/docx",
    }, 201)


def handle_list(handler, _run_async) -> None:
    """GET /api/po — list of generated POs (newest first)."""
    rows = _run_async(db.list_pos(limit=100))
    for r in rows:
        # Iterate over a snapshot of keys — we mutate r values during the loop.
        for k in list(r):
            v = r[k]
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif hasattr(v, "hex") and not isinstance(v, (bytes, str, int, float, list, dict, bool)):
                # asyncpg UUID has .hex; stringify
                r[k] = str(v)
        # JSONB columns come back as dicts/lists already; warnings is a list.
        # Money fields: expose dollars rather than cents.
        r["subtotal"] = (r.pop("subtotal_cents", 0) or 0) / 100
        r["total"] = (r.pop("total_cents", 0) or 0) / 100
        r["download_url"] = f"/api/po/{r['uuid']}/docx"
    handler._json_response(rows)


def handle_download(handler, _run_async, po_uuid: str) -> None:
    """GET /api/po/<uuid>/docx — stream the DOCX bytes."""
    try:
        result = _run_async(db.get_po_docx(po_uuid))
    except Exception as exc:
        log.warning("po.download.error: %s", exc)
        handler._json_response({"error": "invalid UUID"}, 400)
        return

    if not result:
        handler._json_response({"error": "PO not found"}, 404)
        return

    po_number, docx_bytes = result
    safe = _sanitize_filename(po_number) + ".docx"

    handler.send_response(200)
    handler.send_header(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    handler.send_header("Content-Disposition", f'attachment; filename="{safe}"')
    handler.send_header("Content-Length", str(len(docx_bytes)))
    handler._cors_headers()
    handler.end_headers()
    handler.wfile.write(docx_bytes)


def handle_delete(handler, _run_async, po_uuid: str) -> None:
    """DELETE /api/po/<uuid> — hard-delete a PO. Returns 204 on success,
    404 if the UUID didn't match a row, 400 if the UUID is malformed."""
    try:
        removed = _run_async(db.delete_po(po_uuid))
    except Exception as exc:  # asyncpg InvalidTextRepresentation, etc.
        log.warning("po.delete.error: %s", exc)
        handler._json_response({"error": "invalid UUID"}, 400)
        return

    if not removed:
        handler._json_response({"error": "PO not found"}, 404)
        return

    log.info("po.delete.ok uuid=%s", po_uuid)
    handler.send_response(204)
    handler._cors_headers()
    handler.end_headers()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_b64(value: Any) -> Optional[bytes]:
    if not value or not isinstance(value, str):
        return None
    try:
        return base64.standard_b64decode(value)
    except (ValueError, TypeError):
        return None


def _sanitize_filename(name: str) -> str:
    """Allow alnum, dash, underscore, dot. Replace everything else with '-'."""
    out = []
    for ch in name or "PO":
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        else:
            out.append("-")
    sanitized = "".join(out).strip("-") or "PO"
    return sanitized[:80]

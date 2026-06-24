"""Unit tests for vendor_routes — HTTP handlers (no real server).

Builds a minimal mock `handler` and asserts the JSON shape and status
codes returned for each path. Database calls are stubbed via monkeypatch
of the `db` module.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Mock handler + run_async (sync wrapper around coroutines) ────────────────

def _make_handler(*, headers: dict | None = None, body: bytes = b""):
    """Build a minimal handler mock satisfying the surface vendor_routes uses."""
    h = MagicMock()
    h.headers = headers or {}
    h.rfile = io.BytesIO(body)
    h.responses = []  # captured (data, status) pairs
    h.wfile = io.BytesIO()
    h._cors_called = 0

    def json_response(data, status=200):
        h.responses.append((data, status))
    h._json_response = json_response

    def cors():
        h._cors_called += 1
    h._cors_headers = cors

    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


def _run_passthrough(coro):
    """Drive a coroutine to completion (stand-in for api._run_async).

    Stubbed db.* functions in these tests are still `async def`, so calling
    them returns a coroutine that needs awaiting. Use a fresh event loop
    per call so we don't conflict with pytest-asyncio's loop management.
    """
    if asyncio.iscoroutine(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return coro


# ── handle_list ──────────────────────────────────────────────────────────────

def test_handle_list_no_filters_calls_db_with_defaults(monkeypatch):
    import vendor_routes, db
    captured = {}

    async def fake(*, q="", category="", limit=200, offset=0):
        captured.update(q=q, category=category, limit=limit, offset=offset)
        return [{"company": "Acme"}]
    monkeypatch.setattr(db, "list_vendors", fake)

    h = _make_handler()
    vendor_routes.handle_list(h, _run_passthrough, {})
    assert h.responses == [([{"company": "Acme"}], 200)]
    assert captured == {"q": "", "category": "", "limit": 200, "offset": 0}


def test_handle_list_with_q_and_category(monkeypatch):
    import vendor_routes, db
    captured = {}
    async def fake(**kw):
        captured.update(kw)
        return []
    monkeypatch.setattr(db, "list_vendors", fake)

    h = _make_handler()
    params = {"q": ["acme"], "category": ["Aerospace/Aircraft"], "limit": ["50"]}
    vendor_routes.handle_list(h, _run_passthrough, params)
    assert captured["q"] == "acme"
    assert captured["category"] == "Aerospace/Aircraft"
    assert captured["limit"] == 50


def test_handle_list_bad_limit_returns_400(monkeypatch):
    import vendor_routes, db
    monkeypatch.setattr(db, "list_vendors", lambda **kw: [])
    h = _make_handler()
    vendor_routes.handle_list(h, _run_passthrough, {"limit": ["not-a-number"]})
    assert h.responses[0][1] == 400


# ── handle_get ───────────────────────────────────────────────────────────────

def test_handle_get_404_when_not_found(monkeypatch):
    import vendor_routes, db
    async def fake(uuid):
        return None
    monkeypatch.setattr(db, "get_vendor_by_uuid", fake)
    h = _make_handler()
    vendor_routes.handle_get(h, _run_passthrough, "abc")
    assert h.responses[0][1] == 404


def test_handle_get_returns_vendor(monkeypatch):
    import vendor_routes, db
    async def fake(uuid):
        return {"uuid": "abc", "company": "Acme"}
    monkeypatch.setattr(db, "get_vendor_by_uuid", fake)
    h = _make_handler()
    vendor_routes.handle_get(h, _run_passthrough, "abc")
    assert h.responses[0] == ({"uuid": "abc", "company": "Acme"}, 200)


# ── handle_create ────────────────────────────────────────────────────────────

def test_handle_create_requires_company():
    import vendor_routes
    h = _make_handler()
    vendor_routes.handle_create(h, _run_passthrough, {})
    assert h.responses[0][1] == 400


def test_handle_create_201_on_success(monkeypatch):
    import vendor_routes, db
    async def fake(payload, *, source="manual"):
        assert source == "manual"
        return {"uuid": "abc", "company": payload["company"]}
    monkeypatch.setattr(db, "insert_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_create(h, _run_passthrough, {"company": "New Co"})
    assert h.responses[0][1] == 201
    assert h.responses[0][0]["company"] == "New Co"


def test_handle_create_409_on_duplicate(monkeypatch):
    import vendor_routes, db
    async def fake(payload, *, source="manual"):
        # Simulate asyncpg.UniqueViolationError message
        raise Exception("duplicate key value violates unique constraint")
    monkeypatch.setattr(db, "insert_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_create(h, _run_passthrough, {"company": "Dup"})
    assert h.responses[0][1] == 409


# ── handle_update ────────────────────────────────────────────────────────────

def test_handle_update_rejects_non_dict_body():
    import vendor_routes
    h = _make_handler()
    vendor_routes.handle_update(h, _run_passthrough, "abc", "not-a-dict")
    assert h.responses[0][1] == 400


def test_handle_update_404_when_unknown_uuid(monkeypatch):
    import vendor_routes, db
    async def fake(uuid, payload):
        return None
    monkeypatch.setattr(db, "update_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_update(h, _run_passthrough, "abc", {"domain": "x.com"})
    assert h.responses[0][1] == 404


def test_handle_update_returns_updated_row(monkeypatch):
    import vendor_routes, db
    async def fake(uuid, payload):
        return {"uuid": uuid, "company": "X", "domain": payload["domain"]}
    monkeypatch.setattr(db, "update_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_update(h, _run_passthrough, "abc", {"domain": "new.io"})
    assert h.responses[0] == ({"uuid": "abc", "company": "X", "domain": "new.io"}, 200)


# ── handle_delete ────────────────────────────────────────────────────────────

def test_handle_delete_204_on_success(monkeypatch):
    import vendor_routes, db
    async def fake(uuid):
        return True
    monkeypatch.setattr(db, "delete_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_delete(h, _run_passthrough, "abc")
    # 204 sets status via send_response, not _json_response
    h.send_response.assert_called_with(204)


def test_handle_delete_404_when_missing(monkeypatch):
    import vendor_routes, db
    async def fake(uuid):
        return False
    monkeypatch.setattr(db, "delete_vendor", fake)

    h = _make_handler()
    vendor_routes.handle_delete(h, _run_passthrough, "abc")
    assert h.responses[0][1] == 404


# ── handle_upload ────────────────────────────────────────────────────────────

def _build_multipart(file_bytes: bytes, *, filename="vendors.xlsx",
                    field_name="file") -> tuple[bytes, str]:
    """Return (body, content_type_header) for a minimal multipart upload."""
    boundary = "----FormBoundaryXYZ"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        "\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    return body, ctype


def test_handle_upload_rejects_missing_content_length():
    import vendor_routes
    h = _make_handler(headers={})
    vendor_routes.handle_upload(h, _run_passthrough)
    # Either 411 (missing CL) or 400 (empty body) — both are documented
    assert h.responses[0][1] in (400, 411)


def test_handle_upload_rejects_wrong_field_name(monkeypatch):
    import vendor_routes
    # Put the xlsx under a wrong field name
    body, ctype = _build_multipart(b"PK\x03\x04dummy", field_name="not-file")
    h = _make_handler(
        headers={"Content-Length": str(len(body)), "Content-Type": ctype},
        body=body,
    )
    vendor_routes.handle_upload(h, _run_passthrough)
    assert h.responses[0][1] == 400
    assert "required" in h.responses[0][0]["error"].lower()


def test_handle_upload_rejects_non_xlsx_magic_bytes(monkeypatch):
    import vendor_routes
    # File missing PK header
    body, ctype = _build_multipart(b"<html>not an xlsx</html>")
    h = _make_handler(
        headers={"Content-Length": str(len(body)), "Content-Type": ctype},
        body=body,
    )
    vendor_routes.handle_upload(h, _run_passthrough)
    assert h.responses[0][1] == 400
    assert "xlsx" in h.responses[0][0]["error"].lower()


def test_handle_upload_success_path(monkeypatch):
    """End-to-end: real fixture, real parser, stubbed DB."""
    import vendor_routes, db, vendor_parser

    fixture = Path(__file__).parent.parent / "fixtures" / "vendors_mini.xlsx"
    body, ctype = _build_multipart(fixture.read_bytes())

    # Stub DB merge to record what it received without hitting Postgres
    captured = {}
    async def fake_merge(vendors, categories, products):
        captured["counts"] = {
            "vendors_inserted": len(vendors),
            "vendors_updated": 0,
            "categories_upserted": len(categories),
            "products_inserted": len(products),
            "products_skipped_orphan": 0,
        }
        return captured["counts"]
    monkeypatch.setattr(db, "merge_vendor_upload", fake_merge)

    h = _make_handler(
        headers={"Content-Length": str(len(body)), "Content-Type": ctype},
        body=body,
    )
    vendor_routes.handle_upload(h, _run_passthrough)

    assert h.responses[0][1] == 200
    resp = h.responses[0][0]
    assert resp["counts"]["vendors_inserted"] == 3
    assert resp["counts"]["categories_upserted"] == 2
    assert resp["counts"]["products_inserted"] == 4
    # Warnings include the orphan from the fixture
    assert any("unknown llc" in w.lower() for w in resp["warnings"])


# ── handle_list_categories ──────────────────────────────────────────────────

def test_handle_list_categories(monkeypatch):
    import vendor_routes, db
    async def fake():
        return [{"category": "Aerospace"}]
    monkeypatch.setattr(db, "list_rfp_categories", fake)

    h = _make_handler()
    vendor_routes.handle_list_categories(h, _run_passthrough)
    assert h.responses[0] == ([{"category": "Aerospace"}], 200)

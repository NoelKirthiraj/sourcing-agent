"""Tests for the async extract endpoints:
    POST /api/po/extract                     → 202 {job_id, status:'pending'}
    GET  /api/po/extract/status/<job_id>     → poll shape
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import po_jobs


@pytest.fixture(autouse=True)
def _clean_store():
    po_jobs._reset_for_tests()
    yield
    po_jobs._reset_for_tests()


def _make_handler(*, headers: dict | None = None, body: bytes = b""):
    h = MagicMock()
    h.headers = headers or {}
    h.rfile = io.BytesIO(body)
    h.responses = []

    def json_response(data, status=200):
        h.responses.append((data, status))
    h._json_response = json_response
    return h


def _passthrough(x):
    return x


# ── POST /api/po/extract kickoff ─────────────────────────────────────────────

def test_extract_kickoff_returns_202_with_job_id(monkeypatch):
    """The endpoint must return immediately (no waiting on Claude) with a
    job_id the client can poll on."""
    import po_extractor
    import po_reconciler
    import po_routes

    # Stub the extractors so the background thread finishes fast.
    monkeypatch.setattr(
        po_extractor, "extract_contract",
        lambda b, f="": po_extractor.ExtractionResult(data={"contract_no": "CW1"}, warnings=()),
    )
    monkeypatch.setattr(
        po_extractor, "extract_quote",
        lambda b, f="": po_extractor.ExtractionResult(data={"quote_no": "Q1"}, warnings=()),
    )
    monkeypatch.setattr(
        po_reconciler, "reconcile",
        lambda c, q, tender_id=None: MagicMock(
            draft={"po_number": "PO-1"}, warnings=[],
        ),
    )

    boundary = "----test-boundary"
    body = (
        f"--{boundary}\r\n".encode()
        + b'Content-Disposition: form-data; name="contract"; filename="c.pdf"\r\n'
        + b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\ncontract\r\n"
        + f"--{boundary}\r\n".encode()
        + b'Content-Disposition: form-data; name="quote"; filename="q.pdf"\r\n'
        + b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\nquote\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    h = _make_handler(
        headers={"Content-Length": str(len(body)),
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
        body=body,
    )

    po_routes.handle_extract(h, _passthrough)

    data, status = h.responses[0]
    assert status == 202
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], str)
    assert len(data["job_id"]) == 32  # uuid4 hex
    # NB: response must NOT include draft/warnings/etc. — those come from
    # the poll endpoint once the worker finishes.
    assert "draft" not in data


# ── GET /api/po/extract/status/<job_id> ──────────────────────────────────────

def test_status_returns_404_for_unknown_job():
    import po_routes
    h = _make_handler()
    po_routes.handle_extract_status(h, _passthrough, "unknown-job-id")
    data, status = h.responses[0]
    assert status == 404
    assert data["error"] == "job not found"


def test_status_returns_pending_before_worker_starts():
    import po_routes
    job_id = po_jobs.create()
    h = _make_handler()
    po_routes.handle_extract_status(h, _passthrough, job_id)
    data, status = h.responses[0]
    assert status == 200
    assert data["status"] == "pending"
    assert "elapsed" in data
    assert data["elapsed"] >= 0


def test_status_returns_running_while_worker_is_active():
    import po_routes
    job_id = po_jobs.create()
    po_jobs.mark_running(job_id)
    h = _make_handler()
    po_routes.handle_extract_status(h, _passthrough, job_id)
    data, status = h.responses[0]
    assert status == 200
    assert data["status"] == "running"


def test_status_returns_done_with_full_result_payload():
    import po_routes
    job_id = po_jobs.create()
    result = {
        "draft": {"po_number": "PO-123"},
        "warnings": ["something"],
        "extracted_contract": {"contract_no": "CW1"},
        "extracted_quote": {"quote_no": "Q1"},
        "contract_b64": "AAAA",
        "quote_b64": "BBBB",
        "contract_filename": "c.pdf",
        "quote_filename": "q.pdf",
    }
    po_jobs.mark_done(job_id, result)

    h = _make_handler()
    po_routes.handle_extract_status(h, _passthrough, job_id)
    data, status = h.responses[0]
    assert status == 200
    assert data["status"] == "done"
    # All result keys spread into the top level for client convenience.
    assert data["draft"] == {"po_number": "PO-123"}
    assert data["warnings"] == ["something"]
    assert data["contract_b64"] == "AAAA"
    assert data["quote_b64"] == "BBBB"


def test_status_returns_failed_with_error_and_error_status():
    import po_routes
    job_id = po_jobs.create()
    po_jobs.mark_failed(
        job_id,
        "Anthropic API credit balance is exhausted.",
        error_status=503,
    )

    h = _make_handler()
    po_routes.handle_extract_status(h, _passthrough, job_id)
    data, status = h.responses[0]
    # Even for failed jobs, the poll transport itself returns 200 so the
    # client can tell "job is done, and here's what happened" apart from
    # "the poll transport itself broke".
    assert status == 200
    assert data["status"] == "failed"
    assert "credit balance" in data["error"].lower()
    assert data["error_status"] == 503

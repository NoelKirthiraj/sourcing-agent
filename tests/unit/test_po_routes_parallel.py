"""Concurrency guard: the background /api/po/extract job must run contract
and quote extractions in parallel, not serially.

The extract endpoint no longer waits for the extraction to finish — it kicks
off a background thread and returns a job_id (see test_po_jobs.py for the
job store, and test_po_routes_async.py for the endpoint shape). But once
the background worker starts, the two Claude calls must still run
concurrently. These tests exercise the worker code path directly to
guarantee that.
"""
from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_handler(*, headers: dict, body: bytes):
    h = MagicMock()
    h.headers = headers
    h.rfile = io.BytesIO(body)
    h.responses = []

    def json_response(data, status=200):
        h.responses.append((data, status))
    h._json_response = json_response
    return h


def _multipart_body(contract: bytes, quote: bytes):
    boundary = "----test-boundary-xyz"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="contract"; filename="c.pdf"\r\n',
        b"Content-Type: application/pdf\r\n\r\n",
        contract,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="quote"; filename="q.pdf"\r\n',
        b"Content-Type: application/pdf\r\n\r\n",
        quote,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    ct = f"multipart/form-data; boundary={boundary}"
    return body, ct


def _passthrough(x):
    return x


def _canned_result(kind: str):
    import po_extractor
    if kind == "contract":
        data = {"contract_no": "CW1", "items": [], "delivery_date": ""}
    else:
        data = {"quote_no": "Q1", "supplier": {"name": "Acme"}, "items": [], "currency": "USD"}
    return po_extractor.ExtractionResult(data=data, warnings=())


def _wait_until_done(job_id, timeout=5.0):
    """Block the test thread until the job store marks the job done/failed."""
    import po_jobs
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = po_jobs.get(job_id)
        if job and job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach terminal state within {timeout}s")


def test_extract_worker_runs_contract_and_quote_in_parallel(monkeypatch):
    """If the two extractions run in parallel, both threads reach the barrier
    at the same time. If they run serially, the first one blocks the second
    forever and the barrier times out → BrokenBarrierError → test fails."""
    import po_extractor
    import po_jobs
    import po_reconciler
    import po_routes

    po_jobs._reset_for_tests()

    both_arrived = threading.Barrier(2, timeout=5)
    contract_arrived_at = []
    quote_arrived_at = []

    def fake_contract(pdf_bytes, filename=""):
        contract_arrived_at.append(time.monotonic())
        both_arrived.wait()
        return _canned_result("contract")

    def fake_quote(pdf_bytes, filename=""):
        quote_arrived_at.append(time.monotonic())
        both_arrived.wait()
        return _canned_result("quote")

    monkeypatch.setattr(po_extractor, "extract_contract", fake_contract)
    monkeypatch.setattr(po_extractor, "extract_quote", fake_quote)
    monkeypatch.setattr(
        po_reconciler, "reconcile",
        lambda c, q, tender_id=None: MagicMock(
            draft={"po_number": "PO-1"}, warnings=[],
        ),
    )

    body, ct = _multipart_body(b"%PDF-1.4\ncontract", b"%PDF-1.4\nquote")
    h = _make_handler(headers={"Content-Length": str(len(body)), "Content-Type": ct}, body=body)

    po_routes.handle_extract(h, _passthrough)

    # Kickoff response should be immediate with a job_id and 202.
    assert h.responses, "no response captured"
    kickoff_data, kickoff_status = h.responses[0]
    assert kickoff_status == 202
    assert kickoff_data["status"] == "pending"
    job_id = kickoff_data["job_id"]

    # Wait for the background worker to complete.
    job = _wait_until_done(job_id)
    assert job["status"] == "done"

    # Both fakes must have entered — this is what proves concurrency.
    assert len(contract_arrived_at) == 1
    assert len(quote_arrived_at) == 1
    assert abs(contract_arrived_at[0] - quote_arrived_at[0]) < 0.5


def test_extract_worker_wall_clock_is_max_not_sum(monkeypatch):
    """Timing check: two 250ms extractions must take ~250ms, not ~500ms."""
    import po_extractor
    import po_jobs
    import po_reconciler
    import po_routes

    po_jobs._reset_for_tests()

    def slow_contract(pdf_bytes, filename=""):
        time.sleep(0.25)
        return _canned_result("contract")

    def slow_quote(pdf_bytes, filename=""):
        time.sleep(0.25)
        return _canned_result("quote")

    monkeypatch.setattr(po_extractor, "extract_contract", slow_contract)
    monkeypatch.setattr(po_extractor, "extract_quote", slow_quote)
    monkeypatch.setattr(
        po_reconciler, "reconcile",
        lambda c, q, tender_id=None: MagicMock(
            draft={"po_number": "PO-1"}, warnings=[],
        ),
    )

    body, ct = _multipart_body(b"%PDF-1.4\ncontract", b"%PDF-1.4\nquote")
    h = _make_handler(headers={"Content-Length": str(len(body)), "Content-Type": ct}, body=body)

    t0 = time.monotonic()
    po_routes.handle_extract(h, _passthrough)
    job_id = h.responses[0][0]["job_id"]
    job = _wait_until_done(job_id, timeout=2.0)
    elapsed = time.monotonic() - t0

    assert job["status"] == "done"
    assert elapsed < 0.5, (
        f"extract worker took {elapsed:.2f}s for two 0.25s extractions — "
        f"suggests serial execution. Expected ~0.25s if parallel."
    )


def test_extract_worker_records_extraction_error(monkeypatch):
    """When one worker raises PoExtractionError, the job store must record
    it with the correct error_status so the poll endpoint can surface it."""
    import po_extractor
    import po_jobs
    import po_reconciler
    import po_routes

    po_jobs._reset_for_tests()

    def good_contract(pdf_bytes, filename=""):
        return _canned_result("contract")

    def failing_quote(pdf_bytes, filename=""):
        raise po_extractor.PoExtractionError(
            "Anthropic API credit balance is exhausted.",
            status=503,
        )

    monkeypatch.setattr(po_extractor, "extract_contract", good_contract)
    monkeypatch.setattr(po_extractor, "extract_quote", failing_quote)
    monkeypatch.setattr(
        po_reconciler, "reconcile",
        lambda c, q, tender_id=None: MagicMock(draft={}, warnings=[]),
    )

    body, ct = _multipart_body(b"%PDF-1.4\ncontract", b"%PDF-1.4\nquote")
    h = _make_handler(headers={"Content-Length": str(len(body)), "Content-Type": ct}, body=body)

    po_routes.handle_extract(h, _passthrough)
    job_id = h.responses[0][0]["job_id"]
    job = _wait_until_done(job_id)

    assert job["status"] == "failed"
    assert "credit balance" in job["error"].lower()
    assert job["error_status"] == 503

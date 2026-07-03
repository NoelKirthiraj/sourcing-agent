"""Concurrency guard: /api/po/extract must run contract and quote extractions
in parallel, not serially.

The extractions are independent Claude vision calls that used to run one
after the other (~4.5min contract + ~1.5min quote = ~6min total on real POs)
and now run concurrently on a ThreadPoolExecutor.

Both tests here would time out (hang) if the code regressed to serial
execution — that's their point.
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
    """Minimal shape that satisfies po_reconciler.reconcile() and the JSON encoder."""
    import po_extractor
    if kind == "contract":
        data = {"contract_no": "CW1", "items": [], "delivery_date": ""}
    else:
        data = {"quote_no": "Q1", "supplier": {"name": "Acme"}, "items": [], "currency": "USD"}
    return po_extractor.ExtractionResult(data=data, warnings=())


def test_handle_extract_runs_contract_and_quote_in_parallel(monkeypatch):
    """If the two extractions run in parallel, both threads reach their
    barrier at the same time. If they run serially, the first one blocks
    the second forever and the test times out (fails)."""
    import po_extractor
    import po_reconciler
    import po_routes

    both_arrived = threading.Barrier(2, timeout=5)
    contract_arrived_at = []
    quote_arrived_at = []

    def fake_contract(pdf_bytes, filename=""):
        contract_arrived_at.append(time.monotonic())
        both_arrived.wait()  # Times out (raises BrokenBarrierError) if quote never arrives
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

    # Both must have entered their extraction functions — proves concurrency.
    assert len(contract_arrived_at) == 1
    assert len(quote_arrived_at) == 1
    # They should have entered within a very small window (both threads
    # dispatched immediately, no waiting for each other).
    assert abs(contract_arrived_at[0] - quote_arrived_at[0]) < 0.5

    # Response was captured
    assert h.responses, "no response captured"
    data, status = h.responses[0]
    assert status == 200
    assert "draft" in data


def test_handle_extract_wall_clock_is_max_not_sum(monkeypatch):
    """Timing-based check: with two 250ms extractions, serial takes >=500ms;
    parallel takes ~250ms. Assert wall time < 400ms so we catch a serial
    regression without being flaky on slow CI."""
    import po_extractor
    import po_reconciler
    import po_routes

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
    elapsed = time.monotonic() - t0

    assert elapsed < 0.4, (
        f"handle_extract took {elapsed:.2f}s for two 0.25s extractions — "
        f"suggests serial execution. Expected ~0.25s if parallel."
    )


def test_handle_extract_propagates_extraction_error(monkeypatch):
    """When one of the two parallel calls raises PoExtractionError, the
    handler must surface the error message + status, not swallow it."""
    import po_extractor
    import po_reconciler
    import po_routes

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

    assert h.responses, "no response captured"
    data, status = h.responses[0]
    assert status == 503
    assert "credit balance" in data["error"].lower()

"""Renderer smoke test — produces a real DOCX from a known-good draft and
verifies the bytes parse back as a valid Office Open XML document with the
key fields present.
"""
from __future__ import annotations

import io
import zipfile

import pytest

import po_renderer
import po_reconciler


def _golden_draft() -> dict:
    contract = {
        "contract_no": "W8485-258676.002",
        "contract_date": "2025-09-16",
        "title": "20 ton Jack Parts",
        "currency": "USD",
        "delivery_date": "31 March 2026",
        "delivery_address": {"name": "TRONAIR", "lines": ["1 Air Cargo Pkwy", "Swanton, OH"]},
        "items": [{
            "line": 5, "nsn": "1730-21-891-4983", "part_no": "T854700", "ncage": "94861",
            "description": "TEST FIXTURE KIT", "qty": 2, "unit": "EA",
            "unit_price": 24959.55, "extended": 49919.10,
            "delivery_lead_time": "20 weeks ARO",
        }],
        "flow_down_clauses": [
            "Delivery on or before 31 March 2026",
            "Packaging per D-LM-008-036/SF-000",
        ],
    }
    quote = {
        "quote_no": "420291",
        "supplier": {"name": "Tronair", "lines": ["1 Air Cargo Pkwy", "Swanton, OH 43558"]},
        "currency": "USD",
        "items": [{
            "line": 5, "part_no": "T854700", "description": "MALABAR TEST FIXTURE",
            "qty": 2, "unit": "EA", "unit_price": 23771.00, "extended": 47542.00,
            "lead_time": "20 to 22 weeks",
        }],
        "incoterms": "FOB FACTORY, SWANTON, OH",
        "payment_terms": "CASH IN ADVANCE",
    }
    return po_reconciler.reconcile(contract, quote, tender_id=7813).draft


def test_render_produces_valid_docx():
    pytest.importorskip("docx")
    draft = _golden_draft()
    docx_bytes = po_renderer.render(draft)
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1024  # sanity
    # DOCX = ZIP, must be loadable
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        names = zf.namelist()
        assert "word/document.xml" in names
        body = zf.read("word/document.xml").decode("utf-8", errors="replace")
    # Key fields must appear in the document text
    assert "PURCHASE ORDER" in body
    assert "PO-RAD-7813" in body
    assert "Tronair" in body
    assert "T854700" in body
    assert "23,771.00" in body or "23771.00" in body
    assert "47,542.00" in body or "47542.00" in body


def test_render_works_with_empty_clauses():
    pytest.importorskip("docx")
    draft = _golden_draft()
    draft["flow_down_clauses"] = []
    draft["notes"] = ""
    docx_bytes = po_renderer.render(draft)
    assert len(docx_bytes) > 1024


def test_render_handles_multiple_line_items():
    pytest.importorskip("docx")
    draft = _golden_draft()
    draft["items"].append({
        "line": 2, "part_no": "EXTRA-001", "nsn": "", "ncage": "",
        "description": "Spare bracket", "qty": 5, "unit": "EA",
        "unit_price": 100.0, "extended": 500.0, "lead_time": "",
        "price_source": "quote", "match_status": "matched", "match_note": "", "math_ok": True,
    })
    draft["subtotal"] = 48042.00
    draft["total"] = 48042.00
    docx_bytes = po_renderer.render(draft)
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        body = zf.read("word/document.xml").decode("utf-8", errors="replace")
    assert "EXTRA-001" in body
    assert "Spare bracket" in body

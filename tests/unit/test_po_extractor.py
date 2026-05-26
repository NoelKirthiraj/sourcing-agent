"""Unit tests for po_extractor — pre-flight checks only (no LLM call)."""
from __future__ import annotations

import pytest

import po_extractor as E


_MIN_PDF = b"%PDF-1.4\n%mock pdf body\n%%EOF"


def test_validate_pdf_rejects_empty():
    with pytest.raises(E.PoExtractionError) as exc:
        E.validate_pdf(b"", role="contract")
    assert "empty" in exc.value.user_message.lower()


def test_validate_pdf_rejects_oversized():
    big = b"%PDF-1.4\n" + (b"A" * (E.MAX_PDF_BYTES + 1))
    with pytest.raises(E.PoExtractionError) as exc:
        E.validate_pdf(big, role="quote")
    assert exc.value.status == 413
    assert "MB" in exc.value.user_message


def test_validate_pdf_rejects_non_pdf_bytes():
    with pytest.raises(E.PoExtractionError) as exc:
        E.validate_pdf(b"this is not a pdf at all", role="contract")
    assert "PDF" in exc.value.user_message


def test_validate_pdf_rejects_encrypted():
    payload = b"%PDF-1.4\n/Encrypt 5 0 R\nrest of file\n%%EOF"
    with pytest.raises(E.PoExtractionError) as exc:
        E.validate_pdf(payload, role="contract")
    assert "encrypted" in exc.value.user_message.lower()


def test_validate_pdf_accepts_minimal_valid_header():
    # Should not raise
    E.validate_pdf(_MIN_PDF, role="contract")


def test_detect_role_picks_contract_on_dnd_keywords():
    payload = b"%PDF-1.4\nNational Defence ... Defence nationale ... Contracting Authority"
    assert E.detect_role(payload) == "contract"


def test_detect_role_picks_quote_on_quote_keywords():
    payload = b"%PDF-1.4\nQUOTE Sold To: CAGE CODE Tronair NET SALES"
    assert E.detect_role(payload) == "quote"


def test_detect_role_returns_unknown_on_ambiguous_input():
    assert E.detect_role(_MIN_PDF) == "unknown"


def test_looks_like_scan_true_for_image_only_pdf():
    # No font / text operators present
    payload = b"%PDF-1.4\n" + (b"\x00" * 200000) + b"%%EOF"
    assert E.looks_like_scan(payload) is True


def test_looks_like_scan_false_when_text_operators_present():
    payload = b"%PDF-1.4\n/Font << /F1 5 0 R >>\nBT\n(Hello) Tj\nET\n%%EOF"
    assert E.looks_like_scan(payload) is False

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


# ── BadRequestError disambiguation ───────────────────────────────────────────
# The Anthropic SDK's BadRequestError covers many root causes. Historically
# the extractor mapped ALL of them to "PDF could not be processed (too large
# or unreadable)", which sends users on a wild-goose chase when the real
# cause is billing (credit balance exhausted / quota reached). These tests
# lock in the disambiguation logic in _call_claude's except block.

class _FakeBadRequestError(Exception):
    """Stand-in for anthropic.BadRequestError. We don't need the SDK class
    identity since _call_claude checks the message string, but we DO need
    a class whose __name__ matches so the except clause resolves it as the
    same type as the real one when raised from a mock."""


def _bad_request_with(msg: str, monkeypatch):
    """Patch anthropic.Anthropic so client.messages.create raises a
    BadRequestError-shaped exception carrying `msg`. Returns the actual
    anthropic.BadRequestError class so the except clause in the extractor
    matches by isinstance."""
    import anthropic
    from unittest.mock import MagicMock

    class FakeMessages:
        def create(self, **kw):
            # Instantiate the real BadRequestError so isinstance works.
            # Signature: BadRequestError(message, response, body)
            resp = MagicMock()
            resp.status_code = 400
            resp.headers = {}
            raise anthropic.BadRequestError(msg, response=resp, body=None)

    class FakeClient:
        def __init__(self, **kw): pass
        @property
        def messages(self): return FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_bad_request_credit_balance_raises_billing_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _bad_request_with(
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits.",
        monkeypatch,
    )
    with pytest.raises(E.PoExtractionError) as excinfo:
        E._call_claude(_MIN_PDF, "prompt")
    err = excinfo.value
    assert "credit balance is exhausted" in err.user_message.lower()
    assert "console.anthropic.com/settings/billing" in err.user_message
    assert err.status == 503  # not 400 — it's not the caller's fault


def test_bad_request_quota_reached_raises_quota_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _bad_request_with(
        "You have reached your monthly spend limit. See usage & limits.",
        monkeypatch,
    )
    with pytest.raises(E.PoExtractionError) as excinfo:
        E._call_claude(_MIN_PDF, "prompt")
    err = excinfo.value
    assert "quota" in err.user_message.lower() or "usage limit" in err.user_message.lower()
    assert err.status == 503


def test_bad_request_pdf_related_still_returns_pdf_message(monkeypatch):
    """Regression: a BadRequestError that's actually about the PDF (too big,
    unreadable, unsupported format) should keep the existing user-facing
    message so the user knows to re-upload rather than check billing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _bad_request_with(
        "Input is too long or contains unsupported content.",
        monkeypatch,
    )
    with pytest.raises(E.PoExtractionError) as excinfo:
        E._call_claude(_MIN_PDF, "prompt")
    err = excinfo.value
    assert "PDF could not be processed" in err.user_message
    assert err.status == 400
    # Must NOT tell the user to top up billing when the PDF is the culprit
    assert "billing" not in err.user_message.lower()

"""Unit tests for the multipart/form-data parser in po_routes.

These cover the byte-level edge cases the hand-written parser has to handle:
quoted boundaries, missing parts, malformed headers, boundary tokens embedded
inside file content, and quoted filenames.
"""
from __future__ import annotations

import pytest

from po_routes import _parse_multipart, MultipartError


def _build(boundary: str, parts: list[tuple[str, bytes, str | None, str | None]]) -> bytes:
    """Construct a multipart body. Each part is (name, data, filename, content_type)."""
    delim = f"--{boundary}".encode()
    out = b""
    for name, data, filename, ctype in parts:
        out += delim + b"\r\n"
        dispo = f'form-data; name="{name}"'
        if filename is not None:
            dispo += f'; filename="{filename}"'
        out += f"Content-Disposition: {dispo}\r\n".encode()
        if ctype:
            out += f"Content-Type: {ctype}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    out += delim + b"--\r\n"
    return out


def test_parse_simple_two_part_body():
    boundary = "alpha123"
    body = _build(boundary, [
        ("contract", b"%PDF-1.4\ncontract bytes", "contract.pdf", "application/pdf"),
        ("quote",    b"%PDF-1.4\nquote bytes",    "quote.pdf",    "application/pdf"),
    ])
    parts = _parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert set(parts.keys()) == {"contract", "quote"}
    assert parts["contract"]["data"] == b"%PDF-1.4\ncontract bytes"
    assert parts["contract"]["filename"] == "contract.pdf"
    assert parts["contract"]["content_type"] == "application/pdf"


def test_parse_quoted_boundary_in_content_type():
    boundary = "alpha123"
    body = _build(boundary, [("x", b"data", None, None)])
    # Boundary value in quotes (legal per RFC 2046)
    parts = _parse_multipart(body, f'multipart/form-data; boundary="{boundary}"')
    assert "x" in parts
    assert parts["x"]["data"] == b"data"


def test_parse_text_field_without_filename():
    boundary = "alpha123"
    body = _build(boundary, [
        ("tender_id", b"7813", None, None),
        ("contract",  b"%PDF-1.4\nbytes", "c.pdf", "application/pdf"),
    ])
    parts = _parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert parts["tender_id"]["data"] == b"7813"
    assert parts["tender_id"]["filename"] == ""


def test_parse_boundary_embedded_in_file_content_is_safe():
    """Files that happen to contain the boundary string would corrupt naive
    splitters. Our parser uses the full `--boundary` delimiter, which (per
    RFC 2046) MUST be chosen so it does not appear in any part body. We
    verify that a body fragment that *resembles* but isn't identical to the
    boundary doesn't trip the parser."""
    boundary = "alpha123"
    # File contains "--alpha12" — close, but not the actual delim "--alpha123"
    payload = b"some bytes --alpha12 more bytes"
    body = _build(boundary, [
        ("contract", payload, "c.pdf", "application/pdf"),
    ])
    parts = _parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert parts["contract"]["data"] == payload


def test_parse_rejects_missing_content_type():
    body = b"anything"
    with pytest.raises(MultipartError) as exc:
        _parse_multipart(body, "")
    assert "multipart" in str(exc.value).lower()


def test_parse_rejects_wrong_content_type():
    body = b"anything"
    with pytest.raises(MultipartError):
        _parse_multipart(body, "application/json")


def test_parse_rejects_missing_boundary_param():
    body = b"anything"
    with pytest.raises(MultipartError) as exc:
        _parse_multipart(body, "multipart/form-data")
    assert "boundary" in str(exc.value).lower()


def test_parse_rejects_empty_boundary():
    body = b"anything"
    with pytest.raises(MultipartError) as exc:
        _parse_multipart(body, "multipart/form-data; boundary=")
    assert "boundary" in str(exc.value).lower()


def test_parse_skips_parts_with_missing_disposition():
    """A part that doesn't have form-data disposition should be silently
    skipped rather than crashing the request."""
    boundary = "alpha123"
    body = (
        f"--{boundary}\r\n"
        "X-Other: header-with-no-disposition\r\n"
        "\r\n"
        "garbage\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="contract"; filename="c.pdf"\r\n'
        "Content-Type: application/pdf\r\n"
        "\r\n"
        "%PDF-1.4\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    parts = _parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert "contract" in parts
    assert parts["contract"]["data"] == b"%PDF-1.4"


def test_parse_handles_filename_with_special_characters():
    """Filenames containing dashes, dots, and spaces should pass through."""
    boundary = "alpha123"
    body = _build(boundary, [
        ("contract", b"%PDF-1.4\nbytes", "RAD-7813 Contract v2.pdf", "application/pdf"),
    ])
    parts = _parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert parts["contract"]["filename"] == "RAD-7813 Contract v2.pdf"

"""Unit tests for api.py — dashboard API handler (mocked DB)."""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from io import BytesIO
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_handler(method, path, body=None):
    """Create a mock APIHandler with request properties set."""
    from api import APIHandler

    handler = APIHandler.__new__(APIHandler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(json.dumps(body))) if body else "0"}
    handler.rfile = BytesIO(json.dumps(body).encode() if body else b"")

    handler._response_code = None
    handler._response_body = None
    handler._headers_sent = {}

    def send_response(code):
        handler._response_code = code
    def send_header(k, v):
        handler._headers_sent[k] = v
    def end_headers():
        pass
    handler.wfile = BytesIO()
    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    handler.log_message = lambda *a: None

    return handler


def test_health_endpoint():
    handler = _make_handler("GET", "/api/health")
    handler.do_GET()
    output = handler.wfile.getvalue().decode()
    assert '"status": "ok"' in output
    assert handler._response_code == 200


def test_unknown_get_returns_404():
    handler = _make_handler("GET", "/api/nonexistent")
    handler.do_GET()
    assert handler._response_code == 404


def test_unknown_post_returns_404():
    handler = _make_handler("POST", "/api/nonexistent")
    handler.do_POST()
    assert handler._response_code == 404


@patch("api._run_async")
def test_list_tenders(mock_run):
    mock_run.return_value = [
        {"id": 1, "solicitation_no": "WS123", "status": "pending_review"}
    ]
    handler = _make_handler("GET", "/api/tenders?status=pending_review")
    handler.do_GET()
    assert handler._response_code == 200
    output = json.loads(handler.wfile.getvalue())
    assert len(output) == 1
    assert output[0]["solicitation_no"] == "WS123"


@patch("api._run_async")
def test_accept_tender(mock_run):
    mock_run.return_value = {"id": 1, "status": "accepted"}
    handler = _make_handler("POST", "/api/tenders/1/accept")
    handler.do_POST()
    assert handler._response_code == 200


@patch("api._run_async")
def test_reject_tender(mock_run):
    mock_run.return_value = True
    handler = _make_handler("POST", "/api/tenders/1/reject", body={"reason": "Not relevant"})
    handler.do_POST()
    assert handler._response_code == 200


@patch("api._run_async")
def test_list_associates(mock_run):
    mock_run.return_value = [
        {"name": "Edward", "active": True, "active_tenders": 3,
         "pending_count": 1, "accepted_count": 2, "submitted_count": 5}
    ]
    handler = _make_handler("GET", "/api/associates")
    handler.do_GET()
    assert handler._response_code == 200
    output = json.loads(handler.wfile.getvalue())
    assert output[0]["name"] == "Edward"


def test_cors_headers_set():
    handler = _make_handler("GET", "/api/health")
    handler.do_GET()
    assert handler._headers_sent.get("Access-Control-Allow-Origin") == "*"

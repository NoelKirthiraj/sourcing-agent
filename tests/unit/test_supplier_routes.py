"""Unit tests for supplier_routes — HTTP handlers (no real server, no DB).

Builds a minimal mock `handler` and stubs the `db` module, mirroring
test_vendor_routes.py. These cover the request contract: status codes,
response shape, input validation, and the guards that stop a mis-click
becoming a mass email.
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import supplier_routes


# ── Mock handler + run_async ────────────────────────────────────────────────

def _make_handler():
    h = MagicMock()
    h.headers = {}
    h.rfile = io.BytesIO(b"")
    h.responses = []

    def json_response(data, status=200):
        h.responses.append((data, status))

    h._json_response = json_response
    return h


def _run_async(coro):
    return asyncio.run(coro) if asyncio.iscoroutine(coro) else coro


TENDER = {
    "id": 7,
    "solicitation_no": "W8482-275179/A",
    "solicitation_title": "Aircraft towbar supply",
    "summary_of_contract": "Supply of aircraft towbars",
    "requirements": "",
    "gsin_description": "",
    "assigned_associate": "Charles Radovic",
}

CATEGORIES = [{"category": "Aerospace/Aircraft", "keywords": ["aircraft", "towbar"]}]
VENDORS = [
    {"id": 1, "company": "Acme Aero", "rfp_categories": ["Aerospace/Aircraft"],
     "emails": ["jane@acme.com"], "primary_contacts": ["Jane Doe"], "bid_count": 3},
    {"id": 2, "company": "Beta Marine", "rfp_categories": ["Marine/Naval"],
     "emails": [], "primary_contacts": [], "bid_count": 0},
]


@pytest.fixture
def stub_db(monkeypatch):
    """Stub every db call supplier_routes makes; tests override per case."""
    db = MagicMock()

    async def get_tender(tid):
        return TENDER if tid == 7 else None

    async def get_matching_inputs():
        return CATEGORIES, VENDORS, {1: ["Aircraft Towbar"]}

    async def replace_suggestions(tid, suggestions, origin="internal"):
        db.saved = (tid, suggestions, origin)
        return len(suggestions)

    async def list_suggestions(tid):
        return [
            {"company": "Acme Aero", "origin": "internal", "score": 5},
            {"company": "Web Co", "origin": "external", "score": 0},
        ]

    async def get_vendors_by_ids(ids):
        by_id = {v["id"]: v for v in VENDORS}
        return [by_id[i] for i in ids if i in by_id]

    async def create_outreach(tid, drafts, created_by=""):
        db.outreach_created = (tid, drafts, created_by)
        return [{"id": 100 + i, "company": d["company"], "created_at": "2026-10-05T10:00:00"}
                for i, d in enumerate(drafts)]

    async def list_outreach(tid):
        return [{"id": 1, "company": "Acme Aero", "subject": "RFQ"}]

    db.get_tender = get_tender
    db.get_matching_inputs = get_matching_inputs
    db.replace_suggestions = replace_suggestions
    db.list_suggestions = list_suggestions
    db.get_vendors_by_ids = get_vendors_by_ids
    db.create_outreach = create_outreach
    db.list_outreach = list_outreach
    monkeypatch.setattr(supplier_routes, "db", db)
    return db


# ── Path validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["abc", "", "0", "-1", "1.5"])
def test_invalid_tender_id_is_rejected(stub_db, bad):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, bad)
    assert h.responses[0][1] == 400


def test_unknown_tender_returns_404(stub_db):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "999")
    assert h.responses[0][1] == 404


# ── Matching ────────────────────────────────────────────────────────────────

def test_suggest_returns_ranked_suppliers(stub_db):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "7")
    data, status = h.responses[0]
    assert status == 200
    assert data["total"] == 1
    assert data["suggestions"][0]["company"] == "Acme Aero"


def test_suggest_reports_contactable_count(stub_db):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "7")
    assert h.responses[0][0]["contactable"] == 1


def test_suggest_persists_the_shortlist(stub_db):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "7")
    tender_id, saved, origin = stub_db.saved
    assert tender_id == 7
    assert origin == "internal"
    assert saved[0]["company"] == "Acme Aero"


def test_suggest_includes_rationale_for_the_ui(stub_db):
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "7")
    rationale = h.responses[0][0]["suggestions"][0]["rationale"]
    assert "Aerospace/Aircraft" in rationale


def test_no_keywords_configured_warns_instead_of_failing(stub_db, monkeypatch):
    """A data gap the sourcing team can close, not a system error."""
    async def no_keywords():
        return [{"category": "Uncategorised", "keywords": []}], VENDORS, {}

    stub_db.get_matching_inputs = no_keywords
    h = _make_handler()
    supplier_routes.handle_suggest(h, _run_async, "7")
    data, status = h.responses[0]
    assert status == 200
    assert data["suggestions"] == []
    assert "keywords" in data["warning"].lower()


# ── Stored shortlist ────────────────────────────────────────────────────────

def test_internal_and_external_are_returned_separately(stub_db):
    """The UI must never present an unvetted web result as a registry supplier."""
    h = _make_handler()
    supplier_routes.handle_list_suggestions(h, _run_async, "7")
    data = h.responses[0][0]
    assert [s["company"] for s in data["internal"]] == ["Acme Aero"]
    assert [s["company"] for s in data["external"]] == ["Web Co"]


# ── Outreach ────────────────────────────────────────────────────────────────

def test_outreach_generates_drafts(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [1]})
    data, status = h.responses[0]
    assert status == 201
    assert data["created"] == 1
    assert data["drafts"][0]["to"] == "jane@acme.com"
    assert "W8482-275179/A" in data["drafts"][0]["body"]


def test_outreach_signs_with_the_assigned_associate(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [1]})
    assert "Charles Radovic" in h.responses[0][0]["drafts"][0]["body"]


def test_explicit_associate_overrides_the_assignment(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(
        h, _run_async, "7", {"vendor_ids": [1], "associate": "Edouard Radovic"})
    assert "Edouard Radovic" in h.responses[0][0]["drafts"][0]["body"]


def test_outreach_flags_suppliers_without_an_email(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [2]})
    data = h.responses[0][0]
    assert data["missing_email"] == ["Beta Marine"]
    assert data["drafts"][0]["body"]          # still copyable


def test_outreach_attaches_stored_row_ids(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [1]})
    assert h.responses[0][0]["drafts"][0]["id"] == 100


def test_outreach_preserves_selection_order(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [2, 1]})
    assert [d["company"] for d in h.responses[0][0]["drafts"]] == ["Beta Marine", "Acme Aero"]


def test_duplicate_vendor_ids_are_collapsed(stub_db):
    """Tolerates a double-click without emailing the same supplier twice."""
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [1, 1, 1]})
    assert h.responses[0][0]["created"] == 1


# ── Outreach guards ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [{}, {"vendor_ids": []}, {"vendor_ids": "1"}])
def test_outreach_requires_a_non_empty_list(stub_db, payload):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", payload)
    assert h.responses[0][1] == 400


def test_outreach_rejects_non_numeric_vendor_ids(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": ["abc"]})
    assert h.responses[0][1] == 400


def test_outreach_caps_batch_size(stub_db):
    """A selection this large is a mis-click, not a workflow."""
    over = list(range(supplier_routes.MAX_OUTREACH_RECIPIENTS + 1))
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": over})
    data, status = h.responses[0]
    assert status == 400
    assert str(supplier_routes.MAX_OUTREACH_RECIPIENTS) in data["error"]


def test_outreach_on_unknown_vendors_returns_404(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "7", {"vendor_ids": [999]})
    assert h.responses[0][1] == 404


def test_outreach_on_unknown_tender_returns_404(stub_db):
    h = _make_handler()
    supplier_routes.handle_create_outreach(h, _run_async, "999", {"vendor_ids": [1]})
    assert h.responses[0][1] == 404


def test_list_outreach_returns_stored_drafts(stub_db):
    h = _make_handler()
    supplier_routes.handle_list_outreach(h, _run_async, "7")
    data, status = h.responses[0]
    assert status == 200
    assert data[0]["company"] == "Acme Aero"

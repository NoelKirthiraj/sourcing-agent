"""Unit tests for outreach — the template-only quotation-request draft.

v1 sends nothing. These drafts are copied by a human into their own mail
client, so the bar is: correct facts, no empty-looking sections, and never a
crash on a sparsely-populated vendor record.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from outreach import (
    SIGNATURE_ORG,
    build_body,
    build_draft,
    build_drafts,
    build_subject,
    primary_recipient,
)

TENDER = {
    "solicitation_no": "W8482-275179/A",
    "solicitation_title": "Sensitive Switch Assemblies",
    "closing_date": "2026-11-14",
    "time_and_zone": "14:00 EST",
    "client": "Department of National Defence",
    "summary_of_contract": "Supply of sensitive switches to CFB Halifax and CFB Esquimalt.",
    "requirements": "Item 1: SWITCH, Part LSX-F3K8, Qty 2\nItem 2: SWITCH, Part LSX-F3K9, Qty 2",
    "inquiry_link": "https://canadabuys.canada.ca/en/tender-notice/cb-123",
    "assigned_associate": "Alexandra Radovic",
}

VENDOR = {
    "id": 1,
    "company": "Acme Aero",
    "primary_contacts": ["Jane Doe", "John Roe"],
    "emails": ["jane@acme.com", "sales@acme.com"],
}


# ── Recipient selection ─────────────────────────────────────────────────────

def test_first_valid_email_is_used():
    assert primary_recipient(VENDOR) == "jane@acme.com"


def test_entries_without_an_at_sign_are_skipped():
    assert primary_recipient({"emails": ["not-an-email", "real@x.com"]}) == "real@x.com"


def test_no_email_returns_empty_not_an_error():
    assert primary_recipient({"emails": []}) == ""
    assert primary_recipient({}) == ""


# ── Subject ─────────────────────────────────────────────────────────────────

def test_subject_carries_title_and_solicitation_number():
    subject = build_subject(TENDER)
    assert "Sensitive Switch Assemblies" in subject
    assert "W8482-275179/A" in subject


def test_subject_survives_a_missing_solicitation_number():
    subject = build_subject({"solicitation_title": "Widgets"})
    assert "Widgets" in subject and "(" not in subject


def test_subject_has_a_fallback_when_title_is_missing():
    assert build_subject({}) .strip() != "Request for quotation —"


# ── Body content ────────────────────────────────────────────────────────────

def test_body_greets_the_contact_by_first_name():
    assert build_body(TENDER, VENDOR).startswith("Hello Jane,")


def test_body_uses_neutral_greeting_without_a_contact():
    body = build_body(TENDER, {"company": "Acme", "emails": []})
    assert body.startswith("Hello,")


def test_body_names_the_supplier_company():
    assert "Acme Aero" in build_body(TENDER, VENDOR)


def test_body_includes_the_key_facts():
    body = build_body(TENDER, VENDOR)
    for fact in ("W8482-275179/A", "2026-11-14", "14:00 EST",
                 "Department of National Defence"):
        assert fact in body


def test_body_includes_requirement_line_items():
    body = build_body(TENDER, VENDOR)
    assert "LSX-F3K8" in body and "LSX-F3K9" in body


def test_body_includes_the_notice_link():
    assert TENDER["inquiry_link"] in build_body(TENDER, VENDOR)


def test_body_is_signed_by_the_assigned_associate():
    body = build_body(TENDER, VENDOR)
    assert "Alexandra Radovic" in body
    assert SIGNATURE_ORG in body


def test_explicit_associate_overrides_the_tender_assignment():
    body = build_body(TENDER, VENDOR, associate="Marc Radovic")
    assert "Marc Radovic" in body
    assert "Alexandra Radovic" not in body


def test_falls_back_to_org_signature_when_no_associate_known():
    body = build_body({**TENDER, "assigned_associate": ""}, VENDOR)
    assert body.rstrip().endswith(SIGNATURE_ORG)


# ── Sparse data must not produce a broken-looking email ─────────────────────

def test_missing_fields_are_omitted_not_left_blank():
    """An email with an empty 'Closes :' line looks broken to a supplier."""
    body = build_body({"solicitation_title": "Widgets"}, VENDOR)
    assert "Closes" not in body
    assert "Issuing department" not in body
    assert ": \n" not in body


def test_empty_tender_still_produces_a_usable_draft():
    body = build_body({}, {"company": "Acme", "emails": []})
    assert body.startswith("Hello,")
    assert SIGNATURE_ORG in body


def test_gsin_description_is_used_when_summary_is_absent():
    body = build_body({"gsin_description": "Naval spare parts"}, VENDOR)
    assert "Naval spare parts" in body


# ── Truncation ──────────────────────────────────────────────────────────────

def test_long_summary_is_truncated_with_a_marker():
    body = build_body({**TENDER, "summary_of_contract": "word " * 500}, VENDOR)
    assert "[…]" in body


def test_truncation_does_not_split_a_word():
    body = build_body({**TENDER, "summary_of_contract": "supercalifragilistic " * 100}, VENDOR)
    summary_line = next(ln for ln in body.split("\n") if "supercalifragilistic" in ln)
    assert "supercalifragilisti […]" not in summary_line


def test_many_line_items_are_capped_with_a_count():
    many = "\n".join(f"Item {i}: Part P{i}" for i in range(40))
    body = build_body({**TENDER, "requirements": many}, VENDOR)
    assert "further line item(s)" in body
    assert body.count("  - ") <= 13   # 12 items + the overflow line


# ── Draft assembly ──────────────────────────────────────────────────────────

def test_draft_has_the_fields_the_ui_needs():
    draft = build_draft(TENDER, VENDOR)
    for key in ("vendor_id", "company", "to", "subject", "body", "missing_email"):
        assert key in draft
    assert draft["to"] == "jane@acme.com"
    assert draft["missing_email"] is False


def test_draft_is_still_produced_when_the_email_is_missing():
    """The associate may have the address elsewhere; a copyable draft beats a refusal."""
    draft = build_draft(TENDER, {"id": 9, "company": "No Contact Ltd", "emails": []})
    assert draft["missing_email"] is True
    assert draft["body"]
    assert draft["to"] == ""


def test_build_drafts_preserves_selection_order():
    v2 = {"id": 2, "company": "Beta Marine", "emails": ["b@x.com"]}
    drafts = build_drafts(TENDER, [v2, VENDOR])
    assert [d["company"] for d in drafts] == ["Beta Marine", "Acme Aero"]


def test_build_drafts_on_empty_selection():
    assert build_drafts(TENDER, []) == []

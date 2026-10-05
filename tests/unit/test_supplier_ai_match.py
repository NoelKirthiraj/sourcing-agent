"""Unit tests for supplier_ai_match — the second rung of the escalation ladder.

No network; the Anthropic client is stubbed. The tests concentrate on the
guardrail that matters most: the model may only return suppliers that exist in
the registry it was given. An invented supplier is worse than no supplier,
because an associate would email it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import supplier_ai_match
from supplier_ai_match import (
    AiMatchError,
    build_roster,
    build_task,
    parse_results,
    to_suggestions,
)

VENDORS = [
    {"id": 1, "company": "Acme Aero", "rfp_categories": ["Aerospace/Aircraft"],
     "emails": ["jane@acme.com"], "bid_count": 3},
    {"id": 2, "company": "Beta Marine", "rfp_categories": ["Marine/Naval"],
     "emails": [], "bid_count": 0},
    {"id": 3, "company": "Gamma Hydraulics", "rfp_categories": ["Hydraulics/Pneumatics"],
     "emails": ["sales@gamma.com"], "bid_count": 1},
]
PRODUCTS = {1: ["Aircraft Towbar", "Landing Gear Kit"], 3: ["Hose Assembly"]}
VENDORS_BY_ID = {v["id"]: v for v in VENDORS}

TENDER = {
    "solicitation_title": "Aircraft ground support equipment",
    "summary_of_contract": "Supply of towing equipment for CFB Halifax",
    "requirements": "Item 1: tow bar",
}


# ── Roster ──────────────────────────────────────────────────────────────────

def test_roster_includes_id_company_categories_and_products():
    roster = build_roster(VENDORS, PRODUCTS)
    assert "1|Acme Aero" in roster
    assert "cats:Aerospace/Aircraft" in roster
    assert "Aircraft Towbar" in roster


def test_roster_is_stable_across_calls():
    """It is sent as a cached prefix, so byte-identical output matters."""
    assert build_roster(VENDORS, PRODUCTS) == build_roster(list(reversed(VENDORS)), PRODUCTS)


def test_roster_skips_vendors_without_id_or_company():
    roster = build_roster([{"company": "No ID"}, {"id": 9, "company": ""}], {})
    assert roster == ""


def test_roster_lines_are_length_capped():
    fat = [{"id": 1, "company": "C", "rfp_categories": ["x" * 300]}]
    assert all(len(line) <= supplier_ai_match.MAX_LINE_CHARS
               for line in build_roster(fat, {}).split("\n"))


def test_roster_caps_products_per_vendor():
    many = {1: [f"Product {i}" for i in range(20)]}
    line = build_roster([VENDORS[0]], many)
    assert line.count("Product") <= supplier_ai_match.MAX_PRODUCTS_PER_VENDOR


# ── Task prompt ─────────────────────────────────────────────────────────────

def test_task_carries_the_requirement():
    task = build_task(TENDER)
    assert "Aircraft ground support equipment" in task
    assert "CFB Halifax" in task


def test_task_forbids_inventing_suppliers():
    assert "never invent" in build_task(TENDER).lower()


def test_task_survives_an_empty_tender():
    assert "not given" in build_task({})


# ── The guardrail: only real vendor ids survive ─────────────────────────────

def test_unknown_ids_are_discarded():
    """A hallucinated supplier is worse than no supplier."""
    out = parse_results('[{"id": 1, "why": "fits"}, {"id": 999, "why": "invented"}]',
                        {1, 2, 3})
    assert [r["vendor_id"] for r in out] == [1]


def test_duplicate_ids_are_collapsed():
    out = parse_results('[{"id":1,"why":"a"},{"id":1,"why":"b"}]', {1})
    assert len(out) == 1


def test_non_numeric_ids_are_skipped():
    out = parse_results('[{"id":"abc"},{"id":2,"why":"ok"}]', {1, 2})
    assert [r["vendor_id"] for r in out] == [2]


def test_results_are_capped():
    many = ",".join(f'{{"id":{i},"why":"x"}}' for i in range(30))
    assert len(parse_results(f"[{many}]", set(range(30)))) == supplier_ai_match.MAX_RESULTS


@pytest.mark.parametrize("text", ["", "no json", "[", '{"id":1}', "[1,2,3]"])
def test_malformed_output_yields_nothing(text):
    assert parse_results(text, {1, 2, 3}) == []


def test_confidence_defaults_to_medium_when_absent_or_odd():
    assert parse_results('[{"id":1}]', {1})[0]["confidence"] == "medium"
    assert parse_results('[{"id":1,"confidence":"banana"}]', {1})[0]["confidence"] == "medium"


# ── Suggestion shape ────────────────────────────────────────────────────────

def test_suggestions_are_attributed_to_the_model():
    """Unlike the keyword stage, this is a judgement — say so in the rationale."""
    out = to_suggestions(parse_results('[{"id":1,"why":"supplies towbars","confidence":"high"}]',
                                       {1}), VENDORS_BY_ID)
    assert out[0]["origin"] == "ai"
    assert out[0]["rationale"].startswith("AI match (high confidence)")
    assert "supplies towbars" in out[0]["rationale"]


def test_confidence_maps_to_score():
    high = to_suggestions(parse_results('[{"id":1,"confidence":"high"}]', {1}), VENDORS_BY_ID)
    low = to_suggestions(parse_results('[{"id":1,"confidence":"low"}]', {1}), VENDORS_BY_ID)
    assert high[0]["score"] > low[0]["score"]


def test_contactable_reflects_the_registry_not_the_model():
    out = to_suggestions(parse_results('[{"id":2,"why":"x"}]', {2}), VENDORS_BY_ID)
    assert out[0]["contactable"] is False      # Beta Marine has no email


def test_suggestion_without_a_known_vendor_is_dropped():
    assert to_suggestions([{"vendor_id": 42, "why": "x", "confidence": "high"}],
                          VENDORS_BY_ID) == []


def test_missing_why_gets_a_fallback_rationale():
    out = to_suggestions(parse_results('[{"id":1}]', {1}), VENDORS_BY_ID)
    assert out[0]["rationale"]


# ── Failure reporting ───────────────────────────────────────────────────────

def test_missing_api_key_is_reported_as_itself(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AiMatchError) as err:
        supplier_ai_match.match_suppliers(TENDER, VENDORS, PRODUCTS)
    assert "api key" in str(err.value).lower()


def test_credit_exhaustion_is_reported_as_billing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(
        "Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    with pytest.raises(AiMatchError) as err:
        supplier_ai_match.match_suppliers(TENDER, VENDORS, PRODUCTS)
    assert "credit balance is exhausted" in err.value.user_message


def test_empty_registry_returns_nothing_without_calling_the_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert supplier_ai_match.match_suppliers(TENDER, [], {}) == []
    fake.Anthropic.return_value.messages.create.assert_not_called()


# ── End to end with a stubbed client ────────────────────────────────────────

def _stub(monkeypatch, text: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    block = MagicMock(); block.type = "text"; block.text = text
    message = MagicMock(); message.content = [block]; message.usage = MagicMock()
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value = message
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def test_match_returns_registry_suggestions(monkeypatch):
    _stub(monkeypatch, '[{"id":1,"why":"makes towbars","confidence":"high"}]')
    out = supplier_ai_match.match_suppliers(TENDER, VENDORS, PRODUCTS)
    assert [s["company"] for s in out] == ["Acme Aero"]


def test_match_drops_invented_suppliers_end_to_end(monkeypatch):
    _stub(monkeypatch, '[{"id":1,"why":"real"},{"id":4242,"why":"invented"}]')
    out = supplier_ai_match.match_suppliers(TENDER, VENDORS, PRODUCTS)
    assert [s["vendor_id"] for s in out] == [1]


def test_roster_is_sent_as_a_cached_system_block(monkeypatch):
    """Caching is what keeps repeat matches cheap."""
    fake = _stub(monkeypatch, "[]")
    supplier_ai_match.match_suppliers(TENDER, VENDORS, PRODUCTS)
    kwargs = fake.Anthropic.return_value.messages.create.call_args.kwargs
    system = kwargs["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert "Acme Aero" in system["text"]

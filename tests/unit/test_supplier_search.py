"""Unit tests for supplier_search — external (web) supplier discovery.

No network. The Anthropic client is stubbed; these cover the parts that decide
whether a web hit is safe to show: dedup against the registry, tolerant
parsing, and reporting a billing failure as a billing failure rather than
"no suppliers found".
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import supplier_search
from supplier_search import (
    SupplierSearchError,
    build_query,
    dedupe_against_registry,
    normalise_company,
    normalise_domain,
    parse_results,
    to_suggestions,
)

TENDER = {
    "solicitation_title": "Aircraft towbar supply",
    "summary_of_contract": "Supply of aircraft towbars to CFB Halifax",
    "requirements": "Item 1: Towbar, Part LSX-1",
}

GOOD_JSON = """Here is what I found:
[{"company": "Nova Aero Ltd", "domain": "https://www.novaaero.ca/products",
  "why": "Manufactures aircraft ground support equipment", "source_url": "https://novaaero.ca"}]
"""


# ── Normalisation ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Acme Aero Inc.", "acme aero"),
    ("ACME AERO LTD", "acme aero"),
    ("Acme  Aero,  Limited", "acme aero"),
    ("Acme Aero", "acme aero"),
])
def test_company_normalisation_ignores_legal_suffix(raw, expected):
    assert normalise_company(raw) == expected


def test_company_normalisation_strips_multiple_suffixes():
    assert normalise_company("Beta Marine Co Ltd") == "beta marine"


@pytest.mark.parametrize("raw,expected", [
    ("https://www.acme.com/products", "acme.com"),
    ("WWW.ACME.COM", "acme.com"),
    ("acme.com/", "acme.com"),
    ("acme.com.", "acme.com"),
])
def test_domain_normalisation(raw, expected):
    assert normalise_domain(raw) == expected


# ── Dedup against the registry ──────────────────────────────────────────────

def test_known_company_is_dropped_despite_legal_suffix():
    found = [{"company": "Acme Aero Inc.", "domain": "acme.com"}]
    assert dedupe_against_registry(found, ["Acme Aero"], []) == []


def test_known_domain_is_dropped_despite_different_name():
    """Same business, different trading name — the domain gives it away."""
    found = [{"company": "Acme Aerospace Group", "domain": "www.acme.com"}]
    assert dedupe_against_registry(found, [], ["acme.com"]) == []


def test_genuinely_new_supplier_is_kept():
    found = [{"company": "Nova Aero", "domain": "novaaero.ca"}]
    assert len(dedupe_against_registry(found, ["Acme Aero"], ["acme.com"])) == 1


def test_duplicates_within_a_batch_are_collapsed():
    found = [{"company": "Nova Aero", "domain": "novaaero.ca"},
             {"company": "Nova Aero Ltd", "domain": "novaaero.ca"}]
    assert len(dedupe_against_registry(found, [], [])) == 1


def test_entries_without_a_company_are_dropped():
    assert dedupe_against_registry([{"company": "", "domain": "x.com"}], [], []) == []


def test_blank_registry_entries_do_not_swallow_results():
    """An empty company string must not match every web hit."""
    found = [{"company": "Nova Aero", "domain": ""}]
    assert len(dedupe_against_registry(found, ["", None], ["", None])) == 1


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_parses_array_embedded_in_prose():
    results = parse_results(GOOD_JSON)
    assert len(results) == 1
    assert results[0]["company"] == "Nova Aero Ltd"
    assert results[0]["domain"] == "novaaero.ca"


def test_parse_accepts_rationale_or_why_key():
    assert parse_results('[{"company":"X","rationale":"makes things"}]')[0]["rationale"] == "makes things"


def test_parse_accepts_url_or_source_url_key():
    assert parse_results('[{"company":"X","url":"https://x.com"}]')[0]["source_url"] == "https://x.com"


@pytest.mark.parametrize("text", ["", "no json here", "[", "{\"company\": \"X\"}", "[1,2,3]"])
def test_malformed_output_yields_empty_not_an_exception(text):
    """A failed search should return nothing, not break the page."""
    assert parse_results(text) == []


def test_entries_missing_a_company_name_are_skipped():
    assert parse_results('[{"domain":"x.com"},{"company":"Real Co"}]') == [
        {"company": "Real Co", "domain": "", "rationale": "", "source_url": ""}
    ]


# ── Suggestion shape ────────────────────────────────────────────────────────

def test_external_suggestions_are_never_contactable_or_vendor_rows():
    """An unvetted web hit must not look like a registry supplier."""
    out = to_suggestions(parse_results(GOOD_JSON))[0]
    assert out["origin"] == "external"
    assert out["vendor_id"] is None
    assert out["contactable"] is False
    assert out["score"] == 0


def test_missing_rationale_gets_an_explicit_unverified_note():
    out = to_suggestions([{"company": "X"}])[0]
    assert "not verified" in out["rationale"].lower()


# ── Query construction ──────────────────────────────────────────────────────

def test_query_carries_the_requirement_and_asks_for_json():
    q = build_query(TENDER)
    assert "Aircraft towbar supply" in q
    assert "CFB Halifax" in q
    assert "JSON array" in q


def test_query_instructs_against_inventing_companies():
    assert "do not" in build_query(TENDER).lower()


def test_query_survives_an_empty_tender():
    assert "not given" in build_query({})


# ── Failure reporting ───────────────────────────────────────────────────────

def test_missing_api_key_is_reported_as_itself(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SupplierSearchError) as err:
        supplier_search.find_external_suppliers(TENDER, [], [])
    assert "api key" in str(err.value).lower()


def test_credit_exhaustion_is_reported_as_billing_not_no_results(monkeypatch):
    """The 2026-09-07 lesson: never report a billing failure as a content result."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(
        "Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    with pytest.raises(SupplierSearchError) as err:
        supplier_search.find_external_suppliers(TENDER, [], [])
    assert "credit balance is exhausted" in err.value.user_message


def test_ordinary_failure_reports_a_retryable_message(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception("Read timeout")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    with pytest.raises(SupplierSearchError) as err:
        supplier_search.find_external_suppliers(TENDER, [], [])
    assert "try again" in err.value.user_message.lower()


# ── End to end with a stubbed client ────────────────────────────────────────

def _stub_client(monkeypatch, text: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    block = MagicMock()
    block.type = "text"
    block.text = text
    message = MagicMock()
    message.content = [block]
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value = message
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def test_search_returns_deduped_external_suggestions(monkeypatch):
    _stub_client(monkeypatch, GOOD_JSON)
    out = supplier_search.find_external_suppliers(TENDER, ["Acme Aero"], ["acme.com"])
    assert [s["company"] for s in out] == ["Nova Aero Ltd"]
    assert out[0]["origin"] == "external"


def test_search_filters_out_registry_companies(monkeypatch):
    _stub_client(monkeypatch, '[{"company":"Acme Aero Inc","domain":"acme.com"}]')
    assert supplier_search.find_external_suppliers(TENDER, ["Acme Aero"], []) == []


def test_search_caps_the_result_count(monkeypatch):
    many = ",".join(f'{{"company":"Co {i}","domain":"c{i}.com"}}' for i in range(30))
    _stub_client(monkeypatch, f"[{many}]")
    assert len(supplier_search.find_external_suppliers(TENDER, [], [])) == supplier_search.MAX_RESULTS


def test_search_uses_the_dynamic_filtering_tool_version(monkeypatch):
    """`_20260209` is the version supporting dynamic filtering on this model."""
    fake = _stub_client(monkeypatch, "[]")
    supplier_search.find_external_suppliers(TENDER, [], [])
    kwargs = fake.Anthropic.return_value.messages.create.call_args.kwargs
    assert kwargs["tools"][0]["type"] == "web_search_20260209"
    assert kwargs["tools"][0]["max_uses"] == supplier_search.MAX_WEB_SEARCHES


# ── Timeout configuration (regression: 2026-10-05 retry storm) ──────────────

def test_retries_are_disabled():
    """The SDK default of 2 turned a slow search into three attempts — up to
    six minutes at triple the cost, observed in production at 189s."""
    assert supplier_search.ANTHROPIC_MAX_RETRIES == 0


def test_server_worst_case_stays_under_the_ui_deadline():
    """The UI polls for 5 minutes; one attempt must finish inside that or the
    panel reports a timeout while the search is still running and billing."""
    worst_case = supplier_search.ANTHROPIC_TIMEOUT_SECONDS * (
        supplier_search.ANTHROPIC_MAX_RETRIES + 1)
    assert worst_case < 300


def test_timeout_is_long_enough_for_the_configured_searches():
    """120s was a guess that proved too tight for MAX_WEB_SEARCHES round trips."""
    assert supplier_search.ANTHROPIC_TIMEOUT_SECONDS >= 180


def test_client_is_constructed_with_both_limits(monkeypatch):
    fake = _stub_client(monkeypatch, "[]")
    supplier_search.find_external_suppliers(TENDER, [], [])
    kwargs = fake.Anthropic.call_args.kwargs
    assert kwargs["timeout"] == supplier_search.ANTHROPIC_TIMEOUT_SECONDS
    assert kwargs["max_retries"] == 0


@pytest.mark.parametrize("exc", [
    TimeoutError("request timed out"),
    Exception("APITimeoutError: Request timed out."),
])
def test_timeouts_are_recognised(exc):
    assert supplier_search.is_timeout(exc) is True


@pytest.mark.parametrize("exc", [
    Exception("Connection refused"),
    Exception("invalid_request_error"),
])
def test_ordinary_failures_are_not_treated_as_timeouts(exc):
    assert supplier_search.is_timeout(exc) is False


def test_timeout_says_it_timed_out_not_that_it_failed(monkeypatch):
    """'Failed, try again' sends people hunting for a fault that isn't there."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(
        "Request timed out.")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    with pytest.raises(SupplierSearchError) as err:
        supplier_search.find_external_suppliers(TENDER, [], [])
    message = err.value.user_message
    assert "longer than 180 seconds" in message
    assert "stopped" in message


def test_billing_failure_still_wins_over_the_timeout_branch(monkeypatch):
    """A credit error that happens to mention time must still read as billing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(
        "Your credit balance is too low; request timed out waiting.")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    with pytest.raises(SupplierSearchError) as err:
        supplier_search.find_external_suppliers(TENDER, [], [])
    assert "credit balance is exhausted" in err.value.user_message

"""Unit tests for supplier_contact — the optional lookup after a web search.

No network; the Anthropic client is stubbed. The guardrail under test is that
an address is only ever *read off a page*, never constructed from a domain: an
invented address is worse than none, because somebody will email it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import supplier_contact
from supplier_contact import (
    build_prompt,
    candidate_urls,
    clean_emails,
    clean_phones,
    find_contact,
    parse_contact,
)
from supplier_search import SupplierSearchError


# ── Candidate URLs ──────────────────────────────────────────────────────────

def test_source_url_is_offered_first():
    """It is the page the search actually found — likeliest to be real."""
    urls = candidate_urls("acme.com", "https://directory.example/acme")
    assert urls[0] == "https://directory.example/acme"


def test_likely_contact_paths_are_offered():
    """web_fetch only accepts URLs already in the conversation, so listing the
    contact paths is what makes them reachable at all."""
    urls = candidate_urls("acme.com")
    assert "https://acme.com/contact" in urls
    assert "https://acme.com/contact-us" in urls


def test_duplicate_urls_are_collapsed():
    urls = candidate_urls("acme.com", "https://acme.com/")
    assert len(urls) == len(set(urls))


def test_no_domain_and_no_source_yields_nothing_to_fetch():
    assert candidate_urls("", "") == []


def test_domain_is_normalised_before_building_urls():
    urls = candidate_urls("https://www.acme.com/about")
    assert "https://acme.com/contact" in urls


# ── Email cleaning ──────────────────────────────────────────────────────────

def test_supplier_own_domain_sorts_first():
    """A purchasing enquiry belongs with the company, not with the marketing
    agency whose address is also in the footer."""
    out = clean_emails(["hello@agency.com", "sales@acme.com"], "acme.com")
    assert out[0] == "sales@acme.com"


def test_boilerplate_addresses_are_dropped():
    assert clean_emails(["noreply@acme.com", "privacy@acme.com"], "acme.com") == []


def test_placeholder_domains_are_dropped():
    assert clean_emails(["sales@example.com", "a@yourdomain.com"]) == []


def test_malformed_addresses_are_dropped():
    assert clean_emails(["not-an-email", "@acme.com", "a@b", ""]) == []


def test_addresses_are_lowercased_and_deduped():
    assert clean_emails(["Sales@Acme.com", "sales@acme.com"], "acme.com") == ["sales@acme.com"]


def test_email_count_is_capped():
    many = [f"sales{i}@acme.com" for i in range(10)]
    assert len(clean_emails(many, "acme.com")) == supplier_contact.MAX_EMAILS


# ── Phone cleaning ──────────────────────────────────────────────────────────

def test_phone_whitespace_is_normalised():
    assert clean_phones(["  +1  (613)   555-0199 "]) == ["+1 (613) 555-0199"]


def test_same_number_formatted_two_ways_is_one_number():
    assert clean_phones(["+1 (613) 555-0199", "+1 613 555 0199"]) == ["+1 (613) 555-0199"]


@pytest.mark.parametrize("value", ["12345", "", "abc", "1" * 20])
def test_undialable_values_are_dropped(value):
    assert clean_phones([value]) == []


# ── Prompt ──────────────────────────────────────────────────────────────────

def test_prompt_forbids_constructing_an_address():
    text = build_prompt("Acme", "acme.com", ["https://acme.com/contact"]).lower()
    assert "never construct" in text
    assert "never guess" in text


def test_prompt_lists_every_candidate_url():
    urls = ["https://acme.com/contact", "https://acme.com/"]
    text = build_prompt("Acme", "acme.com", urls)
    assert all(u in text for u in urls)


def test_prompt_prefers_a_general_address_over_an_individual():
    assert "named individual" in build_prompt("Acme", "acme.com", [])


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_json_is_found_amid_surrounding_prose():
    out = parse_contact('Here it is: {"emails":["sales@acme.com"]} hope that helps', "acme.com")
    assert out["emails"] == ["sales@acme.com"]


@pytest.mark.parametrize("text", ["", "no json here", "{", "[1,2,3]", "null"])
def test_malformed_output_yields_the_empty_shape(text):
    assert parse_contact(text) == {
        "emails": [], "phones": [], "contact_name": "", "source_url": ""}


def test_parsed_addresses_are_cleaned_too():
    """The model's own output is not trusted to be free of boilerplate."""
    out = parse_contact('{"emails":["noreply@acme.com","sales@acme.com"]}', "acme.com")
    assert out["emails"] == ["sales@acme.com"]


def test_non_http_source_url_is_discarded():
    assert parse_contact('{"source_url":"javascript:alert(1)"}')["source_url"] == ""


def test_contact_name_is_length_capped():
    out = parse_contact('{"contact_name":"' + "x" * 400 + '"}')
    assert len(out["contact_name"]) <= 120


# ── Entry point ─────────────────────────────────────────────────────────────

def test_no_website_is_reported_as_itself():
    """Distinct from a failed lookup — there is simply no page to read."""
    with pytest.raises(SupplierSearchError) as err:
        find_contact("Acme", domain="", source_url="")
    assert "no website" in str(err.value).lower()


def test_blank_company_is_a_no_op():
    assert find_contact("")["emails"] == []


def test_missing_api_key_is_reported_as_itself(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SupplierSearchError) as err:
        find_contact("Acme", domain="acme.com")
    assert "api key" in str(err.value).lower()


def _stub(monkeypatch, text: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    block = MagicMock(); block.type = "text"; block.text = text
    message = MagicMock(); message.content = [block]
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value = message
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def test_contact_is_returned_from_the_page(monkeypatch):
    _stub(monkeypatch, '{"emails":["sales@acme.com"],"phones":["+1 613 555 0199"],'
                       '"source_url":"https://acme.com/contact"}')
    out = find_contact("Acme Aero", domain="acme.com")
    assert out["emails"] == ["sales@acme.com"]
    assert out["source_url"] == "https://acme.com/contact"


def test_a_page_with_no_details_returns_empty_rather_than_raising(monkeypatch):
    """Looked and found nothing is an answer; the caller reports it as one."""
    _stub(monkeypatch, '{"emails":[],"phones":[]}')
    assert find_contact("Acme", domain="acme.com")["emails"] == []


def test_fetch_is_capped_in_uses_and_content(monkeypatch):
    """An unbounded fetch on a 200-page PDF catalogue is the cost risk here."""
    fake = _stub(monkeypatch, "{}")
    find_contact("Acme", domain="acme.com")
    tool = fake.Anthropic.return_value.messages.create.call_args.kwargs["tools"][0]
    assert tool["type"] == supplier_contact.WEB_FETCH_TOOL_TYPE
    assert tool["max_uses"] == supplier_contact.MAX_FETCHES
    assert tool["max_content_tokens"] == supplier_contact.MAX_CONTENT_TOKENS


def test_no_retries_on_the_lookup(monkeypatch):
    """Same reasoning as supplier_search: a silent triple-cost retry is worse
    than letting the associate click again."""
    fake = _stub(monkeypatch, "{}")
    find_contact("Acme", domain="acme.com")
    assert fake.Anthropic.call_args.kwargs["max_retries"] == 0


def test_credit_exhaustion_is_reported_as_billing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(
        "Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    with pytest.raises(SupplierSearchError) as err:
        find_contact("Acme", domain="acme.com")
    assert "credit balance is exhausted" in err.value.user_message


def test_timeout_is_reported_as_slowness_not_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception("Request timed out")
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    with pytest.raises(SupplierSearchError) as err:
        find_contact("Acme", domain="acme.com")
    assert "longer than" in err.value.user_message

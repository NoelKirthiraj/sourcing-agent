"""
External supplier search — finds candidate suppliers the registry doesn't have.

Explicitly triggered, never automatic: it costs money per use and returns
unverified results. Everything here is a *lead*, not a supplier. Results are
deduplicated against the registry and returned with `origin="external"` so the
UI can keep them visually separate; promoting one to a registry vendor is a
deliberate human act, never a side effect of searching.

Uses Claude's server-side web search. The model runs the searches and returns
findings; we parse, filter and dedupe them here.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Iterable, Optional

from llm_errors import api_unavailable_reason

log = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 4096

# Basic web search, deliberately NOT the newer web_search_20260209.
#
# The _20260209 variant adds dynamic filtering, which runs code execution
# server-side to pre-filter results. Measured against this module's own prompt
# it never returned — timing out at 90s, 180s and 240s — while the identical
# prompt on the basic tool completed in 22.5s and parsed 8 usable suppliers.
# The hang is prompt-specific: a short generic query on _20260209 answers in
# ~29s. Until that is understood, basic search is the one that works.
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"

# Observed 22.5s end to end on the basic tool. 90s leaves generous headroom
# without letting a wedged call occupy a thread for minutes.
ANTHROPIC_TIMEOUT_SECONDS = 90.0

# No retries. The SDK defaults to 2, so a slow search silently became three
# attempts — up to six minutes, at triple the cost, while a person watched a
# spinner. Observed in production: a job still running at 189s on its second
# retry. One honest attempt, then let the associate decide whether to re-run.
# (supplier_ai_match keeps the default retries on purpose: it answers in ~6s,
# so a retry there is cheap and genuinely useful.)
ANTHROPIC_MAX_RETRIES = 0

# Each search is billed per use on top of tokens. Back to 4 now that the real
# cause is known to be the tool variant rather than the search count — one
# search timed out just as readily as four, so trimming it bought nothing.
MAX_WEB_SEARCHES = 4

# A web sweep that returns forty names is noise; the associate has to check
# each one by hand.
MAX_RESULTS = 8

# Legal suffixes stripped before comparing company names, so "Acme Aero Inc."
# and "Acme Aero Ltd" don't both appear alongside the registry's "Acme Aero".
_LEGAL_SUFFIXES = (
    "incorporated", "corporation", "limited", "company",
    "inc", "llc", "ltd", "corp", "co", "plc", "gmbh", "sa", "srl", "pty", "bv",
)


def is_timeout(exc: Exception) -> bool:
    """True when the call ran out of time rather than genuinely failing.

    Matched on type name and message because the SDK's timeout class is not
    imported at module scope — anthropic is a lazy import here.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "timeout" in name or "timeout" in text or "timed out" in text


class SupplierSearchError(Exception):
    """Raised when the search could not run. `user_message` is safe to surface."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


# ── Normalisation & dedup ────────────────────────────────────────────────────

def normalise_company(name: str) -> str:
    """Comparison key for company names, ignoring punctuation and legal form."""
    text = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    words = [w for w in text.split() if w]
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def normalise_domain(domain: str) -> str:
    """Bare registrable host: strips scheme, www, path and trailing dot."""
    text = (domain or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/")[0].split("?")[0]
    text = re.sub(r"^www\.", "", text).rstrip(".")
    return text


def dedupe_against_registry(
    found: Iterable[dict],
    known_companies: Iterable[str],
    known_domains: Iterable[str],
) -> list[dict]:
    """Drop web hits we already hold, and any duplicates within the batch."""
    seen_companies = {normalise_company(c) for c in known_companies if c}
    seen_domains = {normalise_domain(d) for d in known_domains if d}
    seen_companies.discard("")
    seen_domains.discard("")

    kept: list[dict] = []
    for item in found:
        company = normalise_company(item.get("company", ""))
        domain = normalise_domain(item.get("domain", ""))
        if not company:
            continue
        if company in seen_companies or (domain and domain in seen_domains):
            continue
        seen_companies.add(company)
        if domain:
            seen_domains.add(domain)
        kept.append(item)
    return kept


# ── Response parsing ─────────────────────────────────────────────────────────

def parse_results(text: str) -> list[dict]:
    """Pull the JSON array out of the model's reply.

    Structured outputs can't be combined with the citations web search emits,
    so the array is parsed from the text. Malformed output yields an empty
    list rather than an exception — a failed search should return nothing,
    not break the page.
    """
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        raw = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    results: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        company = str(item.get("company") or "").strip()
        if not company:
            continue
        results.append({
            "company": company,
            "domain": normalise_domain(str(item.get("domain") or "")),
            "rationale": str(item.get("why") or item.get("rationale") or "").strip(),
            "source_url": str(item.get("source_url") or item.get("url") or "").strip(),
        })
    return results


def to_suggestions(results: list[dict]) -> list[dict]:
    """Shape web hits like registry suggestions so the UI renders them the same.

    `vendor_id` stays None and `contactable` stays False: an external result
    has no registry row and no verified contact address.
    """
    return [{
        "vendor_id": None,
        "company": r["company"],
        "score": 0,
        "rationale": r.get("rationale") or "Found via web search — not verified.",
        "origin": "external",
        "external_domain": r.get("domain", ""),
        "external_source_url": r.get("source_url", ""),
        "contactable": False,
    } for r in results]


# ── Prompt ───────────────────────────────────────────────────────────────────

def build_query(tender: dict) -> str:
    title = (tender.get("solicitation_title") or "").strip()
    summary = (tender.get("summary_of_contract") or tender.get("gsin_description") or "").strip()
    requirement = " ".join((tender.get("requirements") or "").split())[:400]

    return (
        "Find companies that could supply the requirement below for a Government "
        "of Canada procurement. Prefer Canadian suppliers, manufacturers and "
        "authorised distributors that plausibly sell to federal departments.\n\n"
        f"Title: {title or '(not given)'}\n"
        f"Requirement: {summary or '(not given)'}\n"
        f"Line items: {requirement or '(none listed)'}\n\n"
        f"Return at most {MAX_RESULTS} companies as a JSON array, nothing else:\n"
        '[{"company": "...", "domain": "example.com", "why": "one sentence on '
        'what they supply", "source_url": "https://..."}]\n\n'
        "Only include companies you actually found in search results. Do not "
        "invent companies, domains or URLs. If nothing suitable is found, "
        "return []."
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def find_external_suppliers(
    tender: dict,
    known_companies: Iterable[str],
    known_domains: Iterable[str],
) -> list[dict]:
    """Search the web for suppliers not already in the registry.

    Raises SupplierSearchError when the search could not run at all (billing,
    quota, missing key) so the caller can tell that apart from "searched and
    found nothing".
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SupplierSearchError(
            "Anthropic API key is not configured, so web search is unavailable.")

    try:
        import anthropic

        client = anthropic.Anthropic(
            timeout=ANTHROPIC_TIMEOUT_SECONDS,
            max_retries=ANTHROPIC_MAX_RETRIES,
        )
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            tools=[{
                "type": WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": MAX_WEB_SEARCHES,
            }],
            messages=[{"role": "user", "content": build_query(tender)}],
        )
    except Exception as exc:
        if reason := api_unavailable_reason(exc):
            log.error("supplier_search unavailable: %s", reason)
            raise SupplierSearchError(reason) from exc
        if is_timeout(exc):
            # Say what actually happened. "Failed, try again" sends people
            # looking for a fault when the search was simply slow.
            log.warning("supplier_search timed out after %ss", ANTHROPIC_TIMEOUT_SECONDS)
            raise SupplierSearchError(
                f"Web search took longer than {int(ANTHROPIC_TIMEOUT_SECONDS)} seconds "
                "and was stopped. Try again, or add more detail to the tender "
                "requirement so the search can be narrower.") from exc
        log.warning("supplier_search failed: %s", exc)
        raise SupplierSearchError(
            "Web search failed. Please try again in a moment.") from exc

    # The reply interleaves search-result blocks with text; the JSON array is
    # in the text blocks.
    text = "".join(
        block.text for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    )

    results = parse_results(text)
    deduped = dedupe_against_registry(results, known_companies, known_domains)
    log.info("supplier_search found=%d after_dedupe=%d", len(results), len(deduped))
    return to_suggestions(deduped[:MAX_RESULTS])

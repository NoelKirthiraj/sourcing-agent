"""
Contact lookup for a single external supplier — the optional step after a
web search.

The search itself deliberately returns no contact details. It sees whatever
pages the search engine surfaced (directory listings, product catalogues,
homepages) and an email address is almost never on any of them; asking for one
anyway would get `info@<domain>`, which is well-formed, plausible, and
frequently wrong. A hallucinated vendor id fails loudly. A hallucinated address
fails silently into somebody's outbox.

So the address is fetched rather than guessed, and only for a supplier an
associate has pointed at. Eight suppliers searched, one or two looked up: the
pages nobody asked about are never pulled into context at all.

Costs roughly 2,500 input tokens for an average page — under a cent per
supplier — because `web_fetch` carries no per-use charge, unlike `web_search`
at $10 per 1,000.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from llm_errors import api_unavailable_reason
from supplier_search import SupplierSearchError, is_timeout, normalise_domain

log = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 1024

# Basic fetch. The dynamic-filtering variants run the fetch inside code
# execution, which is the same machinery that made web_search_20260209 hang on
# this codebase's prompts (see supplier_search.WEB_SEARCH_TOOL_TYPE). Not worth
# re-opening that for a tool whose whole job is one short page.
WEB_FETCH_TOOL_TYPE = "web_fetch_20250910"

# One page is the common case; four covers "homepage redirects to /contact-us
# which links the real address". Failed fetches count against this.
MAX_FETCHES = 4

# A contact page is small. This caps the damage when a URL turns out to be a
# 200-page PDF catalogue — roughly 10 kB of text, about a third of a cent.
MAX_CONTENT_TOKENS = 4000

# One fetch runs in a few seconds. 45s is generous for four, and well under the
# 90s the search itself gets.
ANTHROPIC_TIMEOUT_SECONDS = 45.0
ANTHROPIC_MAX_RETRIES = 0

# Enough to contact someone; more than this is scraping a staff directory.
MAX_EMAILS = 3
MAX_PHONES = 2

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Addresses that are real but useless to a procurement enquiry, plus the
# placeholders that turn up in page boilerplate.
_JUNK_LOCAL_PARTS = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "privacy", "unsubscribe", "abuse", "postmaster", "webmaster",
    "example", "email", "youremail", "name",
})
_JUNK_DOMAINS = frozenset({
    "example.com", "example.org", "domain.com", "yourdomain.com",
    "sentry.io", "wixpress.com", "squarespace.com",
})


def candidate_urls(domain: str, source_url: str = "") -> list[str]:
    """URLs worth offering the model, most specific first.

    `web_fetch` only accepts URLs that already appeared in the conversation —
    its own guard against a model fetching something it invented. Listing the
    likely contact paths in the prompt is what makes them reachable; without
    this the model can fetch the search hit and nothing else.
    """
    host = normalise_domain(domain)
    urls: list[str] = []
    if source_url.startswith("http"):
        urls.append(source_url)
    if host:
        urls += [
            f"https://{host}/contact",
            f"https://{host}/contact-us",
            f"https://{host}/",
        ]
    # Preserve order while dropping repeats — source_url is often the homepage.
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def clean_emails(values, domain: str = "") -> list[str]:
    """Keep plausible, useful addresses; drop boilerplate and duplicates.

    Addresses on the supplier's own domain sort first — a procurement enquiry
    should go to the company, not to the marketing agency whose address also
    appears in the page footer.
    """
    host = normalise_domain(domain)
    kept: list[str] = []
    seen = set()
    for raw in values or []:
        addr = str(raw or "").strip().strip("<>").lower()
        if not _EMAIL_RE.match(addr) or addr in seen:
            continue
        local, _, addr_domain = addr.partition("@")
        if local in _JUNK_LOCAL_PARTS or addr_domain in _JUNK_DOMAINS:
            continue
        seen.add(addr)
        kept.append(addr)
    kept.sort(key=lambda a: 0 if host and a.endswith("@" + host) else 1)
    return kept[:MAX_EMAILS]


def clean_phones(values) -> list[str]:
    """Keep anything with enough digits to dial; normalise the whitespace."""
    kept: list[str] = []
    seen = set()
    for raw in values or []:
        text = " ".join(str(raw or "").split())
        digits = re.sub(r"\D", "", text)
        if len(digits) < 7 or len(digits) > 15 or digits in seen:
            continue
        seen.add(digits)
        kept.append(text)
    return kept[:MAX_PHONES]


def build_prompt(company: str, domain: str, urls: list[str]) -> str:
    listed = "\n".join(f"- {u}" for u in urls) or "- (none)"
    return (
        f"Find the general business contact details for {company}"
        + (f" ({domain})" if domain else "")
        + ", a potential supplier for a Government of Canada procurement.\n\n"
        "Fetch these pages until you find them, then stop:\n"
        f"{listed}\n\n"
        "Return a sales, info or general enquiries address — the one a "
        "purchasing enquiry should go to. Do not return the address of a "
        "named individual unless it is the only one published.\n\n"
        "Reply with JSON and nothing else:\n"
        '{"emails": ["..."], "phones": ["..."], "contact_name": "", '
        '"source_url": "the page you took these from"}\n\n'
        "Copy the details exactly as they appear on the page. Never construct "
        "an address from the domain name, and never guess: an invented address "
        "is worse than none, because somebody will email it. If the pages do "
        "not publish contact details, return "
        '{"emails": [], "phones": [], "contact_name": "", "source_url": ""}.'
    )


def parse_contact(text: str, domain: str = "") -> dict:
    """Pull the JSON object out of the reply. Malformed output yields nothing."""
    empty = {"emails": [], "phones": [], "contact_name": "", "source_url": ""}
    if not text:
        return empty
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return empty
    try:
        raw = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty

    source = str(raw.get("source_url") or "").strip()
    return {
        "emails": clean_emails(raw.get("emails"), domain),
        "phones": clean_phones(raw.get("phones")),
        "contact_name": str(raw.get("contact_name") or "").strip()[:120],
        "source_url": source if source.startswith("http") else "",
    }


def find_contact(company: str, domain: str = "", source_url: str = "") -> dict:
    """Fetch a supplier's published contact details.

    Returns the empty shape when the pages publish nothing — that is an answer,
    not a failure. Raises SupplierSearchError only when the lookup could not
    run at all, so the caller can tell "looked and found nothing" apart from
    "could not look".
    """
    company = (company or "").strip()
    if not company:
        return {"emails": [], "phones": [], "contact_name": "", "source_url": ""}

    urls = candidate_urls(domain, source_url)
    if not urls:
        raise SupplierSearchError(
            "This result has no website, so there is no page to read a contact "
            "address from.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SupplierSearchError(
            "Anthropic API key is not configured, so contact lookup is "
            "unavailable.")

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
                "type": WEB_FETCH_TOOL_TYPE,
                "name": "web_fetch",
                "max_uses": MAX_FETCHES,
                "max_content_tokens": MAX_CONTENT_TOKENS,
            }],
            messages=[{
                "role": "user",
                "content": build_prompt(company, normalise_domain(domain), urls),
            }],
        )
    except Exception as exc:
        if reason := api_unavailable_reason(exc):
            log.error("supplier_contact unavailable: %s", reason)
            raise SupplierSearchError(reason) from exc
        if is_timeout(exc):
            log.warning("supplier_contact timed out company=%r", company)
            raise SupplierSearchError(
                f"Reading {company}'s website took longer than "
                f"{int(ANTHROPIC_TIMEOUT_SECONDS)} seconds and was stopped.") from exc
        log.warning("supplier_contact failed company=%r: %s", company, exc)
        raise SupplierSearchError(
            "Could not read the supplier's website. Please try again.") from exc

    text = "".join(
        block.text for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    )
    found = parse_contact(text, domain)
    log.info(
        "supplier_contact company=%r emails=%d phones=%d",
        company, len(found["emails"]), len(found["phones"]),
    )
    return found

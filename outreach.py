"""
Outreach drafts — builds the quotation-request email for a matched supplier.

v1 is template-only: no AI, no sending. The draft is assembled from fields the
agent already extracted (solicitation number, closing date, contract summary,
requirement lines) and handed to the associate to copy into their own mail
client. Nothing leaves the system.

Signed by the associate the tender is assigned to, so the supplier replies to a
person rather than a shared inbox.

Pure functions over plain dicts — no database, no network.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Requirement text can run to thousands of characters on a multi-line
# solicitation. A quotation request needs enough to let the supplier say
# yes or no, not the whole annex — the notice link carries the rest.
MAX_SUMMARY_CHARS = 900
MAX_REQUIREMENT_LINES = 12

SIGNATURE_ORG = "RAD Global Procurement"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _first_name(contact: str) -> str:
    """'Jane Doe' → 'Jane'. Falls back to a neutral greeting when the registry
    has no contact name, which is common for bulk-imported vendors."""
    name = _clean(contact)
    return name.split(" ")[0] if name else ""


def primary_recipient(vendor: dict) -> str:
    """First usable address from the vendor's `emails[]` array."""
    for email in vendor.get("emails") or []:
        address = _clean(email)
        if "@" in address:
            return address
    return ""


def _truncate(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a sentence end, then a word end, so the draft never stops mid-word.
    for boundary in (". ", " "):
        idx = cut.rfind(boundary)
        if idx > limit * 0.6:
            return cut[: idx + (1 if boundary == ". " else 0)].rstrip() + " […]"
    return cut.rstrip() + " […]"


def _requirement_lines(tender: dict) -> list[str]:
    """Requirement lines as written by the extractor, capped for readability."""
    raw = tender.get("requirements") or ""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", str(raw)) if ln.strip()]
    if len(lines) > MAX_REQUIREMENT_LINES:
        remaining = len(lines) - MAX_REQUIREMENT_LINES
        lines = lines[:MAX_REQUIREMENT_LINES] + [f"… and {remaining} further line item(s)"]
    return lines


def build_subject(tender: dict) -> str:
    title = _clean(tender.get("solicitation_title")) or "Government of Canada opportunity"
    sol_no = _clean(tender.get("solicitation_no"))
    subject = f"Request for quotation — {title}"
    if sol_no:
        subject += f" ({sol_no})"
    return subject


def build_body(tender: dict, vendor: dict, associate: str = "") -> str:
    """The draft body. Sections with no data are omitted rather than left blank —
    an email with an empty 'Closing:' line looks broken to a supplier."""
    company = _clean(vendor.get("company")) or "your team"
    greeting_name = _first_name((vendor.get("primary_contacts") or [""])[0]
                                if vendor.get("primary_contacts") else "")
    greeting = f"Hello {greeting_name}," if greeting_name else "Hello,"

    lines: list[str] = [
        greeting,
        "",
        f"RAD Global is preparing a response to a Government of Canada solicitation "
        f"and would like to request a quotation from {company}.",
        "",
    ]

    # Facts block — only the fields we actually have.
    facts: list[tuple[str, str]] = []
    if sol_no := _clean(tender.get("solicitation_no")):
        facts.append(("Solicitation", sol_no))
    if title := _clean(tender.get("solicitation_title")):
        facts.append(("Title", title))
    closing = _clean(tender.get("closing_date"))
    if closing:
        zone = _clean(tender.get("time_and_zone"))
        facts.append(("Closes", f"{closing} {zone}".strip()))
    if client := _clean(tender.get("client")):
        facts.append(("Issuing department", client))
    if facts:
        width = max(len(label) for label, _ in facts)
        lines += [f"{label.ljust(width)} : {value}" for label, value in facts]
        lines.append("")

    summary = _clean(tender.get("summary_of_contract")) or _clean(tender.get("gsin_description"))
    if summary:
        lines += ["Requirement", _truncate(summary, MAX_SUMMARY_CHARS), ""]

    if requirement_lines := _requirement_lines(tender):
        lines += ["Line items"] + [f"  - {ln}" for ln in requirement_lines] + [""]

    lines += [
        "Could you confirm whether you are able to quote on this requirement, and "
        "if so provide pricing, lead time, and any applicable certifications?",
        "",
    ]

    if link := _clean(tender.get("inquiry_link")):
        lines += [f"The full notice is available here: {link}", ""]

    signer = _clean(associate) or _clean(tender.get("assigned_associate"))
    lines += ["Thanks,", signer or SIGNATURE_ORG]
    if signer:
        lines.append(SIGNATURE_ORG)

    return "\n".join(lines)


def build_draft(tender: dict, vendor: dict, associate: str = "") -> dict[str, Any]:
    """Complete draft for one supplier.

    `missing_email` is surfaced rather than treated as an error — the associate
    may have the address elsewhere, and a draft they can still copy is more
    useful than a refusal.
    """
    recipient = primary_recipient(vendor)
    return {
        "vendor_id": vendor.get("id"),
        "company": _clean(vendor.get("company")),
        "to": recipient,
        "subject": build_subject(tender),
        "body": build_body(tender, vendor, associate),
        "missing_email": not recipient,
    }


def build_drafts(tender: dict, vendors: list[dict], associate: str = "") -> list[dict[str, Any]]:
    """One draft per selected supplier, in the order given."""
    return [build_draft(tender, vendor, associate) for vendor in vendors]

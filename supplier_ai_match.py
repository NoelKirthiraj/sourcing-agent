"""
AI supplier matching — the second rung of the escalation ladder.

Keyword matching runs first and is free. When it finds nothing (or nothing
convincing), the associate can ask for this, which reads the whole registry and
picks suppliers on meaning rather than shared vocabulary — a towbar supplier for
an "aircraft ground support equipment" tender, say, where no keyword bridges the
two.

Deliberately *not* a re-ranking of the keyword candidates: by the time this
runs there usually are none, so it has to consider every vendor.

Two safeguards matter more than the prompt:

  * The model is only allowed to return vendor ids from the roster it was
    given. Anything else is dropped — an invented supplier is worse than no
    supplier.
  * Only AI-sourced rows are replaced in storage, so an intelligent match never
    clears the keyword shortlist the associate is still looking at.

The roster is sent as a cached system block. It changes rarely, so matching a
second tender in the same session re-reads it at roughly a tenth of the price.
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
ANTHROPIC_MAX_TOKENS = 2048
ANTHROPIC_TIMEOUT_SECONDS = 90.0

# A shortlist the associate has to read by hand. More than this is noise.
MAX_RESULTS = 8

# Keeps one vendor's roster line short enough that the whole registry fits
# comfortably in a cached prefix.
MAX_PRODUCTS_PER_VENDOR = 6
MAX_LINE_CHARS = 180


class AiMatchError(Exception):
    """Raised when the match could not run. `user_message` is safe to surface."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


# ── Prompt construction ──────────────────────────────────────────────────────

def build_roster(vendors: Iterable[dict],
                 products_by_vendor: Optional[dict[int, list[str]]] = None) -> str:
    """One line per vendor: id, company, categories, products.

    Stable across tenders by construction — sorted by id, no timestamps — so it
    can be cached as a prompt prefix.
    """
    products_by_vendor = products_by_vendor or {}
    lines: list[str] = []
    for v in sorted(vendors, key=lambda x: x.get("id") or 0):
        vendor_id = v.get("id")
        if vendor_id is None:
            continue
        company = (v.get("company") or "").strip()
        if not company:
            continue
        cats = ", ".join(v.get("rfp_categories") or [])
        prods = ", ".join((products_by_vendor.get(vendor_id) or [])[:MAX_PRODUCTS_PER_VENDOR])
        line = f"{vendor_id}|{company}"
        if cats:
            line += f"|cats:{cats}"
        if prods:
            line += f"|products:{prods}"
        lines.append(line[:MAX_LINE_CHARS])
    return "\n".join(lines)


def build_task(tender: dict) -> str:
    title = (tender.get("solicitation_title") or "").strip()
    summary = (tender.get("summary_of_contract") or tender.get("gsin_description") or "").strip()
    requirements = " ".join((tender.get("requirements") or "").split())[:800]

    return (
        "A Government of Canada tender is below. From the supplier roster in "
        "the system prompt, pick the suppliers most likely to be able to quote "
        "on it.\n\n"
        f"Title: {title or '(not given)'}\n"
        f"Requirement: {summary or '(not given)'}\n"
        f"Line items: {requirements or '(none listed)'}\n\n"
        f"Return at most {MAX_RESULTS} suppliers as a JSON array, nothing else:\n"
        '[{"id": 12, "why": "one sentence on why this supplier fits", '
        '"confidence": "high|medium|low"}]\n\n'
        "Rules:\n"
        "- Use only ids that appear in the roster. Never invent a supplier.\n"
        "- Judge on what the supplier actually sells, not on word overlap.\n"
        "- Prefer a short, strong list over a long, speculative one.\n"
        "- If no supplier in the roster is a plausible fit, return []."
    )


SYSTEM_PREAMBLE = (
    "You match government procurement requirements to suppliers in a company's "
    "own vendor registry. You are given the full registry; each line is "
    "id|company|cats:…|products:…\n\n"
)


# ── Response handling ────────────────────────────────────────────────────────

_CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}


def parse_results(text: str, valid_ids: set[int]) -> list[dict]:
    """Pull the JSON array out of the reply, dropping anything unusable.

    Ids not in `valid_ids` are discarded: the registry is the authority on
    which suppliers exist, not the model.
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

    out: list[dict] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            vendor_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if vendor_id not in valid_ids or vendor_id in seen:
            continue
        seen.add(vendor_id)
        confidence = str(item.get("confidence") or "medium").strip().lower()
        out.append({
            "vendor_id": vendor_id,
            "why": str(item.get("why") or item.get("rationale") or "").strip(),
            "confidence": confidence if confidence in _CONFIDENCE_SCORE else "medium",
        })
    return out[:MAX_RESULTS]


def to_suggestions(results: list[dict], vendors_by_id: dict[int, dict]) -> list[dict]:
    """Shape AI hits like registry suggestions so the UI renders them the same.

    The rationale is explicitly attributed, because unlike the keyword stage
    this is a model's judgement rather than a reproducible fact.
    """
    out: list[dict] = []
    for r in results:
        vendor = vendors_by_id.get(r["vendor_id"])
        if not vendor:
            continue
        why = r["why"] or "Suggested by intelligent match."
        emails = [e for e in (vendor.get("emails") or []) if e]
        out.append({
            "vendor_id": r["vendor_id"],
            "company": (vendor.get("company") or "").strip(),
            "score": _CONFIDENCE_SCORE.get(r["confidence"], 2),
            "rationale": f"AI match ({r['confidence']} confidence): {why}",
            "origin": "ai",
            "external_domain": "",
            "external_source_url": "",
            "contactable": bool(emails),
        })
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def match_suppliers(
    tender: dict,
    vendors: list[dict],
    products_by_vendor: Optional[dict[int, list[str]]] = None,
) -> list[dict]:
    """Ask the model which registry suppliers fit this tender.

    Raises AiMatchError when the call could not run (billing, quota, missing
    key) so the caller can tell that apart from "looked and found nothing".
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AiMatchError(
            "Anthropic API key is not configured, so intelligent match is unavailable.")

    roster = build_roster(vendors, products_by_vendor)
    if not roster:
        return []

    valid_ids = {v["id"] for v in vendors if v.get("id") is not None}
    vendors_by_id = {v["id"]: v for v in vendors if v.get("id") is not None}

    try:
        import anthropic

        client = anthropic.Anthropic(timeout=ANTHROPIC_TIMEOUT_SECONDS)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": SYSTEM_PREAMBLE + roster,
                # The roster is identical between tenders, so later matches in
                # the same session read it at cache rates.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": build_task(tender)}],
        )
    except Exception as exc:
        if reason := api_unavailable_reason(exc):
            log.error("ai_match unavailable: %s", reason)
            raise AiMatchError(reason) from exc
        log.warning("ai_match failed: %s", exc)
        raise AiMatchError("Intelligent match failed. Please try again in a moment.") from exc

    text = "".join(
        block.text for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    )

    results = parse_results(text, valid_ids)
    usage = getattr(message, "usage", None)
    log.info("ai_match roster=%d returned=%d cache_read=%s",
             len(valid_ids), len(results),
             getattr(usage, "cache_read_input_tokens", "?") if usage else "?")
    return to_suggestions(results, vendors_by_id)

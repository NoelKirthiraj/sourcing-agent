"""
Supplier matching — finds registry vendors that fit an accepted tender.

v1 is deliberately SQL/keyword only, no AI (see docs/SCOPE_SUPPLIER_MATCHING.md).
Two consequences shape this module:

  * Scoring is deterministic and tunable, so the same tender always produces
    the same shortlist in the same order.
  * The rationale is a statement of fact — it names the exact keyword and
    product rows that caused the match — rather than a model's claim. An
    associate can verify a suggestion at a glance.

The bridge between tender prose and vendor records is `rfp_categories.keywords`,
which carries terms like "aircraft; aerospace; jet". It is matched against the
tender's title, GSIN description, extracted summary and requirement lines.

Everything here is a pure function over plain dicts — no database, no network —
so the scoring rules can be tested directly. db.py supplies the inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ── Scoring weights ──────────────────────────────────────────────────────────
# A product hit means this supplier has quoted this kind of item before, which
# is far stronger evidence than sharing a sector, so it is weighted 3x. The bid
# bonus breaks ties toward suppliers who have actually bid rather than ones who
# merely sit in the right category.
PRODUCT_MATCH_POINTS = 3
CATEGORY_MATCH_POINTS = 1
BID_HISTORY_BONUS = 1

# Short product names ("Kit", "Set") match almost any tender text and produce
# noise, so they are ignored as a matching signal.
MIN_PRODUCT_LENGTH = 4

# How many suggestions the UI pre-checks. The rest are returned too, behind
# "show more", unchecked.
DEFAULT_SHORTLIST = 5


@dataclass
class Suggestion:
    """One matched vendor, with everything needed to explain the match."""

    vendor_id: int
    company: str
    score: int
    matched_categories: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    matched_products: list[str] = field(default_factory=list)
    bid_count: int = 0
    emails: list[str] = field(default_factory=list)

    @property
    def contactable(self) -> bool:
        """A vendor we can match but not email is still worth showing — the
        associate may have the address elsewhere — but the UI flags it."""
        return bool(self.emails)

    def rationale(self) -> str:
        """Plain-language explanation naming the evidence, not a model's claim."""
        parts: list[str] = []
        if self.matched_categories:
            kw = f' (matched "{", ".join(self.matched_keywords[:3])}")' if self.matched_keywords else ""
            parts.append(f"Category: {', '.join(self.matched_categories)}{kw}")
        if self.matched_products:
            shown = ", ".join(self.matched_products[:3])
            more = f" +{len(self.matched_products) - 3} more" if len(self.matched_products) > 3 else ""
            parts.append(f"Products: {shown}{more}")
        if self.bid_count:
            parts.append(f"{self.bid_count} prior bid{'s' if self.bid_count != 1 else ''}")
        return ". ".join(parts) + "." if parts else "No match detail."

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "company": self.company,
            "score": self.score,
            "matched_categories": self.matched_categories,
            "matched_keywords": self.matched_keywords,
            "matched_products": self.matched_products,
            "bid_count": self.bid_count,
            "emails": self.emails,
            "contactable": self.contactable,
            "rationale": self.rationale(),
            "origin": "internal",
        }


# ── Text handling ────────────────────────────────────────────────────────────

def tender_text(tender: dict) -> str:
    """The haystack searched for keywords and product names.

    Deliberately includes the LLM-extracted summary and requirement lines —
    those carry the specific part names a title alone omits.
    """
    parts = [
        tender.get("solicitation_title") or "",
        tender.get("gsin_description") or "",
        tender.get("summary_of_contract") or "",
        tender.get("requirements") or "",
    ]
    return " ".join(parts).lower()


def _term_pattern(term: str) -> Optional[re.Pattern]:
    """Word-boundary matcher with an optional trailing plural.

    Bounded on both sides so "ship" doesn't match "shipping", but tolerant of
    a plural so "aircraft" still matches "aircrafts". Multi-word terms like
    "landing gear" work unchanged.
    """
    term = (term or "").strip().lower()
    if not term:
        return None
    return re.compile(rf"\b{re.escape(term)}s?\b")


def match_categories(text: str, categories: Iterable[dict]) -> dict[str, list[str]]:
    """Categories whose keywords appear in the text → the keywords that hit.

    Returning the hits (not just the category) is what lets the rationale name
    the specific word that caused the match.
    """
    # Lowercase here rather than trusting the caller — tender_text() already
    # does, but this function is also called directly.
    text = (text or "").lower()
    hits: dict[str, list[str]] = {}
    for cat in categories:
        name = (cat.get("category") or "").strip()
        if not name:
            continue
        matched = [
            kw.strip()
            for kw in (cat.get("keywords") or [])
            if (pat := _term_pattern(kw)) and pat.search(text)
        ]
        if matched:
            hits[name] = matched
    return hits


def _matching_products(text: str, products: Iterable[str]) -> list[str]:
    """Product names from this vendor that appear in the tender text."""
    found: list[str] = []
    for product in products:
        name = (product or "").strip()
        if len(name) < MIN_PRODUCT_LENGTH:
            continue
        pat = _term_pattern(name)
        if pat and pat.search(text) and name not in found:
            found.append(name)
    return found


# ── Ranking ──────────────────────────────────────────────────────────────────

def rank_vendors(
    tender: dict,
    categories: Iterable[dict],
    vendors: Iterable[dict],
    products_by_vendor: Optional[dict[int, list[str]]] = None,
    *,
    limit: Optional[int] = None,
) -> list[Suggestion]:
    """Score every vendor against the tender, best first.

    `products_by_vendor` maps vendor id → product names (from the
    products_by_vendor table). Vendors matching neither a category nor a
    product are dropped entirely rather than scored zero — a shortlist padded
    with non-matches is worse than a short one.

    Ties break on company name so the order is stable across runs.
    """
    text = tender_text(tender)
    if not text.strip():
        return []

    category_hits = match_categories(text, categories)
    matched_category_names = set(category_hits)
    products_by_vendor = products_by_vendor or {}

    suggestions: list[Suggestion] = []
    for vendor in vendors:
        vendor_id = vendor.get("id")
        if vendor_id is None:
            continue

        overlap = sorted(set(vendor.get("rfp_categories") or []) & matched_category_names)
        product_hits = _matching_products(text, products_by_vendor.get(vendor_id, []))
        if not overlap and not product_hits:
            continue

        bid_count = int(vendor.get("bid_count") or 0)
        score = (
            len(product_hits) * PRODUCT_MATCH_POINTS
            + len(overlap) * CATEGORY_MATCH_POINTS
            + (BID_HISTORY_BONUS if bid_count > 0 else 0)
        )

        # Keywords that earned this vendor its categories, de-duplicated in
        # first-seen order so the rationale reads naturally.
        keywords: list[str] = []
        for cat in overlap:
            for kw in category_hits.get(cat, []):
                if kw not in keywords:
                    keywords.append(kw)

        suggestions.append(Suggestion(
            vendor_id=vendor_id,
            company=(vendor.get("company") or "").strip(),
            score=score,
            matched_categories=overlap,
            matched_keywords=keywords,
            matched_products=product_hits,
            bid_count=bid_count,
            emails=[e for e in (vendor.get("emails") or []) if e],
        ))

    suggestions.sort(key=lambda s: (-s.score, s.company.lower()))
    return suggestions[:limit] if limit else suggestions

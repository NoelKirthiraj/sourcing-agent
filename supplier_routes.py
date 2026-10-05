"""
Supplier matching routes — HTTP handlers for /api/tenders/<id>/suppliers|outreach.

Mirrors vendor_routes.py's shape. Each handler takes the request handler
instance (for response helpers) plus parsed inputs and writes the response.
The api.py dispatcher decides routing.

Endpoints:
  POST   /api/tenders/<id>/suggest-suppliers   → 200 {suggestions, counts}
  GET    /api/tenders/<id>/suggestions          → 200 {internal, external}
  POST   /api/tenders/<id>/outreach             JSON: {vendor_ids, associate?}
                                                → 201 [drafts]
  GET    /api/tenders/<id>/outreach             → 200 [drafts already generated]

Matching is SQL/keyword only and runs in well under a second on a registry of
this size, so unlike PO extraction there is no async job to poll.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import db
import outreach as outreach_mod
import supplier_matcher
import supplier_search

log = logging.getLogger(__name__)

# Matching runs over the whole registry; this caps what we store and return so
# a tender matching a very broad category can't produce an unusable page.
MAX_STORED_SUGGESTIONS = 50

# Guards the outreach payload — an associate selecting more than this in one
# go is a mis-click, not a workflow.
MAX_OUTREACH_RECIPIENTS = 25


def _parse_tender_id(handler, raw: str) -> Optional[int]:
    """Validate the path segment, responding 400 on garbage."""
    try:
        tender_id = int(raw)
        if tender_id <= 0:
            raise ValueError
        return tender_id
    except (TypeError, ValueError):
        handler._json_response({"error": "invalid tender id"}, 400)
        return None


# ── Matching ─────────────────────────────────────────────────────────────────

def handle_suggest(handler, _run_async, raw_tender_id: str) -> None:
    """POST /api/tenders/<id>/suggest-suppliers — run the registry match.

    Button-triggered rather than automatic on accept, so a tender nobody
    intends to source costs nothing.
    """
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    tender = _run_async(db.get_tender(tender_id))
    if not tender:
        handler._json_response({"error": "tender not found"}, 404)
        return

    categories, vendors, products_by_vendor = _run_async(db.get_matching_inputs())

    if not any(c.get("keywords") for c in categories):
        # Without keywords there is no bridge from tender prose to vendor
        # categories. Say so plainly — this is a data gap the sourcing team
        # can close, not a system error to debug.
        handler._json_response({
            "suggestions": [],
            "total": 0,
            "shortlist_size": supplier_matcher.DEFAULT_SHORTLIST,
            "warning": "No RFP category has keywords configured, so suppliers "
                       "cannot be matched. Add keywords in the Vendors tab.",
        })
        return

    ranked = supplier_matcher.rank_vendors(
        tender, categories, vendors, products_by_vendor,
        limit=MAX_STORED_SUGGESTIONS,
    )
    payload = [s.to_dict() for s in ranked]

    _run_async(db.replace_suggestions(tender_id, payload, origin="internal"))

    log.info("supplier.match tender=%s candidates=%d vendors=%d",
             tender_id, len(payload), len(vendors))

    handler._json_response({
        "suggestions": payload,
        "total": len(payload),
        "shortlist_size": supplier_matcher.DEFAULT_SHORTLIST,
        "contactable": sum(1 for s in payload if s["contactable"]),
    })


def handle_suggest_external(handler, _run_async, raw_tender_id: str) -> None:
    """POST /api/tenders/<id>/suggest-suppliers/external — search the web.

    Separate endpoint and separate button because this costs money per use and
    returns unverified leads. Results are stored under origin='external' so the
    registry shortlist is untouched, and they never become vendor rows here.
    """
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    tender = _run_async(db.get_tender(tender_id))
    if not tender:
        handler._json_response({"error": "tender not found"}, 404)
        return

    _, vendors, _ = _run_async(db.get_matching_inputs())
    known_companies = [v.get("company", "") for v in vendors]
    known_domains = [v.get("domain", "") for v in vendors if v.get("domain")]

    try:
        suggestions = supplier_search.find_external_suppliers(
            tender, known_companies, known_domains)
    except supplier_search.SupplierSearchError as exc:
        # Billing, quota and missing-key failures are reported as themselves
        # rather than as "no suppliers found".
        handler._json_response({"error": exc.user_message}, 503)
        return

    _run_async(db.replace_suggestions(tender_id, suggestions, origin="external"))

    log.info("supplier.external tender=%s results=%d", tender_id, len(suggestions))
    handler._json_response({
        "suggestions": suggestions,
        "total": len(suggestions),
        "note": "Web results are unverified and are not in the vendor registry.",
    })


def handle_list_suggestions(handler, _run_async, raw_tender_id: str) -> None:
    """GET /api/tenders/<id>/suggestions — previously stored shortlist.

    Internal and external results are returned in separate lists so the UI
    cannot accidentally present an unvetted web result as a registry supplier.
    """
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    rows = _run_async(db.list_suggestions(tender_id))
    handler._json_response({
        "internal": [r for r in rows if r.get("origin") == "internal"],
        "external": [r for r in rows if r.get("origin") == "external"],
        "shortlist_size": supplier_matcher.DEFAULT_SHORTLIST,
    })


# ── Outreach ─────────────────────────────────────────────────────────────────

def handle_create_outreach(handler, _run_async, raw_tender_id: str, body: dict) -> None:
    """POST /api/tenders/<id>/outreach — generate drafts for selected suppliers.

    Drafts are template-built and returned for copy/paste; nothing is sent.
    Generating a draft is what counts as contact, so this also increments the
    vendor's inquiry_count and sets last_contact.
    """
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    raw_ids = body.get("vendor_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        handler._json_response({"error": "vendor_ids must be a non-empty list"}, 400)
        return
    if len(raw_ids) > MAX_OUTREACH_RECIPIENTS:
        handler._json_response(
            {"error": f"at most {MAX_OUTREACH_RECIPIENTS} suppliers per batch"}, 400)
        return

    vendor_ids: list[int] = []
    for value in raw_ids:
        try:
            vendor_id = int(value)
        except (TypeError, ValueError):
            handler._json_response({"error": f"invalid vendor id: {value!r}"}, 400)
            return
        if vendor_id not in vendor_ids:      # tolerate double-clicks
            vendor_ids.append(vendor_id)

    tender = _run_async(db.get_tender(tender_id))
    if not tender:
        handler._json_response({"error": "tender not found"}, 404)
        return

    vendors = _run_async(db.get_vendors_by_ids(vendor_ids))
    if not vendors:
        handler._json_response({"error": "no matching vendors found"}, 404)
        return

    # The draft is signed by the person doing the sourcing. Fall back to the
    # tender's assigned associate when the client doesn't name one.
    associate = (body.get("associate") or tender.get("assigned_associate") or "").strip()

    drafts = outreach_mod.build_drafts(tender, vendors, associate)
    stored = _run_async(db.create_outreach(tender_id, drafts, created_by=associate))

    # Merge the stored row ids back onto the drafts so the UI can reference
    # them without a second fetch.
    by_company = {row["company"]: row for row in stored}
    for draft in drafts:
        if row := by_company.get(draft["company"]):
            draft["id"] = row["id"]
            draft["created_at"] = row.get("created_at")

    missing = [d["company"] for d in drafts if d["missing_email"]]
    if missing:
        log.info("outreach.missing_email tender=%s companies=%s", tender_id, missing)

    log.info("outreach.created tender=%s drafts=%d by=%s",
             tender_id, len(drafts), associate or "unknown")

    handler._json_response({
        "drafts": drafts,
        "created": len(drafts),
        "missing_email": missing,
    }, 201)


def handle_list_outreach(handler, _run_async, raw_tender_id: str) -> None:
    """GET /api/tenders/<id>/outreach — drafts already generated."""
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    handler._json_response(_run_async(db.list_outreach(tender_id)))

"""
Supplier matching routes — HTTP handlers for /api/tenders/<id>/suppliers|outreach.

Mirrors vendor_routes.py's shape. Each handler takes the request handler
instance (for response helpers) plus parsed inputs and writes the response.
The api.py dispatcher decides routing.

Escalation ladder — each rung is a deliberate click, cheapest first:
  1. keyword match over the registry   free, instant
  2. AI match over the registry        a few cents, ~10-30s
  3. external web search               ~$0.10, 30-120s, async + polled

Endpoints:
  POST  /api/tenders/<id>/suggest-suppliers              → 200 {suggestions, counts}
  POST  /api/tenders/<id>/suggest-suppliers/ai           → 200 {suggestions, counts}
  POST  /api/tenders/<id>/suggest-suppliers/external     → 202 {job_id}
  GET   /api/tenders/<id>/suggest-suppliers/external/status/<job_id>
                                                         → 200 {status, elapsed, ...}
  GET   /api/tenders/<id>/suggestions                    → 200 {internal, ai, external}
  POST  /api/tenders/<id>/outreach   JSON: {vendor_ids, associate?} → 201 [drafts]
  GET   /api/tenders/<id>/outreach                       → 200 [drafts]

Keyword and AI matching answer inline. Only the web search is a polled job —
it is the one rung that takes minutes.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import db
import jobs
import outreach as outreach_mod
import supplier_ai_match
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


def handle_suggest_ai(handler, _run_async, raw_tender_id: str) -> None:
    """POST /api/tenders/<id>/suggest-suppliers/ai — second rung of the ladder.

    Reads the whole registry and picks on meaning rather than shared
    vocabulary. Costs a few cents, so it is only ever reached by an explicit
    click after keyword matching has had its turn.

    Stored under origin='ai', which leaves any keyword shortlist intact.
    """
    tender_id = _parse_tender_id(handler, raw_tender_id)
    if tender_id is None:
        return

    tender = _run_async(db.get_tender(tender_id))
    if not tender:
        handler._json_response({"error": "tender not found"}, 404)
        return

    _, vendors, products_by_vendor = _run_async(db.get_matching_inputs())

    try:
        suggestions = supplier_ai_match.match_suppliers(
            tender, vendors, products_by_vendor)
    except supplier_ai_match.AiMatchError as exc:
        handler._json_response({"error": exc.user_message}, 503)
        return

    _run_async(db.replace_suggestions(tender_id, suggestions, origin="ai"))

    log.info("supplier.ai tender=%s results=%d roster=%d",
             tender_id, len(suggestions), len(vendors))
    handler._json_response({
        "suggestions": suggestions,
        "total": len(suggestions),
        "contactable": sum(1 for s in suggestions if s["contactable"]),
    })


def handle_suggest_external(handler, _run_async, raw_tender_id: str) -> None:
    """POST /api/tenders/<id>/suggest-suppliers/external — start a web search.

    Returns 202 with a job id immediately rather than holding the connection.
    The search itself takes 30–120s; running it inline is what took the whole
    API down on 2026-10-05, and even with a threaded server a two-minute
    request is a poor way to report progress. The client polls
    .../external/status/<job_id>, which also carries elapsed seconds so the UI
    can be honest about the wait.

    Results are stored under origin='external' and never become vendor rows.
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

    job_id = jobs.create()
    log.info("supplier.external.job.created tender=%s job=%s", tender_id, job_id)

    def _run_search() -> None:
        jobs.mark_running(job_id)
        try:
            suggestions = supplier_search.find_external_suppliers(
                tender, known_companies, known_domains)
        except supplier_search.SupplierSearchError as exc:
            # Billing, quota and missing-key failures are reported as
            # themselves rather than as "no suppliers found".
            log.warning("supplier.external.job.failed job=%s: %s", job_id, exc.user_message)
            jobs.mark_failed(job_id, exc.user_message, 503)
            return
        except Exception as exc:                      # last-resort safety net
            log.exception("supplier.external.job.unexpected job=%s: %s", job_id, exc)
            jobs.mark_failed(job_id, "Web search failed unexpectedly.", 500)
            return

        try:
            _run_async(db.replace_suggestions(tender_id, suggestions, origin="external"))
        except Exception as exc:
            log.exception("supplier.external.job.store_failed job=%s: %s", job_id, exc)
            jobs.mark_failed(job_id, "Search succeeded but results could not be saved.", 500)
            return

        log.info("supplier.external.job.done job=%s results=%d", job_id, len(suggestions))
        jobs.mark_done(job_id, {
            "suggestions": suggestions,
            "total": len(suggestions),
            "note": "Web results are unverified and are not in the vendor registry.",
        })

    threading.Thread(target=_run_search, daemon=True,
                     name=f"supplier-search-{job_id[:8]}").start()

    handler._json_response({"job_id": job_id, "status": "pending"}, 202)


def handle_external_status(handler, _run_async, job_id: str) -> None:
    """GET /api/tenders/<id>/suggest-suppliers/external/status/<job_id>.

    Always 200 for a known job so the client can distinguish a poll-transport
    error from a search outcome. `elapsed` drives the progress indicator.
    """
    job = jobs.get(job_id)
    if job is None:
        handler._json_response({"error": "job not found"}, 404)
        return

    status = job["status"]
    elapsed = round(time.time() - job["started_at"], 1)

    if status in ("pending", "running"):
        handler._json_response({"status": status, "elapsed": elapsed})
        return
    if status == "done":
        handler._json_response({"status": "done", "elapsed": elapsed, **job["result"]})
        return
    handler._json_response({
        "status": "failed",
        "elapsed": elapsed,
        "error": job.get("error") or "Unknown error",
        "error_status": job.get("error_status") or 500,
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
        "ai": [r for r in rows if r.get("origin") == "ai"],
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

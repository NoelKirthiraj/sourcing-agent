"""
Phase 2 submit module — submits accepted tenders to CFlow.
Called from the dashboard (on accept) or via CLI (--submit-accepted).
"""
import logging
from typing import Any

import db
from cflow_client import CFlowClient, CFlowConfig

log = logging.getLogger(__name__)


async def submit_tender(tender_id: int, cflow: CFlowClient) -> bool:
    """Submit an accepted tender to CFlow and upload solicitation files.

    Returns True if submission succeeded.
    """
    tender = await db.get_tender(tender_id)
    if not tender:
        log.error("Tender %d not found", tender_id)
        return False

    if tender["status"] not in ("accepted", "pending_review"):
        log.warning("Tender %d has status '%s' — skipping", tender_id, tender["status"])
        return False

    # Build a dict matching the format cflow_client._build_payload() expects
    tender_dict = _db_row_to_tender_dict(tender)

    try:
        record_id = await cflow.create_sourcing_request(tender_dict)
        log.info("✓ CFlow record %s created for tender %d (%s)", record_id, tender_id, tender["solicitation_no"])

        # Upload solicitation file if available
        sol_path = tender.get("solicitation_path", "")
        if sol_path:
            try:
                await cflow.attach_solicitation(record_id, sol_path)
            except Exception as exc:
                log.warning("  File upload failed for tender %d: %s", tender_id, exc)

        await db.mark_submitted(tender_id, record_id)
        return True

    except Exception as exc:
        log.error("✗ CFlow submission failed for tender %d: %s", tender_id, exc)
        return False


async def submit_all_accepted(cflow: CFlowClient) -> dict[str, int]:
    """Submit all accepted tenders to CFlow. Returns counts."""
    tenders = await db.list_tenders(status="accepted")
    submitted = 0
    failed = 0

    for tender in tenders:
        success = await submit_tender(tender["id"], cflow)
        if success:
            submitted += 1
        else:
            failed += 1

    log.info("Submitted %d, failed %d out of %d accepted tenders", submitted, failed, len(tenders))
    return {"submitted": submitted, "failed": failed, "total": len(tenders)}


def _db_row_to_tender_dict(row: dict) -> dict[str, Any]:
    """Convert a PostgreSQL row dict to the format expected by cflow_client."""
    return {
        "solicitation_title": row.get("solicitation_title", ""),
        "solicitation_no": row.get("solicitation_no", ""),
        "inquiry_link": row.get("inquiry_link", ""),
        "closing_date": row.get("closing_date", ""),
        "time_and_zone": row.get("time_and_zone", ""),
        "client": row.get("client", ""),
        "contact_name": row.get("contact_name", ""),
        "contact_email": row.get("contact_email", ""),
        "contact_phone": row.get("contact_phone", ""),
        "gsin_description": row.get("gsin", ""),
        "bid_platform": row.get("bid_platform", ""),
        "notifications": row.get("notifications", ""),
        # Phase 2 extraction fields
        "summary_of_contract": row.get("summary_of_contract", ""),
        "requirements": row.get("requirements", ""),
        "mandatory_criteria": row.get("mandatory_criteria", ""),
        "submission_method": row.get("submission_method", ""),
        "file_type": row.get("file_type", ""),
        "assigned_associate": row.get("assigned_associate", ""),
    }

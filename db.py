"""
PostgreSQL database layer for Phase 2 — replaces JSON state file.
Stores tenders with status workflow, associate assignments, and extraction results.

Requires DATABASE_URL environment variable. Falls back to JSON state if not set.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# Lazy import — asyncpg only required when DATABASE_URL is set
_pool = None


async def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        import asyncpg
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL not set — cannot connect to PostgreSQL")
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        log.info("PostgreSQL connection pool created")
    return _pool


async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL connection pool closed")


async def init_schema():
    """Create tables if they don't exist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tenders (
                id                  SERIAL PRIMARY KEY,
                solicitation_no     VARCHAR(100) UNIQUE,
                solicitation_title  TEXT,
                inquiry_link        TEXT,
                closing_date        VARCHAR(20),
                time_and_zone       VARCHAR(50),
                client              TEXT,
                contact_name        TEXT,
                contact_email       TEXT,
                contact_phone       TEXT,
                gsin                TEXT,
                bid_platform        VARCHAR(20) DEFAULT 'CanadaBuys',
                file_type           VARCHAR(20) DEFAULT '',
                submission_method   VARCHAR(50) DEFAULT '',
                summary_of_contract TEXT DEFAULT '',
                requirements        TEXT DEFAULT '',
                mandatory_criteria  TEXT DEFAULT '',
                solicitation_path   TEXT DEFAULT '',
                requirements_csv_path TEXT DEFAULT '',
                assigned_associate  VARCHAR(50) DEFAULT '',
                status              VARCHAR(20) DEFAULT 'pending_review',
                cflow_record_id     VARCHAR(50) DEFAULT '',
                notifications       TEXT DEFAULT '',
                processing_notes    TEXT DEFAULT '',
                scraped_at          TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at         TIMESTAMPTZ,
                submitted_at        TIMESTAMPTZ,
                created_at          TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS associates (
                id                  SERIAL PRIMARY KEY,
                name                VARCHAR(50) UNIQUE NOT NULL,
                active              BOOLEAN DEFAULT TRUE,
                last_assigned_at    TIMESTAMPTZ
            );

            -- Seed associates if table is empty
            INSERT INTO associates (name)
            SELECT name FROM (VALUES
                ('Charles Radovic'),
                ('Edouard Radovic'),
                ('Richard Radovic')
            ) AS seed(name)
            WHERE NOT EXISTS (SELECT 1 FROM associates LIMIT 1);
        """)
        # Migrations — add columns to existing tables
        await conn.execute("""
            ALTER TABLE tenders ADD COLUMN IF NOT EXISTS processing_notes TEXT DEFAULT '';
        """)

        # Purchase Orders (PO generator feature)
        # UUIDs are generated in Python (uuid.uuid4()) rather than via
        # gen_random_uuid() so we don't need CREATE EXTENSION pgcrypto —
        # which fails on managed Postgres tiers (Railway free, Supabase,
        # Heroku) where the app role isn't a superuser.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pos (
                id                  SERIAL PRIMARY KEY,
                uuid                UUID NOT NULL UNIQUE,
                po_number           TEXT NOT NULL,
                revision            INTEGER NOT NULL DEFAULT 1,
                tender_id           INTEGER REFERENCES tenders(id) ON DELETE SET NULL,
                contract_no         TEXT DEFAULT '',
                supplier_name       TEXT DEFAULT '',
                quote_no            TEXT DEFAULT '',
                currency            VARCHAR(8) DEFAULT 'USD',
                subtotal_cents      BIGINT DEFAULT 0,
                total_cents         BIGINT DEFAULT 0,
                item_count          INTEGER DEFAULT 0,
                status              VARCHAR(20) DEFAULT 'generated',
                contract_pdf        BYTEA,
                quote_pdf           BYTEA,
                contract_filename   TEXT DEFAULT '',
                quote_filename      TEXT DEFAULT '',
                extracted_contract  JSONB,
                extracted_quote     JSONB,
                draft_json          JSONB NOT NULL,
                rendered_docx       BYTEA,
                warnings            JSONB DEFAULT '[]'::jsonb,
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                generated_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (po_number, revision)
            );

            CREATE INDEX IF NOT EXISTS idx_pos_uuid ON pos(uuid);
            CREATE INDEX IF NOT EXISTS idx_pos_tender ON pos(tender_id);
            CREATE INDEX IF NOT EXISTS idx_pos_generated_at ON pos(generated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pos_po_number ON pos(po_number);
        """)

        # Migration — widen po_number to TEXT if an earlier deploy created
        # it as VARCHAR(60). The previous type caused asyncpg parameter
        # inference errors ("text versus character varying") because the
        # po_number is used both as an INSERT value and in a subquery
        # comparison within the same statement.
        await conn.execute("""
            ALTER TABLE pos ALTER COLUMN po_number TYPE TEXT;
        """)
        log.info("Database schema initialized")


# ── Tender CRUD ──────────────────────────────────────────────────────────────

async def tender_exists(solicitation_no: str) -> bool:
    """Check if a tender with this solicitation_no exists (any status except rejected)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM tenders WHERE solicitation_no = $1",
            solicitation_no,
        )
        return row is not None


async def tender_exists_by_link(inquiry_link: str) -> bool:
    """Check if a tender with this inquiry_link exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM tenders WHERE inquiry_link = $1",
            inquiry_link,
        )
        return row is not None


async def stage_tender(tender: dict[str, Any], assigned_associate: str = "") -> int:
    """Insert a new tender with status='pending_review'. Returns the tender ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO tenders (
                solicitation_no, solicitation_title, inquiry_link,
                closing_date, time_and_zone, client,
                contact_name, contact_email, contact_phone,
                gsin, bid_platform, notifications,
                assigned_associate, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'pending_review')
            ON CONFLICT (solicitation_no) DO NOTHING
            RETURNING id
        """,
            tender.get("solicitation_no", ""),
            tender.get("solicitation_title", ""),
            tender.get("inquiry_link", ""),
            tender.get("closing_date", ""),
            tender.get("time_and_zone", ""),
            tender.get("client", ""),
            tender.get("contact_name", ""),
            tender.get("contact_email", ""),
            tender.get("contact_phone", ""),
            tender.get("gsin_description", ""),
            tender.get("bid_platform", "CanadaBuys"),
            tender.get("notifications", ""),
            assigned_associate,
        )
        return row["id"] if row else 0


async def add_processing_note(tender_id: int, note: str):
    """Append a note to the tender's processing_notes field."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tenders SET processing_notes =
                CASE WHEN processing_notes = '' THEN $2
                     ELSE processing_notes || E'\n' || $2
                END
            WHERE id = $1
        """, tender_id, note)


async def update_tender_extraction(
    tender_id: int,
    *,
    summary: str = "",
    requirements: str = "",
    mandatory_criteria: str = "",
    submission_method: str = "",
    file_type: str = "",
    solicitation_path: str = "",
    requirements_csv_path: str = "",
    requirements_csv: str = "",
):
    """Update a tender with LLM extraction results."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tenders SET
                summary_of_contract = $2,
                requirements = $3,
                mandatory_criteria = $4,
                submission_method = $5,
                file_type = $6,
                solicitation_path = $7,
                requirements_csv_path = $8,
                requirements_csv = $9
            WHERE id = $1
        """, tender_id, summary, requirements, mandatory_criteria,
            submission_method, file_type, solicitation_path, requirements_csv_path,
            requirements_csv)


async def accept_tender(tender_id: int) -> Optional[dict]:
    """Mark a tender as accepted and assign an associate via round-robin.
    Allows transition from pending_review or rejected. Returns the tender record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Round-robin: pick the associate with the fewest active (accepted) tenders
        associate_row = await conn.fetchrow("""
            SELECT a.name FROM associates a
            LEFT JOIN tenders t ON t.assigned_associate = a.name AND t.status IN ('accepted', 'submitted')
            WHERE a.active = TRUE
            GROUP BY a.name, a.last_assigned_at
            ORDER BY COUNT(t.id) ASC, a.last_assigned_at ASC NULLS FIRST
            LIMIT 1
        """)
        assigned = associate_row["name"] if associate_row else ""

        row = await conn.fetchrow("""
            UPDATE tenders SET status = 'accepted', reviewed_at = NOW(), assigned_associate = $2
            WHERE id = $1 AND status IN ('pending_review', 'rejected')
            RETURNING *
        """, tender_id, assigned)

        if row and assigned:
            await conn.execute(
                "UPDATE associates SET last_assigned_at = NOW() WHERE name = $1",
                assigned,
            )

        return dict(row) if row else None


async def reject_tender(tender_id: int, reason: str = "") -> bool:
    """Mark a tender as rejected and unassign associate.
    Allows transition from pending_review or accepted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE tenders SET status = 'rejected', reviewed_at = NOW(), assigned_associate = ''
            WHERE id = $1 AND status IN ('pending_review', 'accepted')
        """, tender_id)
        return result == "UPDATE 1"


async def revert_to_pending(tender_id: int) -> bool:
    """Move a tender back to pending_review and unassign associate.
    Allows transition from accepted or rejected."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE tenders SET status = 'pending_review', reviewed_at = NULL, assigned_associate = ''
            WHERE id = $1 AND status IN ('accepted', 'rejected')
        """, tender_id)
        return result == "UPDATE 1"


async def reassign_tender(tender_id: int, new_associate: str) -> bool:
    """Reassign an accepted tender to a different associate."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE tenders SET assigned_associate = $2
            WHERE id = $1 AND status = 'accepted'
        """, tender_id, new_associate)
        return result == "UPDATE 1"


async def mark_submitted(tender_id: int, cflow_record_id: str):
    """Mark a tender as submitted to CFlow."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tenders SET status = 'submitted', cflow_record_id = $2, submitted_at = NOW()
            WHERE id = $1
        """, tender_id, cflow_record_id)


async def get_tender(tender_id: int) -> Optional[dict]:
    """Get a single tender by ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tenders WHERE id = $1", tender_id)
        return dict(row) if row else None


async def count_tenders() -> dict[str, int]:
    """Return live tender counts grouped by status.

    Used by the dashboard so the "Total Captured" metric and the Tender
    Review tab agree even if run_history.json drifts (e.g. a scrape that
    didn't checkpoint cleanly).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM tenders GROUP BY status"
        )
    counts = {r["status"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


async def list_tenders(
    status: str = "",
    associate: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List tenders with optional filters."""
    pool = await get_pool()
    conditions = []
    params = []
    idx = 1

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if associate:
        conditions.append(f"assigned_associate = ${idx}")
        params.append(associate)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM tenders {where} ORDER BY scraped_at DESC LIMIT ${idx} OFFSET ${idx+1}",
            *params,
        )
        return [dict(r) for r in rows]


# ── Associate queries ────────────────────────────────────────────────────────

async def get_next_associate() -> Optional[str]:
    """Get the next associate for round-robin assignment."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT name FROM associates
            WHERE active = TRUE
            ORDER BY last_assigned_at ASC NULLS FIRST
            LIMIT 1
        """)
        if row:
            await conn.execute(
                "UPDATE associates SET last_assigned_at = NOW() WHERE name = $1",
                row["name"],
            )
            return row["name"]
        return None


async def list_associates() -> list[dict]:
    """List all associates with their workload counts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                a.name,
                a.active,
                a.last_assigned_at,
                COUNT(t.id) FILTER (WHERE t.status IN ('pending_review', 'accepted')) AS active_tenders,
                COUNT(t.id) FILTER (WHERE t.status = 'pending_review') AS pending_count,
                COUNT(t.id) FILTER (WHERE t.status = 'accepted') AS accepted_count,
                COUNT(t.id) FILTER (WHERE t.status = 'submitted') AS submitted_count
            FROM associates a
            LEFT JOIN tenders t ON t.assigned_associate = a.name
            GROUP BY a.id, a.name, a.active, a.last_assigned_at
            ORDER BY a.name
        """)
        return [dict(r) for r in rows]


# ── Migration helper ─────────────────────────────────────────────────────────

async def migrate_from_json(json_path: str):
    """Import existing processed_solicitations.json into PostgreSQL."""
    import json
    from pathlib import Path

    path = Path(json_path)
    if not path.exists():
        log.info("No JSON state file to migrate: %s", json_path)
        return 0

    data = json.loads(path.read_text(encoding="utf-8"))
    pool = await get_pool()
    migrated = 0

    async with pool.acquire() as conn:
        for sol_no, entry in data.items():
            try:
                row = await conn.fetchrow("""
                    INSERT INTO tenders (
                        solicitation_no, solicitation_title, inquiry_link,
                        cflow_record_id, status, scraped_at
                    ) VALUES ($1, $2, $3, $4, 'submitted', $5)
                    ON CONFLICT (solicitation_no) DO NOTHING
                    RETURNING id
                """,
                    sol_no,
                    entry.get("title", ""),
                    entry.get("link", ""),
                    entry.get("cflow_request_id", ""),
                    entry.get("processed_at", datetime.now(timezone.utc).isoformat()),
                )
                if row:
                    migrated += 1
            except Exception as exc:
                log.warning("Failed to migrate %s: %s", sol_no, exc)

    log.info("Migrated %d/%d entries from JSON to PostgreSQL", migrated, len(data))
    return migrated


# ── Purchase Order CRUD ──────────────────────────────────────────────────────

async def insert_po(
    *,
    po_number: str,
    draft: dict[str, Any],
    extracted_contract: dict[str, Any],
    extracted_quote: dict[str, Any],
    contract_pdf: Optional[bytes],
    quote_pdf: Optional[bytes],
    contract_filename: str,
    quote_filename: str,
    rendered_docx: bytes,
    warnings: list[str],
    tender_id: Optional[int] = None,
) -> dict:
    """Insert a new PO record. Auto-bumps revision on po_number collision.

    Returns a dict with at least: id, uuid, po_number, revision.
    """
    import json as _json

    pool = await get_pool()
    subtotal_cents = int(round(float(draft.get("subtotal", 0)) * 100))
    total_cents = int(round(float(draft.get("total", 0)) * 100))
    item_count = len(draft.get("items", []))
    currency = draft.get("currency", "USD")
    supplier_name = (draft.get("supplier") or {}).get("name", "") or ""
    contract_no = draft.get("contract_no", "") or ""
    quote_no = draft.get("quote_no", "") or ""

    import uuid as _uuid
    try:
        import asyncpg as _asyncpg
    except ImportError:  # pragma: no cover — pool can't exist without asyncpg
        _asyncpg = None

    # Revision assignment uses a single INSERT with a SELECT subquery for the
    # revision number, plus a small retry loop on UniqueViolationError. The
    # subquery + retry combination keeps us safe under concurrent writes even
    # if we ever move off the single-threaded HTTP server.
    new_uuid = _uuid.uuid4()
    last_err = None
    async with pool.acquire() as conn:
        for attempt in range(3):
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO pos (
                        uuid, po_number, revision, tender_id,
                        contract_no, supplier_name, quote_no,
                        currency, subtotal_cents, total_cents, item_count,
                        contract_pdf, quote_pdf, contract_filename, quote_filename,
                        extracted_contract, extracted_quote, draft_json,
                        rendered_docx, warnings
                    ) VALUES (
                        $1,
                        $2,
                        (SELECT COALESCE(MAX(revision), 0) + 1 FROM pos WHERE po_number = $2),
                        $3,
                        $4,$5,$6,
                        $7,$8,$9,$10,
                        $11,$12,$13,$14,
                        $15::jsonb,$16::jsonb,$17::jsonb,
                        $18,$19::jsonb
                    )
                    RETURNING id, uuid, po_number, revision, generated_at
                    """,
                    new_uuid,
                    po_number, tender_id,
                    contract_no, supplier_name, quote_no,
                    currency, subtotal_cents, total_cents, item_count,
                    contract_pdf, quote_pdf, contract_filename, quote_filename,
                    _json.dumps(extracted_contract or {}),
                    _json.dumps(extracted_quote or {}),
                    _json.dumps(draft),
                    rendered_docx,
                    _json.dumps(warnings or []),
                )
                return dict(row)
            except Exception as exc:
                # Retry only on unique-violation collisions (po_number, revision)
                is_unique_violation = (
                    _asyncpg is not None
                    and isinstance(exc, _asyncpg.UniqueViolationError)
                )
                if not is_unique_violation:
                    raise
                last_err = exc
                log.warning(
                    "po.insert.collision po_number=%r attempt=%d — retrying",
                    po_number, attempt + 1,
                )
        # Exhausted retries — surface a clear error
        raise RuntimeError(
            f"Could not assign a unique revision for PO '{po_number}' after 3 attempts"
        ) from last_err


async def get_po_by_uuid(po_uuid: str) -> Optional[dict]:
    """Fetch a single PO by its uuid. Returns row dict or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pos WHERE uuid = $1::uuid", po_uuid)
        return dict(row) if row else None


async def list_pos(limit: int = 50, offset: int = 0) -> list[dict]:
    """List POs, newest first. Excludes heavy BYTEA columns for performance."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, uuid, po_number, revision, tender_id,
                   contract_no, supplier_name, quote_no,
                   currency, subtotal_cents, total_cents, item_count,
                   status, warnings, created_at, generated_at
            FROM pos
            ORDER BY generated_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        return [dict(r) for r in rows]


async def get_po_docx(po_uuid: str) -> Optional[tuple[str, bytes]]:
    """Fetch rendered DOCX bytes + a filename-safe po_number for a given uuid."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT po_number, revision, rendered_docx FROM pos WHERE uuid = $1::uuid",
            po_uuid,
        )
        if not row or row["rendered_docx"] is None:
            return None
        rev = row["revision"]
        rev_suffix = f"-r{rev:02d}" if rev > 1 else ""
        return (f"{row['po_number']}{rev_suffix}", bytes(row["rendered_docx"]))


async def delete_po(po_uuid: str) -> bool:
    """Hard-delete a PO row by UUID. Returns True if a row was removed.

    Purges all blobs (rendered_docx, contract_pdf, quote_pdf) and the
    extracted JSON. The tender row referenced by tender_id is left
    untouched (the FK is ON DELETE SET NULL, but we're deleting the PO,
    not the tender)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM pos WHERE uuid = $1::uuid",
            po_uuid,
        )
        # asyncpg returns command tag like "DELETE 1" or "DELETE 0"
        return result.endswith(" 1")

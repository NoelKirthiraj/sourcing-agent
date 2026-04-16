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
                ('Edward'), ('Richard'), ('Jack'), ('John'), ('James')
            ) AS seed(name)
            WHERE NOT EXISTS (SELECT 1 FROM associates LIMIT 1);
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
                requirements_csv_path = $8
            WHERE id = $1
        """, tender_id, summary, requirements, mandatory_criteria,
            submission_method, file_type, solicitation_path, requirements_csv_path)


async def accept_tender(tender_id: int) -> Optional[dict]:
    """Mark a tender as accepted. Returns the tender record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE tenders SET status = 'accepted', reviewed_at = NOW()
            WHERE id = $1 AND status = 'pending_review'
            RETURNING *
        """, tender_id)
        return dict(row) if row else None


async def reject_tender(tender_id: int, reason: str = "") -> bool:
    """Mark a tender as rejected."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE tenders SET status = 'rejected', reviewed_at = NOW()
            WHERE id = $1 AND status = 'pending_review'
        """, tender_id)
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
                await conn.execute("""
                    INSERT INTO tenders (
                        solicitation_no, solicitation_title, inquiry_link,
                        cflow_record_id, status, scraped_at
                    ) VALUES ($1, $2, $3, $4, 'submitted', $5)
                    ON CONFLICT (solicitation_no) DO NOTHING
                """,
                    sol_no,
                    entry.get("title", ""),
                    entry.get("link", ""),
                    entry.get("cflow_request_id", ""),
                    entry.get("processed_at", datetime.now(timezone.utc).isoformat()),
                )
                migrated += 1
            except Exception as exc:
                log.warning("Failed to migrate %s: %s", sol_no, exc)

    log.info("Migrated %d/%d entries from JSON to PostgreSQL", migrated, len(data))
    return migrated

"""
Associate management — round-robin assignment and workload tracking.
Wraps db.py associate functions into a clean interface.
"""
import logging
from typing import Optional

import db

log = logging.getLogger(__name__)

ASSOCIATES_DEFAULT = [
    "Charles Radovic",
    "Edouard Radovic",
    "Jean-Michel Beaudoin Bombardier",
    "Office",
    "Richard Radovic",
]


async def assign_next() -> Optional[str]:
    """Get the next associate via round-robin and mark them as assigned.
    Returns the associate name, or None if no active associates."""
    name = await db.get_next_associate()
    if name:
        log.info("Assigned to associate: %s", name)
    else:
        log.warning("No active associates available for assignment")
    return name


async def get_workload() -> list[dict]:
    """Get all associates with their workload counts.
    Returns list of dicts with: name, active, active_tenders, pending_count,
    accepted_count, submitted_count."""
    return await db.list_associates()


async def add_associate(name: str) -> bool:
    """Add a new associate. Returns True if inserted, False if already exists."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO associates (name) VALUES ($1) ON CONFLICT (name) DO NOTHING RETURNING id",
                name,
            )
            if row:
                log.info("Added associate: %s", name)
                return True
            log.info("Associate already exists: %s", name)
            return False
        except Exception as exc:
            log.error("Failed to add associate %s: %s", name, exc)
            return False


async def deactivate_associate(name: str) -> bool:
    """Deactivate an associate. Returns True if updated."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE associates SET active = FALSE WHERE name = $1 AND active = TRUE",
            name,
        )
        success = result == "UPDATE 1"
        if success:
            log.info("Deactivated associate: %s", name)
        return success


async def activate_associate(name: str) -> bool:
    """Reactivate an associate. Returns True if updated."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE associates SET active = TRUE WHERE name = $1 AND active = FALSE",
            name,
        )
        return result == "UPDATE 1"

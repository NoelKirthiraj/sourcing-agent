"""Unit tests for db.py — PostgreSQL layer (mocked, no real DB needed)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class FakeAcquire:
    """Async context manager that returns a mock connection."""
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value = FakeAcquire(conn)
    return pool, conn


@pytest.mark.asyncio
async def test_tender_exists_returns_false_when_not_found(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value=None)

    import db
    db._pool = pool

    result = await db.tender_exists("UNKNOWN-123")
    assert result is False
    conn.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_tender_exists_returns_true_when_found(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"1": 1})

    import db
    db._pool = pool

    result = await db.tender_exists("PW-EZZ-123")
    assert result is True


@pytest.mark.asyncio
async def test_stage_tender_returns_id(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"id": 42})

    import db
    db._pool = pool

    tender = {
        "solicitation_no": "WS123",
        "solicitation_title": "Test Tender",
        "inquiry_link": "https://canadabuys.canada.ca/test",
    }
    result = await db.stage_tender(tender, assigned_associate="Edward")
    assert result == 42


@pytest.mark.asyncio
async def test_stage_tender_returns_zero_on_conflict(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value=None)

    import db
    db._pool = pool

    result = await db.stage_tender({"solicitation_no": "DUPE"})
    assert result == 0


@pytest.mark.asyncio
async def test_accept_tender_updates_status(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"id": 1, "status": "accepted"})

    import db
    db._pool = pool

    result = await db.accept_tender(1)
    assert result is not None
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_reject_tender_returns_true(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value="UPDATE 1")

    import db
    db._pool = pool

    result = await db.reject_tender(1, reason="Not relevant")
    assert result is True


@pytest.mark.asyncio
async def test_reject_tender_returns_false_if_not_pending(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value="UPDATE 0")

    import db
    db._pool = pool

    result = await db.reject_tender(999)
    assert result is False


@pytest.mark.asyncio
async def test_get_next_associate_returns_name(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"name": "Edward"})
    conn.execute = AsyncMock()

    import db
    db._pool = pool

    result = await db.get_next_associate()
    assert result == "Edward"
    conn.execute.assert_called_once()  # UPDATE last_assigned_at


@pytest.mark.asyncio
async def test_get_next_associate_returns_none_when_no_active(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value=None)

    import db
    db._pool = pool

    result = await db.get_next_associate()
    assert result is None


@pytest.mark.asyncio
async def test_list_tenders_with_status_filter(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "solicitation_no": "A", "status": "pending_review"},
    ])

    import db
    db._pool = pool

    result = await db.list_tenders(status="pending_review")
    assert len(result) == 1
    assert result[0]["status"] == "pending_review"


@pytest.mark.asyncio
async def test_list_associates_returns_workload(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[
        {"name": "Edward", "active": True, "last_assigned_at": None,
         "active_tenders": 3, "pending_count": 1, "accepted_count": 2, "submitted_count": 5},
    ])

    import db
    db._pool = pool

    result = await db.list_associates()
    assert len(result) == 1
    assert result[0]["name"] == "Edward"
    assert result[0]["active_tenders"] == 3

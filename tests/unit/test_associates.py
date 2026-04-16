"""Unit tests for associates.py — round-robin assignment (mocked DB)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.mark.asyncio
async def test_assign_next_returns_name():
    with patch("associates.db") as mock_db:
        mock_db.get_next_associate = AsyncMock(return_value="Edward")
        import associates
        associates.db = mock_db
        result = await associates.assign_next()
        assert result == "Edward"


@pytest.mark.asyncio
async def test_assign_next_returns_none_when_no_active():
    with patch("associates.db") as mock_db:
        mock_db.get_next_associate = AsyncMock(return_value=None)
        import associates
        associates.db = mock_db
        result = await associates.assign_next()
        assert result is None


@pytest.mark.asyncio
async def test_get_workload_returns_list():
    with patch("associates.db") as mock_db:
        mock_db.list_associates = AsyncMock(return_value=[
            {"name": "Edward", "active": True, "active_tenders": 3,
             "pending_count": 1, "accepted_count": 2, "submitted_count": 5,
             "last_assigned_at": None},
            {"name": "Richard", "active": True, "active_tenders": 1,
             "pending_count": 1, "accepted_count": 0, "submitted_count": 2,
             "last_assigned_at": None},
        ])
        import associates
        associates.db = mock_db
        result = await associates.get_workload()
        assert len(result) == 2
        assert result[0]["name"] == "Edward"
        assert result[0]["active_tenders"] == 3


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_add_associate_new():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": 6})
    pool = MagicMock()
    pool.acquire.return_value = FakeAcquire(conn)

    with patch("associates.db") as mock_db:
        mock_db.get_pool = AsyncMock(return_value=pool)
        import associates
        associates.db = mock_db
        result = await associates.add_associate("NewPerson")
        assert result is True


@pytest.mark.asyncio
async def test_add_associate_duplicate():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # ON CONFLICT DO NOTHING
    pool = MagicMock()
    pool.acquire.return_value = FakeAcquire(conn)

    with patch("associates.db") as mock_db:
        mock_db.get_pool = AsyncMock(return_value=pool)
        import associates
        associates.db = mock_db
        result = await associates.add_associate("Edward")
        assert result is False


@pytest.mark.asyncio
async def test_deactivate_associate():
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()
    pool.acquire.return_value = FakeAcquire(conn)

    with patch("associates.db") as mock_db:
        mock_db.get_pool = AsyncMock(return_value=pool)
        import associates
        associates.db = mock_db
        result = await associates.deactivate_associate("Edward")
        assert result is True


@pytest.mark.asyncio
async def test_deactivate_nonexistent_returns_false():
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    pool = MagicMock()
    pool.acquire.return_value = FakeAcquire(conn)

    with patch("associates.db") as mock_db:
        mock_db.get_pool = AsyncMock(return_value=pool)
        import associates
        associates.db = mock_db
        result = await associates.deactivate_associate("Nobody")
        assert result is False


def test_default_associates_list():
    import associates
    assert len(associates.ASSOCIATES_DEFAULT) == 5
    assert "Edward" in associates.ASSOCIATES_DEFAULT
    assert "James" in associates.ASSOCIATES_DEFAULT

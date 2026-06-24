"""Unit tests for the vendor CRUD functions in db.py.

Mocks the asyncpg connection via the FakeAcquire pattern used elsewhere
in tests/unit/test_db.py. No real Postgres needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, *args):
        pass


class FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    # Some functions wrap work in `async with conn.transaction():`. Make that
    # context manager succeed without doing anything.
    conn.transaction = MagicMock(return_value=FakeTransaction())
    pool.acquire.return_value = FakeAcquire(conn)
    return pool, conn


# ── list_vendors ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_vendors_no_filter(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    result = await db.list_vendors()
    assert result == []
    # Verify query has no WHERE
    sql = conn.fetch.call_args.args[0]
    assert "WHERE" not in sql


@pytest.mark.asyncio
async def test_list_vendors_with_q_filter(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    await db.list_vendors(q="acme")
    sql = conn.fetch.call_args.args[0]
    params = conn.fetch.call_args.args[1:]
    assert "ILIKE" in sql
    # q parameter is the first one, wrapped with %
    assert params[0] == "%acme%"


@pytest.mark.asyncio
async def test_list_vendors_with_category_filter(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    await db.list_vendors(category="Aerospace/Aircraft")
    sql = conn.fetch.call_args.args[0]
    params = conn.fetch.call_args.args[1:]
    # = ANY(rfp_categories) is the array membership probe
    assert "= ANY(rfp_categories)" in sql
    assert "Aerospace/Aircraft" in params


@pytest.mark.asyncio
async def test_list_vendors_combines_q_and_category(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    await db.list_vendors(q="acme", category="Vehicle/Truck")
    sql = conn.fetch.call_args.args[0]
    assert sql.count("AND") >= 1


# ── get_vendor_by_uuid ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_vendor_by_uuid_not_found_returns_none(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value=None)
    import db
    db._pool = pool

    result = await db.get_vendor_by_uuid("00000000-0000-0000-0000-000000000000")
    assert result is None


@pytest.mark.asyncio
async def test_get_vendor_by_uuid_embeds_products(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={
        "id": 1,
        "uuid": "abc",
        "company": "Acme",
        "domain": "acme.com",
        "primary_contacts": [],
        "emails": ["a@acme.com"],
        "phones": [],
        "websites": [],
        "inquiry_count": 7,
        "last_contact": None,
        "rfp_categories": ["Aerospace"],
        "products_quoted": [],
        "bid_count": 3,
        "bid_history": "",
        "rfps_won": "",
        "source": "upload",
        "notes": "",
        "created_at": None,
        "updated_at": None,
    })
    conn.fetch = AsyncMock(return_value=[
        {"product": "Towbar", "rfp_code": "7813-CR-04", "source_tab": "Vendors", "domain_sender_key": "acme.com"},
    ])
    import db
    db._pool = pool

    result = await db.get_vendor_by_uuid("abc")
    assert result["company"] == "Acme"
    assert isinstance(result["products"], list)
    assert result["products"][0]["product"] == "Towbar"


# ── update_vendor: display-only fields stripped ──────────────────────────────

@pytest.mark.asyncio
async def test_update_vendor_strips_display_only_fields(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "uuid": "abc", "company": "X", "domain": "",
        "primary_contacts": [], "emails": [], "phones": [], "websites": [],
        "inquiry_count": 0, "last_contact": None,
        "rfp_categories": [], "products_quoted": [],
        "bid_count": 0, "bid_history": "", "rfps_won": "",
        "source": "manual", "notes": "",
        "created_at": None, "updated_at": None,
    })
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    # Try to update display-only fields alongside an editable one
    payload = {
        "domain": "new.io",
        "inquiry_count": 999,
        "bid_count": 999,
        "bid_history": "should-not-land",
        "last_contact": "2099-01-01",
    }
    await db.update_vendor("abc", payload)

    # The UPDATE statement should mention `domain` but NONE of the
    # display-only field names.
    sql = conn.fetchrow.call_args.args[0]
    assert "domain" in sql
    assert "inquiry_count" not in sql
    assert "bid_count" not in sql
    assert "bid_history" not in sql
    assert "last_contact" not in sql


@pytest.mark.asyncio
async def test_update_vendor_unknown_uuid_returns_none(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value=None)
    import db
    db._pool = pool

    result = await db.update_vendor("abc", {"domain": "x.com"})
    assert result is None


@pytest.mark.asyncio
async def test_update_vendor_with_no_editable_fields_returns_current(mock_pool):
    """Sending only display-only fields means nothing to update; the function
    should just return the current row instead of issuing a no-op UPDATE."""
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "uuid": "abc", "company": "X", "domain": "",
        "primary_contacts": [], "emails": [], "phones": [], "websites": [],
        "inquiry_count": 0, "last_contact": None,
        "rfp_categories": [], "products_quoted": [],
        "bid_count": 0, "bid_history": "", "rfps_won": "",
        "source": "manual", "notes": "",
        "created_at": None, "updated_at": None,
    })
    conn.fetch = AsyncMock(return_value=[])
    import db
    db._pool = pool

    result = await db.update_vendor("abc", {"inquiry_count": 999})
    # get_vendor_by_uuid path used; UPDATE not issued
    assert result is not None
    # Either fetchrow was called once (just SELECT) or twice depending on
    # codepath — the key check is that an UPDATE never happened.
    for call in conn.fetchrow.call_args_list:
        assert "UPDATE vendors" not in call.args[0]


# ── insert_vendor ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_vendor_uses_manual_source_by_default(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "uuid": "abc", "company": "X", "domain": "",
        "primary_contacts": [], "emails": [], "phones": [], "websites": [],
        "inquiry_count": 0, "last_contact": None,
        "rfp_categories": [], "products_quoted": [],
        "bid_count": 0, "bid_history": "", "rfps_won": "",
        "source": "manual", "notes": "",
        "created_at": None, "updated_at": None,
    })
    import db
    db._pool = pool

    await db.insert_vendor({"company": "X"})
    sql = conn.fetchrow.call_args.args[0]
    params = conn.fetchrow.call_args.args[1:]
    assert "INSERT INTO vendors" in sql
    # 'manual' is the source parameter
    assert "manual" in params


# ── delete_vendor ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_vendor_returns_true_on_hit(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value="DELETE 1")
    import db
    db._pool = pool

    result = await db.delete_vendor("abc")
    assert result is True


@pytest.mark.asyncio
async def test_delete_vendor_returns_false_when_not_found(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value="DELETE 0")
    import db
    db._pool = pool

    result = await db.delete_vendor("abc")
    assert result is False


# ── list_rfp_categories ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rfp_categories(mock_pool):
    pool, conn = mock_pool
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "category": "Aerospace", "keywords": ["aircraft"],
         "vendors_tagged_count": 12, "needs_enrichment_count": 3,
         "updated_at": None},
    ])
    import db
    db._pool = pool

    result = await db.list_rfp_categories()
    assert len(result) == 1
    assert result[0]["category"] == "Aerospace"


# ── merge_vendor_upload ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_merge_vendor_upload_counts_inserts_and_updates(mock_pool):
    pool, conn = mock_pool
    # `xmax = 0` returns True for inserts, False for updates. Simulate one
    # insert and one update.
    conn.fetchrow = AsyncMock(side_effect=[
        {"id": 1, "inserted": True},
        {"id": 2, "inserted": False},
    ])
    conn.execute = AsyncMock(return_value="DELETE 0")
    import db
    db._pool = pool

    vendors = [
        {"company": "New Co", "domain": "new.io"},
        {"company": "Existing Co", "domain": "existing.io"},
    ]
    counts = await db.merge_vendor_upload(vendors, [], [])

    assert counts["vendors_inserted"] == 1
    assert counts["vendors_updated"] == 1


@pytest.mark.asyncio
async def test_merge_vendor_upload_inserts_categories(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"id": 1, "inserted": True})
    conn.execute = AsyncMock(return_value="DELETE 0")
    import db
    db._pool = pool

    categories = [
        {"category": "Aerospace", "keywords": ["aircraft"],
         "vendors_tagged_count": 5, "needs_enrichment_count": 0},
        {"category": "Marine", "keywords": ["ship"],
         "vendors_tagged_count": 3, "needs_enrichment_count": 1},
    ]
    counts = await db.merge_vendor_upload([], categories, [])
    assert counts["categories_upserted"] == 2


@pytest.mark.asyncio
async def test_merge_vendor_upload_tracks_orphan_products(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow = AsyncMock(return_value={"id": 1, "inserted": True})
    conn.execute = AsyncMock(return_value="DELETE 0")
    import db
    db._pool = pool

    vendors = [{"company": "Acme", "domain": "acme.com"}]
    products = [
        {"vendor_company": "Acme", "product": "Towbar",
         "rfp_code": "X", "source_tab": "Vendors", "domain_sender_key": "acme.com"},
        # Orphan — Beta not in vendors
        {"vendor_company": "Beta", "product": "Sonar",
         "rfp_code": "Y", "source_tab": "Vendors", "domain_sender_key": ""},
    ]
    counts = await db.merge_vendor_upload(vendors, [], products)
    assert counts["products_inserted"] == 2
    assert counts["products_skipped_orphan"] == 1

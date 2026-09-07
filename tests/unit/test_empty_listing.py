"""Unit tests for empty-result handling on the tender listing.

On 2026-09-07 (Labour Day Monday, after a weekend) the portal returned
"Showing 0 of 0 results" for the Last-24-hours filter. wait_for_selector
timed out after 30s, the TimeoutError propagated, and the whole scheduled
run died with exit 1. Zero tenders is a normal outcome, not a failure.

But an empty page and a broken page must stay distinguishable: if the
portal claims results and renders none, that still has to fail loudly.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scraper import CanadaBuysScraper, ScraperConfig


EMPTY_BODY = (
    "Search notices SEARCH FILTERS Current filters Remove filter X Open "
    "Remove filter X Last 24 hours Showing 0 of 0 results Notice categories: "
    "Tender notices 0 tender notices have been found. Award notices "
    "0 award notices have been found."
)

FULL_BODY = (
    "Search notices SEARCH FILTERS Current filters Showing 200 of 1,943 results "
    "Notice categories: Tender notices 1,943 tender notices have been found."
)


def make_page(body: str):
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value=body)
    page.locator = MagicMock(return_value=body_locator)
    page.url = "https://canadabuys.canada.ca/en/tender-opportunities?pub%5B1%5D=1"
    page.title = AsyncMock(return_value="Tender opportunities | CanadaBuys")
    return page


@pytest.fixture
def scraper():
    return CanadaBuysScraper(ScraperConfig(headless=True))


# ── "Showing X of Y results" parsing ────────────────────────────────────────

@pytest.mark.asyncio
async def test_reads_zero_from_the_results_banner(scraper):
    assert await scraper._reported_result_count(make_page(EMPTY_BODY)) == 0


@pytest.mark.asyncio
async def test_reads_a_thousands_separated_total(scraper):
    assert await scraper._reported_result_count(make_page(FULL_BODY)) == 1943


@pytest.mark.asyncio
async def test_missing_banner_reads_as_unknown_not_zero(scraper):
    """A page without the banner is not an empty result set — it's a page we
    don't recognise, and must not be silently treated as "nothing today"."""
    page = make_page("Access denied. Your request has been blocked.")
    assert await scraper._reported_result_count(page) is None


@pytest.mark.asyncio
async def test_unreadable_body_reads_as_unknown(scraper):
    page = MagicMock()
    locator = MagicMock()
    locator.inner_text = AsyncMock(side_effect=Exception("page closed"))
    page.locator = MagicMock(return_value=locator)
    assert await scraper._reported_result_count(page) is None


# ── the failure dump never masks the failure ────────────────────────────────

@pytest.mark.asyncio
async def test_listing_failure_logs_url_title_and_body(scraper, caplog):
    page = make_page(FULL_BODY)
    with caplog.at_level(logging.ERROR, logger="scraper"):
        await scraper._log_listing_failure(page, 1943)

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "canadabuys.canada.ca" in logged
    assert "Tender opportunities" in logged
    assert "1943" in logged


@pytest.mark.asyncio
async def test_listing_failure_survives_a_dead_page(scraper):
    page = MagicMock()
    page.title = AsyncMock(side_effect=Exception("closed"))
    locator = MagicMock()
    locator.inner_text = AsyncMock(side_effect=Exception("closed"))
    page.locator = MagicMock(return_value=locator)
    type(page).url = property(lambda self: (_ for _ in ()).throw(Exception("closed")))

    await scraper._log_listing_failure(page, None)  # must not raise


# ── fetch_tender_list itself ────────────────────────────────────────────────

def timing_out_scraper(body: str) -> tuple[CanadaBuysScraper, MagicMock]:
    """A scraper whose listing page never renders a tender link."""
    page = make_page(body)
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock(
        side_effect=PlaywrightTimeoutError("Timeout 30000ms exceeded.")
    )
    page.close = AsyncMock()

    s = CanadaBuysScraper(ScraperConfig(headless=True))
    s._context = MagicMock()
    s._context.new_page = AsyncMock(return_value=page)
    return s, page


@pytest.mark.asyncio
async def test_zero_results_returns_empty_instead_of_raising():
    """The 2026-09-07 failure. This used to raise TimeoutError and exit 1."""
    scraper, page = timing_out_scraper(EMPTY_BODY)

    assert await scraper.fetch_tender_list() == []
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_claimed_results_with_no_links_still_raises():
    """The portal says 1,943 results and rendered none: markup change or a
    block page. That must not be swallowed as a quiet day."""
    scraper, page = timing_out_scraper(FULL_BODY)

    with pytest.raises(PlaywrightTimeoutError):
        await scraper.fetch_tender_list()
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrecognised_page_still_raises():
    scraper, _ = timing_out_scraper("Access denied. Your request was blocked.")

    with pytest.raises(PlaywrightTimeoutError):
        await scraper.fetch_tender_list()


@pytest.mark.asyncio
async def test_a_raising_listing_logs_evidence_first(caplog):
    scraper, _ = timing_out_scraper(FULL_BODY)

    with caplog.at_level(logging.ERROR, logger="scraper"):
        with pytest.raises(PlaywrightTimeoutError):
            await scraper.fetch_tender_list()

    assert any("LISTING DIAG" in r.getMessage() for r in caplog.records)

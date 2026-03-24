"""
One-time script to capture real CanadaBuys HTML into test fixtures.
Run this once after initial setup, and again whenever scraper selectors break.

Usage:
  source .venv/bin/activate
  python capture_fixtures.py
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SEARCH_URL = (
    "https://canadabuys.canada.ca/en/tender-opportunities"
    "?search_filter=&pub%5B1%5D=1&status%5B87%5D=87"
    "&category%5B153%5D=153&category%5B154%5D=154&category%5B156%5D=156"
    "&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words="
)

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("Navigating to CanadaBuys listing page...")
        await page.goto(SEARCH_URL, wait_until="networkidle")
        Path("tests/fixtures/canadabuys_listing.html").write_text(await page.content())
        print("✓ Listing page captured → tests/fixtures/canadabuys_listing.html")

        # Get first tender link from the listing
        link = page.locator(
            "main a[href*='/en/tender-opportunities/tender-notice/'], "
            "main a[href*='/en/tender-opportunities/award-notice/'], "
            "main a[href*='/en/tender-opportunities/contract-history/']"
        ).first
        if await link.count() > 0:
            href = await link.get_attribute("href")
            from urllib.parse import urljoin
            detail_url = urljoin("https://canadabuys.canada.ca", href)
            print(f"Navigating to detail page: {detail_url}")
            await page.goto(detail_url, wait_until="networkidle")
            Path("tests/fixtures/canadabuys_detail.html").write_text(await page.content())
            print("✓ Detail page captured → tests/fixtures/canadabuys_detail.html")
        else:
            print("⚠️  Could not find a tender link on the listing page.")
            print("   Navigate manually in the browser window and press Enter here.")
            input()

        await browser.close()
        print("\n✅ Fixtures captured. Commit tests/fixtures/ to the repository.")

asyncio.run(capture())

"""
CanadaBuys Portal Scraper
--------------------------
Uses Playwright (headless Chromium) to render the portal and extract
key tender listing fields plus selected detail fields.
"""

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

log = logging.getLogger(__name__)


_BASE_SEARCH = "https://canadabuys.canada.ca/en/tender-opportunities?search_filter="

# The portal renders "Showing X of Y results" above the list. Y is the
# authoritative count, and it is present whether or not there are any hits,
# which lets us tell "nothing published today" apart from "the page broke".
_RESULT_COUNT_RE = re.compile(r"Showing\s+[\d,]+\s+of\s+([\d,]+)\s+results", re.I)

# === TEMPORARY (2026-09-07) ===
# Widened from pub[1] (Last 24 hours) to pub[2] (Last 7 days) so a test run
# picks up Friday's postings. The portal offers no 72-hour option — the
# choices are 24 hours, 7 days, 30 days, more than 30 — so 7 days is the
# narrowest window that reaches back past a weekend.
#
# REVERT to `&pub%5B1%5D=1&status%5B87%5D=87` once SAP is verified, unless
# you decide to keep the wider window: at 08:00 ET the 24-hour window is
# mostly overnight, and on Mondays it covers Sunday only, so it is empty
# more often than not.
#
# Daily: Open + Last 7 days (all categories)  [was: Last 24 hours]
DAILY_URL = (
    _BASE_SEARCH
    + "&pub%5B2%5D=2&status%5B87%5D=87"
    "&Apply_filters=Apply+filters&record_per_page=200&current_tab=t&words="
)

# Weekly (Saturdays): Open + Goods + Last 7 days
WEEKLY_URL = (
    _BASE_SEARCH
    + "&pub%5B2%5D=2&status%5B87%5D=87&category%5B153%5D=153"
    "&Apply_filters=Apply+filters&record_per_page=200&current_tab=t&words="
)


@dataclass
class ScraperConfig:
    search_url: str = DAILY_URL
    headless: bool = True
    timeout_ms: int = 30_000
    slow_mo_ms: int = 0
    max_pages: int = 10


class CanadaBuysScraper:
    def __init__(self, config: ScraperConfig):
        self.config = config
        self._playwright = None
        self._browser: Browser | None = None

    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo_ms,
        )
        self._context = await self._browser.new_context(
            user_agent=self._USER_AGENT,
            extra_http_headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
        return self

    async def __aexit__(self, *_):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch_tender_list(self) -> list[dict[str, Any]]:
        all_tenders: list[dict[str, Any]] = []
        seen: set[str] = set()
        page = await self._context.new_page()

        try:
            # First load the base page to establish a session cookie.
            # Without this, the CDN serves stale cached results.
            base_url = "https://canadabuys.canada.ca/en/tender-opportunities"
            base_resp = await page.goto(base_url, timeout=self.config.timeout_ms, wait_until="domcontentloaded")
            if not base_resp or base_resp.status >= 400:
                log.warning("Base page returned %s — CDN bypass may fail", base_resp.status if base_resp else "no response")
            await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

            # Now load the filtered URL — CDN serves fresh results with the session cookie.
            await page.goto(
                self.config.search_url,
                timeout=self.config.timeout_ms,
                wait_until="domcontentloaded",
            )
            await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
            try:
                await page.wait_for_selector(
                    "main a[href*='/en/tender-opportunities/tender-notice/'], "
                    "main a[href*='/en/tender-opportunities/award-notice/']",
                    timeout=self.config.timeout_ms,
                )
            except PlaywrightTimeoutError:
                # An empty result set is a normal outcome, not a failure:
                # weekends, statutory holidays, or an early-morning run before
                # the day's notices are posted. This used to raise and take the
                # whole run down with it.
                total = await self._reported_result_count(page)
                if total == 0:
                    log.info(
                        "Portal reports 0 results for this filter — nothing "
                        "published in the window. Ending run cleanly."
                    )
                    return []
                # The portal claims results but rendered no links, or the count
                # is missing entirely. That is a real anomaly (markup change,
                # block page), so fail loudly — but with evidence this time.
                await self._log_listing_failure(page, total)
                raise

            for page_num in range(1, self.config.max_pages + 1):
                log.info("Scraping page %d...", page_num)
                tenders = await self._extract_listing(page, seen)

                all_tenders.extend(tenders)
                log.info(
                    "  Page %d: %d tender(s) — running total: %d",
                    page_num,
                    len(tenders),
                    len(all_tenders),
                )

                next_btn = page.locator(
                    "a[rel='next'], a[aria-label*='next' i], a[title*='next page' i], "
                    "li.pager__item--next a, .pager__item--next a"
                ).first
                if await next_btn.count() == 0:
                    log.info("No more pages after page %d.", page_num)
                    break

                # Use click() for pagination — the portal's JS handles page transitions.
                # goto() with the href URL hits CDN stale pages, but click() works
                # because it uses the browser's native navigation with session cookies.
                await next_btn.click()
                await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
                try:
                    await page.wait_for_selector(
                        "main a[href*='/en/tender-opportunities/tender-notice/'], "
                        "main a[href*='/en/tender-opportunities/award-notice/']",
                        timeout=15000,
                    )
                except Exception:
                    log.warning("  Page %d selector timeout — may be empty or last page", page_num + 1)
        finally:
            await page.close()

        return all_tenders

    async def fetch_tender_detail(self, url: str) -> dict[str, Any]:
        if not url:
            return {}

        page = await self._context.new_page()
        try:
            await page.goto(url, timeout=self.config.timeout_ms, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
            return await self._extract_detail(page, url)
        except Exception as exc:
            log.warning("Could not load detail page %s: %s", url, exc)
            return {}
        finally:
            await page.close()

    async def _reported_result_count(self, page: Page) -> int | None:
        """The Y from the portal's "Showing X of Y results" banner.

        None means the banner wasn't found at all, which is itself a signal
        that we are not looking at the page we think we are.
        """
        try:
            body = await page.locator("body").inner_text()
        except Exception as exc:
            log.debug("Could not read body text for result count: %s", exc)
            return None
        match = _RESULT_COUNT_RE.search(" ".join(body.split()))
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None

    async def _log_listing_failure(self, page: Page, total: int | None) -> None:
        """Evidence for a listing that neither rendered links nor reported
        zero results. Never raises — it runs on the way to re-raising."""
        try:
            url = page.url
        except Exception:
            url = "?"
        try:
            title = await page.title()
        except Exception:
            title = "?"
        try:
            body = " ".join((await page.locator("body").inner_text()).split())[:600]
        except Exception:
            body = ""
        log.error("LISTING DIAG url=%s", url)
        log.error("LISTING DIAG title=%r reported_total=%s", title, total)
        log.error("LISTING DIAG body=%s", body)

    async def _extract_listing(self, page: Page, seen: set[str]) -> list[dict[str, Any]]:
        tenders: list[dict[str, Any]] = []

        # Primary strategy: grab notice links directly from the page.
        links = page.locator(
            "main a[href*='/en/tender-opportunities/tender-notice/'], "
            "main a[href*='/en/tender-opportunities/award-notice/'], "
            "main a[href*='/en/tender-opportunities/contract-history/']"
        )
        link_count = await links.count()

        for i in range(link_count):
            link = links.nth(i)
            href = await link.get_attribute("href")
            title = _clean(await link.inner_text())
            full_url = _absolute(href)
            if not href or not title or full_url in seen:
                continue
            seen.add(full_url)
            tenders.append(
                {
                    "solicitation_title": title,
                    "inquiry_link": full_url,
                    "solicitation_no": "",
                    "client": "",
                    "closing_date": "",
                    "gsin_description": "",
                    "time_and_zone": "",
                    "notifications": "",
                    "contact_name": "",
                    "contact_email": "",
                    "contact_phone": "",
                }
            )

        if tenders:
            return tenders

        # Fallback: regex parse rendered HTML if Playwright locators miss the list structure.
        html = await page.content()
        pattern = re.compile(
            r'<a[^>]+href="(?P<href>[^"]*/en/tender-opportunities/(?:tender-notice|award-notice|contract-history)/[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for m in pattern.finditer(html):
            href = _absolute(_clean_html(m.group("href")))
            title = _clean(_clean_html(m.group("title")))
            if not href or not title or href in seen:
                continue
            seen.add(href)
            tenders.append(
                {
                    "solicitation_title": title,
                    "inquiry_link": href,
                    "solicitation_no": "",
                    "client": "",
                    "closing_date": "",
                    "gsin_description": "",
                    "time_and_zone": "",
                    "notifications": "",
                    "contact_name": "",
                    "contact_email": "",
                    "contact_phone": "",
                }
            )

        return tenders

    async def _extract_detail(self, page: Page, url: str) -> dict[str, Any]:
        # Description tab is active by default — extract header fields + description.
        text = (await page.locator("body").inner_text()).strip()
        detail: dict[str, Any] = {
            "inquiry_link": url,
            "solicitation_no": _capture(text, r"Solicitation number\s+([^\n]+)"),
            "gsin_description": _capture(text, r"Related notices\s+(.+?)\s+(?:Show more description|Contract duration|Trade agreements|Summary information)"),
            "closing_date": _capture(text, r"Closing date and time\s+([^\n]+)"),
            "time_and_zone": _capture(text, r"Closing date and time\s+.*?(EDT|EST|CDT|CST|MDT|MST|PDT|PST|UTC)"),
            "notifications": _capture(text, r"Last amendment date\s+([^\n]+)"),
        }

        # Detect SAP by presence of a "leaving-canadabuys" SAP login link.
        # Don't use text matching — every page has boilerplate "register in SAP Ariba" footer text.
        sap_login_link = page.locator("a[href*='leaving-canadabuys'][href*='ariba']")
        try:
            sap_count = await sap_login_link.count()
            is_sap = sap_count > 0
            if is_sap:
                detail["sap_link"] = await sap_login_link.first.get_attribute("href") or ""
        except Exception:
            is_sap = False
        detail["bid_platform"] = "SAP" if is_sap else "CanadaBuys"

        # Click the Contact information tab to reveal contact fields.
        contact_tab = page.locator("text=Contact information").first
        if await contact_tab.count() > 0:
            try:
                await contact_tab.click()
                await page.wait_for_timeout(1000)
                text = (await page.locator("body").inner_text()).strip()
            except Exception as exc:
                log.debug("Could not click Contact tab: %s", exc)

        detail["client"] = _capture(text, r"Organization\s+([^\n]+)")
        detail["contact_name"] = _capture(text, r"Contracting authority\s+([^\n]+)")
        detail["contact_email"] = _capture(text, r"Email\s+([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", flags=re.IGNORECASE)
        detail["contact_phone"] = _capture(text, r"Phone\s+([^\n]+)") or ""

        return detail

    async def download_solicitation(self, url: str, download_dir: str) -> list[str]:
        """Navigate to the tender detail page, click 'Bidding details' tab,
        and download any solicitation documents found.

        Returns a list of local file paths for successfully downloaded files.
        """
        page = await self._context.new_page()
        downloaded: list[str] = []
        try:
            await page.goto(url, timeout=self.config.timeout_ms, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

            # Click the "Bidding details" tab to reveal documents.
            bidding_tab = page.locator("text=Bidding details").first
            if await bidding_tab.count() == 0:
                log.debug("No 'Bidding details' tab found on %s", url)
                return []

            await bidding_tab.click()
            await page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
            await page.wait_for_timeout(2000)

            # Find download links — typically PDF/DOC files in the bidding section.
            download_links = page.locator(
                "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], "
                "a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip'], "
                "a[download], a[href*='download']"
            )
            link_count = await download_links.count()
            if link_count == 0:
                log.debug("No downloadable files found on bidding details for %s", url)
                return []

            for i in range(link_count):
                link = download_links.nth(i)
                try:
                    async with page.expect_download(timeout=30000) as dl_info:
                        await link.click()
                    download = await dl_info.value
                    filename = download.suggested_filename or f"solicitation_{i}.pdf"
                    dest = os.path.join(download_dir, filename)
                    await download.save_as(dest)
                    downloaded.append(dest)
                    log.info("  Downloaded: %s", filename)
                except Exception as exc:
                    log.debug("  Download failed for link %d on %s: %s", i, url, exc)
                    continue

        except Exception as exc:
            log.warning("Could not download solicitation from %s: %s", url, exc)
        finally:
            await page.close()

        return downloaded


BASE_URL = "https://canadabuys.canada.ca"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _clean_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')


def _absolute(href: str | None) -> str:
    if not href:
        return ""
    return urljoin(BASE_URL, href)


def _capture(text: str, pattern: str, flags: int = re.DOTALL) -> str:
    m = re.search(pattern, text, flags)
    return _clean(m.group(1)) if m else ""

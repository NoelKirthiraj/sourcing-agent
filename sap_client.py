"""
SAP Business Network auto-login and solicitation download.
Uses Playwright to authenticate and download solicitation documents
when a tender requires SAP rather than direct CanadaBuys download.
"""
import logging
import os
from typing import Any

from playwright.async_api import BrowserContext, Page

log = logging.getLogger(__name__)

# Configurable selectors — override via env vars if SAP changes its login page
SAP_USERNAME_SELECTOR = os.environ.get("SAP_USERNAME_SELECTOR", 'input[name="UserName"], input[type="email"], #username')
SAP_PASSWORD_SELECTOR = os.environ.get("SAP_PASSWORD_SELECTOR", 'input[name="Password"], input[type="password"], #password')
SAP_SUBMIT_SELECTOR = os.environ.get("SAP_SUBMIT_SELECTOR", 'button[type="submit"], input[type="submit"], #submit')


class SAPClient:
    """Playwright-based SAP Business Network client for solicitation download."""

    def __init__(self, context: BrowserContext, username: str = "", password: str = ""):
        self._context = context
        self._username = username or os.environ.get("SAP_USERNAME", "")
        self._password = password or os.environ.get("SAP_PASSWORD", "")
        self._logged_in = False

    @property
    def has_credentials(self) -> bool:
        return bool(self._username and self._password)

    async def download_solicitation(self, sap_url: str, download_dir: str) -> list[str]:
        """Navigate to SAP tender page, log in if needed, download solicitation files.

        Returns list of downloaded file paths. Returns empty list on failure.
        """
        if not self.has_credentials:
            log.warning("SAP credentials not set — cannot auto-download from SAP")
            return []

        if not sap_url:
            return []

        page = await self._context.new_page()
        downloaded: list[str] = []

        try:
            await page.goto(sap_url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=30000)

            # Check if we need to log in
            if not self._logged_in:
                login_success = await self._try_login(page)
                if not login_success:
                    log.warning("SAP login failed — flagging tender for manual download")
                    return []

            # Wait for the page to load after login/navigation
            await page.wait_for_load_state("networkidle", timeout=30000)

            # Look for document/attachment download links
            downloaded = await self._download_documents(page, download_dir)

        except Exception as exc:
            log.warning("SAP download failed for %s: %s", sap_url, exc)
        finally:
            await page.close()

        return downloaded

    async def _try_login(self, page: Page) -> bool:
        """Attempt to log into SAP. Returns True on success."""
        try:
            # Look for username field
            username_field = page.locator(SAP_USERNAME_SELECTOR).first
            if await username_field.count() == 0:
                # No login form — could be already authenticated, SSO redirect, or wrong page.
                # Do NOT cache _logged_in here — we can't verify authentication state.
                log.debug("No username field found — proceeding without login (not cached as authenticated)")
                return True

            await username_field.fill(self._username)

            # Some SAP login flows have a "Continue" step before password
            continue_btn = page.locator('button:has-text("Continue"), button:has-text("Next")').first
            if await continue_btn.count() > 0:
                await continue_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)

            # Fill password
            password_field = page.locator(SAP_PASSWORD_SELECTOR).first
            if await password_field.count() > 0:
                await password_field.fill(self._password)

            # Submit
            submit_btn = page.locator(SAP_SUBMIT_SELECTOR).first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30000)

            # Check for MFA/CAPTCHA — if we're still on a login-like page, fail gracefully
            await page.wait_for_timeout(2000)
            still_login = await page.locator(SAP_USERNAME_SELECTOR).count() > 0
            if still_login:
                log.warning("SAP login appears to have failed — still on login page (possible MFA/CAPTCHA)")
                return False

            self._logged_in = True
            log.info("SAP login successful")
            return True

        except Exception as exc:
            log.warning("SAP login error: %s", exc)
            return False

    async def _download_documents(self, page: Page, download_dir: str) -> list[str]:
        """Find and download solicitation documents from the current SAP page."""
        downloaded: list[str] = []

        # Look for common document link patterns in SAP
        doc_links = page.locator(
            "a[href*='download'], a[href$='.pdf'], a[href$='.doc'], "
            "a[href$='.docx'], a[href$='.zip'], "
            "a[href*='attachment'], a[href*='document']"
        )
        link_count = await doc_links.count()

        if link_count == 0:
            log.debug("No downloadable documents found on SAP page")
            return []

        for i in range(link_count):
            link = doc_links.nth(i)
            try:
                async with page.expect_download(timeout=30000) as dl_info:
                    await link.click()
                download = await dl_info.value
                filename = download.suggested_filename or f"sap_solicitation_{i}.pdf"
                dest = os.path.join(download_dir, filename)
                await download.save_as(dest)
                downloaded.append(dest)
                log.info("  SAP download: %s", filename)
            except Exception as exc:
                log.debug("  SAP download failed for link %d: %s", i, exc)
                continue

        return downloaded

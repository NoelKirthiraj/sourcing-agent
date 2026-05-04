"""
SAP Business Network auto-login and solicitation download.
Uses Playwright to authenticate and download solicitation documents
when a tender requires SAP rather than direct CanadaBuys download.

Tested flow (May 2026):
  1. Navigate to SAP discovery page (public)
  2. Click "Respond" → popup appears
  3. Click "Register/Login" in popup → redirects to login page
  4. Dismiss cookie consent banner ("Accept All")
  5. Fill username → click Next → redirects to service.ariba.com
  6. Fill password → press Enter → logged in
  7. Navigate to BID SOLICITATION section → download documents
"""
import logging
import os
from typing import Any
from urllib.parse import urlparse, parse_qs, unquote

from playwright.async_api import BrowserContext, Page

log = logging.getLogger(__name__)


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
        """Full flow: navigate to SAP, log in, download solicitation files.

        Returns list of downloaded file paths. Returns empty list on failure.
        """
        if not self.has_credentials:
            log.warning("SAP credentials not set — cannot auto-download from SAP")
            return []

        if not sap_url:
            return []

        sap_url = self._resolve_sap_url(sap_url)
        if not sap_url:
            log.warning("Could not resolve SAP URL")
            return []

        page = await self._context.new_page()
        downloaded: list[str] = []

        try:
            # Step 1: Load SAP discovery page
            log.debug("SAP: loading discovery page %s", sap_url[:80])
            await page.goto(sap_url, timeout=60000, wait_until="load")
            await page.wait_for_timeout(5000)

            if not self._logged_in:
                # Step 2: Click Respond to trigger login flow
                respond = page.locator("button:has-text('Respond')").first
                if await respond.count() > 0:
                    await respond.click()
                    await page.wait_for_timeout(3000)

                    # Step 3: Click Register/Login in the popup
                    login_btn = page.locator(
                        "button:has-text('Register/Login'), "
                        "button:has-text('Register'), "
                        "button:has-text('Login')"
                    ).first
                    if await login_btn.count() > 0:
                        try:
                            async with page.expect_navigation(timeout=30000):
                                await login_btn.click()
                        except Exception:
                            pass
                        await page.wait_for_timeout(3000)

                    # Step 4: Dismiss cookie consent
                    cookie_btn = page.locator(
                        "button:has-text('Accept All'), "
                        "button:has-text('Accept'), "
                        "#truste-consent-button"
                    ).first
                    if await cookie_btn.count() > 0:
                        await cookie_btn.click()
                        await page.wait_for_timeout(2000)

                    # Step 5: Fill username
                    login_success = await self._do_login(page)
                    if not login_success:
                        log.warning("SAP login failed")
                        return []
                else:
                    log.debug("No Respond button — page may require different flow")
                    return []

            # Step 7: Download documents from the event page
            current = self._context.pages[-1]
            downloaded = await self._download_event_documents(current, download_dir)

        except Exception as exc:
            log.warning("SAP download failed for %s: %s", sap_url, exc)
        finally:
            # Close any extra pages we opened, but not the main browser context
            for pg in self._context.pages[1:]:
                try:
                    await pg.close()
                except Exception:
                    pass

        return downloaded

    async def _do_login(self, page: Page) -> bool:
        """Handle the SAP login form (username → next → password → enter)."""
        try:
            # Find username field (may be in a frame)
            email_field = None
            for frame in page.frames:
                ef = frame.locator(
                    "input[type='email'], input[name*='user'], "
                    "input[name*='email'], input[name='UserName'], #username"
                ).first
                if await ef.count() > 0:
                    email_field = ef
                    break

            if not email_field or await email_field.count() == 0:
                log.warning("SAP: no username field found on login page")
                return False

            # Fill username and submit
            await email_field.fill(self._username)
            await page.wait_for_timeout(1000)

            # Click Next/Continue (the first step of SAP's two-step login)
            for frame in page.frames:
                submit = frame.locator(
                    "button:has-text('Continue'), button:has-text('Next'), "
                    "button[type='submit']"
                ).first
                if await submit.count() > 0:
                    await submit.click()
                    break

            await page.wait_for_timeout(5000)

            # Page navigates to service.ariba.com for password
            current = self._context.pages[-1]

            # Find password field
            pwd = current.locator("input[type='password']").first
            try:
                await pwd.wait_for(timeout=10000)
            except Exception:
                pass

            if await pwd.count() == 0:
                # Check frames
                for frame in current.frames:
                    pf = frame.locator("input[type='password']").first
                    if await pf.count() > 0:
                        pwd = pf
                        break

            if await pwd.count() == 0:
                log.warning("SAP: no password field found after username")
                return False

            # Fill password and press Enter (button is hard to click due to SAP UI framework)
            await pwd.fill(self._password)
            await page.wait_for_timeout(1000)
            await pwd.press("Enter")
            await page.wait_for_timeout(15000)

            # Verify we're logged in — check if we're on the sourcing page
            current = self._context.pages[-1]
            url = current.url
            if "ariba.com/Sourcing" in url or "ariba.com/dashboard" in url:
                self._logged_in = True
                log.info("SAP login successful (user: %s)", self._username)
                return True

            # Check if still on login page
            still_pwd = await current.locator("input[type='password']").count()
            if still_pwd > 0:
                log.warning("SAP login failed — still on password page (wrong credentials?)")
                return False

            # Might have redirected somewhere unexpected but could still be logged in
            self._logged_in = True
            log.info("SAP login completed (URL: %s)", url[:80])
            return True

        except Exception as exc:
            log.warning("SAP login error: %s", exc)
            return False

    async def _download_event_documents(self, page: Page, download_dir: str) -> list[str]:
        """Download documents from the SAP event page after login."""
        downloaded: list[str] = []
        os.makedirs(download_dir, exist_ok=True)

        try:
            body = (await page.locator("body").inner_text()).strip()

            # Look for "BID SOLICITATION" section in the sidebar
            bid_doc = page.locator("text=BID SOLICITATION").first
            if await bid_doc.count() > 0:
                log.debug("SAP: clicking BID SOLICITATION section")
                await bid_doc.click()
                await page.wait_for_timeout(5000)

            # Look for downloadable file links
            # SAP event pages have links to documents in the content area
            doc_links = page.locator(
                "a[href*='download'], a[href$='.pdf'], a[href$='.doc'], "
                "a[href$='.docx'], a[href$='.xlsx'], a[href$='.zip'], "
                "a[download], a[href*='FileDownload'], a[href*='filedownload']"
            )
            link_count = await doc_links.count()
            log.debug("SAP: found %d potential download links", link_count)

            # Also look for links with file-like text
            file_text_links = page.locator(
                "a:has-text('.pdf'), a:has-text('.doc'), a:has-text('.xlsx')"
            )
            text_link_count = await file_text_links.count()

            all_links = []
            # Collect href-based links
            for i in range(link_count):
                all_links.append(doc_links.nth(i))
            # Collect text-based links (avoiding duplicates)
            for i in range(text_link_count):
                all_links.append(file_text_links.nth(i))

            if not all_links:
                log.debug("SAP: no downloadable documents found on event page")
                return []

            for i, link in enumerate(all_links[:10]):  # Max 10 files
                try:
                    async with page.expect_download(timeout=30000) as dl_info:
                        await link.click()
                    download = await dl_info.value
                    filename = download.suggested_filename or f"sap_doc_{i}.pdf"
                    # Only download English files if both EN/FR exist
                    if "-fr." in filename.lower() or "_fr." in filename.lower() or "-fra" in filename.lower():
                        log.debug("SAP: skipping French file %s", filename)
                        continue
                    dest = os.path.join(download_dir, filename)
                    await download.save_as(dest)
                    downloaded.append(dest)
                    log.info("  SAP downloaded: %s", filename)
                except Exception as exc:
                    log.debug("  SAP download failed for link %d: %s", i, exc)
                    continue

        except Exception as exc:
            log.warning("SAP document download error: %s", exc)

        return downloaded

    @staticmethod
    def _resolve_sap_url(url: str) -> str:
        """Extract the actual SAP URL from a CanadaBuys redirect link."""
        if url.startswith("/"):
            url = "https://canadabuys.canada.ca" + url

        if "ariba.com" in url or "jaggaer.com" in url or "sap.com" in url:
            if "leaving-canadabuys" not in url:
                return url

        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            destin = params.get("destin", params.get("destination", [""]))
            if destin and destin[0]:
                return unquote(destin[0])
        except Exception:
            pass

        return url

"""
SAP Business Network auto-login and solicitation download.
Uses Playwright + Claude Vision to navigate SAP's complex UI.

Flow:
  1. Navigate to SAP discovery page (public)
  2. Click "Respond" → "Register/Login" → login page
  3. Dismiss cookie consent, fill username → Next → password → Enter
  4. On event page: use Claude Vision to find "Download Content" button
  5. Click "Download Content" → export panel opens
  6. Use Claude Vision to find "Download Attachments" in panel
  7. On attachment page: check "Total" checkbox → click "Download Attachments"
"""
import asyncio
import base64
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse, parse_qs, unquote

from playwright.async_api import BrowserContext, Page

log = logging.getLogger(__name__)


def _parse_claude_json(text: str) -> list[dict]:
    """Parse Claude's JSON response, handling markdown fences and string coords."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        result = json.loads(text[start:end+1])
        for item in result:
            item["x"] = int(item.get("x", 0))
            item["y"] = int(item.get("y", 0))
        return result
    except Exception:
        return []


async def _ask_claude_for_buttons(screenshot_path: str) -> list[dict]:
    """Send screenshot to Claude and get clickable element coordinates."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        img_data = base64.standard_b64encode(open(screenshot_path, "rb").read()).decode()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}},
                    {"type": "text", "text": """Look at this SAP page screenshot. Find ONLY elements related to downloading documents.
Look for: "Download Content", "Download Attachments", checkboxes near "Total" or "Select All", document section links.
Return a JSON array with max 10 items: [{"label":"...","x":N,"y":N,"confidence":"high/medium","type":"button/link/checkbox"}]
Return ONLY the JSON array, no explanation. If nothing found, return []."""},
                ],
            }],
        )
        return _parse_claude_json(message.content[0].text)
    except Exception as exc:
        log.warning("Claude vision failed: %s", exc)
        return []


class SAPClient:
    """Playwright + Claude Vision SAP Business Network client."""

    def __init__(self, context: BrowserContext, username: str = "", password: str = ""):
        self._context = context
        self._username = username or os.environ.get("SAP_USERNAME", "")
        self._password = password or os.environ.get("SAP_PASSWORD", "")
        self._logged_in = False

    @property
    def has_credentials(self) -> bool:
        return bool(self._username and self._password)

    async def download_solicitation(self, sap_url: str, download_dir: str) -> list[str]:
        """Full flow: login → vision-guided download."""
        if not self.has_credentials:
            log.warning("SAP credentials not set")
            return []
        if not sap_url:
            return []

        sap_url = self._resolve_sap_url(sap_url)
        if not sap_url:
            return []

        os.makedirs(download_dir, exist_ok=True)
        page = await self._context.new_page()
        downloaded: list[str] = []

        try:
            # Step 1: Load SAP discovery page (SPA — needs time to render)
            log.debug("SAP: loading %s", sap_url[:80])
            await page.goto(sap_url, timeout=60000, wait_until="load")
            await page.wait_for_timeout(15000)  # SAP SPA needs time

            # Step 2: Login if needed
            if not self._logged_in:
                # Wait for SPA to render Respond button
                try:
                    await page.locator("button:has-text('Respond')").wait_for(timeout=15000)
                except:
                    log.debug("SAP: Respond button not found after wait, checking page...")
                success = await self._login_flow(page)
                if not success:
                    log.warning("SAP login failed")
                    return []

            # Step 3: Find event page
            event_page = await self._find_event_page()
            if not event_page:
                log.warning("SAP: could not find event page after login")
                return []

            await event_page.wait_for_timeout(10000)
            log.debug("SAP event page: %s", event_page.url[:80])

            # Step 4: Set up download listener
            dl_files: list = []
            event_page.on("download", lambda dl: dl_files.append(dl))

            # Step 5: Vision-guided download
            downloaded = await self._vision_download(event_page, download_dir, dl_files)

        except Exception as exc:
            log.warning("SAP download failed: %s", exc)
        finally:
            for pg in self._context.pages[1:]:
                try:
                    await pg.close()
                except:
                    pass

        return downloaded

    async def _login_flow(self, page: Page) -> bool:
        """Handle full SAP login: Respond → Register/Login → cookie → username → password."""
        try:
            # Click Respond
            respond = page.locator("button:has-text('Respond')").first
            if await respond.count() == 0:
                log.warning("SAP: no Respond button")
                return False
            await respond.click()
            await page.wait_for_timeout(3000)

            # Click Register/Login in popup
            login_btn = page.locator("button:has-text('Register/Login'), button:has-text('Login')").first
            if await login_btn.count() > 0:
                try:
                    async with page.expect_navigation(timeout=30000):
                        await login_btn.click()
                except:
                    pass
                await page.wait_for_timeout(3000)

            # Dismiss cookie consent
            cookie = page.locator("button:has-text('Accept All'), #truste-consent-button").first
            if await cookie.count() > 0:
                await cookie.click()
                await page.wait_for_timeout(2000)

            # Fill username (in frame)
            username_filled = False
            for frame in page.frames:
                ef = frame.locator("input[id*='username'], input[type='email'], input[name*='user']").first
                if await ef.count() > 0:
                    await ef.fill(self._username)
                    sub = frame.locator("button:has-text('Next'), button[id*='nextButton'], button[type='submit']").first
                    if await sub.count() > 0:
                        await sub.click()
                    username_filled = True
                    break

            if not username_filled:
                log.warning("SAP: no username field")
                return False
            await page.wait_for_timeout(5000)

            # Fill password (may be on a new page)
            current = self._context.pages[-1]
            pwd = current.locator("input[type='password']").first
            try:
                await pwd.wait_for(timeout=10000)
            except:
                pass
            if await pwd.count() == 0:
                for frame in current.frames:
                    pf = frame.locator("input[type='password']").first
                    if await pf.count() > 0:
                        pwd = pf
                        break

            if await pwd.count() == 0:
                log.warning("SAP: no password field")
                return False

            await pwd.fill(self._password)
            await pwd.press("Enter")
            await page.wait_for_timeout(15000)

            # Verify login
            event_page = await self._find_event_page()
            if event_page:
                self._logged_in = True
                log.info("SAP login successful")
                return True

            log.warning("SAP: login completed but event page not found")
            return False

        except Exception as exc:
            log.warning("SAP login error: %s", exc)
            return False

    async def _find_event_page(self) -> Page | None:
        """Find the SAP event page among open pages."""
        for _ in range(6):
            for pg in self._context.pages:
                if "ariba.com/Sourcing" in pg.url:
                    return pg
            await asyncio.sleep(5)
        return None

    async def _vision_download(self, page: Page, download_dir: str, dl_files: list) -> list[str]:
        """Use Claude Vision to navigate SAP download flow."""
        downloaded = []
        import tempfile

        # Step 1: Screenshot event page, find Download Content
        ss1 = os.path.join(tempfile.gettempdir(), "sap_vision_step1.png")
        await page.screenshot(path=ss1)
        buttons = await _ask_claude_for_buttons(ss1)
        log.debug("SAP vision step 1: found %d elements", len(buttons))

        dc = next((b for b in buttons if "download content" in b.get("label", "").lower()), None)
        if not dc:
            log.info("SAP: no Download Content button found by vision")
            return []

        # Click Download Content
        await page.mouse.click(dc["x"], dc["y"])
        await page.wait_for_timeout(5000)

        # Step 2: Find Download Attachments in export panel
        ss2 = os.path.join(tempfile.gettempdir(), "sap_vision_step2.png")
        await page.screenshot(path=ss2)
        panel = await _ask_claude_for_buttons(ss2)
        log.debug("SAP vision step 2: found %d elements", len(panel))

        da = next((b for b in panel if "attach" in b.get("label", "").lower()), None)
        if not da:
            log.info("SAP: no Download Attachments in panel")
            return []

        # Click Download Attachments → opens attachment selection page
        await page.mouse.click(da["x"], da["y"])
        await page.wait_for_timeout(5000)

        # Step 3: Check for "No attachments" message
        body = (await page.locator("body").inner_text()).strip()
        if "no attachments" in body.lower():
            log.info("SAP: no attachments available for this tender")
            return []

        # Step 4: Select all items — find and click the "Total" checkbox
        ss3 = os.path.join(tempfile.gettempdir(), "sap_vision_step3.png")
        await page.screenshot(path=ss3)
        checkboxes = await _ask_claude_for_buttons(ss3)
        log.debug("SAP vision step 3: found %d elements", len(checkboxes))

        # Find the Total/Select All checkbox
        total_cb = next((b for b in checkboxes if "total" in b.get("label", "").lower() or "select all" in b.get("label", "").lower()), None)
        if total_cb:
            log.debug("SAP: clicking Total checkbox at (%d, %d)", total_cb["x"], total_cb["y"])
            await page.mouse.click(total_cb["x"], total_cb["y"])
            await page.wait_for_timeout(3000)

        # Step 5: Click final Download Attachments button
        ss4 = os.path.join(tempfile.gettempdir(), "sap_vision_step4.png")
        await page.screenshot(path=ss4)
        final = await _ask_claude_for_buttons(ss4)

        fd = next((b for b in final if "download" in b.get("label", "").lower() and b.get("type") == "button"), None)
        if not fd:
            # Fallback: the blue "Download Attachments" button is usually top-right
            fd = next((b for b in final if "download attach" in b.get("label", "").lower()), None)

        if fd:
            log.info("SAP: clicking final Download at (%d, %d)", fd["x"], fd["y"])
            await page.mouse.click(fd["x"], fd["y"])
            await page.wait_for_timeout(30000)

            # Save any downloaded files
            for dl in dl_files:
                fname = dl.suggested_filename or "sap_document.pdf"
                if "-fr." in fname.lower() or "_fr." in fname.lower():
                    continue
                dest = os.path.join(download_dir, fname)
                await dl.save_as(dest)
                downloaded.append(dest)
                log.info("  SAP downloaded: %s", fname)
        else:
            log.info("SAP: no final download button found")

        return downloaded

    @staticmethod
    def _resolve_sap_url(url: str) -> str:
        """Extract actual SAP URL from CanadaBuys redirect link."""
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

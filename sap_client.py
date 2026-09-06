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
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs, unquote

from playwright.async_api import BrowserContext, Page

log = logging.getLogger(__name__)

# Where failure screenshots + page dumps land. The GH workflow uploads this
# directory as an artifact so a failed cron run can be diagnosed after the
# fact instead of re-running against a live SAP account.
SAP_DIAGNOSTICS_DIR = Path(os.environ.get("SAP_DIAGNOSTICS_DIR", "sap_diagnostics"))

# How much page text to keep per failure. Enough to catch an MFA prompt,
# a consent wall, or an "invalid credentials" banner; short enough that the
# artifact stays readable.
BODY_SNIPPET_CHARS = 1500


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
        result = _parse_claude_json(message.content[0].text)
        if result:
            return result

        # Retry with simpler prompt if JSON parsing failed
        log.debug("Vision retry with simpler prompt...")
        message2 = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}},
                    {"type": "text", "text": 'Find the "Download Content" button in this screenshot. Return ONLY: [{"label":"Download Content","x":NUMBER,"y":NUMBER,"confidence":"high","type":"button"}]'},
                ],
            }],
        )
        return _parse_claude_json(message2.content[0].text)
    except Exception as exc:
        log.warning("Claude vision failed: %s", exc)
        return []


class SAPClient:
    """Playwright + Claude Vision SAP Business Network client."""

    def __init__(
        self,
        context: BrowserContext,
        username: str = "",
        password: str = "",
        diagnostics_dir: Optional[Path] = None,
    ):
        self._context = context
        self._username = username or os.environ.get("SAP_USERNAME", "")
        self._password = password or os.environ.get("SAP_PASSWORD", "")
        self._logged_in = False
        # Tri-state: None = no login attempted, True = succeeded, False = failed.
        # The caller (agent.py) reads this to distinguish "login failed" from
        # "logged in OK but downloaded nothing", which matters for the
        # halt-on-repeated-failure guardrail.
        self.last_login_succeeded: bool | None = None
        self.last_login_error: str = ""
        self._diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir else SAP_DIAGNOSTICS_DIR
        self._diag_seq = 0

    @property
    def has_credentials(self) -> bool:
        return bool(self._username and self._password)

    async def _capture_failure(self, page: Optional[Page], label: str) -> None:
        """Record what the browser was actually looking at when a step failed.

        Without this, a failed attempt logs one line and throws away
        everything that would identify the cause: whether auth actually
        succeeded, whether an MFA or consent wall appeared, or whether we
        simply landed on a URL that _find_event_page's substring check
        doesn't recognise. 80 consecutive CI failures produced no evidence.

        Never raises. Diagnostics must not mask the failure they describe.
        """
        self._diag_seq += 1
        stem = f"sap-{self._diag_seq:02d}-{label}"

        # Every open page, not just the active one. The event sometimes lands
        # in a tab we didn't expect, and that fact is itself the answer.
        page_urls: list[str] = []
        try:
            page_urls = [p.url for p in self._context.pages]
        except Exception:
            pass

        url, title, body = "", "", ""
        if page is not None:
            try:
                url = page.url or ""
            except Exception:
                pass
            try:
                title = await page.title() or ""
            except Exception:
                pass
            try:
                raw = await page.locator("body").inner_text()
                body = " ".join((raw or "").split())[:BODY_SNIPPET_CHARS]
            except Exception:
                pass

        # Redact everything we persist, not just the body: SAP echoes the
        # username into query strings on some of its login hops.
        url, title, body = self._redact(url), self._redact(title), self._redact(body)
        page_urls = [self._redact(u) for u in page_urls]

        log.warning("SAP DIAG [%s] url=%s title=%s", label, url or "?", title or "?")
        log.warning("SAP DIAG [%s] open_pages=%s", label, page_urls or [])
        if body:
            log.warning("SAP DIAG [%s] body=%s", label, body[:400])

        try:
            self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.debug("SAP DIAG: could not create %s: %s", self._diagnostics_dir, exc)
            return

        try:
            (self._diagnostics_dir / f"{stem}.txt").write_text(
                f"label: {label}\nurl: {url}\ntitle: {title}\n"
                f"open_pages:\n" + "".join(f"  - {u}\n" for u in page_urls) +
                f"\nbody:\n{body}\n",
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug("SAP DIAG: could not write text dump: %s", exc)

        if page is not None:
            try:
                await page.screenshot(path=str(self._diagnostics_dir / f"{stem}.png"))
                log.warning("SAP DIAG [%s] screenshot: %s.png", label, stem)
            except Exception as exc:
                log.debug("SAP DIAG: could not screenshot: %s", exc)

    def _redact(self, text: str) -> str:
        """Strip the SAP username out of anything we persist. The password is
        never rendered (input fields mask it), but the login form echoes the
        username back, and these dumps get uploaded as CI artifacts."""
        if text and self._username:
            return text.replace(self._username, "<SAP_USERNAME>")
        return text

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

        # Reset per call so the tri-state means "what happened during THIS
        # download". Without this, a session reused across tenders keeps
        # reporting the first tender's login result, and agent.py would
        # re-record that same success or failure once per tender.
        self.last_login_succeeded = None
        self.last_login_error = ""

        os.makedirs(download_dir, exist_ok=True)
        page = await self._context.new_page()
        downloaded: list[str] = []

        try:
            # Step 1: Load SAP discovery page (SPA — needs time to render)
            log.debug("SAP: loading %s", sap_url[:80])
            await page.goto(sap_url, timeout=60000, wait_until="load")
            await page.wait_for_timeout(15000)  # SAP SPA needs time

            # Step 2: Login if needed. One session serves the whole run —
            # see _SapSession in agent.py.
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
            else:
                log.info("SAP: reusing existing session, skipping login")

            # Step 3: Find event page
            event_page = await self._find_event_page()
            if not event_page:
                log.warning("SAP: could not find event page after login")
                await self._capture_failure(
                    self._context.pages[-1] if self._context.pages else page,
                    "event-page-missing-post-login",
                )
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
        """Handle full SAP login: Respond → Register/Login → cookie → username → password.

        Sets self.last_login_succeeded (True/False) and self.last_login_error
        so callers can distinguish a credential/auth failure from a downstream
        "nothing to download" outcome.
        """
        try:
            # Click Respond
            respond = page.locator("button:has-text('Respond')").first
            if await respond.count() == 0:
                log.warning("SAP: no Respond button")
                self.last_login_succeeded = False
                self.last_login_error = "no Respond button on discovery page"
                await self._capture_failure(page, "no-respond-button")
                return False
            await respond.click()
            log.info("SAP: clicked Respond")
            await page.wait_for_timeout(3000)

            # Click Register/Login in popup
            login_btn = page.locator("button:has-text('Register/Login'), button:has-text('Login')").first
            if await login_btn.count() > 0:
                try:
                    async with page.expect_navigation(timeout=30000):
                        await login_btn.click()
                except:
                    pass
                log.info("SAP: clicked Register/Login")
                await page.wait_for_timeout(3000)

            # Dismiss cookie consent
            cookie = page.locator("button:has-text('Accept All'), #truste-consent-button").first
            if await cookie.count() > 0:
                await cookie.click()
                log.info("SAP: dismissed cookie consent")
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
                self.last_login_succeeded = False
                self.last_login_error = "no username field after Register/Login"
                await self._capture_failure(page, "no-username-field")
                return False
            log.info("SAP: submitted username")
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
                self.last_login_succeeded = False
                self.last_login_error = "no password field after username submit"
                await self._capture_failure(current, "no-password-field")
                return False

            await pwd.fill(self._password)
            await pwd.press("Enter")
            log.info("SAP: submitted password, waiting for event page")
            await page.wait_for_timeout(15000)

            # Verify login
            event_page = await self._find_event_page()
            if event_page:
                self._logged_in = True
                self.last_login_succeeded = True
                self.last_login_error = ""
                log.info("SAP login successful")
                return True

            # We got as far as submitting the password and then found no page
            # whose URL contains "ariba.com/Sourcing". That is consistent with
            # several very different causes (auth rejected, MFA/consent wall,
            # or a landing URL our matcher doesn't recognise), so capture the
            # page rather than guessing which one it was.
            log.warning("SAP: login completed but event page not found")
            self.last_login_succeeded = False
            self.last_login_error = "login completed but event page not found"
            await self._capture_failure(
                self._context.pages[-1] if self._context.pages else page,
                "event-page-not-found",
            )
            return False

        except Exception as exc:
            log.warning("SAP login error: %s", exc)
            self.last_login_succeeded = False
            self.last_login_error = f"login flow exception: {exc}"
            await self._capture_failure(page, "login-exception")
            return False

    async def _find_event_page(self) -> Page | None:
        """Find the SAP event page among open pages."""
        for _ in range(12):  # 12 × 5s = 60s max wait
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

        # Step 4: Select all items — use DOM to find and click checkboxes
        # SAP uses standard HTML checkboxes in the attachment selection page
        checkboxes = page.locator("input[type='checkbox']")
        cb_count = await checkboxes.count()
        log.debug("SAP: found %d checkboxes on attachment page", cb_count)

        if cb_count > 0:
            # Click the first checkbox (usually "Title" or "Totals" — selects all)
            try:
                await checkboxes.first.click(force=True)
                await page.wait_for_timeout(2000)
                log.debug("SAP: clicked first checkbox (select all)")
            except Exception:
                # Fallback: try vision for the checkbox
                ss3 = os.path.join(tempfile.gettempdir(), "sap_vision_step3.png")
                await page.screenshot(path=ss3)
                cb_buttons = await _ask_claude_for_buttons(ss3)
                total_cb = next((b for b in cb_buttons if "total" in b.get("label", "").lower() or "select" in b.get("label", "").lower()), None)
                if total_cb:
                    await page.mouse.click(total_cb["x"], total_cb["y"])
                    await page.wait_for_timeout(2000)
        else:
            log.debug("SAP: no checkboxes found — may already be selected or different UI")

        # Step 5: Click final Download Attachments button — try DOM first, then vision
        dl_btn = page.locator("button:has-text('Download Attachments'), a:has-text('Download Attachments')").first
        ss4 = os.path.join(tempfile.gettempdir(), "sap_vision_step4.png")
        await page.screenshot(path=ss4)

        if await dl_btn.count() > 0:
            try:
                fd = None  # skip vision, use DOM
                await dl_btn.click(force=True)
                log.info("SAP: clicked Download Attachments via DOM")
                await page.wait_for_timeout(30000)
                # Save downloads
                for dl in dl_files:
                    fname = dl.suggested_filename or "sap_document.pdf"
                    if "-fr." in fname.lower() or "_fr." in fname.lower():
                        continue
                    dest = os.path.join(download_dir, fname)
                    await dl.save_as(dest)
                    downloaded.append(dest)
                    log.info("  SAP downloaded: %s", fname)
                return downloaded
            except Exception as exc:
                log.debug("SAP: DOM click failed, trying vision: %s", exc)

        # Vision fallback for download button
        final = await _ask_claude_for_buttons(ss4)
        fd = next((b for b in final if "download" in b.get("label", "").lower() and b.get("type") == "button"), None)
        if not fd:
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

"""Unit tests for SAP failure diagnostics.

Between 2026-06-09 and 2026-06-26 every SAP login attempt failed (80 of
them, zero successes) and produced exactly one log line: "login completed
but event page not found". That line cannot distinguish rejected
credentials from an MFA wall from a landing URL our matcher doesn't
recognise. These tests pin the capture that makes the difference visible.

No Playwright, no network — the page is a hand-rolled fake.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sap_client import SAPClient


# ── Fakes ───────────────────────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, count: int = 0, text: str = "", raises: bool = False):
        self._count = count
        self._text = text
        self._raises = raises

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def inner_text(self):
        if self._raises:
            raise RuntimeError("detached from DOM")
        return self._text

    async def wait_for(self, **kw):
        return None

    async def click(self, **kw):
        return None


class FakePage:
    """Minimal stand-in for a Playwright Page."""

    def __init__(
        self,
        url: str = "https://portal.us.bn.cloud.ariba.com/dash",
        title: str = "SAP Business Network",
        body: str = "Enter your password to continue",
        respond_count: int = 0,
        fail_title: bool = False,
        fail_body: bool = False,
        fail_screenshot: bool = False,
    ):
        self.url = url
        self._title = title
        self._body = body
        self._respond_count = respond_count
        self._fail_title = fail_title
        self._fail_body = fail_body
        self._fail_screenshot = fail_screenshot
        self.frames = []

    async def title(self):
        if self._fail_title:
            raise RuntimeError("page closed")
        return self._title

    def locator(self, selector: str):
        if "body" in selector:
            return FakeLocator(count=1, text=self._body, raises=self._fail_body)
        if "Respond" in selector:
            return FakeLocator(count=self._respond_count)
        return FakeLocator(count=0)

    async def screenshot(self, path=None, **kw):
        if self._fail_screenshot:
            raise RuntimeError("screenshot timeout")
        Path(path).write_bytes(b"\x89PNG-fake")

    async def wait_for_timeout(self, ms):
        return None


class FakeContext:
    def __init__(self, pages=None, raise_on_pages: bool = False):
        self._pages = pages or []
        self._raise = raise_on_pages

    @property
    def pages(self):
        if self._raise:
            raise RuntimeError("context closed")
        return self._pages


def make_client(tmp_path, pages=None, **ctx_kw) -> SAPClient:
    ctx = FakeContext(pages=pages, **ctx_kw)
    return SAPClient(ctx, username="buyer@rad.example", password="pw",
                     diagnostics_dir=tmp_path)


# ── _capture_failure writes evidence ────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_writes_text_and_screenshot(tmp_path):
    page = FakePage()
    client = make_client(tmp_path, pages=[page])

    await client._capture_failure(page, "event-page-not-found")

    assert (tmp_path / "sap-01-event-page-not-found.txt").exists()
    assert (tmp_path / "sap-01-event-page-not-found.png").exists()


@pytest.mark.asyncio
async def test_capture_records_url_title_and_body(tmp_path):
    page = FakePage(
        url="https://service.ariba.com/Authenticator.aw/mfa",
        title="Verify your identity",
        body="We sent a code to your phone",
    )
    client = make_client(tmp_path, pages=[page])

    await client._capture_failure(page, "event-page-not-found")
    dump = (tmp_path / "sap-01-event-page-not-found.txt").read_text()

    assert "https://service.ariba.com/Authenticator.aw/mfa" in dump
    assert "Verify your identity" in dump
    assert "We sent a code to your phone" in dump


@pytest.mark.asyncio
async def test_capture_lists_every_open_page(tmp_path):
    """The event sometimes lands in a tab we didn't expect. Which tabs are
    open, and at which URLs, is the whole question."""
    active = FakePage(url="https://portal.us.bn.cloud.ariba.com/dash")
    other = FakePage(url="https://service.ariba.com/Sourcing.aw/1234/event")
    client = make_client(tmp_path, pages=[active, other])

    await client._capture_failure(active, "event-page-not-found")
    dump = (tmp_path / "sap-01-event-page-not-found.txt").read_text()

    assert "https://portal.us.bn.cloud.ariba.com/dash" in dump
    assert "https://service.ariba.com/Sourcing.aw/1234/event" in dump


@pytest.mark.asyncio
async def test_capture_redacts_username(tmp_path):
    """These dumps get uploaded as CI artifacts."""
    page = FakePage(body="Signed in as buyer@rad.example — continue")
    client = make_client(tmp_path, pages=[page])

    await client._capture_failure(page, "event-page-not-found")
    dump = (tmp_path / "sap-01-event-page-not-found.txt").read_text()

    assert "buyer@rad.example" not in dump
    assert "<SAP_USERNAME>" in dump


@pytest.mark.asyncio
async def test_capture_redacts_username_from_urls_too(tmp_path):
    """SAP echoes the username into query strings on some login hops."""
    page = FakePage(url="https://service.ariba.com/login?user=buyer@rad.example",
                    title="Sign in as buyer@rad.example", body="")
    client = make_client(tmp_path, pages=[page])

    await client._capture_failure(page, "event-page-not-found")
    dump = (tmp_path / "sap-01-event-page-not-found.txt").read_text()

    assert "buyer@rad.example" not in dump


@pytest.mark.asyncio
async def test_capture_sequence_does_not_clobber_earlier_failures(tmp_path):
    page = FakePage()
    client = make_client(tmp_path, pages=[page])

    await client._capture_failure(page, "no-respond-button")
    await client._capture_failure(page, "event-page-not-found")

    assert (tmp_path / "sap-01-no-respond-button.txt").exists()
    assert (tmp_path / "sap-02-event-page-not-found.txt").exists()


# ── Diagnostics must never mask the failure they describe ───────────────────

@pytest.mark.asyncio
async def test_capture_survives_a_totally_dead_page(tmp_path):
    page = FakePage(fail_title=True, fail_body=True, fail_screenshot=True)
    client = make_client(tmp_path, pages=[page], raise_on_pages=True)

    await client._capture_failure(page, "login-exception")  # must not raise

    dump = (tmp_path / "sap-01-login-exception.txt")
    assert dump.exists()
    assert not (tmp_path / "sap-01-login-exception.png").exists()


@pytest.mark.asyncio
async def test_capture_tolerates_a_missing_page(tmp_path):
    client = make_client(tmp_path, pages=[])
    await client._capture_failure(None, "no-respond-button")  # must not raise
    assert (tmp_path / "sap-01-no-respond-button.txt").exists()


@pytest.mark.asyncio
async def test_capture_tolerates_an_unwritable_directory(tmp_path):
    blocker = tmp_path / "diag"
    blocker.write_text("I am a file, not a directory")
    client = SAPClient(FakeContext(pages=[]), username="u", password="p",
                       diagnostics_dir=blocker)

    await client._capture_failure(FakePage(), "event-page-not-found")  # no raise


# ── The failure paths actually call it ──────────────────────────────────────

@pytest.mark.asyncio
async def test_login_flow_captures_when_respond_button_missing(tmp_path):
    page = FakePage(respond_count=0)
    client = make_client(tmp_path, pages=[page])

    ok = await client._login_flow(page)

    assert ok is False
    assert client.last_login_error == "no Respond button on discovery page"
    assert (tmp_path / "sap-01-no-respond-button.txt").exists()


@pytest.mark.asyncio
async def test_login_flow_logs_diagnostics_at_warning(tmp_path, caplog):
    """INFO-level CI logs must carry the diagnosis, not just the artifact."""
    page = FakePage(respond_count=0, url="https://sap.example/login",
                    title="Sign in")
    client = make_client(tmp_path, pages=[page])

    with caplog.at_level(logging.WARNING, logger="sap_client"):
        await client._login_flow(page)

    diag = [r.getMessage() for r in caplog.records if "SAP DIAG" in r.getMessage()]
    assert any("https://sap.example/login" in m for m in diag)
    assert any("Sign in" in m for m in diag)

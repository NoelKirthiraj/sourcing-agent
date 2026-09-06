"""Unit tests for run-scoped SAP sessions.

agent.run_agent() used to build a fresh browser context and a fresh
SAPClient inside the per-tender loop, so a run with 9 SAP tenders performed
9 full logins at ~110s each. Two costs: ~16 minutes of a 20 minute run, and
nine authentications from one IP inside that window against an account that
does not tolerate concurrent sessions.

These tests pin one login per run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import agent
from sap_client import SAPClient


class FakeSAPClient:
    """Records how many times the agent constructed a client."""

    instances: list["FakeSAPClient"] = []
    login_succeeds = True

    # The agent uses this to drop non-federal Ariba tenants before any
    # login happens, so it has to behave like the real thing.
    _resolve_sap_url = staticmethod(SAPClient._resolve_sap_url)

    def __init__(self, context, *args, **kwargs):
        self.context = context
        self.last_login_succeeded = None
        self.last_login_error = ""
        self.downloads: list[str] = []
        self._logged_in = False
        type(self).instances.append(self)

    async def download_solicitation(self, url, download_dir):
        """Mirrors the real tri-state: the result describes THIS call, and
        an established session skips the login entirely."""
        self.downloads.append(url)
        self.last_login_succeeded = None
        self.last_login_error = ""
        if not self._logged_in:
            self.last_login_succeeded = type(self).login_succeeds
            self.last_login_error = "" if type(self).login_succeeds else "event page not found"
            self._logged_in = type(self).login_succeeds
        return []


def sap_tender(n: int) -> dict:
    return {
        "inquiry_link": f"https://canadabuys.canada.ca/en/tender-notice/cb-{n}",
        "solicitation_title": f"Tender {n}",
        "bid_platform": "SAP",
        "sap_link": f"https://portal.us.bn.cloud.ariba.com/event/{n}",
    }


async def run_agent_over(tenders, *, login_succeeds=True, monkeypatch=None):
    """Drive the real run_agent loop with everything around it mocked.

    Returns (browser, FakeSAPClient.instances, dashboard_data mock).
    """
    FakeSAPClient.instances = []
    FakeSAPClient.login_succeeds = login_succeeds

    scraper = AsyncMock()
    scraper.fetch_tender_list = AsyncMock(return_value=[dict(t) for t in tenders])
    scraper.fetch_tender_detail = AsyncMock(
        side_effect=[{"solicitation_no": f"WS-{i}"} for i, _ in enumerate(tenders)]
    )
    scraper.download_solicitation = AsyncMock(return_value=[])  # force the SAP path
    scraper._USER_AGENT = "Mozilla/5.0 Chrome/120"
    scraper._browser = MagicMock()
    scraper._browser.new_context = AsyncMock(
        side_effect=lambda **kw: MagicMock(pages=[], close=AsyncMock())
    )
    scraper.__aenter__ = AsyncMock(return_value=scraper)
    scraper.__aexit__ = AsyncMock(return_value=False)

    state = MagicMock()
    state.already_processed = MagicMock(return_value=False)

    notifier = MagicMock()
    notifier.send = AsyncMock()

    cflow = MagicMock()
    cflow.create_sourcing_request = AsyncMock(return_value="REQ-1")
    cflow.attach_solicitation = AsyncMock(return_value=True)

    dd = MagicMock()
    dd.get_sap_halt_state = MagicMock(return_value={
        "sap_login_halted": False, "sap_consecutive_failures": 0,
        "sap_halted_at": None, "sap_halted_attempts": 0, "sap_last_error": "",
    })
    dd.record_sap_login_failure = MagicMock(return_value={
        "sap_login_halted": False, "sap_consecutive_failures": 1,
    })
    dd.SAP_HALT_THRESHOLD = 2

    with patch("agent._use_db", return_value=False), \
         patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", return_value=scraper), \
         patch("agent.CFlowClient", return_value=cflow), \
         patch("agent.Notifier", return_value=notifier), \
         patch("agent.AgentState", return_value=state), \
         patch("agent.dashboard_data", dd), \
         patch("sap_client.SAPClient", FakeSAPClient), \
         patch.dict(os.environ, {"SAP_USERNAME": "u@example.com",
                                 "SAP_PASSWORD": "pw"}):
        from scraper import ScraperConfig
        cfg = MagicMock()
        cfg.scraper = ScraperConfig(headless=True, max_pages=1)
        cfg.cflow = MagicMock()
        MockConfig.load.return_value = cfg

        await agent.run_agent()

    return scraper._browser, FakeSAPClient.instances, dd


@pytest.fixture
def scraper():
    """Stand-in for CanadaBuysScraper with a live browser."""
    s = MagicMock()
    s._USER_AGENT = "Mozilla/5.0 (Macintosh) Chrome/120"
    s._browser = MagicMock()
    s._browser.new_context = AsyncMock(side_effect=lambda **kw: MagicMock(pages=[]))
    return s


# ── One context, one client, for the whole run ──────────────────────────────

@pytest.mark.asyncio
async def test_client_is_created_once_and_reused(scraper):
    session = agent._SapSession(scraper)

    first = await session.client()
    second = await session.client()
    third = await session.client()

    assert first is second is third
    assert scraper._browser.new_context.await_count == 1


@pytest.mark.asyncio
async def test_client_is_an_sap_client_on_its_own_context(scraper):
    session = agent._SapSession(scraper)
    client = await session.client()

    assert isinstance(client, SAPClient)
    # A dedicated context, not the CanadaBuys one: those cookies and
    # cache-busting headers break SAP's SPA.
    kwargs = scraper._browser.new_context.await_args.kwargs
    assert kwargs["user_agent"] == scraper._USER_AGENT
    assert kwargs["accept_downloads"] is True


@pytest.mark.asyncio
async def test_no_context_is_opened_when_no_sap_tender_appears(scraper):
    """A run with zero SAP tenders must not touch SAP at all."""
    session = agent._SapSession(scraper)
    await session.close()

    scraper._browser.new_context.assert_not_awaited()


# ── Teardown ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_closes_the_context(scraper):
    ctx = MagicMock(pages=[])
    ctx.close = AsyncMock()
    scraper._browser.new_context = AsyncMock(return_value=ctx)

    session = agent._SapSession(scraper)
    await session.client()
    await session.close()

    ctx.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_is_idempotent(scraper):
    ctx = MagicMock(pages=[])
    ctx.close = AsyncMock()
    scraper._browser.new_context = AsyncMock(return_value=ctx)

    session = agent._SapSession(scraper)
    await session.client()
    await session.close()
    await session.close()

    assert ctx.close.await_count == 1


@pytest.mark.asyncio
async def test_close_swallows_teardown_errors(scraper):
    """The browser may already be gone. That must not kill the run summary."""
    ctx = MagicMock(pages=[])
    ctx.close = AsyncMock(side_effect=Exception("browser already closed"))
    scraper._browser.new_context = AsyncMock(return_value=ctx)

    session = agent._SapSession(scraper)
    await session.client()
    await session.close()  # must not raise


@pytest.mark.asyncio
async def test_client_reopens_after_close(scraper):
    session = agent._SapSession(scraper)
    first = await session.client()
    await session.close()
    second = await session.client()

    assert first is not second
    assert scraper._browser.new_context.await_count == 2


# ── The client itself only logs in once ─────────────────────────────────────

# ── End to end: the real run_agent loop ─────────────────────────────────────

@pytest.mark.asyncio
async def test_nine_sap_tenders_produce_one_login():
    """The defect this PR fixes. On 2026-06-26, 9 SAP tenders meant 9 full
    logins at ~110s each: 16.5 minutes of a 20.4 minute run."""
    browser, clients, _ = await run_agent_over([sap_tender(i) for i in range(9)])

    assert browser.new_context.await_count == 1
    assert len(clients) == 1
    assert len(clients[0].downloads) == 9


@pytest.mark.asyncio
async def test_failed_login_gets_a_clean_context_for_the_next_tender():
    """A half-authenticated session leaves cookies that poison the retry."""
    browser, clients, dd = await run_agent_over(
        [sap_tender(i) for i in range(3)], login_succeeds=False,
    )

    assert browser.new_context.await_count == 3
    assert len(clients) == 3
    assert dd.record_sap_login_failure.call_count == 3


@pytest.mark.asyncio
async def test_successful_login_is_recorded_once_not_per_tender():
    _, clients, dd = await run_agent_over([sap_tender(i) for i in range(4)])

    # The fake reports success on construction and there is only one
    # construction, so the halt counter resets once, not four times.
    assert dd.record_sap_login_success.call_count == 1
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_reused_session_reports_no_login_result(tmp_path):
    """The tri-state must describe THIS download, not the one before it.

    Otherwise agent.py re-records the first tender's login outcome once per
    tender, either resetting the halt counter or inflating it.
    """
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(wait_for=AsyncMock()))
    page.close = AsyncMock()

    ctx = MagicMock()
    ctx.pages = [page]
    ctx.new_page = AsyncMock(return_value=page)

    client = SAPClient(ctx, username="u@example.com", password="p",
                       diagnostics_dir=tmp_path)
    client._find_event_page = AsyncMock(return_value=None)
    client._logged_in = True          # session already established
    client.last_login_succeeded = True  # ...by an earlier tender

    await client.download_solicitation("https://portal.ariba.com/b", str(tmp_path))

    assert client.last_login_succeeded is None


@pytest.mark.asyncio
async def test_second_download_skips_the_login_flow(tmp_path):
    """Reusing a client across tenders must not re-authenticate."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(wait_for=AsyncMock()))
    page.close = AsyncMock()

    ctx = MagicMock()
    ctx.pages = [page]
    ctx.new_page = AsyncMock(return_value=page)

    client = SAPClient(ctx, username="u@example.com", password="p",
                       diagnostics_dir=tmp_path)
    client._login_flow = AsyncMock(return_value=True)
    client._find_event_page = AsyncMock(return_value=None)  # stop after login

    await client.download_solicitation("https://portal.ariba.com/a", str(tmp_path))
    assert client._login_flow.await_count == 1

    # Simulate the first call having established the session.
    client._logged_in = True
    await client.download_solicitation("https://portal.ariba.com/b", str(tmp_path))

    assert client._login_flow.await_count == 1

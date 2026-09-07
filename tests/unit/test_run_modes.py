"""Unit tests for run-mode resolution and CLI overrides.

--weekly, --visible and --pages were silently dead on the full-run path.
run.py loaded a Config, mutated it, and handed nothing to run_agent, which
then called Config.load() again (and in DB mode ignores Config entirely).
Only --dry-run, which passes its own arguments, ever honoured them.

These tests assert the overrides actually reach the scraper config.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import agent
from scraper import WEEKLY_URL


async def run_and_capture(*, saturday: bool = False, tenders=None, **kwargs):
    """Drive run_agent with everything mocked; return the ScraperConfig it
    actually handed to CanadaBuysScraper, plus the recorded run summary."""
    tenders = tenders if tenders is not None else []
    scraper = AsyncMock()
    scraper.fetch_tender_list = AsyncMock(return_value=[dict(t) for t in tenders])
    scraper.fetch_tender_detail = AsyncMock(
        side_effect=[{"solicitation_no": f"WS-{i}"} for i in range(len(tenders))] or None
    )
    scraper.download_solicitation = AsyncMock(return_value=[])
    scraper.__aenter__ = AsyncMock(return_value=scraper)
    scraper.__aexit__ = AsyncMock(return_value=False)
    scraper._browser = MagicMock()

    notifier = MagicMock()
    notifier.send = AsyncMock()

    recorded = {}

    def fake_record_run(summary, data_dir=None):
        recorded["summary"] = summary

    MockScraperCls = MagicMock(return_value=scraper)

    with patch("agent._use_db", return_value=False), \
         patch("agent._is_saturday", return_value=saturday), \
         patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", MockScraperCls), \
         patch("agent.CFlowClient", return_value=MagicMock()), \
         patch("agent.Notifier", return_value=notifier), \
         patch("agent.AgentState", return_value=MagicMock()), \
         patch("agent.dashboard_data") as dd:
        from scraper import ScraperConfig
        cfg = MagicMock()
        cfg.scraper = ScraperConfig(headless=True, max_pages=1)
        cfg.cflow = MagicMock()
        MockConfig.load.return_value = cfg
        dd.record_run = fake_record_run

        await agent.run_agent(**kwargs)

    return MockScraperCls.call_args.args[0], recorded["summary"], scraper


# ── weekly resolution ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explicit_weekly_applies_on_a_weekday():
    """The bug: `--weekly` on a Tuesday used to run daily filters."""
    config, summary, _ = await run_and_capture(saturday=False, weekly=True)

    assert config.search_url == WEEKLY_URL
    assert summary.mode == "weekly"


@pytest.mark.asyncio
async def test_explicit_daily_overrides_the_saturday_autodetect():
    """A manual "daily" dispatch that lands on a Saturday must stay daily."""
    config, summary, _ = await run_and_capture(saturday=True, weekly=False)

    assert config.search_url != WEEKLY_URL
    assert summary.mode == "daily"


@pytest.mark.asyncio
async def test_saturday_autodetect_still_applies_when_unspecified():
    config, summary, _ = await run_and_capture(saturday=True, weekly=None)

    assert config.search_url == WEEKLY_URL
    assert summary.mode == "weekly"


@pytest.mark.asyncio
async def test_weekday_autodetect_stays_daily():
    config, summary, _ = await run_and_capture(saturday=False, weekly=None)

    assert config.search_url != WEEKLY_URL
    assert summary.mode == "daily"


# ── the other two dead flags ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_headless_override_reaches_the_scraper():
    config, _, _ = await run_and_capture(headless=False)
    assert config.headless is False


@pytest.mark.asyncio
async def test_headless_defaults_to_the_configured_value():
    config, _, _ = await run_and_capture()
    assert config.headless is True


@pytest.mark.asyncio
async def test_max_pages_override_reaches_the_scraper():
    config, _, _ = await run_and_capture(max_pages=7)
    assert config.max_pages == 7


@pytest.mark.asyncio
async def test_max_pages_defaults_when_not_given():
    config, _, _ = await run_and_capture()
    assert config.max_pages == 1


# ── --limit bounds the work ─────────────────────────────────────────────────

def listing(n: int) -> list[dict]:
    return [{"inquiry_link": f"https://canadabuys.canada.ca/en/t/{i}",
             "solicitation_title": f"Tender {i}",
             "bid_platform": "CanadaBuys"} for i in range(n)]


@pytest.mark.asyncio
async def test_limit_stops_after_n_tenders():
    _, summary, scraper = await run_and_capture(tenders=listing(5), limit=2)

    assert scraper.fetch_tender_detail.await_count == 2
    # total_found still reports everything the portal returned, so the run
    # history doesn't pretend the day was quieter than it was.
    assert summary.total_found == 5


@pytest.mark.asyncio
async def test_limit_above_the_result_count_is_a_no_op():
    _, _, scraper = await run_and_capture(tenders=listing(3), limit=10)
    assert scraper.fetch_tender_detail.await_count == 3


@pytest.mark.asyncio
async def test_no_limit_processes_everything():
    _, _, scraper = await run_and_capture(tenders=listing(4))
    assert scraper.fetch_tender_detail.await_count == 4


# ── run.py flag → run_agent argument wiring ─────────────────────────────────

def resolve(argv: list[str]):
    """What run.py hands to run_agent for a given command line."""
    import run
    with patch.object(sys, "argv", ["run.py", *argv]):
        args = run.parse_args()
    if args.weekly:
        return True
    if args.daily:
        return False
    return None


def test_bare_run_leaves_mode_to_the_autodetect():
    assert resolve([]) is None


def test_weekly_flag_forces_weekly():
    assert resolve(["--weekly"]) is True


def test_daily_flag_forces_daily():
    assert resolve(["--daily"]) is False


def test_weekly_wins_when_both_flags_are_passed():
    assert resolve(["--weekly", "--daily"]) is True

"""Unit tests for agent.py orchestrator wiring (fully mocked)."""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
from pathlib import Path


@pytest.fixture
def mock_config():
    """Mock Config.load() return value."""
    from scraper import ScraperConfig
    config = MagicMock()
    config.scraper = ScraperConfig(headless=True, max_pages=1)
    config.cflow = MagicMock()
    return config


@pytest.mark.asyncio
async def test_record_run_called_after_notifier_send(mock_config):
    """dashboard_data.record_run is called after notifier.send completes."""
    call_order = []

    mock_notifier_instance = MagicMock()

    async def fake_send(summary):
        call_order.append("notifier_send")

    mock_notifier_instance.send = fake_send

    def fake_record_run(summary, data_dir=None):
        call_order.append("record_run")

    mock_scraper_instance = AsyncMock()
    mock_scraper_instance.fetch_tender_list = AsyncMock(return_value=[])
    mock_scraper_instance.__aenter__ = AsyncMock(return_value=mock_scraper_instance)
    mock_scraper_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", return_value=mock_scraper_instance), \
         patch("agent.CFlowClient", return_value=MagicMock()), \
         patch("agent.Notifier", return_value=mock_notifier_instance), \
         patch("agent.AgentState") as MockState, \
         patch("agent.dashboard_data") as mock_dashboard:

        MockConfig.load.return_value = mock_config
        mock_state = MagicMock()
        MockState.return_value = mock_state
        mock_dashboard.record_run = fake_record_run

        import agent
        await agent.run_agent()

    assert "notifier_send" in call_order
    assert "record_run" in call_order
    assert call_order.index("notifier_send") < call_order.index("record_run")


@pytest.mark.asyncio
async def test_summary_duration_seconds_is_set(mock_config):
    """summary.duration_seconds should be a positive number after run."""
    captured_summary = {}

    def fake_record_run(summary, data_dir=None):
        captured_summary["duration"] = summary.duration_seconds
        captured_summary["summary"] = summary

    mock_scraper_instance = AsyncMock()
    mock_scraper_instance.fetch_tender_list = AsyncMock(return_value=[])
    mock_scraper_instance.__aenter__ = AsyncMock(return_value=mock_scraper_instance)
    mock_scraper_instance.__aexit__ = AsyncMock(return_value=False)

    mock_notifier_instance = MagicMock()
    mock_notifier_instance.send = AsyncMock()

    with patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", return_value=mock_scraper_instance), \
         patch("agent.CFlowClient", return_value=MagicMock()), \
         patch("agent.Notifier", return_value=mock_notifier_instance), \
         patch("agent.AgentState") as MockState, \
         patch("agent.dashboard_data") as mock_dashboard:

        MockConfig.load.return_value = mock_config
        MockState.return_value = MagicMock()
        mock_dashboard.record_run = fake_record_run

        import agent
        await agent.run_agent()

    assert captured_summary["duration"] >= 0


@pytest.mark.asyncio
async def test_summary_mode_set_based_on_weekday(mock_config):
    """summary.mode should be 'weekly' on Saturday (weekday==5) and 'daily' otherwise."""
    captured = {}

    def fake_record_run(summary, data_dir=None):
        captured["mode"] = summary.mode

    mock_scraper_instance = AsyncMock()
    mock_scraper_instance.fetch_tender_list = AsyncMock(return_value=[])
    mock_scraper_instance.__aenter__ = AsyncMock(return_value=mock_scraper_instance)
    mock_scraper_instance.__aexit__ = AsyncMock(return_value=False)

    mock_notifier_instance = MagicMock()
    mock_notifier_instance.send = AsyncMock()

    # Test Saturday -> weekly
    from datetime import datetime
    fake_saturday = datetime(2026, 3, 28)  # Saturday
    assert fake_saturday.weekday() == 5

    with patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", return_value=mock_scraper_instance), \
         patch("agent.CFlowClient", return_value=MagicMock()), \
         patch("agent.Notifier", return_value=mock_notifier_instance), \
         patch("agent.AgentState") as MockState, \
         patch("agent.dashboard_data") as mock_dashboard, \
         patch("agent.datetime") as mock_dt:

        MockConfig.load.return_value = mock_config
        MockState.return_value = MagicMock()
        mock_dashboard.record_run = fake_record_run
        mock_dt.now.return_value = fake_saturday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        import agent
        await agent.run_agent()

    assert captured["mode"] == "weekly"


@pytest.mark.asyncio
async def test_summary_mode_daily_on_weekday(mock_config):
    """summary.mode should be 'daily' on a non-Saturday weekday."""
    captured = {}

    def fake_record_run(summary, data_dir=None):
        captured["mode"] = summary.mode

    mock_scraper_instance = AsyncMock()
    mock_scraper_instance.fetch_tender_list = AsyncMock(return_value=[])
    mock_scraper_instance.__aenter__ = AsyncMock(return_value=mock_scraper_instance)
    mock_scraper_instance.__aexit__ = AsyncMock(return_value=False)

    mock_notifier_instance = MagicMock()
    mock_notifier_instance.send = AsyncMock()

    from datetime import datetime
    fake_monday = datetime(2026, 3, 23)  # Monday
    assert fake_monday.weekday() == 0

    with patch("agent.Config") as MockConfig, \
         patch("agent.CanadaBuysScraper", return_value=mock_scraper_instance), \
         patch("agent.CFlowClient", return_value=MagicMock()), \
         patch("agent.Notifier", return_value=mock_notifier_instance), \
         patch("agent.AgentState") as MockState, \
         patch("agent.dashboard_data") as mock_dashboard, \
         patch("agent.datetime") as mock_dt:

        MockConfig.load.return_value = mock_config
        MockState.return_value = MagicMock()
        mock_dashboard.record_run = fake_record_run
        mock_dt.now.return_value = fake_monday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        import agent
        await agent.run_agent()

    assert captured["mode"] == "daily"

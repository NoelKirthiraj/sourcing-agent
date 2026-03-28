"""
Shared pytest fixtures for the CanadaBuys → CFlow agent tests.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from cflow_client import CFlowClient, CFlowConfig
from scraper import ScraperConfig
from state import AgentState


@pytest.fixture
def tmp_state(tmp_path):
    """AgentState backed by a temp file — isolated per test."""
    return AgentState(path=tmp_path / "state.json")


@pytest.fixture
def cflow_config():
    return CFlowConfig(
        base_url="https://pubapi-us.cflowapps.com/cflowpublicapi",
        api_key="test-api-key",
        user_key="test-user-key",
        username="test@example.com",
        workflow_name="Sourcing Workflow",
        stage_name="Stage 1",
    )


@pytest.fixture
def scraper_config():
    return ScraperConfig(headless=True, max_pages=1)


@pytest.fixture
def sample_tender():
    return {
        "solicitation_title": "IT Security Assessment Services",
        "solicitation_no": "PW-EZZ-123-00001",
        "gsin_description": "EDP - Professional Services",
        "inquiry_link": "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/PW-EZZ-123-00001",
        "closing_date": "2026-04-15",
        "time_and_zone": "14:00 Eastern",
        "notifications": "0 amendments",
        "client": "Shared Services Canada",
        "contact_name": "Jane Smith",
        "contact_email": "jane.smith@ssc-spc.gc.ca",
        "contact_phone": "613-555-0100",
    }

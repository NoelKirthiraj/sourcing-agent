"""Unit tests for sap_client.py — SAP login and download (mocked Playwright)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sap_client import SAPClient


@pytest.fixture
def mock_context():
    ctx = AsyncMock()
    ctx.pages = []
    return ctx


@pytest.fixture
def sap_client(mock_context):
    return SAPClient(mock_context, username="test@sap.com", password="secret123")


def test_has_credentials_true(sap_client):
    assert sap_client.has_credentials is True


def test_has_credentials_false(mock_context):
    with patch.dict(os.environ, {}, clear=True):
        client = SAPClient(mock_context, username="", password="")
        assert client.has_credentials is False


@pytest.mark.asyncio
async def test_download_returns_empty_without_credentials(mock_context):
    with patch.dict(os.environ, {}, clear=True):
        client = SAPClient(mock_context, username="", password="")
        result = await client.download_solicitation("https://sap.example.com", "/tmp")
        assert result == []


@pytest.mark.asyncio
async def test_download_returns_empty_with_empty_url(sap_client):
    result = await sap_client.download_solicitation("", "/tmp")
    assert result == []


def test_resolve_sap_url_direct():
    url = "https://portal.us.bn.cloud.ariba.com/dashboard/123"
    assert SAPClient._resolve_sap_url(url) == url


def test_resolve_sap_url_from_redirect():
    url = "/en/you-are-now-leaving-canadabuys?ariba=1&destin=https%3A//portal.us.bn.cloud.ariba.com/test"
    resolved = SAPClient._resolve_sap_url(url)
    assert resolved == "https://portal.us.bn.cloud.ariba.com/test"


def test_resolve_sap_url_relative_becomes_absolute():
    url = "/en/you-are-now-leaving-canadabuys?destin=https%3A//example.ariba.com/x"
    resolved = SAPClient._resolve_sap_url(url)
    assert resolved.startswith("https://")


def test_resolve_sap_url_no_destin():
    url = "https://canadabuys.canada.ca/en/some-page"
    resolved = SAPClient._resolve_sap_url(url)
    assert resolved == url


@pytest.mark.asyncio
async def test_download_handles_navigation_error(sap_client, mock_context):
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))
    page.close = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=page)
    mock_context.pages = [page]

    result = await sap_client.download_solicitation("https://portal.ariba.com/test", "/tmp")
    assert result == []

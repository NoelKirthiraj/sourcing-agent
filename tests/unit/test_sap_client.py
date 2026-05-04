"""Unit tests for sap_client.py — SAP login and download (mocked Playwright)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sap_client import SAPClient


@pytest.fixture
def mock_context():
    return AsyncMock()


@pytest.fixture
def sap_client(mock_context):
    return SAPClient(mock_context, username="test@sap.com", password="secret123")


def test_has_credentials_true(sap_client):
    assert sap_client.has_credentials is True


def test_has_credentials_false(mock_context):
    client = SAPClient(mock_context, username="", password="")
    assert client.has_credentials is False


@pytest.mark.asyncio
async def test_download_returns_empty_without_credentials(mock_context):
    client = SAPClient(mock_context, username="", password="")
    result = await client.download_solicitation("https://sap.example.com", "/tmp")
    assert result == []


@pytest.mark.asyncio
async def test_download_returns_empty_with_empty_url(sap_client):
    result = await sap_client.download_solicitation("", "/tmp")
    assert result == []


@pytest.mark.asyncio
async def test_download_handles_navigation_error(sap_client, mock_context):
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))
    mock_context.new_page = AsyncMock(return_value=page)

    result = await sap_client.download_solicitation("https://sap.example.com", "/tmp")
    assert result == []
    page.close.assert_called_once()


@pytest.mark.asyncio
async def test_login_detects_no_login_form(sap_client):
    """If no username field found, assume already authenticated."""
    page = AsyncMock()
    locator = AsyncMock()
    locator.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=locator)
    locator.first = locator

    result = await sap_client._try_login(page)
    assert result is True
    # Should NOT cache _logged_in when login form is simply absent
    assert sap_client._logged_in is False


@pytest.mark.asyncio
async def test_login_fills_credentials(sap_client):
    """Test that login fills username, password, and submits."""
    page = AsyncMock()

    # Username field exists
    username_locator = AsyncMock()
    username_locator.count = AsyncMock(return_value=1)
    username_locator.fill = AsyncMock()
    username_locator.first = username_locator

    # No continue button
    continue_locator = AsyncMock()
    continue_locator.count = AsyncMock(return_value=0)
    continue_locator.first = continue_locator

    # Password field
    password_locator = AsyncMock()
    password_locator.count = AsyncMock(return_value=1)
    password_locator.fill = AsyncMock()
    password_locator.first = password_locator

    # Submit button
    submit_locator = AsyncMock()
    submit_locator.count = AsyncMock(return_value=1)
    submit_locator.click = AsyncMock()
    submit_locator.first = submit_locator

    # After submit, no login fields (success)
    post_login_locator = AsyncMock()
    post_login_locator.count = AsyncMock(return_value=0)

    call_count = 0
    def mock_locator(selector):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return username_locator
        elif call_count == 2:
            return continue_locator
        elif call_count == 3:
            return password_locator
        elif call_count == 4:
            return submit_locator
        else:
            return post_login_locator

    page.locator = MagicMock(side_effect=mock_locator)

    result = await sap_client._try_login(page)
    assert result is True
    username_locator.fill.assert_called_with("test@sap.com")
    password_locator.fill.assert_called_with("secret123")


@pytest.mark.asyncio
async def test_login_detects_failure_still_on_login_page(sap_client):
    """If still on login page after submit, login failed."""
    page = AsyncMock()

    locator = AsyncMock()
    locator.count = AsyncMock(return_value=1)  # Always returns login fields
    locator.fill = AsyncMock()
    locator.click = AsyncMock()
    locator.first = locator
    page.locator = MagicMock(return_value=locator)

    result = await sap_client._try_login(page)
    assert result is False

"""Unit tests for telling "vision could not run" apart from "vision found nothing".

On 2026-09-07 an exhausted Anthropic balance surfaced as:

    SAP: no Download Content button found by vision

The button was plainly visible in the screenshot. A billing problem was
reported as a page problem, and it cost an hour of chasing the SAP UI.
po_extractor learned this lesson in #58; these tests pin it for sap_client.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import sap_client
from sap_client import SAPClient, SapVisionUnavailable, _vision_unavailable_reason


# The verbatim message the API returned on 2026-09-07.
CREDIT_ERROR = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits.'}}"
)


# ── classification ──────────────────────────────────────────────────────────

def test_credit_balance_error_is_recognised():
    reason = _vision_unavailable_reason(Exception(CREDIT_ERROR))
    assert reason is not None
    assert "credit balance is exhausted" in reason
    assert "console.anthropic.com/settings/billing" in reason


def test_quota_error_is_recognised():
    reason = _vision_unavailable_reason(Exception("usage limit reached for this org"))
    assert reason is not None
    assert "quota limit" in reason


def test_auth_error_is_recognised():
    reason = _vision_unavailable_reason(Exception("invalid x-api-key"))
    assert reason is not None
    assert "ANTHROPIC_API_KEY" in reason


def test_ordinary_failures_are_not_misclassified():
    """A timeout or a parse failure genuinely is "we tried and got nothing",
    and must keep the old best-effort behaviour."""
    for msg in ["Connection timed out", "Read timeout", "500 internal server error",
                "Expecting value: line 1 column 1"]:
        assert _vision_unavailable_reason(Exception(msg)) is None


# ── _ask_claude_for_buttons ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_error_raises_instead_of_returning_empty(tmp_path):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"PNG")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(CREDIT_ERROR)

    with patch.dict(sys.modules, {"anthropic": fake}):
        with pytest.raises(SapVisionUnavailable) as err:
            await sap_client._ask_claude_for_buttons(str(shot))

    assert "credit balance is exhausted" in err.value.user_message


@pytest.mark.asyncio
async def test_credit_error_is_logged_at_error_level(tmp_path, caplog):
    """It has to be visible in a CI log at default INFO, not just in an
    exception someone has to go find."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"PNG")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception(CREDIT_ERROR)

    with patch.dict(sys.modules, {"anthropic": fake}):
        with caplog.at_level(logging.ERROR, logger="sap_client"):
            with pytest.raises(SapVisionUnavailable):
                await sap_client._ask_claude_for_buttons(str(shot))

    assert any("vision unavailable" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_ordinary_error_still_returns_empty(tmp_path):
    shot = tmp_path / "s.png"
    shot.write_bytes(b"PNG")
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.side_effect = Exception("Read timeout")

    with patch.dict(sys.modules, {"anthropic": fake}):
        assert await sap_client._ask_claude_for_buttons(str(shot)) == []


# ── download_solicitation surfaces it without touching the login state ──────

class _Ctx:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        page = MagicMock()
        page.url = "https://portal.us.bn.cloud.ariba.com/x"
        page.goto = AsyncMock(return_value=MagicMock(status=200))
        page.wait_for_timeout = AsyncMock()
        page.close = AsyncMock()
        page.title = AsyncMock(return_value="SAP")
        loc = MagicMock()
        loc.first = loc
        loc.count = AsyncMock(return_value=1)
        loc.wait_for = AsyncMock()
        loc.click = AsyncMock()
        loc.inner_text = AsyncMock(return_value="")
        page.locator = MagicMock(return_value=loc)
        page.on = MagicMock()
        self.pages.append(page)
        return page


@pytest.mark.asyncio
async def test_vision_failure_is_recorded_separately_from_login(tmp_path):
    """The SAP login halt guardrail must not trip on an Anthropic bill."""
    client = SAPClient(_Ctx(), username="u@example.com", password="p",
                       diagnostics_dir=tmp_path)
    client._logged_in = True
    client._find_event_page = AsyncMock(return_value=MagicMock(
        wait_for_timeout=AsyncMock(), on=MagicMock()))
    client._vision_download = AsyncMock(
        side_effect=SapVisionUnavailable("Anthropic API credit balance is exhausted."))

    files = await client.download_solicitation("https://portal.ariba.com/a", str(tmp_path))

    assert files == []
    assert "credit balance" in client.last_vision_error
    # Login itself was fine — this must not feed the halt counter.
    assert client.last_login_succeeded is None
    assert client.last_login_error == ""


@pytest.mark.asyncio
async def test_vision_error_resets_between_tenders(tmp_path):
    client = SAPClient(_Ctx(), username="u@example.com", password="p",
                       diagnostics_dir=tmp_path)
    client._logged_in = True
    client.last_vision_error = "stale message from the previous tender"
    client._find_event_page = AsyncMock(return_value=MagicMock(
        wait_for_timeout=AsyncMock(), on=MagicMock()))
    client._vision_download = AsyncMock(return_value=[])

    await client.download_solicitation("https://portal.ariba.com/b", str(tmp_path))

    assert client.last_vision_error == ""

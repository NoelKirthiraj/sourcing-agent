"""Unit tests for extractor.py — LLM PDF extraction (mocked API)."""
import json
import pytest
from unittest.mock import patch, MagicMock
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def tmp_pdf(tmp_path):
    """Create a dummy PDF file for testing."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf content")
    return str(pdf)


def _mock_message(content_text):
    """Create a mock Anthropic message response."""
    msg = MagicMock()
    block = MagicMock()
    block.text = content_text
    msg.content = [block]
    return msg


@pytest.mark.asyncio
async def test_extract_single_item(tmp_pdf):
    response_json = json.dumps({
        "summary_of_contract": "Procurement of marine crane for CCGS Gordon Reid.",
        "requirements": "Search and rescue crane, capacity 5 tonnes, marine-grade.",
        "mandatory_criteria": "ISO 9001 certification required.\nMarine-grade corrosion resistance.",
        "submission_method": "E-post",
        "is_multi_inquiry": False,
    })

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_message(response_json)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic", return_value=mock_client):

        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)

    assert result["summary_of_contract"] == "Procurement of marine crane for CCGS Gordon Reid."
    assert result["is_multi_inquiry"] is False
    assert result["submission_method"] == "E-post"
    assert "ISO 9001" in result["mandatory_criteria"]


@pytest.mark.asyncio
async def test_extract_multi_item(tmp_pdf):
    response_json = json.dumps({
        "summary_of_contract": "Supply of multiple parts for CCG vessel.",
        "requirements": [
            {"item": 1, "gsin": "5330", "nsn": "00-0647777", "description": "PACKING, PREFORMED",
             "part_no": "6750674", "ncage": "14414", "quantity": 10},
            {"item": 2, "gsin": "4730", "nsn": "00-8531182", "description": "ADAPTER, STRAIGHT",
             "part_no": "0001272-1020", "ncage": "16662", "quantity": 10},
        ],
        "mandatory_criteria": "All parts must meet DND specifications.",
        "submission_method": "SAP",
        "is_multi_inquiry": True,
    })

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_message(response_json)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic", return_value=mock_client):

        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)

    assert result["is_multi_inquiry"] is True
    assert isinstance(result["requirements"], list)
    assert len(result["requirements"]) == 2
    assert result["requirements"][0]["description"] == "PACKING, PREFORMED"


@pytest.mark.asyncio
async def test_extract_handles_api_error(tmp_pdf):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API rate limit")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic", return_value=mock_client):

        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)

    assert result == {}


@pytest.mark.asyncio
async def test_extract_handles_invalid_json(tmp_pdf):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_message("not valid json at all")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic", return_value=mock_client):

        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)

    assert result == {}


@pytest.mark.asyncio
async def test_extract_missing_pdf():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        from extractor import extract_from_pdf
        result = await extract_from_pdf("/nonexistent/path.pdf")
    assert result == {}


@pytest.mark.asyncio
async def test_extract_no_api_key(tmp_pdf):
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)
    assert result == {}


@pytest.mark.asyncio
async def test_extract_strips_markdown_fences(tmp_pdf):
    response_json = '```json\n{"summary_of_contract": "Test", "requirements": "", "mandatory_criteria": "", "submission_method": "", "is_multi_inquiry": false}\n```'

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_message(response_json)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("anthropic.Anthropic", return_value=mock_client):

        from extractor import extract_from_pdf
        result = await extract_from_pdf(tmp_pdf)

    assert result["summary_of_contract"] == "Test"

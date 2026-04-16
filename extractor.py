"""
LLM-powered solicitation document extraction.
Sends downloaded PDFs to Claude API and extracts structured fields:
Summary of Contract, Requirements, Mandatory Criteria, Submission Method.
"""
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Analyze this solicitation document and extract the following fields.
Return ONLY valid JSON — no markdown, no explanation.

{
  "summary_of_contract": "2-3 sentence overview of what is being procured",
  "requirements": "description of requirements" OR [{"item": 1, "gsin": "...", "nsn": "...", "description": "...", "part_no": "...", "ncage": "...", "quantity": 10, "unit_of_issue": "...", "destination": "...", "packaging": "..."}],
  "mandatory_criteria": "list all mandatory criteria, separated by newlines",
  "submission_method": "one of: E-post, FAX, E-mail, SAP",
  "is_multi_inquiry": true if requirements is a table with multiple items, false if single item
}

Rules:
- If there is a "Requirement & Price" table or similar multi-item table, set is_multi_inquiry to true and return requirements as an array of objects
- If there is a single item/service being procured, set is_multi_inquiry to false and return requirements as a string
- For submission_method, look for how bids should be submitted (electronic posting system, fax, email, or SAP)
- If you cannot determine a field, use an empty string
- Return ONLY the JSON object, nothing else
"""


async def extract_from_pdf(pdf_path: str) -> dict[str, Any]:
    """Send a PDF to Claude API and extract structured solicitation fields.

    Returns dict with keys: summary_of_contract, requirements,
    mandatory_criteria, submission_method, is_multi_inquiry.
    Returns empty dict on failure.
    """
    path = Path(pdf_path)
    if not path.exists():
        log.warning("PDF not found: %s", pdf_path)
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping LLM extraction")
        return {}

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        pdf_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

        message = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": EXTRACTION_PROMPT,
                        },
                    ],
                }
            ],
        )

        response_text = message.content[0].text.strip()
        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            if response_text.endswith("```"):
                response_text = response_text[:-3].strip()

        result = json.loads(response_text)
        log.info("LLM extraction successful for %s", path.name)
        return result

    except json.JSONDecodeError as exc:
        log.warning("LLM returned invalid JSON for %s: %s", pdf_path, exc)
        return {}
    except Exception as exc:
        log.warning("LLM extraction failed for %s: %s", pdf_path, exc)
        return {}

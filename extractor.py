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


def _extract_docx_text(docx_path: str) -> str:
    """Extract plain text from a DOCX file (it's a ZIP of XML)."""
    import re
    import zipfile
    try:
        with zipfile.ZipFile(docx_path) as zf:
            if "word/document.xml" not in zf.namelist():
                return ""
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            # Strip XML tags to get plain text
            text = re.sub(r"<[^>]+>", " ", xml)
            text = re.sub(r"\s+", " ", text).strip()
            return text
    except Exception as exc:
        log.warning("DOCX text extraction failed for %s: %s", docx_path, exc)
        return ""


async def extract_from_pdf(pdf_path: str) -> dict[str, Any]:
    """Send a PDF or DOCX to Claude API and extract structured solicitation fields.

    Returns dict with keys: summary_of_contract, requirements,
    mandatory_criteria, submission_method, is_multi_inquiry.
    Returns empty dict on failure.
    """
    path = Path(pdf_path)
    if not path.exists():
        log.warning("File not found: %s", pdf_path)
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping LLM extraction")
        return {}

    is_docx = path.suffix.lower() in (".docx", ".doc")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        if is_docx:
            # DOCX: extract text and send as text content
            docx_text = _extract_docx_text(pdf_path)
            if not docx_text:
                log.warning("Could not extract text from DOCX: %s", pdf_path)
                return {}
            # Truncate to ~50K chars to stay within token limits
            if len(docx_text) > 50000:
                docx_text = docx_text[:50000] + "\n\n[TRUNCATED]"
            content = [
                {"type": "text", "text": f"Here is the text content of a solicitation document ({path.name}):\n\n{docx_text}\n\n{EXTRACTION_PROMPT}"},
            ]
        else:
            # PDF: send as document
            pdf_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
            content = [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_data,
                    },
                },
                {"type": "text", "text": EXTRACTION_PROMPT},
            ]

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
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
        # Try to salvage partial JSON — extract what we can
        try:
            result = _salvage_partial_json(response_text)
            if result:
                log.info("Salvaged partial extraction for %s", path.name)
                return result
        except Exception:
            pass
        return {}
    except Exception as exc:
        log.warning("LLM extraction failed for %s: %s", pdf_path, exc)
        return {}


def _salvage_partial_json(text: str) -> dict:
    """Try to extract fields from truncated JSON response."""
    result = {}
    # Try to find each field individually
    for field in ["summary_of_contract", "mandatory_criteria", "submission_method", "is_multi_inquiry"]:
        try:
            import re
            pattern = rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"'
            match = re.search(pattern, text)
            if match:
                result[field] = match.group(1).replace("\\n", "\n").replace('\\"', '"')
        except Exception:
            pass
    # Check for is_multi_inquiry as boolean
    if "is_multi_inquiry" not in result:
        if '"is_multi_inquiry": true' in text.lower():
            result["is_multi_inquiry"] = True
        elif '"is_multi_inquiry": false' in text.lower():
            result["is_multi_inquiry"] = False
    # Try to get requirements as string (may be truncated)
    try:
        import re
        req_match = re.search(r'"requirements"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if req_match:
            result["requirements"] = req_match.group(1).replace("\\n", "\n").replace('\\"', '"')
    except Exception:
        pass
    return result if result else {}

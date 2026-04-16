"""
Multi-inquiry vs regular inquiry classification + CSV export.
Takes LLM extraction output and determines:
- File Type: "Regular" or "Multiple"
- For Multiple: generates a CSV file with the requirements table
- For Regular: returns requirements as text for the Requirements field
"""
import csv
import io
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# CSV columns matching the "Requirement & Price" table format
CSV_COLUMNS = [
    "Item",
    "GSIN/NIBS",
    "NSN/NNO",
    "Description",
    "Part No",
    "NCAGE/Code",
    "Quantity",
    "Unit of Issue",
    "Destination",
    "Packaging",
    "Firm Unit Price",
]


def classify(extraction: dict[str, Any]) -> dict[str, Any]:
    """Classify extraction results and produce CFlow-ready output.

    Returns dict with:
      - file_type: "Regular" or "Multiple"
      - requirements_text: str (for Regular — goes into CFlow Requirements field)
      - requirements_csv: str (for Multiple — CSV content as string)
      - csv_path: str (if CSV was written to disk, empty otherwise)
    """
    if not extraction:
        return {
            "file_type": "",
            "requirements_text": "",
            "requirements_csv": "",
            "csv_path": "",
        }

    is_multi = extraction.get("is_multi_inquiry", False)
    requirements = extraction.get("requirements", "")

    if is_multi and isinstance(requirements, list) and len(requirements) > 1:
        csv_content = _build_csv(requirements)
        return {
            "file_type": "Multiple",
            "requirements_text": "",
            "requirements_csv": csv_content,
            "csv_path": "",
        }
    else:
        # Single item or string requirements
        text = requirements if isinstance(requirements, str) else _flatten_requirements(requirements)
        return {
            "file_type": "Regular",
            "requirements_text": text,
            "requirements_csv": "",
            "csv_path": "",
        }


def classify_and_save_csv(extraction: dict[str, Any], output_dir: str, sol_no: str = "") -> dict[str, Any]:
    """Same as classify(), but writes CSV to disk for Multiple inquiries.

    Returns the same dict with csv_path populated if a CSV was written.
    """
    result = classify(extraction)

    if result["file_type"] == "Multiple" and result["requirements_csv"]:
        # Sanitize sol_no to prevent path traversal
        safe_sol_no = "".join(c for c in (sol_no or "requirements") if c.isalnum() or c in "-_")
        filename = f"{safe_sol_no}_requirements.csv"
        csv_path = os.path.join(output_dir, filename)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(result["requirements_csv"])
        result["csv_path"] = csv_path
        log.info("Requirements CSV written: %s (%d bytes)", filename, len(result["requirements_csv"]))

    return result


def _build_csv(requirements: list[dict]) -> str:
    """Convert a list of requirement dicts to CSV string."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for i, req in enumerate(requirements):
        row = {
            "Item": req.get("item", i + 1),
            "GSIN/NIBS": req.get("gsin", ""),
            "NSN/NNO": req.get("nsn", ""),
            "Description": req.get("description", ""),
            "Part No": req.get("part_no", ""),
            "NCAGE/Code": req.get("ncage", ""),
            "Quantity": req.get("quantity", ""),
            "Unit of Issue": req.get("unit_of_issue", ""),
            "Destination": req.get("destination", ""),
            "Packaging": req.get("packaging", ""),
            "Firm Unit Price": req.get("firm_unit_price", ""),
        }
        writer.writerow(row)

    return output.getvalue()


def _flatten_requirements(requirements: Any) -> str:
    """Flatten a single-item list or unexpected format to text."""
    if isinstance(requirements, list):
        if len(requirements) == 1:
            req = requirements[0]
            if isinstance(req, dict):
                return req.get("description", str(req))
            return str(req)
        return "\n".join(str(r) for r in requirements)
    return str(requirements)

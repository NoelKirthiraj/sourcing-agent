"""Unit tests for classifier.py — multi-inquiry detection + CSV export."""
import csv
import io
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from classifier import classify, classify_and_save_csv, CSV_COLUMNS


class TestClassify:
    def test_empty_extraction_returns_empty(self):
        result = classify({})
        assert result["file_type"] == ""
        assert result["requirements_text"] == ""
        assert result["requirements_csv"] == ""

    def test_single_item_returns_regular(self):
        extraction = {
            "is_multi_inquiry": False,
            "requirements": "Search and rescue crane, 5 tonne capacity",
        }
        result = classify(extraction)
        assert result["file_type"] == "Regular"
        assert "crane" in result["requirements_text"]
        assert result["requirements_csv"] == ""

    def test_multi_item_returns_multiple_with_csv(self):
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": 1, "gsin": "5330", "nsn": "00-0647777", "description": "PACKING, PREFORMED",
                 "part_no": "6750674", "ncage": "14414", "quantity": 10},
                {"item": 2, "gsin": "4730", "nsn": "00-8531182", "description": "ADAPTER, STRAIGHT",
                 "part_no": "0001272-1020", "ncage": "16662", "quantity": 10},
            ],
        }
        result = classify(extraction)
        assert result["file_type"] == "Multiple"
        assert result["requirements_text"] == ""
        assert result["requirements_csv"] != ""

    def test_csv_has_correct_headers(self):
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": 1, "description": "Part A", "quantity": 5},
                {"item": 2, "description": "Part B", "quantity": 10},
            ],
        }
        result = classify(extraction)
        reader = csv.DictReader(io.StringIO(result["requirements_csv"]))
        assert list(reader.fieldnames) == CSV_COLUMNS

    def test_csv_has_correct_row_count(self):
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": i, "description": f"Part {i}", "quantity": i * 5}
                for i in range(1, 6)
            ],
        }
        result = classify(extraction)
        reader = csv.DictReader(io.StringIO(result["requirements_csv"]))
        rows = list(reader)
        assert len(rows) == 5
        assert rows[0]["Item"] == "1"
        assert rows[4]["Description"] == "Part 5"

    def test_single_item_list_returns_regular(self):
        """A list with only 1 item should be Regular, not Multiple."""
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": 1, "description": "Single item"},
            ],
        }
        result = classify(extraction)
        assert result["file_type"] == "Regular"
        assert "Single item" in result["requirements_text"]

    def test_string_requirements_always_regular(self):
        extraction = {
            "is_multi_inquiry": True,  # flag says multi but requirements is a string
            "requirements": "This is just text",
        }
        result = classify(extraction)
        assert result["file_type"] == "Regular"
        assert result["requirements_text"] == "This is just text"


class TestClassifyAndSaveCsv:
    def test_writes_csv_file_for_multiple(self, tmp_path):
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": 1, "description": "Part A", "quantity": 5},
                {"item": 2, "description": "Part B", "quantity": 10},
            ],
        }
        result = classify_and_save_csv(extraction, str(tmp_path), sol_no="WS123")
        assert result["csv_path"] != ""
        assert Path(result["csv_path"]).exists()
        assert "WS123" in result["csv_path"]

        # Verify file content
        content = Path(result["csv_path"]).read_text()
        assert "Part A" in content
        assert "Part B" in content

    def test_no_csv_for_regular(self, tmp_path):
        extraction = {
            "is_multi_inquiry": False,
            "requirements": "Single item requirement",
        }
        result = classify_and_save_csv(extraction, str(tmp_path), sol_no="WS456")
        assert result["csv_path"] == ""
        assert result["file_type"] == "Regular"

    def test_default_filename_when_no_sol_no(self, tmp_path):
        extraction = {
            "is_multi_inquiry": True,
            "requirements": [
                {"item": 1, "description": "A"},
                {"item": 2, "description": "B"},
            ],
        }
        result = classify_and_save_csv(extraction, str(tmp_path))
        assert "requirements_requirements.csv" in result["csv_path"]

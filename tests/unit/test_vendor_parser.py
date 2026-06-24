"""Unit tests for vendor_parser.

These cover the pure functions of the xlsx parser using a small golden
fixture (tests/fixtures/vendors_mini.xlsx) plus a handful of in-memory
workbooks for the malformed-input cases.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

import vendor_parser as VP

FIXTURE = Path(__file__).parent.parent / "fixtures" / "vendors_mini.xlsx"


def _xlsx_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ── Golden fixture happy path ────────────────────────────────────────────────

def test_parse_workbook_returns_expected_counts():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    assert len(result.vendors) == 3
    assert len(result.categories) == 2
    assert len(result.products) == 4


def test_parse_vendors_splits_semicolons():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    acme = next(v for v in result.vendors if v["company"] == "Acme Aero")
    assert acme["primary_contacts"] == ["Jane Doe", "John Roe"]
    assert acme["emails"] == ["jane@acme.com", "sales@acme.com"]
    assert acme["rfp_categories"] == ["Aerospace/Aircraft", "Vehicle/Truck"]


def test_parse_vendors_preserves_multiline_bid_history():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    acme = next(v for v in result.vendors if v["company"] == "Acme Aero")
    assert "Bid A" in acme["bid_history"]
    assert "Bid B" in acme["bid_history"]
    assert "\n" in acme["bid_history"]  # multi-line preserved


def test_parse_vendors_coerces_int_columns():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    acme = next(v for v in result.vendors if v["company"] == "Acme Aero")
    assert acme["inquiry_count"] == 7
    assert acme["bid_count"] == 3


def test_parse_vendors_parses_last_contact_as_date():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    acme = next(v for v in result.vendors if v["company"] == "Acme Aero")
    assert acme["last_contact"] == date(2025, 8, 28)


def test_parse_categories_returns_keywords_list():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    aero = next(c for c in result.categories if c["category"] == "Aerospace/Aircraft")
    assert aero["keywords"] == ["aircraft", "aerospace", "jet"]
    assert aero["vendors_tagged_count"] == 12
    assert aero["needs_enrichment_count"] == 3


def test_parse_products_associates_source_tab():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    sources = {p["source_tab"] for p in result.products if p["vendor_company"] == "Acme Aero"}
    assert sources == {"Vendors", "Products by Vendor"}


# ── Bad-data warnings ────────────────────────────────────────────────────────

def test_parse_vendors_bad_int_becomes_zero_with_warning():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    gamma = next(v for v in result.vendors if v["company"] == "Gamma Logistics")
    assert gamma["inquiry_count"] == 0
    assert any("Inquiry Count not numeric" in w for w in result.warnings)


def test_parse_vendors_bad_date_becomes_null_with_warning():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    gamma = next(v for v in result.vendors if v["company"] == "Gamma Logistics")
    assert gamma["last_contact"] is None
    assert any("Last Contact unparseable" in w for w in result.warnings)


def test_orphan_product_surfaces_as_warning():
    result = VP.parse_workbook(_xlsx_bytes(FIXTURE))
    # 'Unknown LLC' is in Products by Vendor but not in Vendors
    assert any("unknown llc" in w.lower() for w in result.warnings)
    # The orphan is still kept in the products list (DB layer inserts with vendor_id NULL)
    assert any(p["vendor_company"] == "Unknown LLC" for p in result.products)


# ── Empty / malformed file handling ──────────────────────────────────────────

def test_parse_workbook_rejects_empty_bytes():
    with pytest.raises(VP.VendorParseError):
        VP.parse_workbook(b"")


def test_parse_workbook_rejects_non_zip_bytes():
    with pytest.raises(VP.VendorParseError):
        VP.parse_workbook(b"this is not a workbook")


# ── Header detection + reorder tolerance ─────────────────────────────────────

def _make_wb(sheets: dict) -> bytes:
    """Build an xlsx from {sheet_name: list-of-rows} and return raw bytes."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_reordered_columns_are_tolerated():
    rows = [
        # Header reordered: Domain before Company
        ["Domain", "Company", "Email(s)"],
        ["xyz.com", "Xyz Corp", "ops@xyz.com"],
    ]
    data = _make_wb({"Vendors": rows, "RFP Categories": [["Category", "Keywords (matched)"]],
                     "Products by Vendor": [["Vendor", "Product"]]})
    result = VP.parse_workbook(data)
    assert any(v["company"] == "Xyz Corp" for v in result.vendors)
    assert result.vendors[0]["emails"] == ["ops@xyz.com"]


def test_case_insensitive_header_match():
    rows = [
        ["company", "DOMAIN", "EMAIL(S)"],
        ["LowerCo", "lower.io", "a@lower.io"],
    ]
    data = _make_wb({"Vendors": rows, "RFP Categories": [["Category", "Keywords"]],
                     "Products by Vendor": [["Vendor", "Product"]]})
    result = VP.parse_workbook(data)
    assert any(v["company"] == "LowerCo" for v in result.vendors)


# ── Required-column enforcement ──────────────────────────────────────────────

def test_missing_company_column_skips_sheet_with_warning():
    rows = [
        # No Company column at all
        ["Domain", "Email(s)"],
        ["a.com", "x@a.com"],
    ]
    data = _make_wb({"Vendors": rows, "RFP Categories": [["Category", "Keywords"]],
                     "Products by Vendor": [["Vendor", "Product"]]})
    result = VP.parse_workbook(data)
    assert result.vendors == []
    assert any("company" in w.lower() and "missing" in w.lower() for w in result.warnings)


def test_missing_company_value_skips_row_with_warning():
    rows = [
        ["Company", "Domain"],
        ["", "no-name.io"],            # blank company → skipped
        ["Real Co", "real.io"],
    ]
    data = _make_wb({"Vendors": rows, "RFP Categories": [["Category", "Keywords"]],
                     "Products by Vendor": [["Vendor", "Product"]]})
    result = VP.parse_workbook(data)
    assert [v["company"] for v in result.vendors] == ["Real Co"]
    assert any("missing Company" in w for w in result.warnings)


def test_missing_required_product_col_skips_sheet():
    # Products sheet missing Product column
    data = _make_wb({
        "Vendors": [["Company"], ["Acme"]],
        "RFP Categories": [["Category", "Keywords"]],
        "Products by Vendor": [["Vendor", "Domain / Sender Key"], ["Acme", "acme.com"]],
    })
    result = VP.parse_workbook(data)
    assert result.products == []
    assert any("Products by Vendor" in w and "missing" in w for w in result.warnings)


# ── Missing-sheet handling ───────────────────────────────────────────────────

def test_missing_vendors_sheet_warns_but_does_not_error():
    data = _make_wb({
        "RFP Categories": [["Category", "Keywords"], ["X", "y"]],
        "Products by Vendor": [["Vendor", "Product"]],
    })
    result = VP.parse_workbook(data)
    assert result.vendors == []
    assert any("Vendors" in w for w in result.warnings)


# ── Semicolon splitting edge cases ───────────────────────────────────────────

def test_semicolon_list_trims_and_drops_empty_items():
    assert VP._split_semicolon_list("a; b; ; c;") == ["a", "b", "c"]
    assert VP._split_semicolon_list(None) == []
    assert VP._split_semicolon_list("") == []
    assert VP._split_semicolon_list("only-one") == ["only-one"]


# ── Date parsing accepts multiple formats ────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2025-08-28", date(2025, 8, 28)),
    ("8/28/2025", date(2025, 8, 28)),
    ("12/19/2025", date(2025, 12, 19)),
    ("2025/08/28", date(2025, 8, 28)),
    ("08-28-2025", date(2025, 8, 28)),
])
def test_to_date_accepts_common_formats(raw, expected):
    parsed, ok = VP._to_date(raw)
    assert ok is True
    assert parsed == expected


def test_to_date_returns_null_ok_for_empty_input():
    parsed, ok = VP._to_date("")
    assert parsed is None
    assert ok is True
    parsed, ok = VP._to_date(None)
    assert parsed is None
    assert ok is True


def test_to_date_returns_not_ok_for_garbage():
    parsed, ok = VP._to_date("Tue 5")
    assert parsed is None
    assert ok is False

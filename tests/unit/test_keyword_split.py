"""Regression tests for the comma-joined category keywords bug (2026-10-05).

The source workbook comma-delimits the RFP Categories keyword column while
every other list column uses semicolons. Running keywords through the
semicolon splitter stored all 18 of a category's keywords as ONE array
element, so supplier matching searched for

    "aircraft, aerodrome, aeronautic, aerospace, propeller, avionic, ..."

verbatim and no tender ever matched a category. Two of four live test tenders
went from 0 suppliers to 13 and 30 once split.

The second half of these tests matters as much as the first: comma-splitting
must NOT reach the other list columns, where commas are real data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from supplier_matcher import match_categories
from vendor_parser import _split_keyword_list, _split_semicolon_list

# Verbatim from the production row that exposed the bug.
REAL_CELL = ("aircraft, aerodrome, aeronautic, aerospace, propeller, avionic, "
             "aviation, F-XX, CF-XX, Tronair, jet, helicopter, airframe, "
             "fuselage, hangar, de-ice(r), landing gear, towbar")


# ── The splitter ────────────────────────────────────────────────────────────

def test_real_production_cell_splits_into_individual_keywords():
    out = _split_keyword_list(REAL_CELL)
    assert len(out) == 18
    assert out[0] == "aircraft"
    assert out[-1] == "towbar"
    assert "landing gear" in out          # multi-word keywords stay intact


def test_semicolons_still_split():
    """The test fixture uses semicolons; both delimiters must work."""
    assert _split_keyword_list("aircraft; aerospace; jet") == ["aircraft", "aerospace", "jet"]


def test_mixed_delimiters_split():
    assert _split_keyword_list("a, b; c") == ["a", "b", "c"]


def test_whitespace_is_trimmed():
    assert _split_keyword_list("  aircraft ,  jet  ") == ["aircraft", "jet"]


def test_empty_segments_are_dropped():
    assert _split_keyword_list("a,,b,;,c") == ["a", "b", "c"]


def test_duplicates_are_dropped_preserving_order():
    assert _split_keyword_list("jet, aircraft, jet") == ["jet", "aircraft"]


@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_cells_yield_nothing(value):
    assert _split_keyword_list(value) == []


def test_single_keyword_without_delimiters():
    assert _split_keyword_list("filtration") == ["filtration"]


# ── The other columns must keep commas ──────────────────────────────────────

def test_product_names_containing_commas_are_not_split():
    """'HOSE ASSEMBLY, AIR DUCT, AIR BREATHING' is one product, not three."""
    out = _split_semicolon_list("HOSE ASSEMBLY, AIR DUCT, AIR BREATHING; Towbar")
    assert out == ["HOSE ASSEMBLY, AIR DUCT, AIR BREATHING", "Towbar"]


def test_contact_names_in_last_first_form_are_not_split():
    assert _split_semicolon_list("Doe, Jane; Roe, John") == ["Doe, Jane", "Roe, John"]


def test_semicolon_splitter_is_unchanged_for_emails():
    assert _split_semicolon_list("a@x.com; b@x.com") == ["a@x.com", "b@x.com"]


# ── End to end: the bug, and the fix ────────────────────────────────────────

def test_unsplit_keywords_match_nothing():
    """Reproduces the production failure."""
    broken = [{"category": "Aerospace/Aircraft", "keywords": [REAL_CELL]}]
    assert match_categories("supply of aircraft towbars", broken) == {}


def test_split_keywords_match_the_tender():
    fixed = [{"category": "Aerospace/Aircraft", "keywords": _split_keyword_list(REAL_CELL)}]
    hits = match_categories("supply of aircraft towbars", fixed)
    assert "Aerospace/Aircraft" in hits
    assert "aircraft" in hits["Aerospace/Aircraft"]
    assert "towbar" in hits["Aerospace/Aircraft"]


def test_split_keywords_match_a_real_tender_title():
    """Tender 591 returned 0 suppliers before the fix."""
    fixed = [{"category": "Vehicle/Truck",
              "keywords": _split_keyword_list("truck, vehicle, trailer, chassis, dump truck")}]
    hits = match_categories("Notice of Proposed Procurement - Dump Truck", fixed)
    assert "Vehicle/Truck" in hits


# ── The migration logic ─────────────────────────────────────────────────────

def _migrate(keywords: list[str]) -> list[str] | None:
    """Mirror of db._migrate_split_category_keywords' per-row decision.
    Returns the new list, or None when the row is left untouched."""
    if not any(("," in k or ";" in k) for k in keywords):
        return None
    out: list[str] = []
    for item in keywords:
        for part in re.split(r"[,;]", item):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def test_migration_splits_a_joined_row():
    assert _migrate([REAL_CELL]) == _split_keyword_list(REAL_CELL)


def test_migration_skips_already_split_rows():
    """Idempotent — a second deploy must be a no-op."""
    assert _migrate(["aircraft", "jet", "towbar"]) is None


def test_migration_is_stable_on_rerun():
    once = _migrate([REAL_CELL])
    assert _migrate(once) is None


def test_migration_leaves_empty_rows_alone():
    assert _migrate([]) is None


def test_migration_handles_partially_split_rows():
    """Some rows could be split and others joined in the same table."""
    assert _migrate(["aircraft", "jet, towbar"]) == ["aircraft", "jet", "towbar"]

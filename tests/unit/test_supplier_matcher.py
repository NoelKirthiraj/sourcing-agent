"""Unit tests for supplier_matcher — SQL/keyword matching and scoring.

v1 has no AI ranking, so these scoring rules *are* the matching quality. They
are pinned deliberately: a change to a weight or a boundary rule changes which
suppliers an associate sees, which is a business-visible outcome.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from supplier_matcher import (
    BID_HISTORY_BONUS,
    CATEGORY_MATCH_POINTS,
    PRODUCT_MATCH_POINTS,
    Suggestion,
    match_categories,
    rank_vendors,
    tender_text,
)

CATEGORIES = [
    {"category": "Aerospace/Aircraft", "keywords": ["aircraft", "aerospace", "jet"]},
    {"category": "Marine/Naval", "keywords": ["navy", "submarine", "ship"]},
    {"category": "Vehicle/Truck", "keywords": ["truck", "vehicle"]},
    {"category": "Uncategorised", "keywords": []},
]

ACME = {"id": 1, "company": "Acme Aero", "rfp_categories": ["Aerospace/Aircraft"],
        "emails": ["sales@acme.com"], "bid_count": 3}
BETA = {"id": 2, "company": "Beta Marine", "rfp_categories": ["Marine/Naval"],
        "emails": ["alex@beta-marine.ca"], "bid_count": 0}
GAMMA = {"id": 3, "company": "Gamma Logistics", "rfp_categories": ["Vehicle/Truck"],
         "emails": [], "bid_count": 0}


def tender(**kw) -> dict:
    base = {"solicitation_title": "", "gsin_description": "",
            "summary_of_contract": "", "requirements": ""}
    base.update(kw)
    return base


# ── Keyword matching ────────────────────────────────────────────────────────

def test_keyword_matches_category():
    hits = match_categories("supply of aircraft towbars", CATEGORIES)
    assert hits == {"Aerospace/Aircraft": ["aircraft"]}


def test_keyword_match_is_case_insensitive():
    assert "Marine/Naval" in match_categories("SUBMARINE spares", CATEGORIES)


def test_plural_form_still_matches():
    """'aircraft' should match 'aircrafts' — suppliers and solicitations
    disagree about plurals constantly."""
    assert "Aerospace/Aircraft" in match_categories("two aircrafts required", CATEGORIES)


def test_substring_does_not_match_across_word_boundary():
    """'ship' must not match 'shipping' — that would tag every tender with a
    delivery clause as naval work."""
    assert match_categories("shipping and handling included", CATEGORIES) == {}


def test_multiple_categories_can_match():
    hits = match_categories("aircraft parts delivered by truck", CATEGORIES)
    assert set(hits) == {"Aerospace/Aircraft", "Vehicle/Truck"}


def test_category_without_keywords_never_matches():
    hits = match_categories("anything at all", CATEGORIES)
    assert "Uncategorised" not in hits


def test_all_matching_keywords_are_returned():
    """The rationale names the words that hit, so all of them are collected."""
    hits = match_categories("aerospace and aircraft components", CATEGORIES)
    assert sorted(hits["Aerospace/Aircraft"]) == ["aerospace", "aircraft"]


# ── Which fields are searched ───────────────────────────────────────────────

def test_text_includes_extracted_summary_and_requirements():
    """Title alone often omits the part names; the extracted fields carry them."""
    text = tender_text(tender(
        solicitation_title="Procurement notice",
        summary_of_contract="Supply of submarine spares",
        requirements="Item 1: hull valve",
    ))
    assert "submarine" in text and "hull valve" in text


def test_matching_works_off_requirements_alone():
    sugg = rank_vendors(tender(requirements="Item 1: jet engine mount"),
                        CATEGORIES, [ACME])
    assert [s.company for s in sugg] == ["Acme Aero"]


# ── Scoring ─────────────────────────────────────────────────────────────────

def test_category_match_scores_one_point_plus_bid_bonus():
    sugg = rank_vendors(tender(solicitation_title="aircraft parts"), CATEGORIES, [ACME])
    assert sugg[0].score == CATEGORY_MATCH_POINTS + BID_HISTORY_BONUS


def test_product_match_outweighs_category_match():
    """A supplier who has quoted this exact item beats one merely in the sector."""
    sugg = rank_vendors(
        tender(solicitation_title="aircraft towbar required"),
        CATEGORIES,
        [ACME, BETA],
        {1: ["Aircraft Towbar"]},
    )
    assert sugg[0].company == "Acme Aero"
    assert sugg[0].score == PRODUCT_MATCH_POINTS + CATEGORY_MATCH_POINTS + BID_HISTORY_BONUS


def test_bid_history_bonus_applies_once_not_per_bid():
    """Three prior bids is +1, not +3 — otherwise one busy supplier dominates."""
    heavy = {**ACME, "bid_count": 40}
    sugg = rank_vendors(tender(solicitation_title="aircraft"), CATEGORIES, [heavy])
    assert sugg[0].score == CATEGORY_MATCH_POINTS + BID_HISTORY_BONUS


def test_vendor_without_bids_gets_no_bonus():
    sugg = rank_vendors(tender(solicitation_title="submarine"), CATEGORIES, [BETA])
    assert sugg[0].score == CATEGORY_MATCH_POINTS


def test_non_matching_vendors_are_dropped_not_zero_scored():
    """A shortlist padded with non-matches is worse than a short one."""
    sugg = rank_vendors(tender(solicitation_title="aircraft"), CATEGORIES,
                        [ACME, BETA, GAMMA])
    assert [s.company for s in sugg] == ["Acme Aero"]


def test_ties_break_on_company_name_for_stable_ordering():
    a = {"id": 10, "company": "Zeta Corp", "rfp_categories": ["Marine/Naval"],
         "emails": [], "bid_count": 0}
    b = {"id": 11, "company": "Alpha Corp", "rfp_categories": ["Marine/Naval"],
         "emails": [], "bid_count": 0}
    sugg = rank_vendors(tender(solicitation_title="navy"), CATEGORIES, [a, b])
    assert [s.company for s in sugg] == ["Alpha Corp", "Zeta Corp"]


def test_short_product_names_are_ignored():
    """'Kit' would match almost any tender text."""
    sugg = rank_vendors(tender(solicitation_title="repair kit for pumps"),
                        CATEGORIES, [GAMMA], {3: ["Kit"]})
    assert sugg == []


def test_limit_truncates_the_shortlist():
    vendors = [{"id": i, "company": f"V{i:02d}", "rfp_categories": ["Marine/Naval"],
                "emails": [], "bid_count": 0} for i in range(10)]
    assert len(rank_vendors(tender(solicitation_title="navy"), CATEGORIES,
                            vendors, limit=5)) == 5


def test_empty_tender_text_yields_nothing():
    assert rank_vendors(tender(), CATEGORIES, [ACME, BETA]) == []


def test_vendor_without_id_is_skipped():
    assert rank_vendors(tender(solicitation_title="aircraft"), CATEGORIES,
                        [{"company": "No ID", "rfp_categories": ["Aerospace/Aircraft"]}]) == []


# ── Rationale: evidence, not assertion ──────────────────────────────────────

def test_rationale_names_the_matching_keyword():
    sugg = rank_vendors(tender(solicitation_title="aircraft parts"), CATEGORIES, [ACME])
    text = sugg[0].rationale()
    assert "Aerospace/Aircraft" in text and "aircraft" in text


def test_rationale_lists_matched_products_and_bids():
    sugg = rank_vendors(tender(solicitation_title="aircraft towbar"), CATEGORIES,
                        [ACME], {1: ["Aircraft Towbar"]})
    text = sugg[0].rationale()
    assert "Aircraft Towbar" in text
    assert "3 prior bids" in text


def test_rationale_uses_singular_for_one_bid():
    one = {**ACME, "bid_count": 1}
    sugg = rank_vendors(tender(solicitation_title="aircraft"), CATEGORIES, [one])
    assert "1 prior bid." in sugg[0].rationale()


def test_rationale_caps_the_product_list():
    products = [f"Aircraft Part {i}" for i in range(6)]
    sugg = rank_vendors(tender(solicitation_title=" ".join(products)), CATEGORIES,
                        [ACME], {1: products})
    assert "+3 more" in sugg[0].rationale()


def test_rationale_is_never_empty():
    assert Suggestion(vendor_id=1, company="X", score=0).rationale()


# ── Contactability ──────────────────────────────────────────────────────────

def test_vendor_without_email_is_matched_but_flagged():
    """Still worth showing — the associate may have the address elsewhere."""
    gamma_bid = {**GAMMA, "bid_count": 2}
    sugg = rank_vendors(tender(solicitation_title="truck parts"), CATEGORIES, [gamma_bid])
    assert sugg[0].company == "Gamma Logistics"
    assert sugg[0].contactable is False


def test_blank_email_entries_do_not_count_as_contactable():
    vendor = {**BETA, "emails": ["", None]}
    sugg = rank_vendors(tender(solicitation_title="navy"), CATEGORIES, [vendor])
    assert sugg[0].contactable is False


def test_to_dict_carries_everything_the_ui_needs():
    sugg = rank_vendors(tender(solicitation_title="aircraft towbar"), CATEGORIES,
                        [ACME], {1: ["Aircraft Towbar"]})[0].to_dict()
    for key in ("vendor_id", "company", "score", "rationale", "contactable", "origin"):
        assert key in sugg
    assert sugg["origin"] == "internal"

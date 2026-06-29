"""Unit tests for the SAP login halt-on-repeated-failure guardrail.

The helpers live in dashboard_data and are file-based (no DB) — we point
them at a tmp_path per test to keep everything isolated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import dashboard_data as DD


# ── get_sap_halt_state ──────────────────────────────────────────────────────

def test_get_state_returns_defaults_when_profile_missing(tmp_path):
    state = DD.get_sap_halt_state(tmp_path)
    assert state == DD.SAP_HALT_DEFAULTS


def test_get_state_returns_defaults_when_profile_lacks_fields(tmp_path):
    (tmp_path / "agent_profile.json").write_text(json.dumps({"xp": 42}))
    state = DD.get_sap_halt_state(tmp_path)
    assert state["sap_login_halted"] is False
    assert state["sap_consecutive_failures"] == 0


def test_get_state_reads_existing_fields(tmp_path):
    (tmp_path / "agent_profile.json").write_text(json.dumps({
        "xp": 42,
        "sap_consecutive_failures": 1,
        "sap_login_halted": False,
        "sap_last_error": "transient",
    }))
    state = DD.get_sap_halt_state(tmp_path)
    assert state["sap_consecutive_failures"] == 1
    assert state["sap_last_error"] == "transient"


def test_get_state_tolerates_malformed_json(tmp_path):
    (tmp_path / "agent_profile.json").write_text("not-json{{{")
    state = DD.get_sap_halt_state(tmp_path)
    assert state == DD.SAP_HALT_DEFAULTS


# ── record_sap_login_failure: increment counter, threshold trigger ──────────

def test_first_failure_increments_but_does_not_halt(tmp_path):
    state = DD.record_sap_login_failure("login completed but event page not found", tmp_path)
    assert state["sap_consecutive_failures"] == 1
    assert state["sap_login_halted"] is False
    assert state["sap_halted_at"] is None
    assert "event page" in state["sap_last_error"]


def test_second_failure_triggers_halt_at_default_threshold(tmp_path):
    DD.record_sap_login_failure("fail-1", tmp_path)
    state = DD.record_sap_login_failure("fail-2", tmp_path)
    assert state["sap_consecutive_failures"] == 2
    assert state["sap_login_halted"] is True
    assert state["sap_halted_at"] is not None
    assert state["sap_halted_attempts"] == 2
    assert state["sap_last_error"] == "fail-2"


def test_custom_threshold(tmp_path):
    # 3-failure threshold: first two should not halt
    DD.record_sap_login_failure("a", tmp_path, threshold=3)
    state = DD.record_sap_login_failure("b", tmp_path, threshold=3)
    assert state["sap_login_halted"] is False
    state = DD.record_sap_login_failure("c", tmp_path, threshold=3)
    assert state["sap_login_halted"] is True


def test_failures_beyond_threshold_dont_re_record_halted_at(tmp_path):
    # 2 failures → halted_at set. 3rd failure should not reset halted_at.
    DD.record_sap_login_failure("a", tmp_path)
    state_after_halt = DD.record_sap_login_failure("b", tmp_path)
    halted_at = state_after_halt["sap_halted_at"]
    state_after_third = DD.record_sap_login_failure("c", tmp_path)
    assert state_after_third["sap_halted_at"] == halted_at
    assert state_after_third["sap_consecutive_failures"] == 3
    # halted_attempts captures the COUNT that first tripped the halt
    assert state_after_third["sap_halted_attempts"] == 2


def test_error_message_is_capped(tmp_path):
    huge = "x" * 5000
    state = DD.record_sap_login_failure(huge, tmp_path)
    assert len(state["sap_last_error"]) == 300


# ── record_sap_login_success: resets everything ─────────────────────────────

def test_success_after_failures_clears_state(tmp_path):
    DD.record_sap_login_failure("a", tmp_path)
    DD.record_sap_login_failure("b", tmp_path)
    DD.record_sap_login_success(tmp_path)
    state = DD.get_sap_halt_state(tmp_path)
    assert state["sap_login_halted"] is False
    assert state["sap_consecutive_failures"] == 0
    assert state["sap_halted_at"] is None
    assert state["sap_last_error"] == ""


def test_success_does_not_obliterate_other_profile_fields(tmp_path):
    """Recording success must preserve unrelated agent_profile fields."""
    (tmp_path / "agent_profile.json").write_text(json.dumps({
        "xp": 999, "level": 7, "achievements": ["first_launch"],
        "sap_consecutive_failures": 1,
    }))
    DD.record_sap_login_success(tmp_path)
    profile = json.loads((tmp_path / "agent_profile.json").read_text())
    assert profile["xp"] == 999
    assert profile["level"] == 7
    assert profile["achievements"] == ["first_launch"]
    assert profile["sap_consecutive_failures"] == 0


# ── clear_sap_halt: same semantics as success (manual reset) ────────────────

def test_clear_sap_halt_resets_to_defaults(tmp_path):
    DD.record_sap_login_failure("a", tmp_path)
    DD.record_sap_login_failure("b", tmp_path)
    state_before = DD.get_sap_halt_state(tmp_path)
    assert state_before["sap_login_halted"] is True

    state_after = DD.clear_sap_halt(tmp_path)
    assert state_after["sap_login_halted"] is False
    assert state_after["sap_consecutive_failures"] == 0


def test_clear_preserves_unrelated_profile_fields(tmp_path):
    (tmp_path / "agent_profile.json").write_text(json.dumps({
        "xp": 500, "total_runs": 30,
        "sap_login_halted": True,
        "sap_consecutive_failures": 4,
        "sap_halted_at": "2026-06-29T12:00:00+00:00",
    }))
    DD.clear_sap_halt(tmp_path)
    profile = json.loads((tmp_path / "agent_profile.json").read_text())
    assert profile["xp"] == 500
    assert profile["total_runs"] == 30
    assert profile["sap_login_halted"] is False


# ── recompute_profile carries forward SAP halt fields ───────────────────────

def test_recompute_profile_preserves_sap_halt_fields():
    existing = {
        "achievements": [],
        "sap_login_halted": True,
        "sap_consecutive_failures": 2,
        "sap_halted_at": "2026-06-29T18:30:00+00:00",
        "sap_halted_attempts": 2,
        "sap_last_error": "login completed but event page not found",
    }
    history = [{"run_at": "2026-06-29T18:00:00Z", "new_count": 5, "error_count": 0,
                "total_found": 30, "skipped_count": 25, "duration_seconds": 100, "mode": "daily"}]
    profile = DD.recompute_profile(history, existing)
    assert profile["sap_login_halted"] is True
    assert profile["sap_consecutive_failures"] == 2
    assert profile["sap_halted_at"] == "2026-06-29T18:30:00+00:00"
    assert profile["sap_halted_attempts"] == 2
    assert profile["sap_last_error"] == "login completed but event page not found"


def test_recompute_profile_uses_defaults_when_no_prior_state():
    profile = DD.recompute_profile([], {})
    assert profile["sap_login_halted"] is False
    assert profile["sap_consecutive_failures"] == 0
    assert profile["sap_halted_at"] is None


# ── End-to-end: failure → halt → cleared → success ──────────────────────────

def test_full_lifecycle_smoke(tmp_path):
    # 0. Clean slate
    assert DD.get_sap_halt_state(tmp_path)["sap_login_halted"] is False

    # 1. First failure: counter=1, no halt
    s1 = DD.record_sap_login_failure("first transient blip", tmp_path)
    assert s1["sap_consecutive_failures"] == 1
    assert s1["sap_login_halted"] is False

    # 2. Second failure: counter=2, HALT triggered
    s2 = DD.record_sap_login_failure("login failed", tmp_path)
    assert s2["sap_login_halted"] is True

    # 3. Operator rotates password + clears halt via CLI
    s3 = DD.clear_sap_halt(tmp_path)
    assert s3["sap_login_halted"] is False
    assert s3["sap_consecutive_failures"] == 0

    # 4. Next cron run logs in successfully → idempotent reset
    DD.record_sap_login_success(tmp_path)
    s4 = DD.get_sap_halt_state(tmp_path)
    assert s4 == DD.SAP_HALT_DEFAULTS

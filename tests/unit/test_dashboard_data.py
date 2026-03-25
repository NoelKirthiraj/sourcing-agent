"""Unit tests for dashboard_data — XP, levels, streaks, achievements, history cap."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dashboard_data import (
    get_level,
    compute_streak,
    evaluate_achievements,
    recompute_profile,
    record_run,
    MAX_HISTORY,
)


@dataclass
class FakeSummary:
    total_found: int = 10
    new_count: int = 3
    skipped_count: int = 7
    error_count: int = 0
    new_tenders: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    duration_seconds: float = 120.0
    mode: str = "daily"


class TestGetLevel:
    def test_zero_xp(self):
        level, title = get_level(0)
        assert title == "Rookie"

    def test_field_agent(self):
        _, title = get_level(30)
        assert title == "Field Agent"

    def test_senior_operative(self):
        _, title = get_level(100)
        assert title == "Senior Operative"

    def test_commander(self):
        _, title = get_level(350)
        assert title == "Commander"

    def test_legend(self):
        _, title = get_level(1000)
        assert title == "Legend"

    def test_above_max(self):
        _, title = get_level(5000)
        assert title == "Legend"


class TestComputeStreak:
    def test_empty_history(self):
        current, best = compute_streak([])
        assert current == 0
        assert best == 0

    def test_all_clean(self):
        history = [{"error_count": 0}] * 5
        current, best = compute_streak(history)
        assert current == 5
        assert best == 5

    def test_broken_streak(self):
        history = [
            {"error_count": 0},
            {"error_count": 0},
            {"error_count": 1},
            {"error_count": 0},
            {"error_count": 0},
            {"error_count": 0},
        ]
        current, best = compute_streak(history)
        assert current == 3
        assert best == 3

    def test_current_streak_broken_at_end(self):
        history = [
            {"error_count": 0},
            {"error_count": 0},
            {"error_count": 0},
            {"error_count": 1},
        ]
        current, best = compute_streak(history)
        assert current == 0
        assert best == 3


class TestEvaluateAchievements:
    def test_first_launch(self):
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "first_launch" in ids

    def test_century(self):
        history = [{"new_count": 100, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "century" in ids

    def test_sharpshooter(self):
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}] * 10
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "sharpshooter" in ids

    def test_speed_demon(self):
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 90, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "speed_demon" in ids

    def test_weekly_warrior(self):
        history = [{"new_count": 35, "error_count": 0, "duration_seconds": 200, "mode": "weekly", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "weekly_warrior" in ids

    def test_thousand(self):
        history = [{"new_count": 1000, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "thousand" in ids

    def test_iron_streak(self):
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}] * 30
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "iron_streak" in ids

    def test_night_owl(self):
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T02:30:00+00:00"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "night_owl" in ids

    def test_preserves_existing_earned_at(self):
        existing = [{"id": "first_launch", "name": "First Launch", "earned_at": "2026-01-01T00:00:00Z"}]
        history = [{"new_count": 1, "error_count": 0, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, existing)
        first = next(a for a in result if a["id"] == "first_launch")
        assert first["earned_at"] == "2026-01-01T00:00:00Z"

    def test_not_earned_without_condition(self):
        history = [{"new_count": 1, "error_count": 1, "duration_seconds": 200, "mode": "daily", "run_at": "2026-03-24T12:00:00Z"}]
        result = evaluate_achievements(history, [])
        ids = [a["id"] for a in result]
        assert "sharpshooter" not in ids


class TestRecomputeProfile:
    def test_basic_profile(self):
        history = [
            {"new_count": 5, "error_count": 0, "run_at": "2026-03-24T12:00:00Z", "duration_seconds": 100, "mode": "daily"},
            {"new_count": 3, "error_count": 0, "run_at": "2026-03-25T12:00:00Z", "duration_seconds": 100, "mode": "daily"},
        ]
        profile = recompute_profile(history, {})
        assert profile["xp"] == 8
        assert profile["total_processed"] == 8
        assert profile["total_runs"] == 2
        assert profile["current_streak"] == 2
        assert profile["last_status"] == "success"

    def test_error_run_status(self):
        history = [{"new_count": 5, "error_count": 2, "run_at": "2026-03-24T12:00:00Z", "duration_seconds": 100, "mode": "daily"}]
        profile = recompute_profile(history, {})
        assert profile["last_status"] == "error"

    def test_empty_history(self):
        profile = recompute_profile([], {})
        assert profile["xp"] == 0
        assert profile["total_runs"] == 0
        assert profile["last_status"] == "sleeping"


class TestRecordRun:
    def test_creates_files(self, tmp_path):
        summary = FakeSummary()
        record_run(summary, tmp_path)
        assert (tmp_path / "run_history.json").exists()
        assert (tmp_path / "agent_profile.json").exists()

    def test_appends_to_history(self, tmp_path):
        summary = FakeSummary()
        record_run(summary, tmp_path)
        record_run(summary, tmp_path)
        history = json.loads((tmp_path / "run_history.json").read_text())
        assert len(history) == 2

    def test_profile_xp_accumulates(self, tmp_path):
        summary = FakeSummary(new_count=5)
        record_run(summary, tmp_path)
        record_run(summary, tmp_path)
        profile = json.loads((tmp_path / "agent_profile.json").read_text())
        assert profile["xp"] == 10

    def test_history_cap(self, tmp_path):
        # Pre-fill with MAX_HISTORY entries
        history = [{"new_count": 1, "error_count": 0, "run_at": f"2026-01-{i:03d}", "total_found": 1, "skipped_count": 0, "errors": [], "new_tenders": [], "duration_seconds": 100, "mode": "daily"} for i in range(MAX_HISTORY)]
        (tmp_path / "run_history.json").write_text(json.dumps(history))
        summary = FakeSummary()
        record_run(summary, tmp_path)
        result = json.loads((tmp_path / "run_history.json").read_text())
        assert len(result) == MAX_HISTORY

    def test_creates_data_dir(self, tmp_path):
        nested = tmp_path / "sub" / "dir"
        summary = FakeSummary()
        record_run(summary, nested)
        assert nested.exists()
        assert (nested / "run_history.json").exists()

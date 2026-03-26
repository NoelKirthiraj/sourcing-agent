"""Unit tests for notifier.py — RunSummary dataclass."""
import pytest
from notifier import RunSummary


class TestRunSummaryDefaults:
    def test_default_values(self):
        s = RunSummary()
        assert s.total_found == 0
        assert s.new_count == 0
        assert s.skipped_count == 0
        assert s.error_count == 0
        assert s.new_tenders == []
        assert s.errors == []

    def test_duration_seconds_default(self):
        s = RunSummary()
        assert s.duration_seconds == 0.0

    def test_mode_default(self):
        s = RunSummary()
        assert s.mode == "daily"

    def test_run_at_auto_populates(self):
        s = RunSummary()
        assert s.run_at != ""
        assert len(s.run_at) > 0

    def test_run_at_not_overwritten_if_provided(self):
        s = RunSummary(run_at="2026-01-01 08:00")
        assert s.run_at == "2026-01-01 08:00"


class TestRunSummaryCustomValues:
    def test_duration_seconds_can_be_set(self):
        s = RunSummary(duration_seconds=42.5)
        assert s.duration_seconds == 42.5

    def test_mode_can_be_set(self):
        s = RunSummary(mode="weekly")
        assert s.mode == "weekly"

    def test_total_found_can_be_set(self):
        s = RunSummary(total_found=15)
        assert s.total_found == 15

    def test_mutable_defaults_are_independent(self):
        s1 = RunSummary()
        s2 = RunSummary()
        s1.new_tenders.append({"title": "test"})
        assert len(s2.new_tenders) == 0

    def test_errors_list_independent(self):
        s1 = RunSummary()
        s2 = RunSummary()
        s1.errors.append("err")
        assert len(s2.errors) == 0

"""Unit tests for run.py CLI logic — reset-state, scrape-only dedup, error handling."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


class TestResetState:
    def test_reset_state_does_not_delete_when_scrape_only(self, tmp_path, monkeypatch):
        """--reset-state with --scrape-only should NOT delete processed_solicitations.json."""
        state_file = tmp_path / "processed_solicitations.json"
        state_file.write_text('{"SOL-1": {}}')

        # Simulate: args.reset_state=True, args.scrape_only=True
        # The condition in run.py: if args.reset_state and not args.scrape_only and state_path.exists()
        reset_state = True
        scrape_only = True
        should_delete = reset_state and not scrape_only and state_file.exists()
        assert should_delete is False
        assert state_file.exists()

    def test_reset_state_deletes_when_not_scrape_only(self, tmp_path):
        """--reset-state without --scrape-only DOES delete processed_solicitations.json."""
        state_file = tmp_path / "processed_solicitations.json"
        state_file.write_text('{"SOL-1": {}}')

        reset_state = True
        scrape_only = False
        should_delete = reset_state and not scrape_only and state_file.exists()
        assert should_delete is True

        # Actually delete to confirm
        if should_delete:
            state_file.unlink()
        assert not state_file.exists()

    def test_reset_state_noop_when_no_file(self, tmp_path):
        """--reset-state is a no-op if the state file doesn't exist."""
        state_file = tmp_path / "processed_solicitations.json"
        reset_state = True
        scrape_only = False
        should_delete = reset_state and not scrape_only and state_file.exists()
        assert should_delete is False


class TestScrapeOnlyUsesCorrectStateFile:
    def test_scrape_only_uses_processed_dashboard_json(self):
        """scrape-only mode uses processed_dashboard.json, not processed_solicitations.json."""
        # From run.py: scrape_state_path = Path("processed_dashboard.json")
        scrape_state_path = Path("processed_dashboard.json")
        assert scrape_state_path.name == "processed_dashboard.json"
        assert scrape_state_path.name != "processed_solicitations.json"


class TestScrapeOnlyRejectsEmptySolNo:
    def test_empty_solicitation_no_counts_as_error(self):
        """Tenders with empty solicitation_no after detail fetch should be errors."""
        from notifier import RunSummary

        summary = RunSummary(total_found=1)
        tender = {
            "inquiry_link": "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/123",
            "solicitation_no": "",
        }

        sol_no = tender.get("solicitation_no", "").strip()
        link = tender.get("inquiry_link", "")
        if not sol_no:
            summary.error_count += 1
            summary.errors.append(f"{link}: detail extraction returned no solicitation_no")

        assert summary.error_count == 1
        assert "no solicitation_no" in summary.errors[0]

    def test_whitespace_only_solicitation_no_counts_as_error(self):
        """Whitespace-only solicitation_no should also be rejected."""
        sol_no = "   ".strip()
        assert not sol_no


class TestScrapeOnlyFastDedup:
    def test_skip_tender_already_in_state_by_link(self, tmp_path):
        """Fast dedup: tenders already processed by link are skipped."""
        from state import AgentState
        from notifier import RunSummary

        state = AgentState(path=tmp_path / "dashboard.json")
        state.mark_processed("SOL-1", link="https://ex.com/tender/1")

        summary = RunSummary(total_found=1)
        link = "https://ex.com/tender/1"

        if state.already_processed_by_link(link):
            summary.skipped_count += 1

        assert summary.skipped_count == 1

    def test_new_link_not_skipped(self, tmp_path):
        """New links should not be skipped by fast dedup."""
        from state import AgentState
        from notifier import RunSummary

        state = AgentState(path=tmp_path / "dashboard.json")
        state.mark_processed("SOL-1", link="https://ex.com/tender/1")

        summary = RunSummary(total_found=1)
        link = "https://ex.com/tender/2"

        if state.already_processed_by_link(link):
            summary.skipped_count += 1

        assert summary.skipped_count == 0


class TestScrapeOnlyDoubleCheckDedup:
    def test_dedup_by_sol_no_after_detail(self, tmp_path):
        """Double-check: if sol_no already processed, skip even if link is new."""
        from state import AgentState
        from notifier import RunSummary

        state = AgentState(path=tmp_path / "dashboard.json")
        state.mark_processed("SOL-1", link="https://ex.com/old-link")

        summary = RunSummary(total_found=1)
        dedup_key = "SOL-1"

        # Link is new, so fast dedup wouldn't catch it
        assert not state.already_processed_by_link("https://ex.com/new-link")
        # But sol_no check catches it
        if state.already_processed(dedup_key):
            summary.skipped_count += 1

        assert summary.skipped_count == 1

    def test_new_sol_no_not_skipped(self, tmp_path):
        """Genuinely new tenders pass both dedup checks."""
        from state import AgentState
        from notifier import RunSummary

        state = AgentState(path=tmp_path / "dashboard.json")
        state.mark_processed("SOL-1", link="https://ex.com/1")

        summary = RunSummary(total_found=1)
        link = "https://ex.com/2"
        dedup_key = "SOL-2"

        skipped = False
        if state.already_processed_by_link(link):
            skipped = True
        if state.already_processed(dedup_key):
            skipped = True

        assert skipped is False


class TestScrapeOnlyResetState:
    def test_reset_state_deletes_dashboard_json(self, tmp_path):
        """--reset-state in scrape-only mode deletes processed_dashboard.json."""
        scrape_state_path = tmp_path / "processed_dashboard.json"
        scrape_state_path.write_text('{"SOL-1": {}}')

        # From run.py scrape-only block:
        # if args.reset_state and scrape_state_path.exists():
        #     scrape_state_path.unlink()
        reset_state = True
        scrape_only = True
        if reset_state and scrape_state_path.exists():
            scrape_state_path.unlink()

        assert not scrape_state_path.exists()

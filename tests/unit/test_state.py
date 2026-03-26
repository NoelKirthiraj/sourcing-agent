"""Unit tests for state.py — AgentState deduplication with link support."""
import json
import pytest
from pathlib import Path
from state import AgentState


class TestAlreadyProcessed:
    def test_returns_false_for_unknown_sol(self, tmp_state):
        assert tmp_state.already_processed("PW-EZZ-999") is False

    def test_returns_true_after_mark(self, tmp_state):
        tmp_state.mark_processed("PW-EZZ-001", request_id="r1", title="T1", link="https://example.com/1")
        assert tmp_state.already_processed("PW-EZZ-001") is True

    def test_returns_false_for_different_sol(self, tmp_state):
        tmp_state.mark_processed("PW-EZZ-001", request_id="r1", title="T1", link="https://example.com/1")
        assert tmp_state.already_processed("PW-EZZ-002") is False


class TestAlreadyProcessedByLink:
    def test_returns_false_for_unknown_link(self, tmp_state):
        assert tmp_state.already_processed_by_link("https://example.com/unknown") is False

    def test_returns_true_after_mark_with_link(self, tmp_state):
        tmp_state.mark_processed("SOL-1", link="https://example.com/tender/1")
        assert tmp_state.already_processed_by_link("https://example.com/tender/1") is True

    def test_returns_false_for_different_link(self, tmp_state):
        tmp_state.mark_processed("SOL-1", link="https://example.com/tender/1")
        assert tmp_state.already_processed_by_link("https://example.com/tender/2") is False

    def test_empty_link_not_tracked(self, tmp_state):
        tmp_state.mark_processed("SOL-1", link="")
        assert tmp_state.already_processed_by_link("") is False


class TestMarkProcessed:
    def test_stores_link_field(self, tmp_state):
        tmp_state.mark_processed("SOL-1", request_id="r1", title="Title", link="https://ex.com/1")
        tmp_state.save()
        data = json.loads(tmp_state._path.read_text())
        assert data["SOL-1"]["link"] == "https://ex.com/1"

    def test_stores_request_id_and_title(self, tmp_state):
        tmp_state.mark_processed("SOL-2", request_id="req-42", title="My Tender", link="")
        tmp_state.save()
        data = json.loads(tmp_state._path.read_text())
        assert data["SOL-2"]["cflow_request_id"] == "req-42"
        assert data["SOL-2"]["title"] == "My Tender"

    def test_processed_at_is_set(self, tmp_state):
        tmp_state.mark_processed("SOL-3", link="https://ex.com/3")
        tmp_state.save()
        data = json.loads(tmp_state._path.read_text())
        assert "processed_at" in data["SOL-3"]
        assert len(data["SOL-3"]["processed_at"]) > 0


class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_links(self, tmp_path):
        path = tmp_path / "state.json"
        state1 = AgentState(path=path)
        state1.mark_processed("SOL-A", request_id="r1", title="A", link="https://ex.com/a")
        state1.mark_processed("SOL-B", request_id="r2", title="B", link="https://ex.com/b")
        state1.save()

        state2 = AgentState(path=path)
        assert state2.already_processed("SOL-A") is True
        assert state2.already_processed("SOL-B") is True
        assert state2.already_processed_by_link("https://ex.com/a") is True
        assert state2.already_processed_by_link("https://ex.com/b") is True

    def test_roundtrip_preserves_all_fields(self, tmp_path):
        path = tmp_path / "state.json"
        state1 = AgentState(path=path)
        state1.mark_processed("SOL-X", request_id="req-99", title="Test Tender", link="https://ex.com/x")
        state1.save()

        raw = json.loads(path.read_text())
        assert raw["SOL-X"]["cflow_request_id"] == "req-99"
        assert raw["SOL-X"]["title"] == "Test Tender"
        assert raw["SOL-X"]["link"] == "https://ex.com/x"


class TestCorruptState:
    def test_corrupt_json_starts_fresh(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{invalid json!!")
        state = AgentState(path=path)
        assert state.already_processed("anything") is False

    def test_empty_file_starts_fresh(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("")
        state = AgentState(path=path)
        assert state.already_processed("anything") is False


class TestLinkSetBuiltFromLoadedData:
    def test_links_set_populated_on_load(self, tmp_path):
        path = tmp_path / "state.json"
        data = {
            "SOL-1": {"cflow_request_id": "r1", "title": "T1", "link": "https://ex.com/1", "processed_at": "2026-01-01"},
            "SOL-2": {"cflow_request_id": "r2", "title": "T2", "link": "https://ex.com/2", "processed_at": "2026-01-01"},
            "SOL-3": {"cflow_request_id": "r3", "title": "T3", "link": "", "processed_at": "2026-01-01"},
        }
        path.write_text(json.dumps(data))
        state = AgentState(path=path)
        assert state.already_processed_by_link("https://ex.com/1") is True
        assert state.already_processed_by_link("https://ex.com/2") is True
        # Empty link should not be in the set
        assert state.already_processed_by_link("") is False

    def test_missing_link_key_in_data(self, tmp_path):
        path = tmp_path / "state.json"
        data = {
            "SOL-OLD": {"cflow_request_id": "r1", "title": "T1", "processed_at": "2026-01-01"},
        }
        path.write_text(json.dumps(data))
        state = AgentState(path=path)
        # Should load without error, old entry has no link
        assert state.already_processed("SOL-OLD") is True
        assert state.already_processed_by_link("") is False

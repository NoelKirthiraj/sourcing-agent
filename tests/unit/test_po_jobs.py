"""Unit tests for the in-memory job store powering async PO extraction."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import po_jobs


@pytest.fixture(autouse=True)
def _clean_store():
    po_jobs._reset_for_tests()
    yield
    po_jobs._reset_for_tests()


def test_create_returns_unique_ids():
    a = po_jobs.create()
    b = po_jobs.create()
    assert a != b
    assert len(a) == 32  # uuid4().hex


def test_new_job_starts_in_pending_state():
    job_id = po_jobs.create()
    job = po_jobs.get(job_id)
    assert job is not None
    assert job["status"] == "pending"
    assert job["started_at"] > 0
    assert job["finished_at"] is None
    assert job["result"] is None
    assert job["error"] is None


def test_mark_running_transitions_state():
    job_id = po_jobs.create()
    po_jobs.mark_running(job_id)
    assert po_jobs.get(job_id)["status"] == "running"


def test_mark_done_stores_result_and_stamps_finished():
    job_id = po_jobs.create()
    po_jobs.mark_done(job_id, {"draft": {"po_number": "PO-1"}})
    job = po_jobs.get(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"draft": {"po_number": "PO-1"}}
    assert job["finished_at"] is not None
    assert job["finished_at"] >= job["started_at"]


def test_mark_failed_stores_error_and_status():
    job_id = po_jobs.create()
    po_jobs.mark_failed(job_id, "credit balance exhausted", error_status=503)
    job = po_jobs.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "credit balance exhausted"
    assert job["error_status"] == 503
    assert job["finished_at"] is not None


def test_mark_failed_default_status_is_500():
    job_id = po_jobs.create()
    po_jobs.mark_failed(job_id, "boom")
    assert po_jobs.get(job_id)["error_status"] == 500


def test_get_unknown_job_returns_none():
    assert po_jobs.get("does-not-exist") is None


def test_get_returns_copy_not_live_reference():
    """External mutations to the returned dict must not corrupt the store."""
    job_id = po_jobs.create()
    snapshot = po_jobs.get(job_id)
    snapshot["status"] = "corrupted"
    fresh = po_jobs.get(job_id)
    assert fresh["status"] == "pending"


def test_mark_operations_on_unknown_id_are_noops():
    """Race guard: if a job has already been evicted, mark_* must not crash."""
    po_jobs.mark_running("nonexistent")
    po_jobs.mark_done("nonexistent", {"foo": "bar"})
    po_jobs.mark_failed("nonexistent", "err", 500)
    # If nothing raised, we're good.


def test_cleanup_removes_expired_finished_jobs(monkeypatch):
    monkeypatch.setattr(po_jobs, "TTL_SECONDS", 0.1)
    job_id = po_jobs.create()
    po_jobs.mark_done(job_id, {})
    # Immediately after mark_done, the job is still fresh.
    assert po_jobs.get(job_id) is not None
    time.sleep(0.15)
    # After TTL expires, the next get triggers cleanup and returns None.
    assert po_jobs.get(job_id) is None


def test_cleanup_leaves_running_jobs_alone(monkeypatch):
    """A running job has no finished_at — TTL must not touch it."""
    monkeypatch.setattr(po_jobs, "TTL_SECONDS", 0.001)
    job_id = po_jobs.create()
    po_jobs.mark_running(job_id)
    time.sleep(0.05)
    # Should still be there — running jobs never expire.
    assert po_jobs.get(job_id) is not None

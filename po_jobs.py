"""In-memory job store for async PO extraction.

The PO extract endpoint runs Claude vision on two PDFs. Real POs can take
5–8 minutes end-to-end, which exceeds Railway's 300s HTTP edge timeout —
users saw "Failed to fetch" while the server was still working. This
module lets the endpoint hand back a job_id immediately and lets the
client poll for status without holding a long-lived connection open.

State is a process-local dict guarded by a Lock. Not durable across
restarts: if Railway redeploys mid-job, the client polling will see 404
and can retry. That's acceptable for a rare edge case.

Entries expire TTL_SECONDS after they finish so we don't leak memory on
the server if a client never comes back to collect a result.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# Completed/failed jobs are kept this long so a client that polls late
# (e.g. tab was backgrounded) can still fetch the result. 30 min is
# generous — polling happens every ~3 s in normal use.
TTL_SECONDS = 30 * 60


def create() -> str:
    """Register a new pending job, return its id."""
    job_id = uuid.uuid4().hex
    with _LOCK:
        _JOBS[job_id] = {
            "status": "pending",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
            "error_status": None,
        }
    return job_id


def mark_running(job_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "running"


def mark_done(job_id: str, result: dict) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "done"
            job["result"] = result
            job["finished_at"] = time.time()


def mark_failed(job_id: str, error: str, error_status: int = 500) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "failed"
            job["error"] = error
            job["error_status"] = error_status
            job["finished_at"] = time.time()


def get(job_id: str) -> Optional[dict]:
    """Return a copy of the job entry, or None. Also opportunistically
    evicts expired entries on each read."""
    _cleanup()
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _cleanup(*, now: Optional[float] = None) -> None:
    """Drop entries whose finished_at is older than TTL_SECONDS."""
    ts = now if now is not None else time.time()
    with _LOCK:
        stale = [
            jid for jid, j in _JOBS.items()
            if j.get("finished_at") and ts - j["finished_at"] > TTL_SECONDS
        ]
        for jid in stale:
            _JOBS.pop(jid, None)


def _reset_for_tests() -> None:
    """Test-only helper. Do not call from production code."""
    with _LOCK:
        _JOBS.clear()

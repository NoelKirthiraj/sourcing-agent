"""Regression tests for the single-threaded-server outage of 2026-10-05.

`api.py` ran a plain `HTTPServer`, so one slow handler blocked every other
request. Pressing "Search web" ran a 30–120s Claude web search synchronously
inside a request thread; during that window Railway's `/api/health` probe got
no answer, the health check failed, the container restarted, and the whole
dashboard showed `502 Application failed to respond`.

The fix is a threaded server plus a lock around the shared asyncio loop (one
loop cannot be driven concurrently, and the asyncpg pool is bound to it). These
tests pin both halves — and the behaviour that actually matters: a long request
must not stop the health check answering.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import api


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server():
    """The real APIHandler on a real socket, in a background thread."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), api.APIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode()


# ── Server configuration ────────────────────────────────────────────────────

def test_server_is_threaded():
    """A plain HTTPServer is what caused the outage."""
    source = Path("api.py").read_text()
    assert "ThreadingHTTPServer((" in source
    assert "HTTPServer((" not in source.replace("ThreadingHTTPServer((", "")


def test_shared_loop_is_lock_guarded():
    assert isinstance(api._loop_lock, type(threading.Lock()))


# ── The behaviour that broke ────────────────────────────────────────────────

def test_health_answers_while_a_slow_request_holds_the_loop(live_server):
    """The actual regression.

    Holding the loop lock simulates a request doing long database work. The
    health probe must still answer — on the old single-threaded server it
    could not, and Railway restarted the container.
    """
    holder_released = threading.Event()

    def hold_loop():
        with api._loop_lock:
            holder_released.wait(timeout=10)

    holder = threading.Thread(target=hold_loop, daemon=True)
    holder.start()
    time.sleep(0.1)          # let it take the lock

    try:
        started = time.monotonic()
        status, body = _get(f"{live_server}/api/health", timeout=5)
        elapsed = time.monotonic() - started
        assert status == 200
        assert '"ok"' in body
        assert elapsed < 2.0, f"health blocked for {elapsed:.1f}s"
    finally:
        holder_released.set()
        holder.join(timeout=5)


def test_concurrent_requests_are_served_in_parallel(live_server):
    """Ten simultaneous probes should not serialise into ten round trips."""
    results: list[int] = []
    lock = threading.Lock()

    def probe():
        try:
            status, _ = _get(f"{live_server}/api/health", timeout=5)
        except Exception:
            status = 0
        with lock:
            results.append(status)

    threads = [threading.Thread(target=probe, daemon=True) for _ in range(10)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    elapsed = time.monotonic() - started

    assert results.count(200) == 10
    assert elapsed < 5.0, f"10 concurrent probes took {elapsed:.1f}s"


def test_unknown_path_still_404s_under_threading(live_server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(f"{live_server}/api/nope")
    assert err.value.code == 404


# ── _run_async under concurrency ────────────────────────────────────────────

def test_run_async_is_safe_from_many_threads():
    """Several request threads calling the shared loop must not corrupt it.

    Without the lock this raises "This event loop is already running" or
    returns another thread's result.
    """
    async def work(n: int) -> int:
        return n * 2

    results: dict[int, int] = {}
    errors: list[Exception] = []
    lock = threading.Lock()

    def call(n: int):
        try:
            value = api._run_async(work(n))
            with lock:
                results[n] = value
        except Exception as exc:                      # pragma: no cover
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=call, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    assert results == {i: i * 2 for i in range(16)}


def test_run_async_reuses_one_loop():
    """A loop per thread would strand the asyncpg pool, which is bound to the
    loop that created it."""
    async def noop():
        return None

    api._run_async(noop())
    first = api._loop
    for _ in range(5):
        threading.Thread(target=lambda: api._run_async(noop())).start()
    time.sleep(0.3)
    assert api._loop is first


def test_run_async_propagates_exceptions():
    async def boom():
        raise ValueError("from the coroutine")

    with pytest.raises(ValueError, match="from the coroutine"):
        api._run_async(boom())


def test_lock_is_released_after_an_exception():
    """A handler raising must not wedge every later request."""
    async def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        api._run_async(boom())

    assert api._loop_lock.acquire(timeout=1), "loop lock was not released"
    api._loop_lock.release()

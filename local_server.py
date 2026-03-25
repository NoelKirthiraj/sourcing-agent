"""
Local trigger server for the Mission Control dashboard.
Run: python local_server.py
Serves the dashboard at http://localhost:8080 and exposes /api/trigger?mode=daily|weekly
"""
import asyncio
import json
import logging
import subprocess
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = 8080
PROJECT_DIR = Path(__file__).parent

# Track running agent process
_agent_lock = threading.Lock()
_agent_running = False
_last_result = {"status": "idle", "message": "No runs yet"}


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/trigger":
            self._handle_trigger(parsed)
        elif parsed.path == "/api/status":
            self._json_response(200, _last_result)
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _handle_trigger(self, parsed):
        global _agent_running
        params = parse_qs(parsed.query)
        mode = params.get("mode", ["daily"])[0]

        if mode not in ("daily", "weekly"):
            self._json_response(400, {"status": "error", "message": "Mode must be daily or weekly"})
            return

        with _agent_lock:
            if _agent_running:
                self._json_response(409, {"status": "running", "message": "Agent is already running"})
                return
            _agent_running = True

        self._json_response(202, {"status": "started", "message": f"{mode.title()} run started"})
        threading.Thread(target=_run_agent, args=(mode,), daemon=True).start()

    def _json_response(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def log_message(self, format, *args):
        if "/api/" in str(args[0]) if args else False:
            log.info(format % args)


def _run_agent(mode: str):
    global _agent_running, _last_result
    _last_result = {"status": "running", "message": f"{mode.title()} run in progress..."}
    log.info("Starting %s agent run...", mode)

    cmd = [sys.executable, "run.py"]
    if mode == "weekly":
        cmd.append("--weekly")

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            _last_result = {"status": "success", "message": f"{mode.title()} run completed"}
            log.info("Agent run completed successfully")
        else:
            _last_result = {
                "status": "error",
                "message": f"Run failed (exit {result.returncode}): {result.stderr[-200:] if result.stderr else 'no output'}",
            }
            log.error("Agent run failed: %s", result.stderr[-200:] if result.stderr else "no output")
    except subprocess.TimeoutExpired:
        _last_result = {"status": "error", "message": "Run timed out after 10 minutes"}
        log.error("Agent run timed out")
    except Exception as exc:
        _last_result = {"status": "error", "message": str(exc)}
        log.error("Agent run error: %s", exc)
    finally:
        with _agent_lock:
            _agent_running = False


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    log.info("Mission Control running at http://localhost:%d", PORT)
    log.info("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()

#!/usr/bin/env python3
"""
Clear the SAP login-halt flag in data/agent_profile.json.

When the daily cron repeatedly fails to log into SAP Ariba (typically
because the SAP_PASSWORD secret was rotated and the cron is still using
the old value), agent.py sets:

    sap_login_halted = true
    sap_halted_at    = <timestamp>
    sap_halted_attempts = <N>
    sap_last_error   = <last login error message>

…to stop further login attempts and prevent permanent account lockout.

This script resets those fields so the next cron run will attempt SAP
login again. Run it AFTER updating SAP_PASSWORD in GitHub Secrets
(otherwise the halt will just re-trigger on the next run).

Usage:
    python tools/clear_sap_halt.py             # clear + show summary
    python tools/clear_sap_halt.py --status    # show current state only
                                                 (no write)

Exit codes:
    0  cleared (or already clear)
    1  agent_profile.json missing / unreadable
    2  --status requested only (no write performed)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add repo root to sys.path so `import dashboard_data` works whether
# the script is invoked as `python tools/clear_sap_halt.py` or
# `tools/clear_sap_halt.py` from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import dashboard_data  # noqa: E402

DATA_DIR = REPO_ROOT / "data"


def _show(state: dict, prefix: str = "") -> None:
    halted = state.get("sap_login_halted")
    fails = state.get("sap_consecutive_failures", 0)
    at = state.get("sap_halted_at") or "—"
    attempts = state.get("sap_halted_attempts", 0)
    last_err = (state.get("sap_last_error") or "").strip() or "(none)"
    flag = "🔴 HALTED" if halted else "🟢 OK"
    print(f"{prefix}{flag}")
    print(f"{prefix}  consecutive_failures: {fails}")
    print(f"{prefix}  halted_at:            {at}")
    print(f"{prefix}  halted_attempts:      {attempts}")
    print(f"{prefix}  last_error:           {last_err[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument(
        "--status", action="store_true",
        help="Show current halt state without modifying anything.",
    )
    args = parser.parse_args()

    profile_path = DATA_DIR / "agent_profile.json"
    if not profile_path.exists():
        print(f"❌ {profile_path} not found.", file=sys.stderr)
        return 1

    before = dashboard_data.get_sap_halt_state(DATA_DIR)
    print("Current SAP halt state:")
    _show(before, prefix="  ")
    print()

    if args.status:
        return 2

    if not before.get("sap_login_halted") and not before.get("sap_consecutive_failures"):
        print("Nothing to clear — already in clean state.")
        return 0

    dashboard_data.clear_sap_halt(DATA_DIR)
    after = dashboard_data.get_sap_halt_state(DATA_DIR)
    print("Cleared. New state:")
    _show(after, prefix="  ")
    print()
    print("Next steps:")
    print("  1. Make sure SAP_PASSWORD in GitHub Secrets is updated to the new value")
    print("     (otherwise the next run will re-halt on the same failure).")
    print(f"  2. Commit + push the modified {profile_path.relative_to(REPO_ROOT)}:")
    print(f"       git add {profile_path.relative_to(REPO_ROOT)}")
    print('       git commit -m "ops: clear SAP login halt after password rotation"')
    print("       git push")
    print("  3. (Optional) Trigger a manual run to verify:")
    print("       gh workflow run daily_agent.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Dashboard data persistence — run history + agent profile (XP, levels, achievements).
Called by agent.py after each run. Produces JSON files consumed by the static dashboard.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_HISTORY = 365

# ── SAP login-halt guardrail ─────────────────────────────────────────────────
# When the cron repeatedly fails to log into SAP Ariba (typically because the
# password was rotated and our copy is stale), continuing to retry causes
# the SAP account to lock out for fraud-review. We track consecutive failure
# counts in agent_profile.json and halt the SAP login step (only) after N
# strikes. The CanadaBuys scrape continues normally.
#
# Default threshold (2) means: tolerate one transient blip, then stop. After
# rotating the SAP password the user runs `python tools/clear_sap_halt.py`
# to reset the counter + clear the halt flag.
SAP_HALT_THRESHOLD = 2

SAP_HALT_DEFAULTS = {
    "sap_consecutive_failures": 0,
    "sap_login_halted": False,
    "sap_halted_at": None,
    "sap_halted_attempts": 0,
    "sap_last_error": "",
}


def _read_profile(data_dir: Path) -> dict:
    """Read agent_profile.json. Empty dict if missing or malformed."""
    p = data_dir / "agent_profile.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not parse %s — returning empty profile", p)
        return {}


def _write_profile(profile: dict, data_dir: Path) -> None:
    p = data_dir / "agent_profile.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def get_sap_halt_state(data_dir: Path) -> dict:
    """Current SAP halt fields, with defaults filled in for any missing keys."""
    profile = _read_profile(data_dir)
    state = dict(SAP_HALT_DEFAULTS)
    for k in SAP_HALT_DEFAULTS:
        if k in profile:
            state[k] = profile[k]
    return state


def record_sap_login_success(data_dir: Path) -> None:
    """Reset the consecutive-failure counter and clear any halt flag.

    Called by agent.py whenever an SAP login attempt actually succeeds —
    we know the credentials still work, so the halt (if any) is stale.
    """
    profile = _read_profile(data_dir)
    profile.update(SAP_HALT_DEFAULTS)
    _write_profile(profile, data_dir)


def _ensure_sap_halt_defaults(profile: dict) -> dict:
    """Backfill any missing SAP halt fields with defaults so callers
    can always rely on the full block being present in the dict."""
    for k, v in SAP_HALT_DEFAULTS.items():
        profile.setdefault(k, v)
    return profile


def record_sap_login_failure(
    error_msg: str,
    data_dir: Path,
    threshold: int = SAP_HALT_THRESHOLD,
) -> dict:
    """Increment the consecutive-failure counter. Set halt flag if threshold
    reached. Returns the updated state so the caller can log accordingly.
    """
    profile = _ensure_sap_halt_defaults(_read_profile(data_dir))
    fails = int(profile.get("sap_consecutive_failures", 0)) + 1
    profile["sap_consecutive_failures"] = fails
    # Cap error string to avoid pathological growth from stack-trace dumps.
    profile["sap_last_error"] = (error_msg or "")[:300]
    if fails >= threshold and not profile.get("sap_login_halted"):
        profile["sap_login_halted"] = True
        profile["sap_halted_at"] = datetime.now(timezone.utc).isoformat()
        profile["sap_halted_attempts"] = fails
    _write_profile(profile, data_dir)
    return profile


def clear_sap_halt(data_dir: Path) -> dict:
    """Reset all SAP halt fields. Called by `tools/clear_sap_halt.py` after
    the operator has rotated the SAP password in GitHub Secrets.
    """
    profile = _read_profile(data_dir)
    profile.update(SAP_HALT_DEFAULTS)
    _write_profile(profile, data_dir)
    return profile

LEVEL_THRESHOLDS = [
    (0, "Rookie"),
    (10, "Rookie"),
    (30, "Field Agent"),
    (70, "Field Agent"),
    (100, "Senior Operative"),
    (200, "Senior Operative"),
    (350, "Commander"),
    (500, "Commander"),
    (750, "Commander"),
    (1000, "Legend"),
]

ACHIEVEMENTS = [
    {
        "id": "first_launch",
        "name": "First Launch",
        "description": "First production run",
    },
    {
        "id": "century",
        "name": "Century",
        "description": "100 tenders processed",
    },
    {
        "id": "sharpshooter",
        "name": "Sharpshooter",
        "description": "10 consecutive zero-error runs",
    },
    {
        "id": "speed_demon",
        "name": "Speed Demon",
        "description": "Run completed under 3 minutes",
    },
    {
        "id": "weekly_warrior",
        "name": "Weekly Warrior",
        "description": "30+ tenders in a single weekly scan",
    },
    {
        "id": "thousand",
        "name": "Thousand",
        "description": "1,000 tenders processed",
    },
    {
        "id": "iron_streak",
        "name": "Iron Streak",
        "description": "30 consecutive error-free runs",
    },
    {
        "id": "night_owl",
        "name": "Night Owl",
        "description": "Manual run after midnight",
    },
]


def get_level(xp: int) -> tuple[int, str]:
    """Return (level_number, level_title) for a given XP total."""
    level = 1
    title = "Rookie"
    for i, (threshold, name) in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
            title = name
    return level, title


def compute_streak(history: list[dict]) -> tuple[int, int]:
    """Return (current_streak, best_streak) of consecutive zero-error runs."""
    current = 0
    best = 0
    for run in reversed(history):
        if run.get("error_count", 0) == 0:
            current += 1
        else:
            break
    streak = 0
    for run in history:
        if run.get("error_count", 0) == 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return current, best


def evaluate_achievements(history: list[dict], existing: list[dict]) -> list[dict]:
    """Evaluate all achievement conditions against run history."""
    earned = {a["id"]: a for a in existing}
    total_processed = sum(r.get("new_count", 0) for r in history)
    _, best_streak = compute_streak(history)

    checks = {
        "first_launch": len(history) >= 1,
        "century": total_processed >= 100,
        "sharpshooter": best_streak >= 10,
        "speed_demon": any(r.get("duration_seconds", 999) < 180 for r in history),
        "weekly_warrior": any(
            r.get("mode") == "weekly" and r.get("new_count", 0) >= 30
            for r in history
        ),
        "thousand": total_processed >= 1000,
        "iron_streak": best_streak >= 30,
        "night_owl": any(
            _is_night_run(r.get("run_at", "")) for r in history
        ),
    }

    now = datetime.now(timezone.utc).isoformat()
    for achievement in ACHIEVEMENTS:
        aid = achievement["id"]
        if aid not in earned and checks.get(aid, False):
            earned[aid] = {
                "id": aid,
                "name": achievement["name"],
                "earned_at": now,
            }

    return list(earned.values())


def _is_night_run(run_at: str) -> bool:
    """Check if a run timestamp is after midnight (00:00-05:00 local-ish)."""
    if not run_at:
        return False
    try:
        dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        return dt.hour < 5
    except (ValueError, AttributeError):
        return False


def recompute_profile(history: list[dict], existing_profile: dict) -> dict:
    """Recompute the full agent profile from run history."""
    total_processed = sum(r.get("new_count", 0) for r in history)
    total_runs = len(history)
    current_streak, best_streak = compute_streak(history)
    level, level_title = get_level(total_processed)
    achievements = evaluate_achievements(
        history, existing_profile.get("achievements", [])
    )
    last_run = history[-1] if history else {}
    last_status = "sleeping"
    if last_run:
        if last_run.get("error_count", 0) > 0:
            last_status = "error"
        elif last_run.get("new_count", 0) == 0:
            last_status = "idle"
        else:
            last_status = "success"

    profile = {
        "xp": total_processed,
        "level": level,
        "level_title": level_title,
        "total_processed": total_processed,
        "total_runs": total_runs,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "achievements": achievements,
        "last_run_at": last_run.get("run_at", ""),
        "last_status": last_status,
    }
    # Carry forward the SAP halt fields verbatim. They're independent of
    # the recomputed metrics and mid-run writes (record_sap_login_*) must
    # not be overwritten by the end-of-run record_run() flow.
    for k, default in SAP_HALT_DEFAULTS.items():
        profile[k] = existing_profile.get(k, default)
    return profile


def record_run(summary: Any, data_dir: Path) -> None:
    """Append a run record to history and recompute the agent profile."""
    data_dir.mkdir(parents=True, exist_ok=True)
    history_path = data_dir / "run_history.json"
    profile_path = data_dir / "agent_profile.json"

    # Load existing history
    history: list[dict] = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Could not read %s — starting fresh", history_path)

    # Load existing profile (for preserving achievement earned_at timestamps)
    existing_profile: dict = {}
    if profile_path.exists():
        try:
            existing_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Build run record
    run_record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "total_found": summary.total_found,
        "new_count": summary.new_count,
        "skipped_count": summary.skipped_count,
        "error_count": summary.error_count,
        "errors": summary.errors[:10],
        # Full sol-no list (uncapped) — used by the dashboard to compute
        # "Archived" count by diffing against live DB. new_tenders below
        # stays capped at 20 for size, but only carries detail fields.
        "new_solicitation_nos": [
            t.get("solicitation_no", "") for t in summary.new_tenders
            if t.get("solicitation_no")
        ],
        "new_tenders": [
            {
                "solicitation_no": t.get("solicitation_no", ""),
                "solicitation_title": t.get("solicitation_title", ""),
                "gsin_description": t.get("gsin_description", ""),
                "inquiry_link": t.get("inquiry_link", ""),
                "closing_date": t.get("closing_date", ""),
                "time_and_zone": t.get("time_and_zone", ""),
                "notifications": t.get("notifications", ""),
                "client": t.get("client", ""),
                "contact_name": t.get("contact_name", ""),
                "contact_email": t.get("contact_email", ""),
                "contact_phone": t.get("contact_phone", ""),
            }
            for t in summary.new_tenders[:20]
        ],
        "duration_seconds": round(summary.duration_seconds, 1),
        "mode": summary.mode,
    }

    history.append(run_record)

    # Trim to max history
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    # Recompute profile
    profile = recompute_profile(history, existing_profile)

    # Write both files
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    log.info("Dashboard data updated: %d runs tracked, XP=%d, Level=%s",
             len(history), profile["xp"], profile["level_title"])

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
        last_status = "error" if last_run.get("error_count", 0) > 0 else "success"

    return {
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
        "new_tenders": [
            {
                "solicitation_no": t.get("solicitation_no", ""),
                "solicitation_title": t.get("solicitation_title", ""),
                "client": t.get("client", ""),
                "closing_date": t.get("closing_date", ""),
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

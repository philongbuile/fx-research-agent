"""Who is on duty for the demo agents: a configurable shift roster plus manual overrides.

The trading loop is deterministic code; the on-duty AI model owns reflection,
policy changes, lessons and code fixes. Every trade, policy version and lesson
records the model on duty, so results can be compared per model later.
"""

import tomllib
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_ROSTER = {
    "timezone": "Asia/Ho_Chi_Minh",
    "shift": [
        {"agent": "codex", "model": "gpt-6-sol", "start": "08:00", "end": "18:00"},
        {"agent": "claude", "model": "claude-opus-5-5", "start": "18:00", "end": "08:00"},
    ],
}


def load_roster(path: Path | None) -> dict:
    if path and Path(path).exists():
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return DEFAULT_ROSTER


def _covers(start: time, end: time, t: time) -> bool:
    return start <= t < end if start < end else t >= start or t < end  # wraps midnight


def on_duty(roster: dict, now: datetime, override: dict | None = None) -> dict:
    """{agent, model, source} for `now`. An unexpired override wins over the schedule."""
    if override and datetime.fromisoformat(override["until"]) > now:
        return {"agent": override["agent"], "model": override["model"], "source": "override",
                "until": override["until"], "reason": override.get("reason", "")}
    local = now.astimezone(ZoneInfo(roster["timezone"]))
    for s in roster["shift"]:
        start, end = time.fromisoformat(s["start"]), time.fromisoformat(s["end"])
        if _covers(start, end, local.time()):
            ends = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
            if ends <= local:
                ends += timedelta(days=1)
            return {"agent": s["agent"], "model": s["model"], "source": "schedule",
                    "until": ends.isoformat(), "timezone": roster["timezone"]}
    return {"agent": "unassigned", "model": "unassigned", "source": "schedule", "until": None}


def label(duty: dict) -> str:
    return f"{duty['agent']}/{duty['model']}"

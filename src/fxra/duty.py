"""Who is on duty for the demo agents: a configurable roster of possibly overlapping shifts.

The trading loop is deterministic code. Agents on duty handle operations,
reflection, lessons and code fixes; each H-002 arm belongs to one agent (D-33),
which alone edits that arm's policy. The first matching shift is primary
(operations); later matches are co-duty.
"""

import tomllib
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_ROSTER = {
    "timezone": "Asia/Ho_Chi_Minh",
    "shift": [
        {"agent": "codex", "model": "gpt-6-sol", "start": "00:00", "end": "00:00"},  # 24/7, primary
        {"agent": "claude", "model": "claude-opus-5-5", "start": "18:00", "end": "08:00"},  # owner can use Claude
    ],
}


def load_roster(path: Path | None) -> dict:
    if path and Path(path).exists():
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return DEFAULT_ROSTER


def _covers(start: time, end: time, t: time) -> bool:
    return start <= t < end if start < end else t >= start or t < end  # wraps midnight; start == end is all day


def on_duty(roster: dict, now: datetime, override: dict | None = None) -> dict:
    """Primary {agent, model, source, until} for `now`, plus `co_duty` agents also on shift.

    An unexpired override becomes primary; the scheduled agents stay as co-duty.
    """
    local = now.astimezone(ZoneInfo(roster["timezone"]))
    matching = []
    for s in roster["shift"]:
        start, end = time.fromisoformat(s["start"]), time.fromisoformat(s["end"])
        if _covers(start, end, local.time()):
            ends = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
            if ends <= local:
                ends += timedelta(days=1)
            matching.append({"agent": s["agent"], "model": s["model"], "source": "schedule",
                             "until": ends.isoformat(), "timezone": roster["timezone"]})
    if override and datetime.fromisoformat(override["until"]) > now:
        matching.insert(0, {"agent": override["agent"], "model": override["model"], "source": "override",
                            "until": override["until"], "reason": override.get("reason", "")})
    if not matching:
        return {"agent": "unassigned", "model": "unassigned", "source": "schedule", "until": None, "co_duty": []}
    primary = matching[0]
    co = [m for m in matching[1:] if m["agent"] != primary["agent"]]
    return {**primary, "co_duty": co}


def label(duty: dict) -> str:
    return f"{duty['agent']}/{duty['model']}"

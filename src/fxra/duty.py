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
    # Which model each agent currently runs. Change it here (or in config/duty.toml) when the
    # agent switches model or effort; trades record the label in force when they were opened.
    "agents": {
        "codex": {"model": "gpt-6.1-sol", "effort": "medium", "label": "Codex GPT-6.1 Sol Medium"},
        "claude": {"model": "claude-opus-5-5", "effort": "medium", "label": "Claude Opus 5.5 Medium"},
    },
    "shift": [
        {"agent": "codex", "start": "00:00", "end": "00:00"},  # 24/7, primary
        {"agent": "claude", "start": "18:00", "end": "08:00"},  # owner can use Claude
    ],
}


def load_roster(path: Path | None) -> dict:
    if path and Path(path).exists():
        roster = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return {**roster, "agents": {**DEFAULT_ROSTER["agents"], **roster.get("agents", {})}}
    return DEFAULT_ROSTER


def agent_info(roster: dict, agent: str) -> dict:
    """{model, effort, label} of an agent; unknown agents fall back to their name."""
    info = roster.get("agents", {}).get(agent, {})
    model = info.get("model", agent)
    return {"agent": agent, "model": model, "effort": info.get("effort", ""),
            "label": info.get("label") or " ".join(x for x in (model, info.get("effort", "")) if x)}


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
            info = agent_info(roster, s["agent"])
            matching.append({**info, "model": s.get("model", info["model"]), "source": "schedule",
                             "until": ends.isoformat(), "timezone": roster["timezone"]})
    if override and datetime.fromisoformat(override["until"]) > now:
        info = agent_info(roster, override["agent"])
        label = info["label"] if override["model"] == info["model"] else override["model"]
        matching.insert(0, {**info, "model": override["model"], "label": label, "source": "override",
                            "until": override["until"], "reason": override.get("reason", "")})
    if not matching:
        return {"agent": "unassigned", "model": "unassigned", "label": "unassigned", "source": "schedule",
                "until": None, "co_duty": []}
    primary = matching[0]
    co = [m for m in matching[1:] if m["agent"] != primary["agent"]]
    return {**primary, "co_duty": co}


def label(duty: dict) -> str:
    return f"{duty['agent']}/{duty['model']}"

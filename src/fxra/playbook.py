"""Trading playbook (D-39): candidate rules distilled from lessons, with evidence recounted from the journal.

A rule names a trade filter, e.g. {"pattern": ["breakout10", "breakout20"], "hour": ["18"]}, and an
action: "avoid" (these trades lose), "prefer" (these trades win) or "watch" (an open question). Its
evidence is recomputed from closed H-002 trades on every read, so a rule never cites stale numbers.
When the matched trades reach the rule's `confirm_n` and the numbers support it, the rule is "ready":
its author turns it into a `demo-propose` for the other model, and only an agreed proposal changes a
policy. The playbook never changes trading by itself.
"""

from datetime import datetime

from .lesson_insights import closed_trades, label, stats
from .trade_journal import Journal

FIELDS = ("pattern", "symbol", "session", "hour", "side", "arm", "vol", "h1_trend", "dow", "confluence")
ACTIONS = ("avoid", "prefer", "watch")
STATUSES = ("candidate", "proposed", "adopted", "retired")


def parse_filter(items: list[str]) -> dict:
    """["pattern=breakout10,breakout20", "hour=18"] -> {"pattern": [...], "hour": ["18"]}."""
    out: dict = {}
    for item in items or ():
        key, _, values = item.partition("=")
        key = key.strip()
        if key not in FIELDS or not values.strip():
            raise ValueError(f"filter must be <field>=<v1,v2> with field in {FIELDS}: {item!r}")
        vals = [v.strip() for v in values.split(",") if v.strip()]
        out.setdefault(key, []).extend(v.zfill(2) if key == "hour" else v for v in vals)
    if not out:
        raise ValueError("a rule needs at least one --when filter")
    return out


def matches(filter_: dict, trade: dict) -> bool:
    return all(str(trade.get(k)) in v for k, v in filter_.items())


def evaluate(rule: dict, rows: list[dict]) -> dict:
    """Matched vs other trades, a sample-size label and whether the numbers back the rule."""
    inside = [t for t in rows if matches(rule["filter"], t)]
    outside = [t for t in rows if not matches(rule["filter"], t)]
    s_in = stats(inside) if inside else None
    s_out = stats(outside) if outside else None
    r_in = s_in and s_in["avg_r"]
    r_out = s_out and s_out["avg_r"]
    if not s_in or r_in is None or rule["action"] == "watch":
        verdict = "no data" if not s_in else "observing"
    elif rule["action"] == "avoid":
        verdict = "supported" if r_in < 0 and (r_out is None or r_in < r_out) else "contradicted" if r_in > 0 else "unclear"
    else:
        verdict = "supported" if r_in > 0 and (r_out is None or r_in > r_out) else "contradicted" if r_in < 0 else "unclear"
    n = s_in["n"] if s_in else 0
    ready = rule["status"] == "candidate" and n >= rule["confirm_n"] and verdict == "supported"
    return {**rule, "evidence": {"matched": s_in, "others": s_out, "label": label(n), "verdict": verdict,
                                 "progress": f"{n}/{rule['confirm_n']}", "ready_to_propose": ready}}


def report(journal: Journal, rows: list[dict] | None = None, assumed_cost_pips: float = 0.8) -> list[dict]:
    rules = journal.rules()
    if not rules:
        return []
    rows = closed_trades(journal, assumed_cost_pips) if rows is None else rows
    return [evaluate(r, rows) for r in rules]


def _fmt(s: dict | None) -> str:
    if not s:
        return "no trades"
    avg = f"{s['avg_r']:+.2f}R" if s["avg_r"] is not None else "n/a R"
    return f"n={s['n']}, win {s['win_rate']:.0%}, avg {avg}, net {s['net']:+.2f} USD"


def document(journal: Journal, rows: list[dict] | None = None) -> dict | None:
    """One Hindsight document with every active rule and its current evidence."""
    rules = [r for r in report(journal, rows) if r["status"] != "retired"]
    if not rules:
        return None
    lines = [f"#{r['id']} [{r['status']}] {r['action'].upper()}: {r['title']} "
             f"(when {'; '.join(f'{k}={','.join(v)}' for k, v in r['filter'].items())}). "
             f"Evidence {r['evidence']['progress']} [{r['evidence']['label']}], {r['evidence']['verdict']}: "
             f"matched {_fmt(r['evidence']['matched'])}; others {_fmt(r['evidence']['others'])}. "
             f"Why: {r['rationale']} (by {r['author']})" for r in rules]
    content = ("H-002 trading playbook: candidate rules distilled from lessons, with evidence recounted from the "
               "demo journal. A rule changes a policy only after the other model agrees to a proposal. "
               "Demo in-sample observations, not evidence of edge.\n" + "\n".join(lines))
    return {"document": "fxra-playbook", "content": content, "tags": ["demo", "playbook", "H-002"]}


def add(journal: Journal, at: datetime, author: str, title: str, action: str, when: list[str], rationale: str,
        confirm_n: int) -> int:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if "/" not in author:
        raise ValueError("author must be <agent>/<model>")
    if confirm_n < 10:
        raise ValueError("confirm_n below 10 is noise; use 20-30 or more")
    if not rationale:
        raise ValueError("a rule needs a rationale citing the lesson or numbers behind it")
    return journal.add_rule(at, author, title, action, parse_filter(when), rationale, confirm_n)

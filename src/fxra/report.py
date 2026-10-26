"""Markdown comparison of run records (B-09). Every number comes from run.json, nothing else."""

import json
from collections import defaultdict
from pathlib import Path

STATS = ["Sharpe Ratio", "Probabilistic Sharpe Ratio", "Net Profit", "Drawdown", "Total Orders"]


def load_records(runs_root: Path) -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in Path(runs_root).glob("*/run.json")),
                  key=lambda r: r["created_at"])


def not_evidence(r: dict) -> list[str]:
    """Why a run cannot count as evidence of edge. Empty list = nothing flagged here (not a verdict)."""
    why = []
    if r["technical_only"]:
        why.append("technical_only")
    if not r["reproducible"]:
        why.append("not reproducible")
    segment = r.get("window", {}).get("segment")
    if segment != "out_of_sample":
        why.append("in_sample" if segment == "in_sample" else "segment unknown")
    if r["status"] != "completed":
        why.append(r["status"])
    return why


def _row(r: dict) -> str:
    w = r.get("window")
    where = f'{w["segment"]} {w["start"]}..{w["end"]}' if w else "n/a (no window)"
    if r.get("group_id"):
        where += f' (group {r["group_id"]})'
    if r.get("stress"):
        where += f' (spread x{r["stress"]["spread_multiplier"]}, slippage {r["stress"]["slippage_points"]} pts)'
    stats = r["metrics"].get("statistics", {})
    cells = [r["run_id"], r["kind"], where, r["status"], str(r["reproducible"]).lower(),
             str(r["technical_only"]).lower(), *(str(stats.get(k, "")) for k in STATS),
             ", ".join(not_evidence(r)) or "-"]
    return "| " + " | ".join(cells) + " |"


def render(records: list[dict], hypothesis: str | None = None) -> str:
    by_h = defaultdict(list)
    for r in records:
        if hypothesis in (None, r["hypothesis_id"]):
            by_h[r["hypothesis_id"]].append(r)
    if not by_h:
        return "No runs found.\n"
    header = ("| run_id | kind | segment / window | status | reproducible | technical_only | "
              + " | ".join(STATS) + " | not evidence because |\n" + "|" + "---|" * (6 + len(STATS) + 1))
    out = []
    for h, rs in sorted(by_h.items()):
        out.append(f"## {h}\n\nTrials: {len(rs)} (every run record, any status)\n")
        parts = [("In-sample", [r for r in rs if r.get("window", {}).get("segment") == "in_sample"]),
                 ("Out-of-sample", [r for r in rs if r.get("window", {}).get("segment") == "out_of_sample"]),
                 ("No window recorded", [r for r in rs if "window" not in r])]
        for title, rows in parts:
            if rows:
                out.append(f"### {title}\n\n{header}\n" + "\n".join(_row(r) for r in rows) + "\n")
    return "\n".join(out)

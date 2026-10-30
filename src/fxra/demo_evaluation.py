"""Read-only audit of H-003 decisions and fills. Never creates/migrates a journal."""

import hashlib
import json
import secrets
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from . import runs
from .trade_journal import PIP_SIZES

TAKES = {"approved", "sent", "rejected", "error"}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return result.astimezone(timezone.utc)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def stats(values: list[float]) -> dict:
    return {"n": len(values), "mean_R": sum(values) / len(values) if values else None,
            "sum_R": sum(values), "positive": sum(v > 0 for v in values)}


def audit(path: Path, since: str, until: str) -> dict:
    """Explicit half-open UTC window; candidate cohorts use creation, fills use closing time."""
    start, end = timestamp(since), timestamp(until)
    if start >= end:
        raise ValueError("since must precede until")
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # candidates, fills and equity share one consistent read snapshot
        candidates = [dict(r) for r in db.execute(
            "SELECT id,arm,symbol,side,bar,created_at,status,decided_at,client_order_id,"
            "shadow,memory_refs,reason_codes FROM candidates")]
        trades = [dict(r) for r in db.execute(
            "SELECT t.entry_id,t.side,t.open_time,t.close_time,t.open_price,t.close_price,t.net,"
            "t.exit_reason,g.arm,g.symbol,o.stop_loss FROM trades t "
            "JOIN entry_tags g ON g.entry_id=t.entry_id "
            "LEFT JOIN orders o ON o.client_order_id=t.entry_id WHERE g.strategy='H-003'")]
        equity = [dict(r) for r in db.execute("SELECT at,balance,equity FROM equity ORDER BY at")]
        db.rollback()
    in_window = lambda s: s is not None and start <= timestamp(s) < end
    candidates = [c for c in candidates if in_window(c["created_at"])]
    for c in candidates:
        for key in ("shadow", "memory_refs", "reason_codes"):
            c[key] = json.loads(c[key]) if c[key] else None
    # Retain all linked cohort trades, including trades that close outside the requested window.
    ids = {c["client_order_id"] for c in candidates if c["client_order_id"]}
    trades = [t for t in trades if t["entry_id"] in ids or in_window(t["close_time"])]
    equity = [e for e in equity if timestamp(e["at"]) < end]
    result = {}
    for arm in sorted({c["arm"] for c in candidates} | {t["arm"] for t in trades}):
        mine = [c for c in candidates if c["arm"] == arm]
        decided = [c for c in mine if c["status"] in TAKES | {"skipped"}]
        pairs = []
        for c in mine:
            t = next((t for t in trades if t["entry_id"] == c["client_order_id"]), None)
            # Actual outcome must have been observable by the evaluation cutoff.
            if not t or t["net"] is None or not t["close_time"] or timestamp(t["close_time"]) >= end:
                continue
            dist = abs(t["open_price"] - t["stop_loss"]) if t["stop_loss"] else None
            if not c["shadow"] or not dist:
                continue
            price_r = (1 if t["side"] == "buy" else -1) * (t["close_price"] - t["open_price"]) / dist
            pairs.append({"id": c["id"], "legacy_shadow_R": c["shadow"]["R"], "actual_price_R": price_r,
                          "actual_price_R_assumed_cost": price_r - 0.8 * PIP_SIZES[t["symbol"]] / dist,
                          "broker_net_usd": t["net"], "exit_reason": t["exit_reason"]})
        closed = [t for t in trades if t["arm"] == arm and t["net"] is not None and in_window(t["close_time"])]
        losses = -sum(t["net"] for t in closed if t["net"] < 0)
        shadow = lambda states: stats([c["shadow"]["R"] for c in mine
                                      if c["shadow"] and c["status"] in states])
        result[arm] = {
            "candidates": len(mine), "statuses": dict(Counter(c["status"] for c in mine)),
            "decision_memory_attributed": sum(bool(c["memory_refs"]) for c in decided),
            "legacy_shadow_take_including_rejected": shadow(TAKES),
            "legacy_shadow_explicit_skip": shadow({"skipped"}),
            "legacy_shadow_expired": shadow({"expired"}),
            "unscored_decisions": sum(not c["shadow"] for c in decided),
            "closed_fills": len(closed), "broker_net_usd": sum(t["net"] for t in closed),
            "profit_factor": sum(max(0, t["net"]) for t in closed) / losses if losses else None,
            "open_fills_at_last_sync": sum(t["arm"] == arm and t["net"] is None for t in trades),
            "matched": {"pairs": pairs, "legacy_shadow": stats([p["legacy_shadow_R"] for p in pairs]),
                        "actual_price_after_assumed_cost": stats([p["actual_price_R_assumed_cost"] for p in pairs])},
        }
    records = {"candidates": candidates, "trades": trades, "equity": equity}
    return {"window": {"since": start.isoformat(), "until_exclusive": end.isoformat()}, "arms": result,
            "last_equity_snapshot": equity[-1] if equity else None, "records": records,
            "source_sha256": digest(records),
            "limitations": ["Retrospective audit, not an out-of-sample trial or evidence of edge.",
                            "No broker reconciliation after last journal sync; open fills are last-known state.",
                            "Legacy shadow is bar-close M15 simulation, not executable decision-time return.",
                            "Price R with an additional assumed 0.8-pip cost is distinct from broker net.",
                            "Historical shadow availability timestamps are absent; an earlier --until cannot "
                            "reconstruct which shadows had matured then. This is not point-in-time replay."]}


def write_run(payload: dict, root: Path, *, kind: str, hypothesis_id: str, now=None, status="completed") -> dict:
    """Separate observation-run format; never masquerades as a LEAN strategy backtest."""
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    code = runs.git_info()
    record = {"schema_version": 1, "run_id": run_id, "created_at": now.isoformat(), "kind": kind,
              "hypothesis_id": hypothesis_id, "technical_only": True,
              "reproducible": not code["git_dirty"] and code["git_commit"] is not None,
              "code": code, "status": status, "metrics": payload,
              "artifacts": ["run.json"], "evidence": "observation only; no gate or promotion"}
    schema = runs.REPO / "schemas" / "demo-evaluation.schema.json"
    jsonschema.validate(record, json.loads(schema.read_text(encoding="utf-8")))
    folder = Path(root) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / "run.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    return {"run_id": run_id, "run_dir": str(folder), "reproducible": record["reproducible"], "metrics": payload}

"""Frozen, offline decision comparison. No broker, model API, or trading-loop imports.

Inputs are explicitly supplied point-in-time packets plus ordered bid/ask observations.
Missing inputs/quotes/decisions remain failures, never silently turn into model skips.
"""

import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import runs
from .contracts import SymbolSpec
from .demo_evaluation import digest, stats, timestamp

ARMS = ("rule", "model_no_memory", "model_memory")


def input_hash(op: dict) -> str:
    """Bind identity, contract and information cutoff; exclude future quotes/replies."""
    return digest({key: op[key] for key in ("id", "symbol", "side", "spec", "available_at",
                                          "inputs_as_of", "decision_at", "inputs")})


def number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"invalid {name}")
    return value


def freeze(config_path: Path, output: Path, *, now=None) -> dict:
    """Pin config, prompt and memory text; refuse dirty code and historical windows."""
    now = now or datetime.now(timezone.utc)
    code = runs.git_info()
    if code["git_dirty"] or not code["git_commit"]:
        raise ValueError("freeze requires clean committed code")
    path = Path(config_path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("status") != "reviewed":
        raise ValueError("study config must be reviewed before freezing")
    if not cfg.get("hypothesis_id") or not cfg.get("symbols") or not cfg.get("baseline_pattern"):
        raise ValueError("hypothesis_id, symbols and baseline_pattern are required")
    if not cfg.get("model") or not cfg.get("reviewed_by"):
        raise ValueError("model and reviewed_by are required")
    start, end = timestamp(cfg["start"]), timestamp(cfg["end"])
    if start < now or end <= start:
        raise ValueError("study window must be future and nonempty")
    for key in ("hold_seconds", "max_quote_gap_seconds", "max_entry_delay_seconds", "sl_atr", "min_sl_pips", "tp_rr"):
        number(cfg[key], key, positive=True)
    for key in ("slippage_pips_per_side", "commission_R", "swap_R"):
        number(cfg[key], key)
    number(cfg["max_closed_drawdown_R"], "max_closed_drawdown_R", positive=True)
    number(cfg["min_incremental_daily_R"], "min_incremental_daily_R")
    for key in ("max_open_positions", "min_days", "min_closed_per_arm"):
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if type(cfg["bootstrap_seed"]) is not int:
        raise ValueError("bootstrap_seed must be an integer")
    assets = {}
    for name in ("prompt", "memory"):
        raw = (path.parent / cfg[f"{name}_path"]).read_text(encoding="utf-8")
        assets[name] = {"content": raw, "sha256": digest(raw)}
    memory = json.loads(assets["memory"]["content"])
    if timestamp(memory["as_of"]) > now or not memory["documents"]:
        raise ValueError("memory snapshot must be nonempty and observable before freeze")
    if len({d["id"] for d in memory["documents"]}) != len(memory["documents"]):
        raise ValueError("duplicate memory ids")
    for doc in memory["documents"]:
        if not doc.get("content") or timestamp(doc["available_at"]) > timestamp(memory["as_of"]):
            raise ValueError("memory documents must include text and pre-freeze availability")
    manifest = {"schema_version": 1, "frozen_at": now.isoformat(), "code": code, "config": cfg, "assets": assets}
    manifest["sha256"] = digest(manifest)
    # Exclusive create prevents replacing the hypothesis's existing frozen manifest.
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
    return manifest


def validate_manifest(manifest: dict) -> None:
    if digest({k: v for k, v in manifest.items() if k != "sha256"}) != manifest["sha256"]:
        raise ValueError("frozen manifest hash mismatch")
    for asset in manifest["assets"].values():
        if digest(asset["content"]) != asset["sha256"]:
            raise ValueError("frozen asset hash mismatch")
    code = runs.git_info()
    if code["git_dirty"] or code["git_commit"] != manifest["code"]["git_commit"]:
        raise ValueError("evaluate with the clean frozen code commit")


def replay(op: dict, cfg: dict) -> dict:
    """Sampled quote replay; bid exits longs, ask exits shorts. Gaps fail closed.

    This defines a NEW common fixed-duration policy, not reconstruction of H-003's
    rounded bar-time exit. Zero broker calls; equal initial risk is one normalized R.
    """
    spec = SymbolSpec(**op["spec"])
    if spec.symbol != op["symbol"] or op["side"] not in ("buy", "sell"):
        raise ValueError("symbol/side mismatch")
    pip = number(spec.pip_size, "pip_size", positive=True)
    dist = max(number(op["inputs"]["atr"], "atr", positive=True) * cfg["sl_atr"], cfg["min_sl_pips"] * pip)
    at = timestamp(op["decision_at"])
    quotes = op["quotes"]
    previous = None
    for q in quotes:
        qt = timestamp(q["time"])
        if previous is not None and qt <= previous:
            raise ValueError("quotes must be strictly ordered, without duplicates")
        previous = qt
        if number(q["bid"], "bid", positive=True) > number(q["ask"], "ask", positive=True):
            raise ValueError("crossed quote")
    future = [q for q in quotes if timestamp(q["time"]) >= at]
    if not future:
        return {"status": "missing_entry_quote"}
    first = future[0]
    if (timestamp(first["time"]) - at).total_seconds() > cfg["max_entry_delay_seconds"]:
        return {"status": "late_entry_quote"}
    sign = 1 if op["side"] == "buy" else -1
    slip = cfg["slippage_pips_per_side"] * pip
    entry = first["ask"] + slip if sign == 1 else first["bid"] - slip
    deadline = timestamp(first["time"]) + timedelta(seconds=cfg["hold_seconds"])
    last = timestamp(first["time"])
    for q in future:
        qt = timestamp(q["time"])
        if (qt - last).total_seconds() > cfg["max_quote_gap_seconds"]:
            return {"status": "quote_gap"}
        last = qt
        price = q["bid"] if sign == 1 else q["ask"]
        move = sign * (price - entry)
        # A stop gaps at the observed executable price; a target is credited at
        # its limit only. The same adverse slippage assumption applies at exit.
        if move <= -dist:
            exit_price, reason = price - sign * slip, "stop_loss"
        elif move >= dist * cfg["tp_rr"]:
            exit_price, reason = entry + sign * dist * cfg["tp_rr"] - sign * slip, "take_profit"
        elif qt >= deadline:
            exit_price, reason = price - sign * slip, "time_exit"
        else:
            continue
        return {"status": "closed", "entry_at": first["time"], "exit_at": q["time"],
                "R": sign * (exit_price - entry) / dist - cfg["commission_R"] - cfg["swap_R"],
                "exit_reason": reason}
    return {"status": "unmatured"}


def evaluate(manifest: dict, packet: dict) -> dict:
    validate_manifest(manifest)
    cfg = manifest["config"]
    if packet["manifest_sha256"] != manifest["sha256"]:
        raise ValueError("packet belongs to a different frozen experiment")
    if not packet.get("provenance"):
        raise ValueError("quote provenance required")
    ops = packet["opportunities"]
    if len({op["id"] for op in ops}) != len(ops):
        raise ValueError("duplicate opportunity id")
    memory_ids = {d["id"] for d in json.loads(manifest["assets"]["memory"]["content"])["documents"]}
    rows = []
    for op in sorted(ops, key=lambda o: (timestamp(o["decision_at"]), o["id"])):
        at = timestamp(op["decision_at"])
        if not timestamp(cfg["start"]) <= at < timestamp(cfg["end"]):
            raise ValueError("opportunity outside frozen window")
        if op["symbol"] not in cfg["symbols"]:
            raise ValueError("symbol outside frozen scope")
        if timestamp(op["inputs_as_of"]) > timestamp(op["available_at"]) or timestamp(op["available_at"]) > at:
            raise ValueError("future inputs")
        expected = input_hash(op)
        if set(op["decisions"]) - set(ARMS[1:]):
            raise ValueError("unexpected decision arm")
        for arm in ARMS[1:]:
            dec = op["decisions"].get(arm)
            if dec is None:
                continue  # explicitly counted as missing below
            when = timestamp(dec["at"])
            if not timestamp(op["available_at"]) <= when <= at:
                raise ValueError("decision outside common information/entry cutoff")
            if dec["input_sha256"] != expected or dec["prompt_sha256"] != manifest["assets"]["prompt"]["sha256"]:
                raise ValueError("model inputs/prompt differ from frozen comparison")
            if dec["model"] != cfg["model"] or type(dec["take"]) is not bool:
                raise ValueError("model/take mismatch")
            refs = set(dec["memory_refs"])
            if (arm == "model_no_memory" and refs) or (arm == "model_memory" and refs != memory_ids):
                raise ValueError("memory attribution differs from assigned treatment")
            if arm == "model_memory" and dec.get("memory_sha256") != manifest["assets"]["memory"]["sha256"]:
                raise ValueError("memory snapshot mismatch")
        decisions = {"rule": cfg["baseline_pattern"] in op["inputs"]["patterns_for"]}
        decisions.update({a: op["decisions"][a]["take"] if a in op["decisions"] else None for a in ARMS[1:]})
        rows.append({"id": op["id"], "symbol": op["symbol"], "side": op["side"],
                     "decision_at": op["decision_at"], "decisions": decisions, "replay": replay(op, cfg)})
    by_arm = {}
    for arm in ARMS:
        active, closed, coverage = [], [], Counter()
        for row in rows:
            at = timestamp(row["decision_at"])
            active = [p for p in active if timestamp(p["exit_at"]) > at]
            choice = row["decisions"][arm]
            if choice is None:
                coverage["missing_decision"] += 1
            elif not choice:
                coverage["skip"] += 1
            elif len(active) >= cfg["max_open_positions"] or any(p["symbol"] == row["symbol"] for p in active):
                coverage["exposure_blocked"] += 1
            elif row["replay"]["status"] != "closed":
                coverage[row["replay"]["status"]] += 1
                # Unknown exit must not free exposure for subsequent trades.
                active.append({"symbol": row["symbol"], "exit_at": cfg["end"]})
            else:
                trade = {"id": row["id"], "symbol": row["symbol"], **row["replay"]}
                active.append(trade)
                closed.append(trade)
                coverage["closed"] += 1
        equity, peak, dd = 0.0, 0.0, 0.0
        days = set()
        for trade in sorted(closed, key=lambda t: timestamp(t["exit_at"])):
            equity += trade["R"]
            peak = max(peak, equity)
            dd = max(dd, peak - equity)
            days.add(timestamp(trade["entry_at"]).date().isoformat())
        by_arm[arm] = {"coverage": dict(coverage), "returns": stats([t["R"] for t in closed]),
                       "closed_equity_max_drawdown_R": dd, "entry_days": len(days), "trades": closed}
    # Paired marked-to-close portfolio increments, including zero for explicit skips
    # and exposure blocks. Missing/invalid data disqualify a continuation verdict.
    complete = bool(rows) and all(r["replay"]["status"] == "closed" and
                                  all(v is not None for v in r["decisions"].values()) for r in rows)
    ready = complete and all(a["entry_days"] >= cfg["min_days"] and
                             a["returns"]["n"] >= cfg["min_closed_per_arm"] for a in by_arm.values())
    daily = defaultdict(lambda: dict.fromkeys(ARMS, 0.0))
    for row in rows:
        daily[timestamp(row["decision_at"]).date().isoformat()]
    for arm, data in by_arm.items():
        for trade in data["trades"]:
            daily[timestamp(trade["entry_at"]).date().isoformat()][arm] += trade["R"]
    comparisons = {}
    for left, right in (("model_no_memory", "rule"), ("model_memory", "rule"),
                        ("model_memory", "model_no_memory")):
        diffs = [values[left] - values[right] for _, values in sorted(daily.items())]
        rng = random.Random(cfg["bootstrap_seed"])
        boot = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(2000)) if len(diffs) > 1 else []
        comparisons[f"{left}_minus_{right}"] = {
            "days": len(diffs), "mean_daily_R_difference": sum(diffs) / len(diffs) if diffs else None,
            "exploratory_daily_bootstrap_95pct": [boot[49], boot[1949]] if boot else None}
    criteria = {"positive_net_R": {a: data["returns"]["sum_R"] > 0 for a, data in by_arm.items()},
                "closed_drawdown_within_bound": {a: data["closed_equity_max_drawdown_R"] <= cfg["max_closed_drawdown_R"]
                                                 for a, data in by_arm.items()},
                "incremental_mean_above_minimum": {name: values["mean_daily_R_difference"] is not None and
                                                  values["mean_daily_R_difference"] > cfg["min_incremental_daily_R"]
                                                  for name, values in comparisons.items()}}
    return {"manifest_sha256": manifest["sha256"], "packet_sha256": digest(packet), "arms": by_arm,
            "daily_returns_R": dict(daily), "paired_daily_comparisons": comparisons,
            "frozen_criterion_checks": criteria,
            "opportunities": rows, "coverage_complete": complete, "minimum_sample_met": ready,
            "verdict": "review_required" if ready else "insufficient_evidence",
            "limitations": ["Sampled quote simulation, not broker-confirmed fills or evidence of edge.",
                            "Closed-equity drawdown excludes intratrade unrealized drawdown.",
                            "Declared commission/swap in R and slippage are assumptions, not verified costs.",
                            "Daily bootstrap is exploratory; serial dependence and multiple comparisons remain.",
                            "Input timestamps and memory attribution are assertions by the trusted collector; "
                            "hashes detect changes but cannot prove an LLM did not see other information.",
                            "Coverage is within the supplied packet; collector completeness and source "
                            "provenance require independent review. This evaluator does not collect quotes or invoke models.",
                            "No automatic promotion, policy changes, order sending or resumption."]}

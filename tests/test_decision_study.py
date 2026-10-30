import copy
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from fxra import decision_study as study
from fxra.contracts import SymbolSpec
from fxra.demo_evaluation import digest

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
ENTRY = NOW + timedelta(days=1)
SPEC = asdict(SymbolSpec("EURUSD", "synthetic", 5, .00001, .0001, 100000, .01, 10, .01, "USD"))


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(study.runs, "git_info", lambda: {"git_commit": "frozen-code", "git_dirty": False})
    cfg = {"status": "reviewed", "reviewed_by": "fixture-reviewer", "hypothesis_id": "SYNTHETIC",
           "symbols": ["EURUSD"], "baseline_pattern": "ema50_stretch_fade", "model": "fixture-model",
           "start": ENTRY.isoformat(), "end": (ENTRY + timedelta(days=4)).isoformat(),
           "hold_seconds": 60, "max_quote_gap_seconds": 30, "max_entry_delay_seconds": 5,
           "sl_atr": 1.5, "min_sl_pips": 8, "tp_rr": 1.5, "slippage_pips_per_side": 0,
           "commission_R": .02, "swap_R": 0, "max_open_positions": 1, "min_days": 2,
           "min_closed_per_arm": 2, "bootstrap_seed": 1, "prompt_path": "prompt.txt", "memory_path": "memory.json"}
    cfg.update(max_closed_drawdown_R=5, min_incremental_daily_R=0)
    (tmp_path / "prompt.txt").write_text("Synthetic fixed prompt.")
    (tmp_path / "memory.json").write_text(json.dumps({"as_of": NOW.isoformat(), "documents": [
        {"id": "lesson", "content": "Synthetic lesson.", "available_at": NOW.isoformat()}]}))
    path = tmp_path / "study.json"
    path.write_text(json.dumps(cfg))
    manifest = study.freeze(path, tmp_path / "frozen.json", now=NOW)
    return manifest, path


def opportunity(manifest, *, at=ENTRY, cid="one"):
    cfg = manifest["config"]
    inputs = {"atr": .0004, "patterns_for": [cfg["baseline_pattern"]]}
    decision = {"at": at.isoformat(), "take": True, "input_sha256": digest(inputs),
                "prompt_sha256": manifest["assets"]["prompt"]["sha256"], "model": cfg["model"], "memory_refs": []}
    op = {"id": cid, "symbol": "EURUSD", "side": "buy", "spec": SPEC,
            "available_at": at.isoformat(), "inputs_as_of": at.isoformat(), "decision_at": at.isoformat(),
            "inputs": inputs, "decisions": {"model_no_memory": decision,
                "model_memory": {**decision, "memory_refs": ["lesson"],
                                 "memory_sha256": manifest["assets"]["memory"]["sha256"]}},
            "quotes": [{"time": (at + timedelta(seconds=s)).isoformat(), "bid": b, "ask": b + .0001}
                       for s, b in ((-1, 1.105), (0, 1.1), (20, 1.1004), (40, 1.1008), (60, 1.1009))]}
    for dec in op["decisions"].values():
        dec["input_sha256"] = study.input_hash(op)
    return op


def packet(manifest, *ops):
    return {"manifest_sha256": manifest["sha256"], "provenance": "synthetic fixture, no market evidence",
            "opportunities": list(ops)}


def test_common_executable_entry_ignores_prefill_extremes_and_uses_bid_ask(frozen):
    m, _ = frozen
    op = opportunity(m)
    out = study.replay(op, m["config"])
    assert out["entry_at"] == ENTRY.isoformat() and out["exit_reason"] == "time_exit"
    assert out["R"] == pytest.approx(.98)  # ask 1.1001 -> bid 1.1009; 8-pip risk minus .02R
    op["side"] = "sell"
    sell = study.replay(op, m["config"])
    assert sell["R"] < -1  # gap through stop fills at ask, not idealized stop price


def test_gaps_missing_quotes_and_incomplete_horizons_are_not_losses_or_skips(frozen):
    m, _ = frozen
    op = opportunity(m)
    op["quotes"] = []
    assert study.replay(op, m["config"])["status"] == "missing_entry_quote"
    op = opportunity(m)
    op["quotes"] = op["quotes"][:2]
    assert study.replay(op, m["config"])["status"] == "unmatured"
    op = opportunity(m)
    del op["quotes"][2]
    assert study.replay(op, m["config"])["status"] == "quote_gap"
    op = opportunity(m)
    op["quotes"] = op["quotes"][2:]
    assert study.replay(op, m["config"])["status"] == "late_entry_quote"


@pytest.mark.parametrize("mutation,match", [
    (lambda o: o.update(inputs_as_of=(ENTRY + timedelta(seconds=1)).isoformat()), "future inputs"),
    (lambda o: o["decisions"]["model_no_memory"].update(memory_refs=["lesson"]), "memory attribution"),
    (lambda o: o["decisions"]["model_memory"].update(memory_sha256="changed"), "snapshot mismatch"),
    (lambda o: o["decisions"]["model_memory"].update(input_sha256="changed"), "inputs/prompt"),
    (lambda o: o["decisions"]["model_memory"].update(at=(ENTRY + timedelta(seconds=1)).isoformat()), "cutoff"),
    (lambda o: o["quotes"][1].update(ask=1.0), "crossed"),
    (lambda o: o["quotes"][1].update(bid=float('nan')), "finite"),
])
def test_point_in_time_and_treatment_fail_closed(frozen, mutation, match):
    m, _ = frozen
    op = opportunity(m)
    mutation(op)
    with pytest.raises(ValueError, match=match):
        study.evaluate(m, packet(m, op))


def test_identical_arms_have_zero_incremental_value_and_daily_paired_report(frozen):
    m, _ = frozen
    result = study.evaluate(m, packet(m, opportunity(m), opportunity(m, at=ENTRY + timedelta(days=1), cid="two")))
    assert result["coverage_complete"] and result["minimum_sample_met"]
    assert result["verdict"] == "review_required"  # never a strategy approval
    assert not any(result["frozen_criterion_checks"]["incremental_mean_above_minimum"].values())
    for comparison in result["paired_daily_comparisons"].values():
        assert comparison["mean_daily_R_difference"] == 0
        assert comparison["exploratory_daily_bootstrap_95pct"] == [0, 0]


def test_shared_exposure_and_missing_decisions_are_reported_separately(frozen):
    m, _ = frozen
    first, second = opportunity(m), opportunity(m, at=ENTRY + timedelta(seconds=10), cid="two")
    del second["decisions"]["model_memory"]
    result = study.evaluate(m, packet(m, first, second))
    assert result["arms"]["rule"]["coverage"] == {"closed": 1, "exposure_blocked": 1}
    assert result["arms"]["model_memory"]["coverage"] == {"closed": 1, "missing_decision": 1}
    assert not result["coverage_complete"] and result["verdict"] == "insufficient_evidence"


def test_freeze_hashes_assets_and_refuses_overwrite_historical_windows_and_dirty_code(frozen, tmp_path, monkeypatch):
    m, path = frozen
    with pytest.raises(FileExistsError):
        study.freeze(path, tmp_path / "frozen.json", now=NOW)
    altered = copy.deepcopy(m)
    altered["assets"]["prompt"]["content"] = "changed"
    with pytest.raises(ValueError, match="manifest hash"):
        study.evaluate(altered, packet(altered))
    with pytest.raises(ValueError, match="future"):
        study.freeze(path, tmp_path / "past.json", now=ENTRY + timedelta(seconds=1))
    monkeypatch.setattr(study.runs, "git_info", lambda: {"git_commit": "new-code", "git_dirty": False})
    with pytest.raises(ValueError, match="frozen code"):
        study.evaluate(m, packet(m))
    monkeypatch.setattr(study.runs, "git_info", lambda: {"git_commit": "new-code", "git_dirty": True})
    with pytest.raises(ValueError, match="clean"):
        study.freeze(path, tmp_path / "dirty.json", now=NOW)


def test_duplicate_opportunities_and_quotes_rejected(frozen):
    m, _ = frozen
    op = opportunity(m)
    with pytest.raises(ValueError, match="duplicate opportunity"):
        study.evaluate(m, packet(m, op, op))
    op["quotes"].append(op["quotes"][-1])
    with pytest.raises(ValueError, match="ordered"):
        study.evaluate(m, packet(m, op))


def test_identity_is_bound_but_future_quotes_cannot_affect_input_hash(frozen):
    m, _ = frozen
    op = opportunity(m)
    original = study.input_hash(op)
    op["quotes"][-1]["bid"] += .1
    assert study.input_hash(op) == original
    op["side"] = "sell"
    with pytest.raises(ValueError, match="inputs/prompt"):
        study.evaluate(m, packet(m, op))


def test_cli_keeps_failed_invalid_numeric_trial_without_echoing_packet(frozen, tmp_path, capsys):
    from fxra.cli import main

    m, _ = frozen
    op = opportunity(m)
    op["quotes"][1]["bid"] = float('nan')
    source = tmp_path / "packet.json"
    source.write_text(json.dumps(packet(m, op)))
    assert main(["decision-study", "evaluate", "--manifest", str(tmp_path / "frozen.json"),
                 "--packet", str(source), "--runs-root", str(tmp_path / "runs")]) == 1
    files = list((tmp_path / "runs").glob("*/run.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["status"] == "failed" and "finite" in record["metrics"]["error"]
    assert "quotes" not in capsys.readouterr().out

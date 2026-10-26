"""B-09 report, B-16 walk-forward, B-17 stress. No Docker: lean.* is stubbed."""

import json
from datetime import date

import pytest

from fxra import cli, lean, report, runs

IMG = "quantconnect/lean:18206"
SMOKE = runs.REPO / "lean/TechnicalSmokeTest/research.toml"


@pytest.fixture
def cfg(tmp_path):
    """Smoke config plus an out_of_sample range and a [walk_forward] table."""
    text = SMOKE.read_text() + ("out_of_sample = { start = 2014-05-12, end = 2014-05-14 }\n"
                                "[walk_forward]\nstart = 2014-05-01\nend = 2014-05-15\n"
                                "train_days = 3\ntest_days = 2\nstep_days = 4\n")
    p = tmp_path / "research.toml"
    p.write_text(text)
    return p


@pytest.fixture
def fake_lean(monkeypatch):
    calls = []
    monkeypatch.setattr(lean, "image_ref", lambda: IMG)

    def fake_backtest(project, results, data_dir=None, parameters=None):
        calls.append(parameters)
        return 0
    monkeypatch.setattr(lean, "backtest", fake_backtest)
    return calls


def _args(cfg, root, **kw):
    import argparse
    return argparse.Namespace(config=cfg, runs_root=root, data_dir=None, segment="in_sample", spread=None,
                              slippage_points=0.0, **kw)


def _records(root):
    return report.load_records(root)  # ordered by created_at


def _complete(run_dir):
    """Pretend LEAN produced a summary so finalize_run marks the run completed."""
    (run_dir / "lean").mkdir(exist_ok=True)
    (run_dir / "lean" / "x-summary.json").write_text(json.dumps({"statistics": {"Sharpe Ratio": "1.5"}}))
    return runs.finalize_run(run_dir, ok=True)


def test_walk_forward_windows_edges_and_partial_last_dropped():
    w = runs.walk_forward_windows(date(2014, 5, 1), date(2014, 5, 15), train_days=3, test_days=2, step_days=4)
    assert [x["test"] for x in w] == [{"start": "2014-05-04", "end": "2014-05-05"},
                                      {"start": "2014-05-08", "end": "2014-05-09"},
                                      {"start": "2014-05-12", "end": "2014-05-13"}]
    assert w[0]["train"] == {"start": "2014-05-01", "end": "2014-05-03"}
    # window starting 05-13 would test 05-16..05-17: dropped. Exact fit on the end date is kept.
    assert runs.walk_forward_windows(date(2014, 5, 1), date(2014, 5, 5), 3, 2, 1)[-1]["test"]["end"] == "2014-05-05"
    assert runs.walk_forward_windows(date(2014, 5, 1), date(2014, 5, 4), 3, 2, 1) == []
    with pytest.raises(ValueError):
        runs.walk_forward_windows(date(2014, 5, 1), date(2014, 5, 5), 3, 2, 0)


def test_backtest_in_sample_records_window_and_passes_dates(tmp_path, cfg, fake_lean):
    assert cli.cmd_backtest(_args(cfg, tmp_path / "r")) == 1  # no summary file from the stub -> failed
    (rec,) = _records(tmp_path / "r")
    assert rec["window"] == {"segment": "in_sample", "start": "2014-05-05", "end": "2014-05-09"}
    assert fake_lean == [{"start": "2014-05-05", "end": "2014-05-09"}]


def test_oos_guard_blocks_second_completed_run_but_not_after_failure(tmp_path, cfg, fake_lean):
    root = tmp_path / "r"
    a = _args(cfg, root)
    a.segment = "out_of_sample"
    cli.cmd_backtest(a)  # engine "ran" but no summary: failed, recorded
    (failed,) = _records(root)
    assert failed["status"] == "failed" and failed["window"]["segment"] == "out_of_sample"
    cli.cmd_backtest(a)  # a failed run does not block a retry
    assert len(_records(root)) == 2
    _complete(root / _records(root)[0]["run_id"])  # now one OOS run completed
    with pytest.raises(SystemExit, match="already has a completed out-of-sample run"):
        cli.cmd_backtest(a)
    assert len(_records(root)) == 2  # refused before anything was recorded
    # a different hypothesis or an in-sample run is unaffected
    assert runs.completed_oos_run(root, "OTHER") is None
    cli.cmd_backtest(_args(cfg, root))
    assert len(_records(root)) == 3


def test_walk_forward_one_record_per_window_shared_group(tmp_path, cfg, fake_lean, monkeypatch):
    monkeypatch.setattr(runs, "git_info", lambda cwd=runs.REPO: {"git_commit": "a" * 40, "git_dirty": False})
    cli.cmd_walk_forward(_args(cfg, tmp_path / "r"))
    recs = _records(tmp_path / "r")
    assert len(recs) == 3 and {r["kind"] for r in recs} == {"walk_forward"}
    assert len({r["group_id"] for r in recs}) == 1
    assert all(r["window"]["segment"] == "out_of_sample" and r["reproducible"] for r in recs)
    assert [p["start"] for p in fake_lean] == ["2014-05-04", "2014-05-08", "2014-05-12"]


def test_walk_forward_refuses_dirty_code(tmp_path, cfg, fake_lean, monkeypatch):
    monkeypatch.setattr(runs, "git_info", lambda cwd=runs.REPO: {"git_commit": "a" * 40, "git_dirty": True})
    with pytest.raises(SystemExit, match="dirty"):
        cli.cmd_walk_forward(_args(cfg, tmp_path / "r"))
    assert not (tmp_path / "r").exists() and fake_lean == []


def test_stress_variants_are_in_sample_stress_records(tmp_path, cfg, fake_lean):
    a = _args(cfg, tmp_path / "r")
    a.spread, a.slippage_points = [1.5, 2.0], 3.0
    cli.cmd_stress(a)
    recs = _records(tmp_path / "r")
    assert [r["stress"] for r in recs] == [{"spread_multiplier": 1.5, "slippage_points": 3.0},
                                           {"spread_multiplier": 2.0, "slippage_points": 3.0}]
    assert all(r["kind"] == "stress" and r["window"]["segment"] == "in_sample" for r in recs)
    assert fake_lean[1] == {"start": "2014-05-05", "end": "2014-05-09",
                            "spread_multiplier": 2.0, "slippage_points": 3.0}
    with pytest.raises(SystemExit):
        cli.cmd_stress(_args(cfg, tmp_path / "r"))  # no variant given


def test_old_record_without_new_fields_still_valid(tmp_path):
    rec = json.loads((runs.new_run(SMOKE, tmp_path, IMG) / "run.json").read_text())
    assert not {"window", "stress", "group_id"} & rec.keys()
    runs.validate(rec)


def test_report_groups_counts_trials_and_flags(tmp_path, cfg, fake_lean, monkeypatch):
    root = tmp_path / "r"
    monkeypatch.setattr(runs, "git_info", lambda cwd=runs.REPO: {"git_commit": "a" * 40, "git_dirty": False})
    cli.cmd_backtest(_args(cfg, root))                       # in-sample, failed (no summary)
    oos = _args(cfg, root)
    oos.segment = "out_of_sample"
    cli.cmd_backtest(oos)
    _complete(root / _records(root)[1]["run_id"])            # OOS completed
    runs.new_run(SMOKE, root, IMG)                           # legacy record: no window, status created
    out = report.render(report.load_records(root))
    assert "Trials: 3" in out
    assert out.index("### In-sample") < out.index("### Out-of-sample") < out.index("### No window recorded")
    oos_row = next(l for l in out.splitlines() if "out_of_sample 2014-05-12" in l)
    assert "| 1.5 |" in oos_row                                # Sharpe from run.json
    assert "technical_only" in oos_row                        # smoke test is never evidence
    assert "in_sample" not in oos_row.split("|")[-2]
    assert "failed" in next(l for l in out.splitlines() if "in_sample 2014-05-05" in l).split("|")[-2]
    assert "segment unknown" in next(l for l in out.splitlines() if "n/a (no window)" in l)
    assert "TECH-0001" in out and report.render(report.load_records(root), "nope") == "No runs found.\n"

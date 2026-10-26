import json

import jsonschema
import pytest

from fxra import runs


def test_new_run_record_validates(tmp_path):
    run_dir = runs.new_run(runs.REPO / "lean/TechnicalSmokeTest/research.toml", tmp_path, "quantconnect/lean:18206")
    rec = json.loads((run_dir / "run.json").read_text())
    assert rec["technical_only"] is True
    assert rec["data"]["start"] == "2014-05-05"
    assert rec["split"]["out_of_sample"] is None
    assert len(rec["config"]["sha256"]) == 64
    assert (run_dir / "config.toml").exists()


def test_finalize_without_lean_output_is_failed(tmp_path):
    run_dir = runs.new_run(runs.REPO / "lean/TechnicalSmokeTest/research.toml", tmp_path, "quantconnect/lean:18206")
    assert runs.finalize_run(run_dir, ok=True)["status"] == "failed"


def test_unfilled_template_is_rejected(tmp_path):
    # Placeholders like "<minute|hour|daily>" must be replaced before a run can be recorded.
    with pytest.raises(jsonschema.ValidationError):
        runs.new_run(runs.REPO / "config/research.example.toml", tmp_path, "quantconnect/lean:18206")


def test_schema_rejects_unpinned_engine_image(tmp_path):
    with pytest.raises(jsonschema.ValidationError):
        runs.new_run(runs.REPO / "lean/TechnicalSmokeTest/research.toml", tmp_path, "quantconnect/lean:latest")

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from fxra.mt5_capture import Mt5CaptureAdapter
from fxra.contracts import Resolution

FIXTURE = Path(__file__).parent / "fixtures/mt5_capture/observed.json"


def adapter(tmp_path, mutate=None):
    data = json.loads(FIXTURE.read_text())
    if mutate:
        mutate(data)
    refs = []
    for name in ("first", "last"):
        blob = json.dumps(data[name]).encode()
        p = tmp_path / f"{name}.json"
        p.write_bytes(blob)
        refs.append({"path": str(p), "sha256": hashlib.sha256(blob).hexdigest()})
    return Mt5CaptureAdapter(*refs, pip_size=.0001)


def test_observed_window_only_and_raw_epochs_unchanged(tmp_path):
    a = adapter(tmp_path)
    rows = a.fetch_bars("EURUSD", Resolution.MINUTE, a.start, a.end).to_pylist()
    assert [r["time"].isoformat() for r in rows] == ["2026-01-06T09:01:00+00:00", "2026-01-06T09:02:00+00:00"]
    assert rows[0]["spread_points"] == 10
    assert a.symbol_spec("EURUSD").volume_max == 500
    assert a.provenance()["time_validation"]["historical_dst_verified"] is False
    assert a.provenance()["technical_only"] is True
    with pytest.raises(ValueError, match="outside"):
        a.fetch_bars("EURUSD", Resolution.MINUTE, a.start - timedelta(minutes=1), a.end)
    with pytest.raises(ValueError, match="only observed M1"):
        a.fetch_bars("EURUSD", Resolution.HOUR, a.start, a.end)


@pytest.mark.parametrize("mutation", [
    lambda d: d["last"]["quote_observation"].update(raw_time=1767700982, raw_time_msc=1767700982000),
    lambda d: d["last"]["quote_observation"].update(quote_valid=False),
    lambda d: d["last"]["quote_observation"].update(observed_at_utc="2026-01-06T09:03:03"),
    lambda d: d["last"]["symbol_spec"].update(digits=4),
    lambda d: d["last"].update(environment="live"),
])
def test_bad_calibration_refused(tmp_path, mutation):
    with pytest.raises(ValueError):
        adapter(tmp_path, mutation)


@pytest.mark.parametrize("mutation", [
    lambda d: d["last"]["rows"].pop(),
    lambda d: d["last"]["rows"].append(d["last"]["rows"][0]),
    lambda d: d["last"]["rows"][0].update(close=2),
    lambda d: d["last"]["rows"].reverse(),
])
def test_missing_duplicate_invalid_or_unordered_bars_refused(tmp_path, mutation):
    a = adapter(tmp_path, mutation)
    with pytest.raises(ValueError):
        a.fetch_bars("EURUSD", Resolution.MINUTE, a.start, a.end)


def test_capture_tampering_refused(tmp_path):
    a = adapter(tmp_path)
    refs = a.refs
    Path(refs[1]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        Mt5CaptureAdapter(*refs, pip_size=.0001)


def test_technical_flag_propagates_and_cannot_be_claimed_as_strategy_data(tmp_path):
    from fxra import lean_export, rawstore, runs
    a = adapter(tmp_path)
    table = a.fetch_bars("EURUSD", Resolution.MINUTE, a.start, a.end)
    raw = tmp_path / "raw"
    rawstore.write_partition(raw, "bars", a.source, "EURUSD", Resolution.MINUTE, a.start.date(),
                             table, {"name": "Mt5CaptureAdapter", "version": a.adapter_version}, a.provenance())
    entry = lean_export.export(raw, tmp_path / "lean", a.source, "EURUSD", Resolution.MINUTE,
                               a.start.date(), a.start.date())
    assert entry["technical_only"] is True and rawstore.verify(raw) == []
    cfg = tmp_path / "research.toml"
    text = (runs.REPO / "lean/MT5DataReplay/research.toml").read_text()
    cfg.write_text(text.replace("technical_only = true", "technical_only = false"))
    with pytest.raises(ValueError, match="technical-only data"):
        runs.new_run(cfg, tmp_path / "runs", "test-image", data_export=tmp_path / "lean/export.json")
    assert not (tmp_path / "runs").exists()

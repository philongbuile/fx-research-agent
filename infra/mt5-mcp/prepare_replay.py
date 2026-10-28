"""Prepare an explicitly technical replay from a short live-observed M1 window.

Run with the normal project environment; this script imports no native MT5 SDK.
"""
import argparse
import hashlib
import json
from pathlib import Path

from fxra import lean_export, quality, rawstore
from fxra.contracts import Resolution
from fxra.mt5_capture import Mt5CaptureAdapter


def prepare(first, last, out, raw_root):
    adapter = Mt5CaptureAdapter(first, last, pip_size=.0001)
    table = adapter.fetch_bars("EURUSD", Resolution.MINUTE, adapter.start, adapter.end)
    out = Path(out)
    if out.exists():
        raise ValueError("use a new export directory; previous exports are retained")
    # Only the adapter's observed interval is admitted. Never import whole captures.
    partition = rawstore.write_partition(raw_root, "bars", adapter.source, "EURUSD", Resolution.MINUTE,
                                         adapter.start.date(), table,
                                         {"name": type(adapter).__name__, "version": adapter.adapter_version},
                                         adapter.provenance())
    issues = rawstore.verify(raw_root)
    if issues:
        raise ValueError("raw-store verification failed")
    entry = lean_export.export(raw_root, out, adapter.source, "EURUSD", Resolution.MINUTE,
                               adapter.start.date(), adapter.start.date())
    expected = [{**r, "time": r["time"].isoformat()} for r in table.to_pylist()]
    validation = {"technical_only": True, "source": adapter.source,
                  "expected_bars": expected, "time_validation": adapter.provenance()["time_validation"]}
    check_file = out / "forex/oanda/replay-check.json"
    check_file.write_text(json.dumps(validation, indent=2))
    metadata = json.loads((out / "export.json").read_text())
    metadata["exports"][0]["technical_replay_check"] = {
        "path": "forex/oanda/replay-check.json", "sha256": hashlib.sha256(check_file.read_bytes()).hexdigest()}
    (out / "export.json").write_text(json.dumps(metadata, indent=2))
    rep = quality.report(raw_root, adapter.source, "EURUSD", Resolution.MINUTE,
                         adapter.start.date(), adapter.start.date())
    (out / "quality-day.json").write_text(json.dumps(rep, indent=2))
    (out / "quality-day.md").write_text(quality.to_markdown(rep))
    result = {"partition": str(partition), "export": str(out), "rows": len(expected),
              "observed_start": adapter.start.isoformat(), "observed_end_exclusive": adapter.end.isoformat(),
              "technical_only": True, "full_day_coverage": False, "export_metadata": str(out / "export.json")}
    (out / "preparation.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--first", required=True)
    p.add_argument("--first-sha256", required=True)
    p.add_argument("--last", required=True)
    p.add_argument("--last-sha256", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--raw-root", default="data/raw")
    a = p.parse_args()
    print(json.dumps(prepare({"path": a.first, "sha256": a.first_sha256},
                             {"path": a.last, "sha256": a.last_sha256}, a.out, Path(a.raw_root)), indent=2))

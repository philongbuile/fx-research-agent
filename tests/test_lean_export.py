"""Golden test: raw-store bid bars -> LEAN files must reproduce LEAN's own sample data (B-14)."""

import json
import zipfile
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pytest

from fxra import lean_export, rawstore, runs
from fxra.contracts import BARS_SCHEMA, Resolution, SymbolSpec

FIX = Path(__file__).parent / "fixtures" / "lean_sample"
DAY = date(2014, 5, 5)
SPEC = SymbolSpec("EURUSD", "lean-sample", 5, 0.00001, 0.0001, 100000, 0.01, 100, 0.01, "USD")


def sample(name):
    return [l.split(",") for l in (FIX / name).read_text().splitlines()]


def store(root, rows, res, times, price_side="bid", null_spread=False):
    """Bid side + spread_points from ask close - bid close, as a broker export would give us."""
    table = pa.Table.from_pylist([
        {"time": t, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
         "tick_volume": None,
         "spread_points": None if null_spread else round((float(r[9]) - float(r[4])) / SPEC.point)}
        for t, r in zip(times, rows)], schema=BARS_SCHEMA)
    rawstore.write_partition(root, "bars", "lean-sample", "EURUSD", res, DAY, table, {"name": "test", "version": "1"},
                             {"price_side": price_side, "source_tz": "UTC", "symbol_spec": asdict(SPEC)})


def unzip(path):
    with zipfile.ZipFile(path) as z:
        (name,) = z.namelist()
        return name, [l.split(",") for l in z.read(name).decode().splitlines()]


def midnight():
    return datetime(2014, 5, 5, tzinfo=timezone.utc)


def check_rule(got, want):
    spread = round((float(want[9]) - float(want[4])) / SPEC.point)
    assert got[9] == want[9]  # ask close: spread was derived from it
    for i in (6, 7, 8):  # ask open/high/low: bid + the bar's close spread (documented approximation)
        assert float(got[i]) == pytest.approx(float(got[i - 5]) + spread * SPEC.point, abs=1e-9)


def test_minute_matches_lean_sample(tmp_path):
    rows = sample("20140505_eurusd_minute_quote.csv")
    store(tmp_path / "raw", rows, Resolution.MINUTE, [midnight() + timedelta(milliseconds=int(r[0])) for r in rows])
    out = tmp_path / "lean"
    e = lean_export.export(tmp_path / "raw", out, "lean-sample", "EURUSD", Resolution.MINUTE, DAY, DAY)

    z = out / "forex/oanda/minute/eurusd/20140505_quote.zip"
    name, got = unzip(z)
    assert name == "20140505_eurusd_minute_quote.csv" and len(got) == len(rows) == 40
    for g, w in zip(got, rows):
        assert g[:5] == w[:5]  # ms since midnight (bar open, unshifted) and bid OHLC: exact text
        check_rule(g, w)
    assert any(g[6:9] != w[6:9] for g, w in zip(got, rows))  # sample ask o/h/l is not fully reproducible

    meta = json.loads((out / "export.json").read_text())["exports"]
    assert len(meta) == 1 and meta[0]["manifest_refs"] == e["manifest_refs"] and len(e["manifest_refs"]) == 1
    assert meta[0]["price_side"] == "bid" and meta[0]["spread"]["fixed_spread_points"] is None
    assert meta[0]["lean"]["files"][0]["path"] == "forex/oanda/minute/eurusd/20140505_quote.zip"
    assert meta[0]["lean"]["files"][0]["sha256"] == runs.sha256_file(z)


def test_hour_matches_lean_sample(tmp_path):
    rows = sample("eurusd_hour_20140505.csv")
    times = [datetime.strptime(r[0], "%Y%m%d %H:%M").replace(tzinfo=timezone.utc) for r in rows]
    store(tmp_path / "raw", rows, Resolution.HOUR, times)
    lean_export.export(tmp_path / "raw", tmp_path / "lean", "lean-sample", "EURUSD", Resolution.HOUR, DAY, DAY)
    name, got = unzip(tmp_path / "lean/forex/oanda/hour/eurusd.zip")
    assert name == "eurusd.csv" and len(got) == len(rows)
    for g, w in zip(got, rows):
        assert g[0] == w[0]  # "YYYYMMDD HH:MM", bar open
        assert [float(x) for x in g[1:5]] == [float(x) for x in w[1:5]]  # sample trims zeros: compare values
        assert float(g[9]) == float(w[9])
        for i in (6, 7, 8):
            assert float(g[i]) == pytest.approx(float(g[i - 5]) + float(w[9]) - float(w[4]), abs=1e-9)


def test_mid_side_splits_spread(tmp_path):
    rows = sample("20140505_eurusd_minute_quote.csv")[:1]  # bid close 1.38711, ask close 1.38726: 15 points
    store(tmp_path / "raw", rows, Resolution.MINUTE, [midnight()], price_side="mid")
    lean_export.export(tmp_path / "raw", tmp_path / "lean", "lean-sample", "EURUSD", Resolution.MINUTE, DAY, DAY)
    _, got = unzip(tmp_path / "lean/forex/oanda/minute/eurusd/20140505_quote.zip")
    assert got[0][4] == "1.387035" and got[0][9] == "1.387185"  # 1.38711 -/+ 7.5 points


def test_null_spread_refused_unless_fixed(tmp_path):
    rows = sample("20140505_eurusd_minute_quote.csv")[:2]
    store(tmp_path / "raw", rows, Resolution.MINUTE, [midnight(), midnight() + timedelta(minutes=1)], null_spread=True)
    args = (tmp_path / "raw", tmp_path / "lean", "lean-sample", "EURUSD", Resolution.MINUTE, DAY, DAY)
    with pytest.raises(ValueError, match="null spread_points"):
        lean_export.export(*args)
    e = lean_export.export(*args, fixed_spread_points=12)
    assert e["spread"]["fixed_spread_points"] == 12 and e["spread"]["bars_with_fixed_spread"] == 2
    _, got = unzip(tmp_path / "lean/forex/oanda/minute/eurusd/20140505_quote.zip")
    assert float(got[0][9]) == pytest.approx(float(got[0][4]) + 12 * SPEC.point, abs=1e-9)


def test_run_record_cites_export(tmp_path):
    rows = sample("20140505_eurusd_minute_quote.csv")[:1]
    store(tmp_path / "raw", rows, Resolution.MINUTE, [midnight()])
    e = lean_export.export(tmp_path / "raw", tmp_path / "lean", "lean-sample", "EURUSD", Resolution.MINUTE, DAY, DAY)
    cfg = tmp_path / "research.toml"
    cfg.write_text((runs.REPO / "lean/TechnicalSmokeTest/research.toml").read_text())
    ej = tmp_path / "lean/export.json"
    with pytest.raises(ValueError, match="data.source"):  # config says lean-image-sample
        runs.new_run(cfg, tmp_path / "runs", "quantconnect/lean:18206", data_export=ej)
    assert not (tmp_path / "runs").exists()
    cfg.write_text(cfg.read_text().replace('"lean-image-sample"', '"lean-sample"'))
    rec = json.loads((runs.new_run(cfg, tmp_path / "runs", "quantconnect/lean:18206", data_export=ej)
                      / "run.json").read_text())
    assert rec["data"]["manifests"] == e["manifest_refs"]
    assert rec["data"]["export"]["sha256"] == runs.sha256_file(ej)

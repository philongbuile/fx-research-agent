import json
import shutil
from datetime import date, datetime, timezone

import pyarrow as pa
import pytest

from fxra import rawstore
from fxra.adapters import CsvBarsAdapter
from fxra.contracts import BARS_SCHEMA, Resolution, SymbolSpec

DAY = date(2026, 1, 5)
SPEC = SymbolSpec("SYM", "csv-test", 5, 0.00001, 0.0001, 100000, 0.01, 100, 0.01, "USD")


def bars(*hours):
    return pa.Table.from_pylist([{"time": datetime(2026, 1, 5, h, tzinfo=timezone.utc), "open": 1.1, "high": 1.2,
                                  "low": 1.0, "close": 1.15, "tick_volume": 1, "spread_points": 10} for h in hours],
                                schema=BARS_SCHEMA)


def write(root, table, day=DAY, **kw):
    return rawstore.write_partition(root, "bars", "csv-test", "SYM", Resolution.HOUR, day, table,
                                    {"name": "test", "version": "1"}, {"note": "unit test"}, **kw)


def read(root, day=DAY):
    return rawstore.read_partition(root, "bars", "csv-test", "SYM", Resolution.HOUR, day)


def test_write_verify_and_immutability(tmp_path):
    p = write(tmp_path, bars(0, 1, 2))
    m = json.loads((p / "_manifest.json").read_text())
    assert m["row_count"] == 3 and len(m["sha256"]) == 64 and m["provenance"] == {"note": "unit test"}
    assert p.parent.as_posix().endswith("bars/source=csv-test/symbol=SYM/resolution=hour/date=2026-01-05")
    assert p.name == "batch=" + m["batch_id"]
    assert m["manifest_version"] == 2 and m["supersedes"] is None and m["supersede_reason"] is None
    assert rawstore.verify(tmp_path) == []
    with pytest.raises(rawstore.PartitionExists):
        write(tmp_path, bars(3))


def test_verify_detects_tampering(tmp_path):
    p = write(tmp_path, bars(0))
    (p / "data.parquet").write_bytes(b"tampered")
    assert any("sha256 mismatch" in x for x in rawstore.verify(tmp_path))


def test_supersede_keeps_old_batch_and_reader_returns_latest(tmp_path):
    old = write(tmp_path, bars(0, 1))
    new = write(tmp_path, bars(0, 1, 2), supersede="vendor fix")
    assert (old / "data.parquet").exists()
    m = json.loads((new / "_manifest.json").read_text())
    assert m["supersedes"] == old.name[len("batch="):] and m["supersede_reason"] == "vendor fix"
    assert rawstore.latest_batch(tmp_path, "bars", "csv-test", "SYM", Resolution.HOUR, DAY) == new
    assert read(tmp_path).num_rows == 3
    third = write(tmp_path, bars(5), supersede="again")  # likely same second: ids must still increase
    assert third.name > new.name > old.name and read(tmp_path).num_rows == 1
    assert rawstore.verify(tmp_path) == []


def test_existing_day_needs_supersede_reason(tmp_path):
    write(tmp_path, bars(0))
    with pytest.raises(rawstore.PartitionExists):
        write(tmp_path, bars(1))
    with pytest.raises(ValueError, match="reason"):
        write(tmp_path, bars(1), supersede=" ")


def test_read_detects_sha_mismatch(tmp_path):
    p = write(tmp_path, bars(0))
    (p / "data.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        read(tmp_path)


def test_verify_catches_dangling_supersedes(tmp_path):
    old = write(tmp_path, bars(0))
    write(tmp_path, bars(1), supersede="fix")
    shutil.rmtree(old)
    assert any("supersedes missing batch" in x for x in rawstore.verify(tmp_path))


def test_read_range_skips_missing_day_and_returns_refs(tmp_path):
    write(tmp_path, bars(0, 1))
    d3 = date(2026, 1, 7)
    t = pa.Table.from_pylist([{"time": datetime(2026, 1, 7, 4, tzinfo=timezone.utc), "open": 1.1, "high": 1.2,
                               "low": 1.0, "close": 1.15, "tick_volume": 1, "spread_points": 10}], schema=BARS_SCHEMA)
    write(tmp_path, t, day=d3)
    tbl, refs = rawstore.read_range(tmp_path, "bars", "csv-test", "SYM", Resolution.HOUR, DAY, d3)  # Jan 6 missing
    assert tbl.num_rows == 3 and len(refs) == 2 and all("/batch=" in r["path"] for r in refs)
    empty, none = rawstore.read_range(tmp_path, "bars", "csv-test", "SYM", Resolution.HOUR,
                                      date(2026, 2, 1), date(2026, 2, 3))
    assert empty.num_rows == 0 and none == []


def test_lock_blocks_second_writer(tmp_path):
    final = rawstore.partition_dir(tmp_path, "bars", "csv-test", "SYM", Resolution.HOUR, DAY)
    final.parent.mkdir(parents=True)
    final.with_name(final.name + ".lock").write_text("other host")
    with pytest.raises(rawstore.PartitionLocked):
        write(tmp_path, bars(0))
    assert not final.exists()


@pytest.mark.parametrize("table, msg", [
    (bars(2, 1), "strictly increasing"),
    (bars(1, 1), "strictly increasing"),
])
def test_rejects_bad_rows(tmp_path, table, msg):
    with pytest.raises(ValueError, match=msg):
        write(tmp_path, table)


def test_rejects_rows_outside_day(tmp_path):
    with pytest.raises(ValueError, match="outside UTC day"):
        write(tmp_path, bars(0), day=date(2026, 1, 6))


def test_rejects_unsafe_path_parts(tmp_path):
    with pytest.raises(ValueError):
        rawstore.partition_dir(tmp_path, "bars", "../etc", "SYM", Resolution.HOUR, DAY)


def test_csv_adapter_converts_server_time_to_utc(tmp_path):
    f = tmp_path / "bars.csv"
    f.write_text("time,open,high,low,close,tick_volume,spread\n"
                 "2026.01.05 02:00,1.1,1.2,1.0,1.15,5,12\n"
                 "2026.01.05 03:00,1.15,1.25,1.1,1.2,6,11\n")
    a = CsvBarsAdapter(f, "csv-test", "Etc/GMT-2", "%Y.%m.%d %H:%M", "bid", SPEC)  # Etc/GMT-2 = UTC+2
    t = a.fetch_bars("SYM", Resolution.HOUR, datetime(2026, 1, 5, tzinfo=timezone.utc),
                     datetime(2026, 1, 6, tzinfo=timezone.utc))
    assert [x.hour for x in t.column("time").to_pylist()] == [0, 1]
    assert t.column("spread_points").to_pylist() == [12, 11]
    write(tmp_path, t)
    assert rawstore.verify(tmp_path) == []


def test_csv_adapter_rejects_inconsistent_ohlc(tmp_path):
    f = tmp_path / "bars.csv"
    f.write_text("time,open,high,low,close\n2026.01.05 02:00,1.1,1.0,1.0,1.15\n")
    a = CsvBarsAdapter(f, "csv-test", "UTC", "%Y.%m.%d %H:%M", "bid", SPEC)
    with pytest.raises(ValueError, match="OHLC"):
        a.fetch_bars("SYM", Resolution.HOUR, datetime(2026, 1, 1, tzinfo=timezone.utc),
                     datetime(2026, 2, 1, tzinfo=timezone.utc))

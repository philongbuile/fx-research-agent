import json
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


def write(root, table, day=DAY):
    return rawstore.write_partition(root, "bars", "csv-test", "SYM", Resolution.HOUR, day, table,
                                    {"name": "test", "version": "1"}, {"note": "unit test"})


def test_write_verify_and_immutability(tmp_path):
    p = write(tmp_path, bars(0, 1, 2))
    m = json.loads((p / "_manifest.json").read_text())
    assert m["row_count"] == 3 and len(m["sha256"]) == 64 and m["provenance"] == {"note": "unit test"}
    assert p.as_posix().endswith("bars/source=csv-test/symbol=SYM/resolution=hour/date=2026-01-05")
    assert rawstore.verify(tmp_path) == []
    with pytest.raises(rawstore.PartitionExists):
        write(tmp_path, bars(3))


def test_verify_detects_tampering(tmp_path):
    p = write(tmp_path, bars(0))
    (p / "data.parquet").write_bytes(b"tampered")
    assert any("sha256 mismatch" in x for x in rawstore.verify(tmp_path))


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

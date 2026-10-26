from datetime import date, datetime, timedelta, timezone

import pyarrow as pa

from fxra import quality, rawstore
from fxra.cli import main
from fxra.contracts import BARS_SCHEMA, Resolution

UTC = timezone.utc


def put(root, day, times, res=Resolution.HOUR, spread=10, flat=()):
    rows = [{"time": t, "open": 1.1, "high": 1.1 if t in flat else 1.2, "low": 1.1 if t in flat else 1.0,
             "close": 1.1, "tick_volume": 1, "spread_points": spread(t) if callable(spread) else spread}
            for t in times]
    rawstore.write_partition(root, "bars", "s", "SYM", res, day, pa.Table.from_pylist(rows, schema=BARS_SCHEMA),
                             {"name": "t", "version": "1"}, {})


def grid(day, step, h0=0, h1=24):
    t = datetime(day.year, day.month, day.day, h0, tzinfo=UTC)
    end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=h1)
    while t < end:
        yield t
        t += step


def rep(root, a, b, res=Resolution.HOUR):
    return quality.report(root, "s", "SYM", res, a, b)


def test_full_day_and_hole(tmp_path):
    d1, d2 = date(2026, 1, 6), date(2026, 1, 7)  # Tue, Wed
    hole = (datetime(2026, 1, 7, 10, 0, tzinfo=UTC), datetime(2026, 1, 7, 10, 15, tzinfo=UTC))
    put(tmp_path, d1, grid(d1, timedelta(minutes=1)), Resolution.MINUTE)
    put(tmp_path, d2, [t for t in grid(d2, timedelta(minutes=1)) if not hole[0] <= t < hole[1]], Resolution.MINUTE)
    r = rep(tmp_path, d1, d2, Resolution.MINUTE)
    a, b = r["days"]
    assert (a["rows"], a["expected"], a["missing"], a["longest_gap_bars"]) == (1440, 1440, 0, 0)
    assert (b["missing"], b["longest_gap_bars"], b["longest_gap_start"]) == (15, 15, "2026-01-07T10:00:00Z")
    assert r["summary"]["missing_pct"] == round(100 * 15 / 2880, 3)
    assert "Holidays are not modelled" in quality.to_markdown(r)


def test_dst_change_week(tmp_path):
    sun_edt, sun_est = date(2026, 10, 25), date(2026, 11, 1)  # DST ends Sun Nov 1 2026
    for d in (sun_edt, sun_est):
        put(tmp_path, d, grid(d, timedelta(hours=1), 21))  # 21..23 UTC
    r = rep(tmp_path, sun_edt, sun_est)
    by = {d["date"]: d for d in r["days"]}
    assert by["2026-10-25"]["expected"] == 3 and by["2026-11-01"]["expected"] == 2
    assert by["2026-11-01"]["outside_session"] == 1  # the 21:00 bar is before the 22:00 UTC open
    assert by["2026-11-01"]["missing"] == 0
    cal = quality.sessions(sun_est, sun_est, "Sun 17:00", "Fri 17:00", "America/New_York")
    assert any(o == datetime(2026, 11, 1, 22, tzinfo=UTC) for o, _ in cal)


def test_spread_flat_and_weekend_boundary(tmp_path):
    fri, sun = date(2026, 1, 9), date(2026, 1, 11)  # EST: close Fri 22:00 UTC, open Sun 22:00 UTC
    put(tmp_path, fri, grid(fri, timedelta(hours=1), 0, 22), spread=lambda t: t.hour,
        flat={datetime(2026, 1, 9, 3, tzinfo=UTC)})
    put(tmp_path, sun, grid(sun, timedelta(hours=1), 23))  # first bar 23:00, an hour late
    r = rep(tmp_path, fri, sun)
    f = r["days"][0]
    assert f["flat_bars"] == 1 and f["spread_points"]["null"] == 0
    assert f["spread_points"]["max"] == 21 and f["spread_points"]["p50"] == 10
    assert next(h for h in r["spread_by_utc_hour"] if h["hour_utc"] == 5)["p50"] == 5
    (b,) = r["weekend_boundaries"]
    assert b["last_before"] == "2026-01-09T21:00:00Z" and b["last_before_offset_min"] == 0
    assert b["first_after"] == "2026-01-11T23:00:00Z" and b["first_after_offset_min"] == 60


def test_null_spread_and_missing_day_and_cli(tmp_path, capsys):
    d = date(2026, 1, 6)
    put(tmp_path, d, grid(d, timedelta(hours=1)), spread=None)
    r = rep(tmp_path, d, d + timedelta(days=1))
    assert r["days"][0]["spread_points"]["null"] == 24 and r["days"][0]["spread_points"]["p50"] is None
    assert r["summary"]["days_with_no_data"] == ["2026-01-07"]
    assert r["days"][1]["longest_gap_bars"] == 24
    rc = main(["quality", "--root", str(tmp_path), "--source", "s", "--symbol", "SYM", "--resolution", "hour",
               "--start", "2026-01-06", "--end", "2026-01-07", "--json"])
    assert rc == 0 and '"days_with_no_data"' in capsys.readouterr().out

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from fxra.contracts import BARS_SCHEMA
from fxra.hour_bars import aggregate_hours

START = datetime(2026, 1, 6, 23, tzinfo=timezone.utc)


def rows(n=120):
    return [{"time": START + timedelta(minutes=i), "open": 1.1,
             "high": 1.2 + i / 10000, "low": 1.0 - i / 10000,
             "close": 1.15 + i / 10000, "tick_volume": i,
             "spread_points": 10} for i in range(n)]


def aggregate(data, **kwargs):
    params = dict(start=START, end=START + timedelta(hours=2),
                  available_at=START + timedelta(hours=2))
    params.update(kwargs)
    return aggregate_hours(pa.Table.from_pylist(data, schema=BARS_SCHEMA), **params)


def test_utc_midnight_ohlc_volume_and_no_invented_spread():
    result = aggregate(rows())
    a, b = result.bars.to_pylist()
    assert not result.missing_minutes
    assert a == {"time": START, "open": 1.1, "high": 1.2059, "low": .9941,
                 "close": 1.1559, "tick_volume": sum(range(60)), "spread_points": None}
    assert b["time"] == START + timedelta(hours=1)
    assert b["close"] == 1.15 + 119 / 10000


def test_missing_and_completely_absent_hours_are_explicit():
    data = rows(60)
    missing = data.pop(30)["time"]
    result = aggregate(data)
    assert result.bars.num_rows == 0
    assert result.missing_minutes[START] == (missing,)
    assert len(result.missing_minutes[START + timedelta(hours=1)]) == 60


def test_null_volume_is_not_replaced_with_zero():
    data = rows()
    data[15]["tick_volume"] = None
    assert aggregate(data).bars.to_pylist()[0]["tick_volume"] is None


@pytest.mark.parametrize("mutate", [
    lambda r: r.reverse(),
    lambda r: r.insert(1, dict(r[0])),
    lambda r: r[0].update(time=START - timedelta(minutes=1)),
    lambda r: r[0].update(time=START + timedelta(seconds=1)),
    lambda r: r[0].update(high=1.01),
    lambda r: r[0].update(close=float("nan")),
    lambda r: r[0].update(low=None),
    lambda r: r[0].update(tick_volume=-1),
    lambda r: r[0].update(spread_points=-1),
])
def test_bad_input_is_rejected_without_repair(mutate):
    data = rows()
    mutate(data)
    with pytest.raises(ValueError):
        aggregate(data)


@pytest.mark.parametrize("override", [
    {"available_at": START + timedelta(hours=2, microseconds=-1)},
    {"start": START.replace(tzinfo=None)},
    {"start": START + timedelta(minutes=1)},
    {"end": START},
])
def test_closed_utc_hour_boundaries_required(override):
    with pytest.raises(ValueError):
        aggregate(rows(), **override)


def test_wrong_schema_rejected():
    with pytest.raises(ValueError, match="schema"):
        aggregate_hours(pa.table({"close": [1.1]}), start=START,
                        end=START + timedelta(hours=1), available_at=START + timedelta(hours=1))

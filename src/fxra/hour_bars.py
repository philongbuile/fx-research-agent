"""Pure M1-to-H1 signal aggregation; never certifies source time or fill prices."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyarrow as pa

from .contracts import BARS_SCHEMA


@dataclass(frozen=True)
class HourBars:
    bars: pa.Table
    # Includes completely absent hours: callers must not bridge these in a signal.
    missing_minutes: dict[datetime, tuple[datetime, ...]]


def aggregate_hours(minutes: pa.Table, *, start: datetime, end: datetime,
                    available_at: datetime) -> HourBars:
    """Aggregate closed UTC hours in [start, end), without sorting or filling.

    The caller supplies already verified single-source/symbol/price-side M1 data.
    This in-memory transformation neither writes raw partitions nor changes their
    provenance or technical-only status. Hour spreads are deliberately unknown;
    minute minima cannot represent executable hourly bid/ask prices.
    """
    for t in (start, end, available_at):
        if t.tzinfo is None or t.utcoffset() != timedelta(0):
            raise ValueError("aware UTC boundaries required")
    if any(t.minute or t.second or t.microsecond for t in (start, end)) or end <= start:
        raise ValueError("nonempty hour-aligned range required")
    if available_at < end:
        raise ValueError("unfinished hour requested")
    if not minutes.schema.equals(BARS_SCHEMA, check_metadata=False):
        raise ValueError("canonical M1 bars schema required")
    rows = minutes.to_pylist()
    previous = None
    by_time = {}
    for row in rows:
        t = row["time"]
        if t is None or not start <= t < end or t.second or t.microsecond:
            raise ValueError("minute outside range or not minute-aligned")
        if previous is not None and t <= previous:
            raise ValueError("duplicate or unordered minutes")
        previous = t
        prices = [row[k] for k in ("open", "high", "low", "close")]
        if any(p is None or not math.isfinite(p) or p <= 0 for p in prices):
            raise ValueError("invalid minute prices")
        if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("invalid minute OHLC")
        if any(row[k] is not None and row[k] < 0 for k in ("tick_volume", "spread_points")):
            raise ValueError("negative minute volume or spread")
        by_time[t] = row
    output, missing = [], {}
    hour = start
    while hour < end:
        expected = [hour + timedelta(minutes=i) for i in range(60)]
        absent = tuple(t for t in expected if t not in by_time)
        if absent:
            missing[hour] = absent
        else:
            group = [by_time[t] for t in expected]
            volumes = [r["tick_volume"] for r in group]
            output.append({"time": hour, "open": group[0]["open"],
                           "high": max(r["high"] for r in group),
                           "low": min(r["low"] for r in group),
                           "close": group[-1]["close"],
                           "tick_volume": None if None in volumes else sum(volumes),
                           "spread_points": None})
        hour += timedelta(hours=1)
    return HourBars(pa.Table.from_pylist(output, schema=BARS_SCHEMA), missing)

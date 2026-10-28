"""H-001 closed-bar indicator calculation, without orders, sizing or fills."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pyarrow as pa

from .hour_bars import aggregate_hours


@dataclass(frozen=True)
class BreakoutSignal:
    available_at: datetime
    side: str | None
    atr: float
    long_exit_below: float
    short_exit_above: float


def evaluate(minutes: pa.Table, *, signal_open: datetime,
             available_at: datetime) -> BreakoutSignal | None:
    """Evaluate the PR23 24/14/10 proposal on exactly 25 complete UTC hours.

    No signal is available before the last hour closes. Missing hours suppress
    the entire evaluation, including exit thresholds: no gap is bridged. Callers
    must preserve existing stops and flag uncertain execution independently.
    """
    end = signal_open + timedelta(hours=1)
    result = aggregate_hours(minutes, start=signal_open - timedelta(hours=24),
                             end=end, available_at=available_at)
    if result.missing_minutes:
        return None
    bars = result.bars.to_pylist()
    prior, current = bars[:-1], bars[-1]
    upper = max(r["high"] for r in prior)
    lower = min(r["low"] for r in prior)
    side = "buy" if current["close"] > upper else "sell" if current["close"] < lower else None
    ranges = [max(bars[i]["high"] - bars[i]["low"],
                  abs(bars[i]["high"] - bars[i - 1]["close"]),
                  abs(bars[i]["low"] - bars[i - 1]["close"]))
              for i in range(len(bars) - 14, len(bars))]
    return BreakoutSignal(end, side, sum(ranges) / 14,
                          min(r["low"] for r in prior[-10:]),
                          max(r["high"] for r in prior[-10:]))

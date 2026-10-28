"""Fixture-tested replay adapter for a short, contemporaneously observed MT5 window.

This deliberately does not calibrate historical DST. Two fresh, advancing quote
anchors bound a short intraday window. Everything outside that window is refused.
"""

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from .contracts import BARS_SCHEMA, Resolution, SymbolSpec

UTC = timezone.utc


def _load(ref):
    path = Path(ref["path"])
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != ref["sha256"]:
        raise ValueError("capture checksum mismatch")
    return json.loads(blob)


def _anchor(capture):
    q = capture["quote_observation"]
    observed = datetime.fromisoformat(q["observed_at_utc"])
    if observed.utcoffset() != timedelta(0) or not q["quote_valid"]:
        raise ValueError("anchor requires a valid quote and aware UTC observation")
    if not (math.isfinite(q["bid"]) and math.isfinite(q["ask"]) and 0 < q["bid"] <= q["ask"]):
        raise ValueError("invalid anchor prices")
    epoch = q["raw_time_msc"] / 1000
    if abs(epoch - q["raw_time"]) >= 1:
        raise ValueError("inconsistent anchor epochs")
    offset = round((epoch - observed.timestamp()) / 60)
    age = observed.timestamp() - (epoch - offset * 60)
    if abs(offset) > 14 * 60 or not -1 <= age <= 10:
        raise ValueError("anchor not fresh at an integral-minute offset")
    return observed, epoch, offset


class Mt5CaptureAdapter:
    """Research-only M1 adapter, limited to the live-observed interval, not history."""

    source = "mt5-demo-1-observed"
    adapter_version = "mt5-observed-window-1"

    def __init__(self, first_ref: dict, last_ref: dict, *, pip_size: float):
        first, last = _load(first_ref), _load(last_ref)
        if any(c.get("environment") != "demo" or c.get("symbol") != "EURUSD"
               or c.get("resolution") != "minute" or c.get("source") != "mt5-demo-1"
               for c in (first, last)):
            raise ValueError("unexpected capture scope")
        if first["symbol_spec"] != last["symbol_spec"]:
            raise ValueError("symbol contract changed within observation")
        a, raw_a, offset = _anchor(first)
        b, raw_b, other_offset = _anchor(last)
        if not timedelta(minutes=2) <= b - a <= timedelta(hours=1):
            raise ValueError("observation must span 2 minutes to 1 hour")
        if offset != other_offset or abs((raw_b - raw_a) - (b - a).total_seconds()) > 10:
            raise ValueError("clock offset changed or quote clock failed to advance")
        # Do not cross midnight or infer weekend/holiday/DST rules.
        if a.date() != b.date() or a.weekday() >= 5:
            raise ValueError("only a same-day weekday observation is supported")
        self.start = a.replace(second=0, microsecond=0) + timedelta(minutes=1)
        self.end = b.replace(second=0, microsecond=0)
        if self.end <= self.start:
            raise ValueError("no fully observed minute bars")
        self.offset = offset
        self.refs = [dict(first_ref), dict(last_ref)]
        self.capture = last
        s = last["symbol_spec"]
        if s["chart_mode"] != 0 or not math.isfinite(pip_size) or pip_size <= 0:
            raise ValueError("bid bars and an explicit positive pip convention required")
        self.spec = SymbolSpec("EURUSD", self.source, s["digits"], s["point"], pip_size,
                               s["trade_contract_size"], s["volume_min"], s["volume_max"],
                               s["volume_step"], s["currency_profit"])

    def symbol_spec(self, symbol):
        if symbol != self.spec.symbol:
            raise ValueError("symbol outside capture scope")
        return self.spec

    def fetch_bars(self, symbol, resolution, start, end):
        self.symbol_spec(symbol)
        if Resolution(resolution) != Resolution.MINUTE:
            raise ValueError("only observed M1 bars supported")
        if start.tzinfo is None or end.tzinfo is None or not self.start <= start < end <= self.end:
            raise ValueError("requested range outside verified observation window")
        rows = []
        for r in self.capture["rows"]:
            stamp = datetime.fromtimestamp(r["time"], UTC) - timedelta(minutes=self.offset)
            if not start <= stamp < end:
                continue
            prices = [r[k] for k in ("open", "high", "low", "close")]
            if (any(not math.isfinite(p) or p <= 0 for p in prices)
                    or r["low"] > min(r["open"], r["close"])
                    or r["high"] < max(r["open"], r["close"])
                    or r["spread"] < 0 or r["tick_volume"] < 0
                    or stamp.second or stamp.microsecond):
                raise ValueError("invalid captured bar")
            rows.append({"time": stamp, **{k: r[k] for k in ("open", "high", "low", "close")},
                         "tick_volume": r["tick_volume"], "spread_points": r["spread"]})
        expected = int((end - start).total_seconds() / 60)
        if (len(rows) != expected or not rows or rows[0]["time"] != start
                or any(b["time"] - a["time"] != timedelta(minutes=1) for a, b in zip(rows, rows[1:]))):
            raise ValueError("missing, duplicate or unordered observed bars; no filling permitted")
        return pa.Table.from_pylist(rows, schema=BARS_SCHEMA)

    def provenance(self):
        return {"price_side": "bid", "source_tz": f"observed fixed offset {self.offset:+d} minutes",
                "symbol_spec": asdict(self.spec), "capture_refs": self.refs,
                "time_validation": {"scope": "contemporaneous_observed_window_only",
                                    "start": self.start.isoformat(), "end_exclusive": self.end.isoformat(),
                                    "offset_minutes": self.offset, "historical_dst_verified": False},
                "calendar": "partial intraday weekday window; no holiday/weekend model inferred",
                "spread_semantics": "MT5 minimum minute spread; reconstructed ask OHLC is approximate",
                "pip_convention": "explicit caller convention; not inferred from an account",
                "technical_only": True}

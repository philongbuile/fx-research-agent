"""Adapter interfaces.

Market data adapters read from a source and return tables in contracts.py shapes.
Execution adapters send orders. Phase 0 ships no live execution adapter: the
only implementation is DryRunExecution, which records and never sends.
"""

import csv
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import pyarrow as pa

from .contracts import BARS_SCHEMA, OrderIntent, Resolution, SymbolSpec
from .risk import RiskDecision


class MarketDataAdapter(Protocol):
    source: str  # stable id written into every manifest, e.g. "csv-import", "mt5:<broker-server>"
    adapter_version: str

    def fetch_bars(self, symbol: str, resolution: Resolution, start: datetime, end: datetime) -> pa.Table:
        """Bars with time in [start, end), UTC, BARS_SCHEMA, sorted, no duplicate times."""
        ...

    def symbol_spec(self, symbol: str) -> SymbolSpec: ...

    def provenance(self) -> dict:
        """Facts needed to reproduce the fetch: source timezone, price side, terminal/build, query."""
        ...


class ExecutionAdapter(Protocol):
    environment: str  # paper | demo | live

    def submit(self, intent: OrderIntent, decision: RiskDecision) -> str:
        """Send one order. Must refuse when decision is not approved. Returns broker order id."""
        ...


class CsvBarsAdapter:
    """Reads bars from a CSV export (e.g. MT5 "Export bars"). Broker-neutral.

    Expected columns (header names configurable): time, open, high, low, close,
    optional tick_volume, spread. `source_tz` is the timezone of the CSV time
    column; MT5 exports use broker server time, which is often not UTC.
    """

    adapter_version = "csv-1"

    def __init__(self, path: Path, source: str, source_tz: str, time_format: str, price_side: str,
                 spec: SymbolSpec, columns: dict[str, str] | None = None):
        self.path, self.source, self.source_tz = Path(path), source, source_tz
        self.time_format, self.price_side, self.spec = time_format, price_side, spec
        self.columns = {k: k for k in ("time", "open", "high", "low", "close", "tick_volume", "spread")} | (columns or {})

    def fetch_bars(self, symbol: str, resolution: Resolution, start: datetime, end: datetime) -> pa.Table:
        if symbol != self.spec.symbol:
            raise ValueError(f"adapter holds {self.spec.symbol}, asked for {symbol}")
        tz, c = ZoneInfo(self.source_tz), self.columns
        rows: dict[datetime, dict] = {}
        with self.path.open(newline="") as f:
            for r in csv.DictReader(f):
                t = datetime.strptime(r[c["time"]], self.time_format).replace(tzinfo=tz).astimezone(timezone.utc)
                if not (start <= t < end):
                    continue
                if t in rows:
                    raise ValueError(f"duplicate bar time {t.isoformat()} in {self.path}")
                o, h, l, cl = (float(r[c[k]]) for k in ("open", "high", "low", "close"))
                if not (l <= min(o, cl) and h >= max(o, cl)):
                    raise ValueError(f"OHLC inconsistent at {t.isoformat()}")
                rows[t] = {"time": t, "open": o, "high": h, "low": l, "close": cl,
                           "tick_volume": int(r[c["tick_volume"]]) if r.get(c["tick_volume"]) else None,
                           "spread_points": int(r[c["spread"]]) if r.get(c["spread"]) else None}
        return pa.Table.from_pylist([rows[t] for t in sorted(rows)], schema=BARS_SCHEMA)

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        return self.spec

    def provenance(self) -> dict:
        return {"file": self.path.name, "source_tz": self.source_tz, "price_side": self.price_side,
                "time_format": self.time_format, "columns": self.columns, "symbol_spec": asdict(self.spec)}


class DryRunExecution:
    """Records approved intents to a JSONL journal. Sends nothing to any broker."""

    environment = "dry_run"

    def __init__(self, journal: Path):
        self.journal = Path(journal)

    def submit(self, intent: OrderIntent, decision: RiskDecision) -> str:
        if not decision.approved:
            raise PermissionError(f"risk gate rejected {intent.client_order_id}: {decision.reasons}")
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a") as f:
            f.write(json.dumps({"intent": asdict(intent), "decision": decision.to_dict()}) + "\n")
        return f"dry-run:{intent.client_order_id}"


def day_range(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)

"""Canonical data contracts. docs/data-contracts.md describes the same shapes in prose.

All timestamps are UTC. Adapters convert broker/server time to UTC before
anything is written, and record the source timezone in the manifest.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pyarrow as pa

UTC_TS = pa.timestamp("us", tz="UTC")


class Resolution(StrEnum):
    TICK = "tick"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAILY = "daily"


# Bar timestamp = bar OPEN time (UTC). LEAN files use the open time too (LEAN derives
# EndTime = Time + period), so the LEAN export does not shift (D-08). Prices are mid
# unless price_side says otherwise.
BARS_SCHEMA = pa.schema(
    [
        pa.field("time", UTC_TS, nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("tick_volume", pa.int64()),
        pa.field("spread_points", pa.int32()),  # broker spread at bar, in points (MT5 "spread"), if provided
    ],
    metadata={"price_side": "declared-in-manifest"},
)

QUOTES_SCHEMA = pa.schema(
    [
        pa.field("time", UTC_TS, nullable=False),
        pa.field("bid", pa.float64(), nullable=False),
        pa.field("ask", pa.float64(), nullable=False),
        pa.field("bid_size", pa.float64()),
        pa.field("ask_size", pa.float64()),
    ]
)

# One row per symbol per observation day. Swap values change; they are data, not config.
SWAPS_SCHEMA = pa.schema(
    [
        pa.field("observed_at", UTC_TS, nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("swap_long", pa.float64(), nullable=False),
        pa.field("swap_short", pa.float64(), nullable=False),
        pa.field("swap_mode", pa.string(), nullable=False),  # e.g. points / account currency / percent; broker-defined
        pa.field("triple_swap_weekday", pa.int8()),  # 0=Mon .. 6=Sun
    ]
)

# Fills from backtest, paper or demo. Never mixed: `environment` is mandatory.
FILLS_SCHEMA = pa.schema(
    [
        pa.field("time", UTC_TS, nullable=False),
        pa.field("environment", pa.string(), nullable=False),  # backtest | paper | demo | live
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("client_order_id", pa.string(), nullable=False),
        pa.field("broker_order_id", pa.string()),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("side", pa.string(), nullable=False),  # buy | sell
        pa.field("quantity_lots", pa.float64(), nullable=False),
        pa.field("price", pa.float64(), nullable=False),
        pa.field("commission", pa.float64()),
        pa.field("swap", pa.float64()),
        pa.field("slippage_points", pa.float64()),
    ]
)

SCHEMAS = {"bars": BARS_SCHEMA, "quotes": QUOTES_SCHEMA, "swaps": SWAPS_SCHEMA, "fills": FILLS_SCHEMA}


@dataclass(frozen=True)
class SymbolSpec:
    """Contract facts for one symbol at one broker. Different brokers differ; never share across sources."""

    symbol: str
    source: str
    digits: int  # price decimals, e.g. 5 for EURUSD at many brokers, 3 for JPY pairs
    point: float  # smallest price increment, 10**-digits
    pip_size: float  # 10 * point for 5/3-digit quotes; stated explicitly, never derived silently
    contract_size: float  # units of base currency per 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    quote_currency: str


@dataclass(frozen=True)
class Quote:
    symbol: str
    time: datetime
    bid: float
    ask: float


@dataclass(frozen=True)
class AccountSnapshot:
    time: datetime
    currency: str
    equity: float
    day_start_equity: float
    open_positions: int


@dataclass(frozen=True)
class OrderIntent:
    """What a strategy wants. Not an order: it must pass the risk gate first."""

    client_order_id: str  # idempotency key; the same id is never submitted twice
    strategy_id: str
    strategy_version: str  # git commit of the reviewed strategy code
    symbol: str
    side: str  # buy | sell
    quantity_lots: float
    order_type: str  # market | limit | stop
    stop_loss: float | None = None
    take_profit: float | None = None
    limit_price: float | None = None

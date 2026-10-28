"""Local, market-data-only MCP profile. Optional dependencies live outside fxra.

Uses the reviewed upstream MT5Client for terminal lifecycle, not its all-tools
server, credentials, account resources, order helpers or time conversions.
Captures are quarantined: raw MT5 epochs are NOT asserted to be UTC.
"""

import argparse
import hashlib
import json
import logging
import math
import threading
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

UPSTREAM_COMMIT = "daa905d36135aa1f00017efccc9b39fba7130c3f"
TOOLS = {"mt5_data_status", "mt5_symbol_spec", "mt5_quote", "mt5_capture_bars"}
MAX_BARS = 10000


class ReadOnlyBackend:
    """Deny-by-default native API facade; never exposed as an MCP argument.

    This is an application boundary, not a sandbox against arbitrary host Python.
    Identity is kept privately in RAM and checked before AND after each read.
    """

    _reads = frozenset({"symbol_info", "symbol_info_tick", "symbols_get", "copy_rates_from_pos", "version"})

    def __init__(self, native, terminal_path):
        self.__native = native
        self.__terminal = Path(terminal_path).resolve()
        self.__identity = None
        self.__lock = threading.RLock()

    def __getattr__(self, name):
        if name == "TIMEFRAME_M1":
            return self.__native.TIMEFRAME_M1
        if name not in self._reads:
            raise PermissionError("MT5_OPERATION_UNAVAILABLE")

        def read(*args, **kwargs):
            with self.__lock:
                self.check()
                try:
                    result = getattr(self.__native, name)(*args, **kwargs)
                except Exception:
                    raise RuntimeError("MT5_READ_FAILED") from None
                self.check()
                return result
        return read

    def check(self):
        try:
            info = self.__native.terminal_info()
            account = self.__native.account_info()
            if info is None or account is None or not info.connected:
                raise RuntimeError
            if Path(info.path).resolve() != self.__terminal.parent:
                raise RuntimeError
            if account.trade_mode != self.__native.ACCOUNT_TRADE_MODE_DEMO:
                raise RuntimeError
            identity = (account.login, account.server)
            if not isinstance(account.login, int) or account.login <= 0 or not account.server:
                raise RuntimeError
            if self.__identity is not None and identity != self.__identity:
                raise RuntimeError
        except Exception:
            raise RuntimeError("MT5_DEMO_BINDING_UNAVAILABLE") from None
        self.__identity = identity

    def initialize(self, *args, **kwargs):
        with self.__lock:
            if args != (str(self.__terminal),) or kwargs:
                raise PermissionError("MT5_INITIALIZE_REQUIRES_BOUND_PATH")
            try:
                ok = self.__native.initialize(str(self.__terminal), timeout=10000)
                if not ok:
                    return False
                self.check()
                return True
            except Exception:
                raise RuntimeError("MT5_DEMO_BINDING_UNAVAILABLE") from None

    def terminal_info(self):
        self.check()
        return SimpleNamespace(connected=True)  # No title, path, account or inferred timestamp.

    def last_error(self):
        return (0, "redacted")  # Never copy raw terminal errors/identifiers.

    def shutdown(self):
        self.__native.shutdown()  # Releases this process's IPC; does not close the terminal.


def inspect_bars(rows):
    """Structural checks only; a naive epoch grid is not a verified session calendar."""
    times = [r["time"] for r in rows]
    invalid = sum(
        not all(math.isfinite(float(r[k])) and float(r[k]) > 0 for k in ("open", "high", "low", "close"))
        or r["low"] > min(r["open"], r["close"])
        or r["high"] < max(r["open"], r["close"])
        or r["low"] > r["high"] or r["spread"] < 0
        or r["tick_volume"] < 0 or r["real_volume"] < 0
        for r in rows
    )
    return {"rows": len(rows), "duplicate_times": len(times) - len(set(times)),
            "strictly_increasing": all(a < b for a, b in zip(times, times[1:])),
            "minute_aligned": all(t % 60 == 0 for t in times), "invalid_bars": invalid,
            "intervals_over_one_minute": sum(b - a > 60 for a, b in zip(times, times[1:])),
            "session_quality": "pending_timezone_and_calendar_verification"}


class MarketService:
    def __init__(self, backend, client, captures):
        self.backend, self.client, self.captures = backend, client, Path(captures)
        self.lock = threading.RLock()

    def _ready(self):
        self.client.connect()
        self.backend.check()

    def status(self):
        with self.lock:
            self._ready()
            return {"connected": True, "environment": "demo", "account_alias": "demo-1",
                    "profile": "market_data_only", "execution": "unavailable",
                    "upstream_commit": UPSTREAM_COMMIT, "timestamp_status": "unverified"}

    def spec(self, symbol):
        if symbol != "EURUSD":
            raise ValueError("SYMBOL_OUTSIDE_RESEARCH_SCOPE")
        with self.lock:
            self._ready()
            raw = self.backend.symbol_info(symbol)
            if raw is None:
                raise ValueError("SYMBOL_UNAVAILABLE")
            fields = ("name", "digits", "point", "trade_contract_size", "volume_min", "volume_max",
                      "volume_step", "currency_base", "currency_profit", "currency_margin", "chart_mode",
                      "spread_float", "swap_mode", "swap_long", "swap_short", "swap_rollover3days")
            return {k: getattr(raw, k) for k in fields}

    def quote(self, symbol):
        self.spec(symbol)
        with self.lock:
            raw = self.backend.symbol_info_tick(symbol)
            if raw is None:
                raise ValueError("QUOTE_UNAVAILABLE")
            bid, ask = float(raw.bid), float(raw.ask)
            valid = math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask
            return {"symbol": symbol, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "raw_time": int(raw.time), "raw_time_msc": int(raw.time_msc),
                    "bid": bid, "ask": ask, "quote_valid": valid,
                    "timestamp_status": "unverified", "research_ready": False}

    def capture(self, symbol, count):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_BARS:
            raise ValueError("BAR_COUNT_OUT_OF_BOUNDS")
        with self.lock:
            spec = self.spec(symbol)
            # start_pos=1 excludes the terminal's current unfinished M1 bar.
            raw = self.backend.copy_rates_from_pos(symbol, self.backend.TIMEFRAME_M1, 1, count)
            if raw is None or len(raw) == 0:
                raise ValueError("HISTORY_UNAVAILABLE")
            if len(raw) > count:
                raise ValueError("HISTORY_EXCEEDS_REQUEST")
            fields = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")
            rows = [{k: (float(r[k]) if k in {"open", "high", "low", "close"} else int(r[k]))
                     for k in fields} for r in raw]
            quality = inspect_bars(rows)
            quote = self.quote(symbol)
            quality["quote_valid_at_capture"] = quote["quote_valid"]
            self.backend.check()
            software = {"profile_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            for package in ("mt5-trading-mcp", "MetaTrader5", "mcp"):
                try:
                    software[package] = version(package)
                except PackageNotFoundError:
                    software[package] = "not-installed-test-environment"
            captured = datetime.now(timezone.utc)
            capture_id = f"{captured:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            dest = self.captures / capture_id
            dest.mkdir(parents=True, exist_ok=False)
            record = {"schema_version": 1, "capture_id": capture_id, "source": "mt5-demo-1",
                      "account_alias": "demo-1", "environment": "demo", "symbol": symbol,
                      "resolution": "minute", "captured_at_utc": captured.isoformat(),
                      "upstream_commit": UPSTREAM_COMMIT,
                      "software": software,
                      "request": {"method": "copy_rates_from_pos", "start_pos": 1, "count": count},
                      "timestamp_status": "unverified_raw_mt5_epoch", "symbol_spec": spec,
                      "quote_observation": quote, "quality": quality, "rows": rows}
            payload = json.dumps(record, indent=2, allow_nan=False).encode()
            (dest / "capture.json").write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            (dest / "sha256.txt").write_text(digest + "\n", encoding="ascii")
            return {"capture_id": capture_id, "path": str(dest / "capture.json"), "sha256": digest,
                    "quality": quality, "timestamp_status": record["timestamp_status"],
                    "research_ready": False}


def build_server(service):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("fxra-mt5-data", log_level="CRITICAL")

    def safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            # Native/upstream exception messages can contain account identifiers.
            raise ValueError("MT5_DATA_REQUEST_FAILED; check the local demo terminal and request bounds") from None

    @server.tool()
    def mt5_data_status() -> dict:
        """Check sanitized demo connectivity. No account identifiers or balances."""
        return safe(service.status)

    @server.tool()
    def mt5_symbol_spec(symbol: str = "EURUSD") -> dict:
        """Read EURUSD contract details, without broker/account identifiers."""
        return safe(service.spec, symbol)

    @server.tool()
    def mt5_quote(symbol: str = "EURUSD") -> dict:
        """Read bid/ask and preserve raw timestamps; UTC domain is unverified."""
        return safe(service.quote, symbol)

    @server.tool()
    def mt5_capture_bars(symbol: str = "EURUSD", count: int = 1000) -> dict:
        """Capture up to 10000 closed M1 bars in fixed quarantine storage; return checks/hash."""
        return safe(service.capture, symbol, count)

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", required=True)
    parser.add_argument("--captures", required=True)
    args = parser.parse_args()
    logging.getLogger("mt5_mcp").setLevel(logging.CRITICAL)
    for package, expected in {"mt5-trading-mcp": "1.6.1", "MetaTrader5": "5.0.5735", "mcp": "1.30.0"}.items():
        if version(package) != expected:
            raise RuntimeError("MT5_REVIEWED_RUNTIME_VERSION_MISMATCH")
    import MetaTrader5
    from mt5_mcp.adapter.mt5_client import MT5Client
    backend = ReadOnlyBackend(MetaTrader5, args.terminal)
    client = MT5Client(terminal_path=str(Path(args.terminal).resolve()), mt5_module=backend)
    service = MarketService(backend, client, args.captures)
    try:
        build_server(service).run(transport="stdio")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fxra.mt5_readonly import MAX_BARS, TOOLS, MarketService, ReadOnlyBackend, build_server, inspect_bars


class FakeNative:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M1 = 1

    def __init__(self, terminal):
        self.terminal = terminal
        self.account = SimpleNamespace(login=123, server="private", trade_mode=0)
        self.calls = []
        self.switch_on_read = False

    def initialize(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=True, path=str(Path(self.terminal).parent))

    def account_info(self):
        return self.account

    def copy_rates_from_pos(self, *args):
        self.calls.append(args)
        if self.switch_on_read:
            self.account.login += 1
        return [{"time": 60, "open": 1., "high": 2., "low": .5, "close": 1.5,
                 "spread": 3, "tick_volume": 2, "real_volume": 0}]

    def symbol_info(self, symbol):
        return SimpleNamespace(name=symbol, digits=5, point=.00001, trade_contract_size=100000.,
                               volume_min=.01, volume_max=100., volume_step=.01, currency_base="EUR",
                               currency_profit="USD", currency_margin="EUR", chart_mode=0,
                               spread_float=True, swap_mode=1, swap_long=-1., swap_short=-2., swap_rollover3days=3)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(time=60, time_msc=60000, bid=1., ask=1.1)


def setup(tmp_path):
    terminal = str((tmp_path / "terminal64.exe").resolve())
    native = FakeNative(terminal)
    backend = ReadOnlyBackend(native, terminal)
    client = SimpleNamespace(connect=lambda: backend.initialize(terminal))
    return native, backend, MarketService(backend, client, tmp_path / "captures")


@pytest.mark.parametrize("operation", ["order_send", "order_check", "login", "account_info", "positions_get",
                                         "symbol_select", "__import__", "exec", "copy_ticks_range"])
def test_backend_denies_unknown_and_mutation_operations(tmp_path, operation):
    native, backend, _ = setup(tmp_path)
    with pytest.raises(PermissionError, match="UNAVAILABLE"):
        getattr(backend, operation)
    assert native.calls == []


def test_identity_and_live_switch_fail_closed(tmp_path):
    native, backend, _ = setup(tmp_path)
    backend.check()
    native.account.trade_mode = 2
    with pytest.raises(RuntimeError, match="BINDING"):
        backend.check()
    native.account.trade_mode = 0
    native.account.login += 1
    with pytest.raises(RuntimeError, match="BINDING"):
        backend.check()


def test_switch_during_read_prevents_capture(tmp_path):
    native, _, service = setup(tmp_path)
    native.switch_on_read = True
    with pytest.raises(RuntimeError, match="BINDING"):
        service.capture("EURUSD", 10)
    assert not service.captures.exists()


def test_wrong_terminal_and_credential_initialization_refused(tmp_path):
    native, backend, _ = setup(tmp_path)
    with pytest.raises(PermissionError):
        backend.initialize(native.terminal, login=123)
    native.terminal = str(tmp_path / "other" / "terminal64.exe")
    with pytest.raises(RuntimeError, match="BINDING"):
        backend.check()


def test_capture_preserves_raw_data_and_redacts_identity(tmp_path):
    native, _, service = setup(tmp_path)
    result = service.capture("EURUSD", 10)
    data = Path(result["path"]).read_text()
    record = json.loads(data)
    assert "private" not in data and "login" not in data
    assert record["rows"][0]["time"] == 60
    assert record["timestamp_status"] == "unverified_raw_mt5_epoch"
    assert result["research_ready"] is False
    assert ("EURUSD", 1, 1, 10) in native.calls
    assert result["quality"]["invalid_bars"] == 0
    again = service.capture("EURUSD", 10)
    assert again["path"] != result["path"]


@pytest.mark.parametrize("count", [0, -1, MAX_BARS + 1, True, 1.5])
def test_out_of_bounds_read_never_calls_terminal(tmp_path, count):
    native, _, service = setup(tmp_path)
    with pytest.raises(ValueError):
        service.capture("EURUSD", count)
    assert native.calls == []


def test_other_symbol_refused(tmp_path):
    native, _, service = setup(tmp_path)
    with pytest.raises(ValueError):
        service.capture("GBPUSD", 10)
    assert native.calls == []


def test_crossed_quote_is_flagged_not_silently_trusted(tmp_path):
    native, _, service = setup(tmp_path)
    native.symbol_info_tick = lambda symbol: SimpleNamespace(time=60, time_msc=60000, bid=2., ask=1.)
    result = service.quote("EURUSD")
    assert result["quote_valid"] is False and result["research_ready"] is False
    capture = service.capture("EURUSD", 10)
    record = json.loads(Path(capture["path"]).read_text())
    assert record["quality"]["quote_valid_at_capture"] is False


def test_structural_quality_does_not_assume_a_calendar():
    row = {"time": 60, "open": 1., "high": 2., "low": .5, "close": 3.,
           "spread": -1, "tick_volume": 2, "real_volume": 0}
    result = inspect_bars([row, row])
    assert result["duplicate_times"] == 1 and result["invalid_bars"] == 2
    assert result["session_quality"] == "pending_timezone_and_calendar_verification"


def test_server_registry_and_error_redaction(monkeypatch):
    class FakeServer:
        def __init__(self, *args, **kwargs):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", SimpleNamespace(FastMCP=FakeServer))

    def failure():
        raise RuntimeError("private-account-marker")

    server = build_server(SimpleNamespace(status=failure))
    assert set(server.tools) == TOOLS
    assert "order_send" not in server.tools and "get_account_info" not in server.tools
    with pytest.raises(ValueError, match="MT5_DATA_REQUEST_FAILED") as err:
        server.tools["mt5_data_status"]()
    assert "private-account-marker" not in str(err.value)
    assert err.value.__suppress_context__ is True

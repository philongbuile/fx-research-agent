from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from fxra import mt5_execution as mx

TERMINAL = Path("C:/MT5/terminal64.exe")
NOW = datetime(2026, 10, 28, 14, 0, 5, tzinfo=timezone.utc).timestamp()
OFFSET = 2 * 3600


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2
    TIMEFRAME_H1 = 16385
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL = 1, 0, 1
    ORDER_TIME_GTC, ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 0, 1, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008

    def __init__(self):
        self.trade_mode, self.login = self.ACCOUNT_TRADE_MODE_DEMO, 111
        self.sent, self.positions, self.deals = [], [], []
        self.response = "done"

    def initialize(self, path, timeout):
        return True

    def terminal_info(self):
        return NS(connected=True, path=str(TERMINAL.parent))

    def account_info(self):
        return NS(trade_mode=self.trade_mode, login=self.login, server="X-Demo", currency="USD",
                  balance=10000.0, equity=10000.0, margin_free=10000.0, leverage=100, margin_mode=2)

    def symbol_info(self, s):
        return NS(visible=True, digits=5, point=0.00001, trade_contract_size=100000, volume_min=0.01,
                  volume_max=100, volume_step=0.01, currency_profit="USD", trade_stops_level=0,
                  swap_long=-1.0, swap_short=0.5, filling_mode=1)

    def symbol_info_tick(self, s):
        return NS(time=int(NOW) + OFFSET, bid=1.10000, ask=1.10002)

    def copy_rates_from_pos(self, s, tf, start, count):
        base = int(NOW) - int(NOW) % 3600 + OFFSET
        return [{"time": base - 3600 * (count - i), "open": 1.1, "high": 1.101, "low": 1.099, "close": 1.1,
                 "tick_volume": 10, "spread": 2} for i in range(count)]

    def positions_get(self, symbol=None, ticket=None):
        return [p for p in self.positions if ticket is None or p.ticket == ticket]

    def history_deals_get(self, since, until):
        return self.deals

    def order_check(self, request):
        return NS(retcode=0, comment="Done")

    def order_send(self, request):
        self.sent.append(request)
        if self.response is None:
            return None
        return NS(retcode=self.TRADE_RETCODE_DONE, comment="Request executed", order=5, deal=6,
                  price=request["price"], volume=request["volume"])


def bridge(fake=None):
    fake = fake or FakeMT5()
    b = mx.DemoBinding(fake, TERMINAL)
    b.connect()
    return mx.Bridge(b, clock=lambda: NOW), fake


def entry(cid="H1B-26102813-B", sl=1.0990, approved=True, side="buy"):
    return {"mode": "demo",
            "intent": {"client_order_id": cid, "symbol": "EURUSD", "side": side, "quantity_lots": 0.01,
                       "order_type": "market", "stop_loss": sl},
            "decision": {"approved": approved, "client_order_id": cid}}


def test_server_offset_rounds_and_rejects_stale_ticks():
    assert mx.server_offset(int(NOW) + OFFSET, NOW) == OFFSET
    assert mx.server_offset(int(NOW) + OFFSET - 600, NOW) is None


def test_snapshot_converts_server_time_to_utc_and_hides_identity():
    b, _ = bridge()
    snap = b.snapshot({"h1_bars": 3})
    assert snap["server_offset_seconds"] == OFFSET
    assert snap["h1_bars"][-1]["time"] == "2026-10-28T13:00:00+00:00"
    assert "login" not in str(snap["account"]) and "X-Demo" not in str(snap)


def test_live_account_is_refused():
    fake = FakeMT5()
    fake.trade_mode = fake.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(PermissionError, match="NOT_A_DEMO"):
        bridge(fake)


def test_account_switch_mid_session_is_refused():
    b, fake = bridge()
    fake.login = 222
    with pytest.raises(PermissionError, match="ACCOUNT_CHANGED"):
        b.send(entry())
    assert fake.sent == []


def test_send_requires_approved_decision_demo_mode_and_stop():
    b, fake = bridge()
    with pytest.raises(PermissionError):
        b.send(entry(approved=False))
    with pytest.raises(PermissionError):
        b.send({**entry(), "mode": "dry_run"})
    with pytest.raises(ValueError, match="STOP_LOSS_REQUIRED"):
        b.send(entry(sl=None))
    with pytest.raises(ValueError, match="WRONG_SIDE"):
        b.send(entry(sl=1.2))
    assert fake.sent == []


def test_send_fills_with_magic_comment_and_stop():
    b, fake = bridge()
    out = b.send(entry())
    assert out["status"] == "filled"
    req = fake.sent[0]
    assert req["magic"] == mx.MAGIC and req["comment"] == "H1B-26102813-B" and req["sl"] == 1.099
    assert req["price"] == 1.10002  # buy at ask


def test_known_id_is_never_resent():
    b, fake = bridge()
    fake.positions = [NS(ticket=9, magic=mx.MAGIC, comment="H1B-26102813-B", type=0)]
    assert b.send(entry())["status"] == "already_open"
    assert fake.sent == []


def test_lost_response_reconciles_instead_of_resending():
    b, fake = bridge()
    fake.response = None
    assert b.send(entry())["status"] == "unknown"
    assert len(fake.sent) == 1


def test_close_only_own_position_and_opposite_side():
    b, fake = bridge()
    assert b.close({"ticket": 7, "client_order_id": "H1B-X-7"})["status"] == "no_such_position"
    fake.positions = [NS(ticket=7, magic=mx.MAGIC, comment="H1B-26102813-B", type=0, volume=0.01)]
    assert b.close({"ticket": 7, "client_order_id": "H1B-X-7"})["status"] == "filled"
    req = fake.sent[0]
    assert req["type"] == fake.ORDER_TYPE_SELL and req["position"] == 7 and req["price"] == 1.10000

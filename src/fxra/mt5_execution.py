"""First-party MT5 demo execution bridge (D-30). Stdlib + MetaTrader5 only.

Runs in the reviewed MT5 runtime (artifacts/mt5-mcp-runtime), not in the main venv:

    python -m fxra.mt5_execution --terminal <terminal64.exe> <snapshot|send|close>  (JSON on stdin)

Every call re-checks that the attached account is a DEMO account and that its
identity did not change since the first check of this process. Login and server
are kept in RAM only; output carries aliases and sanitized values. There is no
generic order passthrough: only market entry with a stop loss, and closing a
position this agent opened (matched by magic number).
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAGIC = 20261028  # every order of the demo agent carries this magic number
SYMBOLS = frozenset({"EURUSD"})
MAX_DEVIATION_POINTS = 20
COMMENT_MAX = 31  # MT5 order comment limit


class DemoBinding:
    """Holds the native module and refuses anything that is not the bound demo account."""

    def __init__(self, native, terminal_path):
        self.mt5 = native
        self.terminal = Path(terminal_path).resolve()
        self._identity = None

    def connect(self):
        if not self.mt5.initialize(str(self.terminal), timeout=10000):
            raise RuntimeError("MT5_TERMINAL_UNAVAILABLE")
        self.check()

    def check(self):
        info, account = self.mt5.terminal_info(), self.mt5.account_info()
        if info is None or account is None or not info.connected:
            raise RuntimeError("MT5_NOT_CONNECTED")
        if Path(info.path).resolve() != self.terminal.parent:
            raise RuntimeError("MT5_TERMINAL_MISMATCH")
        if account.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise PermissionError("MT5_NOT_A_DEMO_ACCOUNT")  # D-30: live is never traded
        identity = (account.login, account.server)
        if self._identity is not None and identity != self._identity:
            raise PermissionError("MT5_ACCOUNT_CHANGED")
        self._identity = identity
        return account


def _utc(epoch: float, offset: int) -> str:
    return datetime.fromtimestamp(epoch - offset, timezone.utc).isoformat()


def server_offset(tick_time: int, now_utc: float) -> int | None:
    """Broker server clock offset from a fresh tick, rounded to 15 minutes.

    Live-only estimate: it says nothing about historical DST. None when the tick
    is too old to tell (market closed or quiet).
    """
    raw = tick_time - now_utc
    offset = round(raw / 900) * 900
    return offset if abs(raw - offset) <= 120 else None


class Bridge:
    def __init__(self, binding: DemoBinding, clock=time.time):
        self.b, self.mt5, self.clock = binding, binding.mt5, clock

    def _symbol(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError("SYMBOL_OUTSIDE_SCOPE")
        info = self.mt5.symbol_info(symbol)
        if info is None or (not info.visible and not self.mt5.symbol_select(symbol, True)):
            raise ValueError("SYMBOL_UNAVAILABLE")
        return info

    def snapshot(self, req):
        symbol, hours = req.get("symbol", "EURUSD"), int(req.get("h1_bars", 40))
        if not 1 <= hours <= 500:
            raise ValueError("BAR_COUNT_OUT_OF_BOUNDS")
        account = self.b.check()
        info = self._symbol(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        now = self.clock()
        offset = server_offset(int(tick.time), now) if tick else None
        out = {
            "observed_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "server_offset_seconds": offset,
            "account": {"alias": "demo-1", "environment": "demo", "currency": account.currency,
                        "balance": account.balance, "equity": account.equity,
                        "margin_free": account.margin_free, "leverage": account.leverage,
                        "hedging": account.margin_mode == self.mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING},
            "spec": {"symbol": symbol, "digits": info.digits, "point": info.point,
                     "contract_size": info.trade_contract_size, "volume_min": info.volume_min,
                     "volume_max": info.volume_max, "volume_step": info.volume_step,
                     "quote_currency": info.currency_profit, "stops_level": info.trade_stops_level,
                     "swap_long": info.swap_long, "swap_short": info.swap_short},
            "quote": None, "h1_bars": [], "positions": [], "deals": [],
        }
        if offset is None:
            return out  # without a clock we cannot place bars or quotes in UTC
        out["quote"] = {"symbol": symbol, "bid": tick.bid, "ask": tick.ask, "time": _utc(tick.time, offset)}
        rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_H1, 1, hours)  # 1 = skip open bar
        out["h1_bars"] = [{"time": _utc(int(r["time"]), offset), "open": float(r["open"]),
                           "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]),
                           "tick_volume": int(r["tick_volume"]), "spread_points": int(r["spread"])}
                          for r in (rates if rates is not None else [])]
        for p in self.mt5.positions_get(symbol=symbol) or ():
            if p.magic == MAGIC:
                out["positions"].append({
                    "ticket": p.ticket, "side": "buy" if p.type == self.mt5.POSITION_TYPE_BUY else "sell",
                    "volume": p.volume, "price_open": p.price_open, "sl": p.sl, "tp": p.tp,
                    "price_current": p.price_current, "profit": p.profit, "swap": p.swap,
                    "time": _utc(p.time, offset), "comment": p.comment})
        since = datetime.fromtimestamp(now - 30 * 86400, timezone.utc)
        until = datetime.fromtimestamp(now + 86400, timezone.utc)
        for d in self.mt5.history_deals_get(since, until) or ():
            if d.magic == MAGIC and d.symbol == symbol:
                out["deals"].append({
                    "ticket": d.ticket, "order": d.order, "position_id": d.position_id,
                    "time": _utc(d.time, offset),
                    "side": "buy" if d.type == self.mt5.DEAL_TYPE_BUY else "sell",
                    "entry": {self.mt5.DEAL_ENTRY_IN: "in", self.mt5.DEAL_ENTRY_OUT: "out"}.get(d.entry, "other"),
                    "volume": d.volume, "price": d.price, "profit": d.profit,
                    "commission": d.commission, "swap": d.swap, "fee": getattr(d, "fee", 0.0),
                    "comment": d.comment})
        return out

    def _already_sent(self, symbol, comment):
        """Reconciliation: an order with this id exists as a position or a deal."""
        for p in self.mt5.positions_get(symbol=symbol) or ():
            if p.magic == MAGIC and p.comment == comment:
                return {"status": "already_open", "ticket": p.ticket}
        now = self.clock()
        deals = self.mt5.history_deals_get(datetime.fromtimestamp(now - 30 * 86400, timezone.utc),
                                           datetime.fromtimestamp(now + 86400, timezone.utc)) or ()
        for d in deals:
            if d.magic == MAGIC and d.comment == comment:
                return {"status": "already_filled", "deal": d.ticket, "position_id": d.position_id}
        return None

    def _filling(self, info):
        if info.filling_mode & 1:
            return self.mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request, symbol, comment):
        found = self._already_sent(symbol, comment)
        if found:
            return found  # never resend an id the broker already knows
        check = self.mt5.order_check(request)
        if check is None or check.retcode not in (0, self.mt5.TRADE_RETCODE_DONE):
            return {"status": "rejected_by_check", "retcode": getattr(check, "retcode", None),
                    "reason": getattr(check, "comment", "order_check failed")}
        self.b.check()  # revalidate demo identity immediately before the mutation
        result = self.mt5.order_send(request)
        if result is None:
            found = self._already_sent(symbol, comment)  # lost response: reconcile, do not resend
            return found or {"status": "unknown", "reason": "no response; reconcile before retrying"}
        ok = result.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED)
        return {"status": "filled" if ok else "rejected", "retcode": result.retcode,
                "reason": result.comment, "order": result.order, "deal": result.deal,
                "price": result.price, "volume": result.volume}

    def send(self, req):
        """Market entry. Requires an approved risk decision for this exact id and a stop loss."""
        intent, decision = req["intent"], req["decision"]
        cid = intent["client_order_id"]
        if not decision.get("approved") or decision.get("client_order_id") != cid:
            raise PermissionError("RISK_DECISION_NOT_APPROVED")
        if req.get("mode") != "demo":
            raise PermissionError("MODE_NOT_DEMO")
        if not cid or len(cid) > COMMENT_MAX:
            raise ValueError("CLIENT_ORDER_ID_INVALID")
        if intent["order_type"] != "market" or intent["side"] not in ("buy", "sell"):
            raise ValueError("ONLY_MARKET_BUY_OR_SELL")
        sl = intent.get("stop_loss")
        if not isinstance(sl, (int, float)) or not math.isfinite(sl) or sl <= 0:
            raise ValueError("STOP_LOSS_REQUIRED")
        self.b.check()
        info = self._symbol(intent["symbol"])
        tick = self.mt5.symbol_info_tick(intent["symbol"])
        buy = intent["side"] == "buy"
        price = tick.ask if buy else tick.bid
        if (buy and sl >= tick.bid) or (not buy and sl <= tick.ask):
            raise ValueError("STOP_LOSS_WRONG_SIDE")
        request = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": intent["symbol"],
                   "volume": float(intent["quantity_lots"]),
                   "type": self.mt5.ORDER_TYPE_BUY if buy else self.mt5.ORDER_TYPE_SELL,
                   "price": price, "sl": round(sl, info.digits), "tp": 0.0,
                   "deviation": MAX_DEVIATION_POINTS, "magic": MAGIC, "comment": cid,
                   "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        return self._send(request, intent["symbol"], cid)

    def close(self, req):
        """Close a whole position opened by this agent. Never opens reverse exposure."""
        ticket, cid, symbol = int(req["ticket"]), req["client_order_id"], req.get("symbol", "EURUSD")
        if not cid or len(cid) > COMMENT_MAX:
            raise ValueError("CLIENT_ORDER_ID_INVALID")
        self.b.check()
        info = self._symbol(symbol)
        found = self._already_sent(symbol, cid)
        if found:
            return found
        positions = [p for p in self.mt5.positions_get(ticket=ticket) or () if p.magic == MAGIC]
        if len(positions) != 1:
            return {"status": "no_such_position", "ticket": ticket}
        p = positions[0]
        tick = self.mt5.symbol_info_tick(symbol)
        long = p.type == self.mt5.POSITION_TYPE_BUY
        request = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": p.volume,
                   "type": self.mt5.ORDER_TYPE_SELL if long else self.mt5.ORDER_TYPE_BUY,
                   "position": ticket, "price": tick.bid if long else tick.ask,
                   "deviation": MAX_DEVIATION_POINTS, "magic": MAGIC, "comment": cid,
                   "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        return self._send(request, symbol, cid)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", required=True)
    parser.add_argument("command", choices=["snapshot", "send", "close"])
    args = parser.parse_args(argv)
    import MetaTrader5
    binding = DemoBinding(MetaTrader5, args.terminal)
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")  # PowerShell pipes add a BOM
        binding.connect()
        result = getattr(Bridge(binding), args.command)(req)
        print(json.dumps({"ok": True, "result": result}))
    except Exception as e:  # sanitized: error class and our own code only, no native text
        code = str(e) if str(e).isupper() or str(e).startswith(("MT5_", "RISK_", "MODE_")) else type(e).__name__
        print(json.dumps({"ok": False, "error": code}))
        return 2
    finally:
        MetaTrader5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

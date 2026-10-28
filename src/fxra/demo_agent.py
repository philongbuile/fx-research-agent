"""H-001B demo agent: H-001's frozen 24/14/10 rules on broker H1 bars, MT5 demo (D-30).

One `step` per closed hour, idempotent by bar time:
sync broker deals -> trades/lessons, then either manage the open position
(10-bar opposite channel or 24-bar time exit; the 2xATR stop sits at the broker)
or open a new one when flat and the closed bar breaks the 24-bar channel.
Every entry passes `risk.evaluate`. Orders go only through the demo bridge.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import risk
from .breakout import signal_from_hours
from .contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
from .trade_journal import Journal

STRATEGY_ID = "H-001B"
STRATEGY_VERSION = "h001b-v1"  # bump on any rule change; risk.toml approves "H-001B@h001b-v1"
SYMBOL = "EURUSD"
LOTS = 0.01
STOP_ATR = 2.0
MAX_HOLD_HOURS = 24
REPO = Path(__file__).resolve().parents[2]


class SubprocessBroker:
    """Calls `fxra.mt5_execution` inside the reviewed MT5 runtime (it has MetaTrader5, we do not)."""

    def __init__(self, python: Path, terminal: Path, src: Path = REPO / "src", timeout=60):
        self.python, self.terminal, self.src, self.timeout = Path(python), Path(terminal), Path(src), timeout

    def call(self, command: str, payload: dict) -> dict:
        env = {**os.environ, "PYTHONPATH": str(self.src), "PYTHONNOUSERSITE": "1"}
        proc = subprocess.run([str(self.python), "-m", "fxra.mt5_execution", "--terminal", str(self.terminal), command],
                              input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=self.timeout)
        try:
            out = json.loads(proc.stdout.decode().strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise RuntimeError("MT5_BRIDGE_NO_OUTPUT") from None
        if not out.get("ok"):
            raise RuntimeError(out.get("error", "MT5_BRIDGE_FAILED"))
        return out["result"]

    def snapshot(self, payload):
        return self.call("snapshot", payload)

    def send(self, payload):
        return self.call("send", payload)

    def close(self, payload):
        return self.call("close", payload)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def last_contiguous(bars: list[dict], n: int) -> list[dict] | None:
    """The newest n bars if they are consecutive hours; a weekend or broker gap gives None."""
    if len(bars) < n:
        return None
    tail = bars[-n:]
    times = [_t(b["time"]) for b in tail]
    if any(b - a != timedelta(hours=1) for a, b in zip(times, times[1:])):
        return None
    return tail


def entry_id(bar_time: datetime, side: str) -> str:
    return f"H1B-{bar_time:%y%m%d%H}-{side[0].upper()}"


def exit_id(ticket: int) -> str:
    return f"H1B-X-{ticket}"


def _lesson_text(trade: dict, bars: list[dict], currency: str) -> str:
    """Structured, factual post-trade note. Rules are not tuned from it (research honesty)."""
    o, c = _t(trade["open_time"]), _t(trade["close_time"])
    window = [b for b in bars if o - timedelta(hours=1) < _t(b["time"]) <= c]
    long = trade["side"] == "buy"
    mfe = mae = None
    if window:
        hi, lo = max(b["high"] for b in window), min(b["low"] for b in window)
        mfe = (hi - trade["open_price"]) if long else (trade["open_price"] - lo)
        mae = (trade["open_price"] - lo) if long else (hi - trade["open_price"])
    pips = lambda x: "n/a" if x is None else f"{x / 0.0001:.1f} pips"
    held = (c - o).total_seconds() / 3600
    verdict = "win" if trade["net"] > 0 else "loss" if trade["net"] < 0 else "flat"
    return (f"{STRATEGY_ID} {trade['side']} {trade['volume']} lot: {verdict} {trade['net']:+.2f} {currency} "
            f"(gross {trade['profit']:+.2f}, commission {trade['commission']:+.2f}, swap {trade['swap']:+.2f}). "
            f"Entry {trade['open_price']} at {o:%Y-%m-%d %H:%M}Z, exit {trade['close_price']} at {c:%Y-%m-%d %H:%M}Z "
            f"after {held:.1f} h, reason {trade['exit_reason'] or 'unknown'}. "
            f"Best excursion {pips(mfe)}, worst {pips(mae)} (H1 bars, approximate). "
            f"Demo forward-test observation only, not evidence of edge.")


def _exit_reason(trade: dict, journal: Journal) -> str:
    if trade["exit_id"] and trade["exit_id"].startswith("H1B-X-"):
        row = journal.db.execute("SELECT result FROM orders WHERE client_order_id=?", (trade["exit_id"],)).fetchone()
        return json.loads(row[0]).get("rule", "rule_exit") if row else "rule_exit"
    if (trade["exit_id"] or "").lower().startswith("[sl"):  # MT5 marks stop-outs "[sl <price>]"
        return "stop_loss"
    return "broker_or_manual"


def step(journal: Journal, broker, limits: risk.RiskLimits | None, now: datetime, *, send: bool = True) -> dict:
    """Run one decision cycle. Returns what happened, also stored in the journal."""
    snap = broker.snapshot({"symbol": SYMBOL, "h1_bars": 60})
    acct, spec_raw = snap["account"], snap["spec"]
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"])

    for trade in journal.sync_deals(snap["deals"]):
        reason = _exit_reason(trade, journal)
        journal.set_exit_reason(trade["position_id"], reason)
        trade["exit_reason"] = reason
        journal.add_lesson(now, trade["position_id"], "agent", _lesson_text(trade, snap["h1_bars"], acct["currency"]))

    if snap["quote"] is None:
        return {"action": "skip", "reason": "no fresh tick: market closed or broker clock unknown"}
    latest = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    bars = last_contiguous(snap["h1_bars"], 25)
    if not snap["h1_bars"] or _t(snap["h1_bars"][-1]["time"]) != latest:
        return {"action": "skip", "reason": f"latest closed H1 bar is not {latest:%Y-%m-%d %H:%M}Z (market closed?)"}
    bar_key = latest.isoformat()
    if journal.decided(bar_key):
        return {"action": "skip", "reason": "already decided for this bar", "bar": bar_key}
    lessons = journal.recent_lessons(5)
    base = {"quote": snap["quote"], "lessons_consulted": [l["id"] for l in lessons],
            "positions": snap["positions"], "account": {k: acct[k] for k in ("currency", "balance", "equity", "hedging")}}
    if bars is None:
        journal.decide(bar_key, now, "no_trade", None, "fewer than 25 consecutive H1 bars (weekend or broker gap)", base)
        return {"action": "no_trade", "bar": bar_key, "reason": "gap"}
    sig = signal_from_hours(bars, available_at=latest + timedelta(hours=1))
    close = bars[-1]["close"]
    signal = {"side": sig.side, "atr": sig.atr, "close": close,
              "channel_high_24": max(b["high"] for b in bars[:-1]), "channel_low_24": min(b["low"] for b in bars[:-1]),
              "long_exit_below": sig.long_exit_below, "short_exit_above": sig.short_exit_above}
    details = {**base, "signal": signal}

    if snap["positions"]:
        pos = snap["positions"][0]
        held = int((latest + timedelta(hours=1) - _t(pos["time"])).total_seconds() // 3600)
        rule = None
        if pos["side"] == "buy" and close < sig.long_exit_below:
            rule = "opposite_10_bar_channel"
        elif pos["side"] == "sell" and close > sig.short_exit_above:
            rule = "opposite_10_bar_channel"
        elif held >= MAX_HOLD_HOURS:
            rule = "max_hold_24h"
        if rule is None:
            journal.decide(bar_key, now, "hold", pos["side"], f"holding {held} h; no exit rule triggered", details)
            return {"action": "hold", "bar": bar_key}
        cid = exit_id(pos["ticket"])
        result = broker.close({"ticket": pos["ticket"], "client_order_id": cid, "symbol": SYMBOL}) if send \
            else {"status": "not_sent"}
        result["rule"] = rule
        journal.order(cid, now, "exit", "sell" if pos["side"] == "buy" else "buy", pos["volume"], None,
                      result["status"], {}, result)
        journal.decide(bar_key, now, "exit", pos["side"], rule, {**details, "order": result})
        return {"action": "exit", "bar": bar_key, "rule": rule, "result": result}

    if sig.side is None:
        journal.decide(bar_key, now, "no_trade", None,
                       f"close {close} inside 24-bar channel [{signal['channel_low_24']}, {signal['channel_high_24']}]",
                       details)
        return {"action": "no_trade", "bar": bar_key}

    q = snap["quote"]
    entry = q["ask"] if sig.side == "buy" else q["bid"]
    digits = spec_raw["digits"]
    stop = round(entry - STOP_ATR * sig.atr if sig.side == "buy" else entry + STOP_ATR * sig.atr, digits)
    cid = entry_id(latest, sig.side)
    intent = OrderIntent(cid, STRATEGY_ID, STRATEGY_VERSION, SYMBOL, sig.side, LOTS, "market", stop_loss=stop)
    spec = SymbolSpec(SYMBOL, "mt5-demo-1", digits, spec_raw["point"], spec_raw["point"] * 10,
                      spec_raw["contract_size"], spec_raw["volume_min"], spec_raw["volume_max"],
                      spec_raw["volume_step"], spec_raw["quote_currency"])
    quote = Quote(SYMBOL, _t(q["time"]), q["bid"], q["ask"])
    account = AccountSnapshot(now, acct["currency"], acct["equity"], day_start, len(snap["positions"]))
    decision = risk.evaluate(intent, limits, spec, quote, account, now, journal.order_ids(),
                             risk.kill_switch_active(limits))
    details = {**details, "intent": intent.__dict__, "risk": decision.to_dict()}
    if not decision.approved:
        journal.order(cid, now, "entry", sig.side, LOTS, stop, "risk_rejected", decision.to_dict(), {})
        journal.decide(bar_key, now, "blocked", sig.side, "risk gate: " + ", ".join(decision.reasons), details)
        return {"action": "blocked", "bar": bar_key, "reasons": decision.reasons}
    if not send:
        journal.decide(bar_key, now, "signal_not_sent", sig.side, "dry step: order not sent", details)
        return {"action": "signal_not_sent", "bar": bar_key, "intent": intent.__dict__}
    journal.order(cid, now, "entry", sig.side, LOTS, stop, "sending", decision.to_dict(), {})  # persist before send
    try:
        result = broker.send({"mode": limits.mode, "intent": intent.__dict__, "decision": decision.to_dict()})
    except RuntimeError as e:
        result = {"status": "error", "reason": str(e)}
    journal.order(cid, now, "entry", sig.side, LOTS, stop, result["status"], decision.to_dict(), result)
    journal.decide(bar_key, now, "entry", sig.side,
                   f"close {close} broke 24-bar {'high' if sig.side == 'buy' else 'low'}; stop {stop} = 2xATR",
                   {**details, "order": result})
    return {"action": "entry", "bar": bar_key, "side": sig.side, "result": result}

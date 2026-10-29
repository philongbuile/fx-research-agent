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
from .trade_journal import PIP_SIZES, Journal, confluence

STRATEGY_ID = "H-001B"
STRATEGY_VERSION = "h001b-v1"  # bump on any rule change; risk.toml approves "H-001B@h001b-v1"
SYMBOL = "EURUSD"
LOTS = 0.01
STOP_ATR = 2.0
MAX_HOLD_HOURS = 24
MAGIC = 20261028  # fxra.mt5_execution.MAGICS; that module is not importable here (MT5 runtime only)
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


def excursion(trade: dict, market: dict) -> tuple[float, float, str]:
    """Best and worst price move during the trade (price units), from M15 bars when present, else H1.

    Snapshots skip the still-open bar, so the last part of the hold can be missing:
    the realised move is a floor for the best (win) or the worst (loss) excursion.
    """
    o, c = _t(trade["open_time"]), _t(trade["close_time"])
    bars, span, frame = market.get("m15_bars"), timedelta(minutes=15), "M15"
    if not bars:
        bars, span, frame = market.get("h1_bars") or [], timedelta(hours=1), "H1"
    sign = 1 if trade["side"] == "buy" else -1
    realised = sign * (trade["close_price"] - trade["open_price"])
    best, worst = max(realised, 0.0), max(-realised, 0.0)
    for b in bars:
        if o - span < _t(b["time"]) <= c:
            hi, lo = sign * (b["high"] - trade["open_price"]), sign * (b["low"] - trade["open_price"])
            best, worst = max(best, hi, lo), max(worst, -hi, -lo)
    return best, worst, frame


def _lesson_text(trade: dict, market: dict, currency: str, info: dict | None = None) -> str:
    """Structured, factual post-trade note: setup and conditions first, so later recall can find
    "what happened to <pattern> in <session/regime>". Rules are not tuned from it (research honesty)."""
    info = info or {}
    f = info.get("features") or {}
    o, c = _t(trade["open_time"]), _t(trade["close_time"])
    symbol = trade.get("symbol") or SYMBOL
    pip = PIP_SIZES.get(symbol, 0.0001)
    sign = 1 if trade["side"] == "buy" else -1
    moved = sign * (trade["close_price"] - trade["open_price"])
    risk_dist = abs(trade["open_price"] - info["stop_loss"]) if info.get("stop_loss") else None
    in_r = lambda x: f"{x / risk_dist:+.2f}R" if risk_dist else f"{x / pip:+.1f} pips"
    best, worst, frame = excursion(trade, market)
    held = (c - o).total_seconds() / 3600
    verdict = "win" if trade["net"] > 0 else "loss" if trade["net"] < 0 else "flat"
    reason = trade["exit_reason"] or "unknown"
    eid = trade["entry_id"] or ""

    if eid.startswith(("H2", "H3")):
        hour = trade.get("hour_utc")
        when = ", ".join(x for x in (trade.get("session"), f"{hour}h UTC" if hour is not None else None,
                                     f.get("dow") or f"{o:%a}") if x)
        regime = ", ".join(x for x in (f"{f['vol']} vol" if f.get("vol") else None,
                                       f"H1 trend {f['h1_trend']}" if f.get("h1_trend") else None,
                                       f"M15 trend {f['m15_trend']}" if f.get("m15_trend") else None) if x)
        what = (f"H-003-{'codex' if eid.startswith('H3GX') else 'claude'} model-approved "
                f"({'+'.join(f.get('patterns_for') or [])})" if eid.startswith("H3")
                else f"H-002-{trade.get('arm') or 'claude'} {trade.get('pattern') or ''}")
        head = (f"{what} {trade['side']} {symbol}"
                f" ({when}{'; ' + regime if regime else ''}): {verdict} {in_r(moved)}, {reason}.")
    else:
        label = "TECH-0003 execution check" if eid.startswith("T3-") else STRATEGY_ID
        head = f"{label} {trade['side']} {symbol} ({o:%a} {o:%H}h UTC): {verdict} {in_r(moved)}, {reason}."

    setup = []
    if f.get("sl_pips") is not None:
        setup.append(f"planned stop {f['sl_pips']:.1f} pips, target {f.get('tp_pips', 0):.1f} pips")
    elif risk_dist:
        setup.append(f"planned stop {risk_dist / pip:.1f} pips")
    if f.get("atr_pips") is not None:
        setup.append(f"ATR {f['atr_pips']:.1f} pips" + (f" (pctile {f['atr_pctile']})" if "atr_pctile" in f else ""))
    if f.get("spread_pips") is not None:
        setup.append(f"spread {f['spread_pips']} pips")
    others = {k: v for k, v in (f.get("matches") or {}).items() if k != trade.get("pattern")}
    if others:
        setup.append("other signals that bar: " + ", ".join(f"{k} {v}" for k, v in sorted(others.items())))
    if trade.get("pattern") and confluence(f, trade["side"]):
        setup.append(f"confluence {confluence(f, trade['side'])}")
    if f.get("event") not in (None, "none"):
        impact = f", {f['event_impact']} for {f.get('event_currency')}" if f.get("event_impact") else ""
        setup.append(f"news {f['event']}: {f.get('event_currency')} {f.get('event_name')} "
                     f"({f.get('event_minutes', 0):+d} min from release{impact})")
    if trade.get("policy_version") is not None:
        setup.append(f"policy v{trade['policy_version']}")
    if f.get("decision_reason"):
        setup.append(f"model {f.get('decider')} chose stop {f.get('sl_atr')}x ATR, target {f.get('tp_rr')}R, "
                     f"{f.get('decision_latency_min')} min after the bar, because: {f['decision_reason']}")
    return (f"{head} "
            + (("Setup: " + "; ".join(setup) + ". ") if setup else "")
            + f"Result {moved / pip:+.1f} pips, {verdict} {trade['net']:+.2f} {currency} (gross {trade['profit']:+.2f}, "
            f"commission {trade['commission']:+.2f}, swap {trade['swap']:+.2f}), {trade['volume']} lot. "
            f"Entry {trade['open_price']} at {o:%Y-%m-%d %H:%M}Z, exit {trade['close_price']} at {c:%Y-%m-%d %H:%M}Z "
            f"after {held:.1f} h. Excursion best {in_r(best)}, worst {in_r(-worst)} ({frame} bars, approximate). "
            f"Demo forward-test observation only, not evidence of edge.")


def _exit_reason(trade: dict, journal: Journal) -> str:
    if (trade["exit_id"] or "").lower().startswith("[tp"):
        return "take_profit"
    if trade["exit_id"] and trade["exit_id"].startswith(("H1B-X-", "T3X-", "H2X-", "H3X-")):
        row = journal.db.execute("SELECT result FROM orders WHERE client_order_id=?", (trade["exit_id"],)).fetchone()
        return json.loads(row[0]).get("rule", "rule_exit") if row else "rule_exit"
    if (trade["exit_id"] or "").lower().startswith("[sl"):  # MT5 marks stop-outs "[sl <price>]"
        return "stop_loss"
    return "broker_or_manual"


def test_round_trip(journal: Journal, broker, limits: risk.RiskLimits | None, now: datetime, *,
                    hold_seconds: int = 60, sleep=None) -> dict:
    """TECH-0003: one owner-requested 0.01-lot buy with a 30-pip stop, closed after hold_seconds.

    Proves the bridge, risk gate, journal and dashboard end to end. Not a strategy trade.
    """
    import time
    snap = broker.snapshot({"symbol": SYMBOL, "h1_bars": 1})
    if snap["quote"] is None:
        return {"action": "skip", "reason": "no fresh tick: market closed"}
    if snap["positions"]:
        return {"action": "skip", "reason": "agent already has an open position"}
    acct, s, q = snap["account"], snap["spec"], snap["quote"]
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"])
    cid = f"T3-{now:%y%m%d%H%M%S}"
    stop = round(q["ask"] - 0.0030, s["digits"])
    intent = OrderIntent(cid, "TECH-0003", "exec-check-v1", SYMBOL, "buy", LOTS, "market", stop_loss=stop)
    spec = SymbolSpec(SYMBOL, "mt5-demo-1", s["digits"], s["point"], s["point"] * 10, s["contract_size"],
                      s["volume_min"], s["volume_max"], s["volume_step"], s["quote_currency"])
    decision = risk.evaluate(intent, limits, spec, Quote(SYMBOL, _t(q["time"]), q["bid"], q["ask"]),
                             AccountSnapshot(now, acct["currency"], acct["equity"], day_start, 0),
                             now, journal.order_ids(), risk.kill_switch_active(limits))
    if not decision.approved:
        journal.order(cid, now, "entry", "buy", LOTS, stop, "risk_rejected", decision.to_dict(), {})
        return {"action": "blocked", "reasons": decision.reasons}
    journal.order(cid, now, "entry", "buy", LOTS, stop, "sending", decision.to_dict(), {})
    opened = broker.send({"mode": limits.mode, "intent": intent.__dict__, "decision": decision.to_dict()})
    journal.order(cid, now, "entry", "buy", LOTS, stop, opened["status"], decision.to_dict(), opened)
    if opened["status"] != "filled":
        return {"action": "entry_failed", "result": opened}
    (sleep or time.sleep)(hold_seconds)
    positions = [p for p in broker.snapshot({"symbol": SYMBOL, "h1_bars": 1})["positions"]
                 if p["comment"] == cid or p["ticket"] == opened.get("order")]  # brokers may rewrite comments
    if not positions:
        return {"action": "entry_only", "reason": "position already gone (stop hit?)", "entry": opened}
    xid = f"T3X-{positions[0]['ticket']}"
    closed = broker.close({"ticket": positions[0]["ticket"], "client_order_id": xid, "symbol": SYMBOL})
    closed["rule"] = "tech_check_timed_close"
    journal.order(xid, datetime.now(timezone.utc), "exit", "sell", LOTS, None, closed["status"], {}, closed)
    return {"action": "round_trip", "entry": opened, "exit": closed}


EXIT_PREFIX = {"H-001B": "H1B-X-", "H-002-claude": "H2X-", "H-002-codex": "H2X-",
               "H-003-claude": "H3X-", "H-003-codex": "H3X-"}


def close_positions(journal: Journal, broker, strategies: set[str], now: datetime, reason: str, author: str, *,
                    send: bool = True) -> list[dict]:
    """Owner-requested close of every open position of the named strategies/arms, through the demo adapter.

    Each exit is journaled before and after the send with rule "owner_close", so lessons and
    stats show why the trade ended. Never opens anything.
    """
    snap = broker.snapshot(SNAPSHOT_REQUEST)
    out = []
    for pos in snap["positions"]:
        if pos.get("strategy") not in strategies:
            continue
        cid = f"{EXIT_PREFIX[pos['strategy']]}{pos['ticket']}"
        side = "sell" if pos["side"] == "buy" else "buy"
        journal.order(cid, now, "exit", side, pos["volume"], None, "sending" if send else "not_sent", {}, {})
        result = broker.close({"ticket": pos["ticket"], "client_order_id": cid, "symbol": pos.get("symbol", SYMBOL)})             if send else {"status": "not_sent"}
        result.update(rule="owner_close", reason=reason, author=author)
        journal.order(cid, now, "exit", side, pos["volume"], None, result["status"], {}, result)
        out.append({"ticket": pos["ticket"], "strategy": pos["strategy"], "symbol": pos.get("symbol"),
                    "profit": pos.get("profit"), "status": result["status"]})
    return out


SNAPSHOT_REQUEST = {"symbols": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD",
                                "EURJPY", "GBPJPY", "AUDJPY", "NZDUSD", "EURGBP"],  # D-41 Asia/London pairs
                    "h1_bars": 60, "m15_bars": 120}


def step(journal: Journal, broker, limits: risk.RiskLimits | None, now: datetime, *, send: bool = True,
         snap: dict | None = None, duty: dict | None = None) -> dict:
    """Run one decision cycle. Returns what happened, also stored in the journal.

    Also syncs broker deals for every demo strategy into trades and lessons.
    """
    snap = snap or broker.snapshot(SNAPSHOT_REQUEST)
    acct, spec_raw = snap["account"], snap["spec"]
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"])

    for trade in journal.sync_deals(snap["deals"]):
        reason = _exit_reason(trade, journal)
        journal.set_exit_reason(trade["position_id"], reason)
        trade["exit_reason"] = reason
        market = (snap.get("markets") or {}).get(trade.get("symbol") or SYMBOL, snap)
        journal.add_lesson(now, trade["position_id"], "agent",
                           _lesson_text(trade, market, acct["currency"], journal.entry_info(trade["entry_id"])))

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

    own = [p for p in snap["positions"] if p.get("magic", MAGIC) == MAGIC]
    if own:
        pos = own[0]
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
    journal.tag_entry(cid, {"strategy": STRATEGY_ID, "hour_utc": latest.hour,
                            "duty_agent": (duty or {}).get("agent"), "duty_model": (duty or {}).get("model"),
                            "features": signal})
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

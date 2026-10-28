"""H-002 explorer: several M15 entry patterns on the MT5 demo, learning which work when (D-31).

Exploration phase. Each closed M15 bar, every pattern is checked; when several
match, an epsilon-greedy choice prefers patterns with better realised results
but keeps exploring. Stops and targets sit at the broker (ATR-based), and the
agent closes positions after `max_hold_bars`. Parameters live in a versioned
policy that Claude/Codex reflection may change; every trade records the policy
version, pattern, UTC hour and session. Results are exploratory, never evidence
of edge: any rule distilled from them must be re-tested on fresh data.
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import risk
from .contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
from .trade_journal import Journal

STRATEGY_ID = "H-002"
STRATEGY_VERSION = "explore"  # risk.toml approves "H-002@explore"; the policy version is tracked per trade
MAGIC = 20261029
SYMBOL = "EURUSD"
PIP = 0.0001
PATTERNS = ("breakout20", "ema_pullback", "bb_rsi_reversion", "inside_bar_break")

DEFAULT_POLICY = {
    "version": 1,
    "lots": 0.1,
    "enabled": list(PATTERNS),
    "sl_atr": 1.0, "min_sl_pips": 5.0, "tp_rr": 1.5, "max_hold_bars": 8,
    "avoid_hours_utc": [21],  # daily rollover: wide spreads, thin book
    "epsilon": 0.3, "min_samples": 3,
    "assumed_cost_pips": 0.8,  # demo spread is ~0; learning stats subtract this to stay honest
}


# indicators (pure, oldest bar first) ------------------------------------------
def ema(values, n):
    k, out = 2 / (n + 1), []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def atr(bars, n=14):
    tr = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
          for p, b in zip(bars, bars[1:])]
    return sum(tr[-n:]) / n


def rsi(closes, n=14):
    gains = [max(b - a, 0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0) for a, b in zip(closes, closes[1:])]
    g, l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for gi, li in zip(gains[n:], losses[n:]):
        g, l = (g * (n - 1) + gi) / n, (l * (n - 1) + li) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def session(hour: int) -> str:
    return "asia" if hour < 7 else "london" if hour < 12 else "overlap_ny" if hour < 17 else "late_ny"


def patterns(bars: list[dict]) -> dict[str, str]:
    """Pattern name -> side for the last closed bar. Needs at least 60 bars."""
    if len(bars) < 60:
        raise ValueError("at least 60 M15 bars required")
    closes = [b["close"] for b in bars]
    last, prev = bars[-1], bars[-2]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    out = {}
    hi20, lo20 = max(b["high"] for b in bars[-21:-1]), min(b["low"] for b in bars[-21:-1])
    if last["close"] > hi20:
        out["breakout20"] = "buy"
    elif last["close"] < lo20:
        out["breakout20"] = "sell"
    if e20[-1] > e50[-1] and last["low"] <= e20[-1] < last["close"] and last["close"] > last["open"]:
        out["ema_pullback"] = "buy"
    elif e20[-1] < e50[-1] and last["high"] >= e20[-1] > last["close"] and last["close"] < last["open"]:
        out["ema_pullback"] = "sell"
    window = closes[-20:]
    mean = sum(window) / 20
    sd = (sum((c - mean) ** 2 for c in window) / 20) ** 0.5
    r = rsi(closes[-60:])
    if last["close"] < mean - 2 * sd and r < 30:
        out["bb_rsi_reversion"] = "buy"
    elif last["close"] > mean + 2 * sd and r > 70:
        out["bb_rsi_reversion"] = "sell"
    mother, inside = bars[-3], prev
    if inside["high"] <= mother["high"] and inside["low"] >= mother["low"]:
        if last["close"] > mother["high"]:
            out["inside_bar_break"] = "buy"
        elif last["close"] < mother["low"]:
            out["inside_bar_break"] = "sell"
    return out


def choose(matches: dict[str, str], stats: dict[str, dict], policy: dict, rng: random.Random) -> str | None:
    """Epsilon-greedy over matched, enabled patterns using realised average net pips."""
    names = [m for m in matches if m in policy["enabled"]]
    if not names:
        return None
    unexplored = [m for m in names if stats.get(m, {}).get("n", 0) < policy["min_samples"]]
    if unexplored:
        return rng.choice(unexplored)
    if rng.random() < policy["epsilon"]:
        return rng.choice(names)
    return max(names, key=lambda m: stats[m]["avg_pips_after_cost"])


# policy ----------------------------------------------------------------------
def load_policy(path: Path) -> dict:
    try:
        return {**DEFAULT_POLICY, **json.loads(Path(path).read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY)


def update_policy(path: Path, journal: Journal, changes: dict, author: str, reason: str) -> dict:
    old = load_policy(path)
    unknown = set(changes) - set(DEFAULT_POLICY) - {"version"}
    if unknown:
        raise ValueError(f"unknown policy keys: {sorted(unknown)}")
    if "enabled" in changes and not set(changes["enabled"]) <= set(PATTERNS):
        raise ValueError(f"patterns must be among {PATTERNS}")
    new = {**old, **changes, "version": old["version"] + 1}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(new, indent=2))
    journal.policy_change(new["version"], datetime.now(timezone.utc), author, reason, new)
    return new


# one decision per closed M15 bar --------------------------------------------
def _t(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def step(journal: Journal, broker, limits, now: datetime, snap: dict, policy: dict, *,
         send: bool = True, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    if snap["quote"] is None:
        return {"action": "skip", "reason": "no fresh tick"}
    latest = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0) - timedelta(minutes=15)
    bars = snap.get("m15_bars") or []
    if not bars or _t(bars[-1]["time"]) != latest:
        return {"action": "skip", "reason": f"latest closed M15 bar is not {latest:%H:%M}Z"}
    key = f"{STRATEGY_ID}:{latest.isoformat()}"
    if journal.decided(key):
        return {"action": "skip", "reason": "already decided for this bar", "bar": key}
    a = atr(bars[-15:])
    base = {"strategy": STRATEGY_ID, "policy_version": policy["version"], "quote": snap["quote"],
            "signal": {"atr": a, "close": bars[-1]["close"]}}

    own = [p for p in snap["positions"] if p.get("magic") == MAGIC]
    if own:
        pos = own[0]
        held = int((latest + timedelta(minutes=15) - _t(pos["time"])).total_seconds() // 900)
        if held < policy["max_hold_bars"]:
            journal.decide(key, now, "hold", pos["side"], f"H-002 holding {held}/{policy['max_hold_bars']} bars", base)
            return {"action": "hold", "bar": key}
        cid = f"H2X-{pos['ticket']}"
        result = broker.close({"ticket": pos["ticket"], "client_order_id": cid, "symbol": SYMBOL}) if send \
            else {"status": "not_sent"}
        result["rule"] = "max_hold_bars"
        journal.order(cid, now, "exit", "sell" if pos["side"] == "buy" else "buy", pos["volume"], None,
                      result["status"], {}, result)
        journal.decide(key, now, "exit", pos["side"], f"H-002 time exit after {held} bars", {**base, "order": result})
        return {"action": "exit", "bar": key, "result": result}

    hour = latest.hour
    if hour in policy["avoid_hours_utc"]:
        journal.decide(key, now, "no_trade", None, f"H-002 avoids {hour}h UTC (policy)", base)
        return {"action": "no_trade", "bar": key}
    matches = patterns(bars)
    stats = journal.learning_stats(policy["assumed_cost_pips"])["by_pattern"]
    chosen = choose(matches, stats, policy, rng)
    base["signal"]["matches"] = matches
    if chosen is None:
        journal.decide(key, now, "no_trade", None,
                       "H-002 no enabled pattern matched" if not matches else f"H-002 matched {sorted(matches)} but disabled",
                       base)
        return {"action": "no_trade", "bar": key}
    side = matches[chosen]
    q, s, acct = snap["quote"], snap["spec"], snap["account"]
    entry = q["ask"] if side == "buy" else q["bid"]
    dist = max(policy["sl_atr"] * a, policy["min_sl_pips"] * PIP)
    sign = 1 if side == "buy" else -1
    stop = round(entry - sign * dist, s["digits"])
    target = round(entry + sign * dist * policy["tp_rr"], s["digits"])
    cid = f"H2-{latest:%y%m%d%H%M}-{side[0].upper()}"
    lots = float(policy["lots"])
    intent = OrderIntent(cid, STRATEGY_ID, STRATEGY_VERSION, SYMBOL, side, lots, "market",
                         stop_loss=stop, take_profit=target)
    spec = SymbolSpec(SYMBOL, "mt5-demo-1", s["digits"], s["point"], s["point"] * 10, s["contract_size"],
                      s["volume_min"], s["volume_max"], s["volume_step"], s["quote_currency"])
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"])
    decision = risk.evaluate(intent, limits, spec, Quote(SYMBOL, _t(q["time"]), q["bid"], q["ask"]),
                             AccountSnapshot(now, acct["currency"], acct["equity"], day_start, len(snap["positions"])),
                             now, journal.order_ids(), risk.kill_switch_active(limits))
    tags = {"strategy": STRATEGY_ID, "pattern": chosen, "hour_utc": hour, "session": session(hour),
            "policy_version": policy["version"], "features": {"matches": matches, "atr_pips": a / PIP,
                                                              "sl_pips": dist / PIP, "tp_pips": dist * policy["tp_rr"] / PIP}}
    details = {**base, "intent": intent.__dict__, "risk": decision.to_dict(), "tags": tags}
    why = f"H-002 {chosen} {side} at {hour}h UTC ({session(hour)}); SL {dist / PIP:.1f} pips, TP {dist * policy['tp_rr'] / PIP:.1f}"
    if not decision.approved:
        journal.order(cid, now, "entry", side, lots, stop, "risk_rejected", decision.to_dict(), {})
        journal.decide(key, now, "blocked", side, "risk gate: " + ", ".join(decision.reasons), details)
        return {"action": "blocked", "bar": key, "reasons": decision.reasons}
    journal.tag_entry(cid, tags)
    if not send:
        journal.decide(key, now, "signal_not_sent", side, why + " (dry)", details)
        return {"action": "signal_not_sent", "bar": key, "pattern": chosen}
    journal.order(cid, now, "entry", side, lots, stop, "sending", decision.to_dict(), {})
    try:
        result = broker.send({"mode": limits.mode, "magic": MAGIC, "intent": intent.__dict__,
                              "decision": decision.to_dict()})
    except RuntimeError as e:
        result = {"status": "error", "reason": str(e)}
    journal.order(cid, now, "entry", side, lots, stop, result["status"], decision.to_dict(), result)
    journal.decide(key, now, "entry", side, why, {**details, "order": result})
    return {"action": "entry", "bar": key, "pattern": chosen, "side": side, "result": result}

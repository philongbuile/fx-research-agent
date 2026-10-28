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

from . import econ_calendar, risk
from .contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
from .econ_calendar import NEWS_PATTERNS
from .trade_journal import PIP_SIZES, Journal

STRATEGY_ID = "H-002"
STRATEGY_VERSION = "explore"  # risk.toml approves "H-002@explore"; arm and policy version are tracked per trade
MAGIC = 20261029  # the Claude arm keeps the original H-002 magic number

# D-33: one arm per AI model, same market and patterns, own policy. Only the owner edits its policy.
ARMS = {
    "claude": {"arm": "H-002-claude", "owner": "claude", "magic": 20261029, "code": "CL"},
    "codex": {"arm": "H-002-codex", "owner": "codex", "magic": 20261030, "code": "CX"},
}
SYMBOL = "EURUSD"
PIP = 0.0001
# Owner's choice, 2026-10-28. EURUSD keeps its original ids/decision keys; others carry a 2-letter code.
SYMBOL_CODES = {"EURUSD": "", "GBPUSD": "GU", "USDJPY": "UJ", "AUDUSD": "AU", "XAUUSD": "XA",
                # owner 2026-10-29 (D-41): Asia and London session pairs
                "EURJPY": "EJ", "GBPJPY": "GJ", "AUDJPY": "AJ", "NZDUSD": "NU", "EURGBP": "EG"}
PATTERNS = ("breakout20", "ema_pullback", "bb_rsi_reversion", "inside_bar_break",
            "breakout10", "ema_cross_9_21", "macd_cross", "momentum_candle", "engulfing", "pin_bar",
            "rsi_cross_50", "rsi_extreme_fade", "three_bar_momentum", "bb_squeeze_break",
            "failed_breakout_fade", "ema50_stretch_fade") + NEWS_PATTERNS  # news patterns: D-40

DEFAULT_POLICY = {
    "version": 1,
    "lots": 0.1,
    "lots_by_symbol": {"XAUUSD": 0.1},  # gold: 1 lot = 100 USD per 1 USD move
    "symbols": list(SYMBOL_CODES),
    "risk_per_trade_pct": None,  # when set, size = equity * pct / stop distance, capped by risk.toml max lots
    "enabled": list(PATTERNS),
    "sl_atr": 1.0, "min_sl_pips": 5.0, "tp_rr": 1.5, "max_hold_bars": 8,
    "avoid_hours_utc": [21],  # daily rollover: wide spreads, thin book
    "epsilon": 0.3, "min_samples": 3,
    "entries_per_bar": None,  # None = open every matched enabled pattern not already open in this arm
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
    if l == 0:
        return 50.0 if g == 0 else 100.0  # a flat series is neutral, not overbought
    return 100 - 100 / (1 + g / l)


def session(hour: int) -> str:
    return "asia" if hour < 7 else "london" if hour < 12 else "overlap_ny" if hour < 17 else "late_ny"


def market_context(bars: list[dict], h1_bars: list[dict], quote: dict, pip: float, bar_time: datetime) -> dict:
    """Conditions at entry, stored with the trade so lessons and digests can say *when* a pattern works.

    Trend = EMA20 vs EMA50 (M15, and H1 when 50 H1 bars exist). Volatility regime =
    where the current M15 ATR(14) sits among the ATRs of the bars in the snapshot.
    """
    trend = lambda closes: ("up" if ema(closes, 20)[-1] > ema(closes, 50)[-1] else "down") if len(closes) >= 50 else None
    atrs = [atr(bars[i - 15:i]) for i in range(15, len(bars) + 1)]
    pct = sum(x <= atrs[-1] for x in atrs) / len(atrs)
    return {"m15_trend": trend([b["close"] for b in bars]), "h1_trend": trend([b["close"] for b in h1_bars]),
            "atr_pctile": round(pct, 2), "vol": "low" if pct < 1 / 3 else "high" if pct > 2 / 3 else "normal",
            "spread_pips": round((quote["ask"] - quote["bid"]) / pip, 2), "dow": bar_time.strftime("%a")}


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
    _more_patterns(bars, closes, r, out)
    return out


def _cross(fast, slow):
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        return "buy"
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        return "sell"
    return None


def _more_patterns(bars, closes, r, out):
    """Exploration patterns added 2026-10-28 (owner: as many patterns as possible).

    Deliberately simple and partly contradictory (trend vs fade). The explorer's
    statistics, not intuition, decide which ones deserve to survive.
    """
    last, prev = bars[-1], bars[-2]
    a = atr(bars[-15:])
    rng = last["high"] - last["low"]
    body = last["close"] - last["open"]

    hi10, lo10 = max(b["high"] for b in bars[-11:-1]), min(b["low"] for b in bars[-11:-1])
    if last["close"] > hi10:
        out["breakout10"] = "buy"
    elif last["close"] < lo10:
        out["breakout10"] = "sell"

    if side := _cross(ema(closes, 9), ema(closes, 21)):
        out["ema_cross_9_21"] = side

    macd = [f - s for f, s in zip(ema(closes, 12), ema(closes, 26))]
    if side := _cross(macd, ema(macd, 9)):
        out["macd_cross"] = side

    if abs(body) > 1.5 * a:
        out["momentum_candle"] = "buy" if body > 0 else "sell"

    if prev["close"] < prev["open"] and body > 0 and last["open"] <= prev["close"] and last["close"] >= prev["open"]:
        out["engulfing"] = "buy"
    elif prev["close"] > prev["open"] and body < 0 and last["open"] >= prev["close"] and last["close"] <= prev["open"]:
        out["engulfing"] = "sell"

    if rng >= 0.5 * a > 0:
        if min(last["open"], last["close"]) - last["low"] >= 0.6 * rng:
            out["pin_bar"] = "buy"  # long lower wick: lows rejected
        elif last["high"] - max(last["open"], last["close"]) >= 0.6 * rng:
            out["pin_bar"] = "sell"

    r_prev = rsi(closes[-61:-1])
    if r_prev < 50 <= r:
        out["rsi_cross_50"] = "buy"
    elif r_prev > 50 >= r:
        out["rsi_cross_50"] = "sell"
    if r < 25:
        out["rsi_extreme_fade"] = "buy"
    elif r > 75:
        out["rsi_extreme_fade"] = "sell"

    three = bars[-3:]
    if all(b["close"] > b["open"] for b in three) and closes[-3] < closes[-2] < closes[-1]:
        out["three_bar_momentum"] = "buy"
    elif all(b["close"] < b["open"] for b in three) and closes[-3] > closes[-2] > closes[-1]:
        out["three_bar_momentum"] = "sell"

    def band(end):
        w = closes[end - 20:end]
        m = sum(w) / 20
        return m, (sum((c - m) ** 2 for c in w) / 20) ** 0.5
    widths = [band(i)[1] for i in range(len(closes) - 50, len(closes))]  # bands up to the previous bar
    mean, sd = band(len(closes))
    if widths[-1] <= min(widths) * 1.0001:
        if last["close"] > mean + 2 * sd:
            out["bb_squeeze_break"] = "buy"
        elif last["close"] < mean - 2 * sd:
            out["bb_squeeze_break"] = "sell"

    hi20, lo20 = max(b["high"] for b in bars[-21:-1]), min(b["low"] for b in bars[-21:-1])
    if last["high"] > hi20 and last["close"] < hi20:
        out["failed_breakout_fade"] = "sell"
    elif last["low"] < lo20 and last["close"] > lo20:
        out["failed_breakout_fade"] = "buy"

    stretch = last["close"] - ema(closes, 50)[-1]
    if stretch > 2 * a:
        out["ema50_stretch_fade"] = "sell"
    elif stretch < -2 * a:
        out["ema50_stretch_fade"] = "buy"


def choose(matches: dict[str, str], stats: dict[str, dict], policy: dict, rng: random.Random) -> str | None:
    """Epsilon-greedy over matched, enabled patterns using realised average net pips."""
    ranked = rank(matches, stats, policy, rng)
    return ranked[0] if ranked else None


def rank(matches: dict[str, str], stats: dict[str, dict], policy: dict, rng: random.Random) -> list[str]:
    """Order matched, enabled patterns: unexplored first, then epsilon-greedy by average net pips."""
    names = [m for m in matches if m in policy["enabled"]]
    rng.shuffle(names)
    unexplored = [m for m in names if stats.get(m, {}).get("n", 0) < policy["min_samples"]]
    explored = [m for m in names if m not in unexplored]
    if rng.random() >= policy["epsilon"]:
        explored.sort(key=lambda m: stats[m]["avg_pips_after_cost"], reverse=True)
    return unexplored + explored


def size_lots(policy: dict, equity: float, stop_distance: float, spec: dict, max_lots: float) -> float:
    """Fixed lots (`lots_by_symbol` overrides `lots`), or risk-based: lose about risk_per_trade_pct at the stop.

    Risk-based sizing assumes the quote currency is the account currency (USD); for
    other quote currencies (USDJPY) it falls back to fixed lots. Rounded down to the
    volume step, never below volume_min, capped by the risk gate limit.
    """
    fixed = float((policy.get("lots_by_symbol") or {}).get(spec.get("symbol"), policy["lots"]))
    pct = policy.get("risk_per_trade_pct")
    if not pct or spec.get("quote_currency", "USD") != "USD":
        return fixed
    raw = equity * pct / 100 / (stop_distance * spec["contract_size"])
    step = spec["volume_step"]
    lots = max(spec["volume_min"], int(raw / step + 1e-9) * step)
    return round(min(lots, max_lots) if max_lots else lots, 2)


# policy ----------------------------------------------------------------------
def load_policy(path: Path) -> dict:
    try:
        return {**DEFAULT_POLICY, **json.loads(Path(path).read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY)


def update_policy(path: Path, journal: Journal, changes: dict, author: str, reason: str,
                  arm: str = "claude") -> dict:
    old = load_policy(path)
    unknown = set(changes) - set(DEFAULT_POLICY) - {"version"}
    if unknown:
        raise ValueError(f"unknown policy keys: {sorted(unknown)}")
    if "enabled" in changes and not set(changes["enabled"]) <= set(PATTERNS):
        raise ValueError(f"patterns must be among {PATTERNS}")
    new = {**old, **changes, "version": old["version"] + 1}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(new, indent=2))
    journal.policy_change(new["version"], datetime.now(timezone.utc), author, reason, new, arm=arm)
    return new


def policy_path(demo_dir: Path, arm: str) -> Path:
    return Path(demo_dir) / f"h002-policy-{arm}.json"


def arm_policy(demo_dir: Path, arm: str, journal: Journal) -> dict:
    """Load an arm's policy. First use: the Claude arm adopts the pre-D-33 single policy,
    the Codex arm starts as a copy of the Claude arm (same starting line for both)."""
    path = policy_path(demo_dir, arm)
    if not path.exists():
        legacy = Path(demo_dir) / "h002-policy.json"
        source = legacy if arm == "claude" else policy_path(demo_dir, "claude")
        if arm != "claude" and not source.exists():
            arm_policy(demo_dir, "claude", journal)
        start = load_policy(source if source.exists() else legacy)
        # Claude continues the legacy version line (its history was migrated); Codex starts at 1.
        start["version"] = journal.last_policy_version(arm) if arm == "claude" else 0
        path.write_text(json.dumps(start, indent=2))
        update_policy(path, journal, {}, "system", f"{arm} arm initialised from {source.name}", arm=arm)
    return load_policy(path)


def edit_arm_policy(demo_dir: Path, arm: str, journal: Journal, changes: dict, author: str, reason: str) -> dict:
    """Only the arm's owner may change it (author is '<agent>/<model>')."""
    if author.split("/")[0] != ARMS[arm]["owner"]:
        raise PermissionError(f"{arm} arm belongs to {ARMS[arm]['owner']}; propose the change instead")
    arm_policy(demo_dir, arm, journal)
    return update_policy(policy_path(demo_dir, arm), journal, changes, author, reason, arm=arm)


# one decision per closed M15 bar --------------------------------------------
def _t(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


ACTION_RANK = ("entry", "blocked", "exit", "hold", "no_trade", "skip", "error")


def step(journal: Journal, broker, limits, now: datetime, snap: dict, policy: dict, *,
         send: bool = True, rng: random.Random | None = None, duty: dict | None = None,
         arm: str = "claude", arm_model: str | None = None) -> dict:
    """One decision per closed M15 bar, per enabled symbol, for one arm.

    `arm_model` is the label of the model that owns the arm right now (D-35); it is
    stored with every entry so results stay attributable after a model switch.

    Per symbol: time-exits the arm's positions that reached max_hold_bars, then
    opens one position for every matched enabled pattern not already open in this
    arm on that symbol (or the best `entries_per_bar`). Each entry passes the risk gate.
    """
    rng = rng or random.Random()
    markets = snap.get("markets") or {SYMBOL: snap}
    results = {}
    for symbol in policy.get("symbols") or [SYMBOL]:
        if symbol not in markets:
            continue
        try:
            results[symbol] = _step_symbol(journal, broker, limits, now, snap, markets[symbol], symbol, policy,
                                           send=send, rng=rng, duty=duty, arm=arm, arm_model=arm_model)
        except Exception as e:  # one symbol failing must not stop the others
            results[symbol] = {"action": "error", "reason": f"{type(e).__name__}: {e}"}
        snap = {**snap, "positions": snap["positions"] + [
            {"magic": ARMS[arm]["magic"], "symbol": symbol, "placeholder": True}
            for e in results[symbol].get("entries", []) if e.get("status") == "filled"]}  # count new positions
    if not results:
        return {"action": "skip", "reason": "no enabled symbol in snapshot"}
    combined = min(results.values(), key=lambda r: ACTION_RANK.index(r["action"]) if r["action"] in ACTION_RANK else 99)
    tagged = lambda k: [{**x, "symbol": s} for s, r in results.items() for x in r.get(k, [])]
    return {"action": combined["action"], "bar": combined.get("bar", ""), "reason": combined.get("reason", ""),
            "entries": tagged("entries"), "exits": tagged("exits"), "blocked": tagged("blocked"), "symbols": results}


def _step_symbol(journal: Journal, broker, limits, now: datetime, snap: dict, market: dict, symbol: str,
                 policy: dict, *, send: bool, rng: random.Random, duty: dict | None, arm: str,
                 arm_model: str | None = None) -> dict:
    cfg = ARMS[arm]
    name, magic, code = cfg["arm"], cfg["magic"], SYMBOL_CODES.get(symbol, symbol[:2])
    pip = PIP_SIZES[symbol]
    if market["quote"] is None:
        return {"action": "skip", "reason": "no fresh tick"}
    latest = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0) - timedelta(minutes=15)
    bars = market.get("m15_bars") or []
    if not bars or _t(bars[-1]["time"]) != latest:
        return {"action": "skip", "reason": f"latest closed M15 bar is not {latest:%H:%M}Z"}
    key = f"{name}:{latest.isoformat()}" if not code else f"{name}:{symbol}:{latest.isoformat()}"
    if journal.decided(key):
        return {"action": "skip", "reason": "already decided for this bar", "bar": key}
    a = atr(bars[-15:])
    base = {"strategy": name, "arm": arm, "symbol": symbol, "policy_version": policy["version"],
            "quote": market["quote"], "signal": {"atr": a, "atr_pips": a / pip, "close": bars[-1]["close"]}}
    notes, exits, entries, blocked = [], [], [], []

    own = [p for p in snap["positions"]
           if p.get("magic") == magic and p.get("symbol", SYMBOL) == symbol and not p.get("placeholder")]
    still_open = []
    for pos in own:
        held = int((latest + timedelta(minutes=15) - _t(pos["time"])).total_seconds() // 900)
        if held < policy["max_hold_bars"]:
            still_open.append(pos)
            continue
        cid = f"H2X-{pos['ticket']}"
        result = broker.close({"ticket": pos["ticket"], "client_order_id": cid, "symbol": symbol}) if send \
            else {"status": "not_sent"}
        result["rule"] = "max_hold_bars"
        journal.order(cid, now, "exit", "sell" if pos["side"] == "buy" else "buy", pos["volume"], None,
                      result["status"], {}, result)
        exits.append({"ticket": pos["ticket"], "held": held, "status": result["status"]})
    if exits:
        notes.append(f"time exit {len(exits)} position(s)")
    open_patterns = {journal.entry_pattern(p.get("comment", "")) for p in still_open}

    hour = latest.hour
    matches = patterns(bars)
    events = snap.get("events") or []  # economic calendar (D-40); empty when the MT5 export is not running
    matches.update(econ_calendar.news_patterns(events, symbol, latest, bars))
    base["signal"]["matches"] = matches
    if hour in policy["avoid_hours_utc"]:
        notes.append(f"avoids {hour}h UTC (policy)")
        candidates = []
    else:
        stats = journal.learning_stats(policy["assumed_cost_pips"])["by_pattern"]  # shared knowledge, all arms/symbols
        candidates = [m for m in rank(matches, stats, policy, rng) if m not in open_patterns]
        if policy.get("entries_per_bar"):
            candidates = candidates[: int(policy["entries_per_bar"])]
        if matches and not candidates:
            notes.append(f"matched {sorted(matches)} but disabled or already open")

    q, s, acct = market["quote"], {**market["spec"], "symbol": symbol}, snap["account"]
    spec = SymbolSpec(symbol, "mt5-demo-1", s["digits"], s["point"], pip, s["contract_size"],
                      s["volume_min"], s["volume_max"], s["volume_step"], s["quote_currency"])
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"]) if candidates else None
    context = {**market_context(bars, market.get("h1_bars") or [], q, pip, latest),
               **econ_calendar.context(events, symbol, latest + timedelta(minutes=15))} if candidates else {}
    open_count = len(snap["positions"]) - len(exits)
    for chosen in candidates:
        side = matches[chosen]
        entry = q["ask"] if side == "buy" else q["bid"]
        dist = max(policy["sl_atr"] * a, policy["min_sl_pips"] * pip)
        sign = 1 if side == "buy" else -1
        stop = round(entry - sign * dist, s["digits"])
        target = round(entry + sign * dist * policy["tp_rr"], s["digits"])
        prefix = f"H2{cfg['code']}-" + (f"{code}-" if code else "")
        cid = f"{prefix}{latest:%y%m%d%H%M}-{PATTERNS.index(chosen):02d}{side[0].upper()}"
        lots = size_lots(policy, acct["equity"], dist, s, limits.max_lots_per_order if limits else 0.0)
        intent = OrderIntent(cid, STRATEGY_ID, STRATEGY_VERSION, symbol, side, lots, "market",
                             stop_loss=stop, take_profit=target)
        decision = risk.evaluate(intent, limits, spec, Quote(symbol, _t(q["time"]), q["bid"], q["ask"]),
                                 AccountSnapshot(now, acct["currency"], acct["equity"], day_start, open_count),
                                 now, journal.order_ids(), risk.kill_switch_active(limits))
        if not decision.approved:
            journal.order(cid, now, "entry", side, lots, stop, "risk_rejected", decision.to_dict(), {})
            blocked.append({"pattern": chosen, "reasons": decision.reasons})
            continue
        journal.tag_entry(cid, {"strategy": STRATEGY_ID, "arm": arm, "arm_model": arm_model, "symbol": symbol,
                                "pattern": chosen,
                                "hour_utc": hour, "session": session(hour), "policy_version": policy["version"],
                                "duty_agent": (duty or {}).get("agent"), "duty_model": (duty or {}).get("model"),
                                "features": {"matches": matches, "atr_pips": a / pip, "sl_pips": dist / pip,
                                             "tp_pips": dist * policy["tp_rr"] / pip, **context}})
        if not send:
            entries.append({"pattern": chosen, "side": side, "status": "not_sent"})
            continue
        journal.order(cid, now, "entry", side, lots, stop, "sending", decision.to_dict(), {})  # persist before send
        try:
            result = broker.send({"mode": limits.mode, "magic": magic, "intent": intent.__dict__,
                                  "decision": decision.to_dict()})
        except RuntimeError as e:
            result = {"status": "error", "reason": str(e)}
        journal.order(cid, now, "entry", side, lots, stop, result["status"], decision.to_dict(), result)
        entries.append({"pattern": chosen, "side": side, "lots": lots, "sl_pips": round(dist / pip, 1),
                        "status": result["status"]})
        open_count += 1

    if entries:
        action = "entry"
        notes.insert(0, "; ".join(f"{e['pattern']} {e['side']}" for e in entries) + f" ({hour}h UTC, {session(hour)})")
    elif blocked:
        action = "blocked"
        notes.append("risk gate: " + ", ".join(sorted({r for b in blocked for r in b["reasons"]})))
    elif exits:
        action = "exit"
    elif still_open:
        action = "hold"
        notes.append(f"holding {len(still_open)} position(s)")
    else:
        action = "no_trade"
        if not matches:
            notes.append("no pattern matched")
    sides = {e["side"] for e in entries}
    journal.decide(key, now, action, sides.pop() if len(sides) == 1 else None, f"{name} {symbol}: " + "; ".join(notes),
                   {**base, "entries": entries, "exits": exits, "blocked": blocked})
    return {"action": action, "bar": key, "entries": entries, "exits": exits, "blocked": blocked}

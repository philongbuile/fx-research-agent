"""Decision trace and learning log (D-44): one structured record per market evaluation or decision.

Observation only. Traces are a read model rebuilt from the journal's own records
(decisions, candidates, orders, entry_tags, trades); `sync` writes only the
`decision_traces` table and never changes what the strategies decide or send.

No chain-of-thought is collected. A trace holds the inputs the decider was given,
the codes of the rule that fired (or the codes, confidence and short reason the model
submitted with its decision), codes fxra derives from the inputs afterwards (origin
"derived", never shown as the decider's reasons) and the outcome measured later.
See docs/decision-log.md.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import demo_agent, h002, h003
from .trade_journal import PIP_SIZES, TRADES_SQL, Journal, confluence

CODES_VERSION = 1
FINAL_AFTER = timedelta(days=3)  # a trace stops being rebuilt at the latest this long after the decision
SNAPSHOT_SPAN = timedelta(hours=30)  # demo snapshots carry 120 M15 bars
REPO = Path(__file__).resolve().parents[2]
DECIDE_SKILL = {"claude": ".claude/skills/fxra-demo-decide/SKILL.md", "codex": ".agents/skills/fxra-demo-decide/SKILL.md"}
KEY = re.compile(r"^(H-00[23](?:-[a-z]+)?):(?:([A-Z]{6}):)?(.+)$")
M15 = timedelta(minutes=15)

# code -> (origin, description). rule: recorded by the script; model: submitted with `fxra demo-decide`;
# owner: owner-requested action; derived: computed by fxra from the inputs afterwards; outcome: measured later.
CODES = {
    # H-003 loop and the model's decision
    "candidate_proposed": ("rule", "The H-003 loop proposed this candidate because these patterns matched on the closed bar"),
    "awaiting_model": ("rule", "Waiting for the arm's model to take or skip it"),
    "expired_unanswered": ("rule", "No model decision before the candidate expired"),
    "approval_stale": ("rule", "Approved, but the loop could not send it before the setup went stale"),
    "model_approved": ("model", "The model took the candidate"),
    "model_skipped": ("model", "The model skipped the candidate"),
    "custom_stop": ("model", "The model chose a stop multiple other than the default"),
    "custom_target": ("model", "The model chose a target multiple other than the default"),
    # model vocabulary for --codes
    "trend_aligned": ("model", "Trend agrees with the side"),
    "trend_against": ("model", "Trend points against the side"),
    "signals_agree": ("model", "Several patterns agree"),
    "signals_opposed": ("model", "Other patterns point the other way"),
    "solo_signal": ("model", "Only one pattern supports the side"),
    "pattern_history_positive": ("model", "The supporting patterns' recorded results are positive after cost"),
    "pattern_history_negative": ("model", "The supporting patterns' recorded results are negative after cost"),
    "news_risk": ("model", "A scheduled release is close"),
    "volatility_high": ("model", "Volatility is high for this symbol"),
    "volatility_low": ("model", "Volatility is low for this symbol"),
    "spread_wide": ("model", "Spread is wide relative to the stop"),
    "extended_move": ("model", "Price is already extended; late in the move"),
    "exposure_conflict": ("model", "An open position on this or a correlated symbol conflicts or doubles up"),
    "session_thin": ("model", "Thin session or rollover hours"),
    "playbook_prefer": ("model", "A playbook rule prefers this setup"),
    "playbook_avoid": ("model", "A playbook rule avoids this setup"),
    "risk_reward_ok": ("model", "Stop and target give an acceptable risk/reward"),
    "risk_reward_poor": ("model", "Stop and target give a poor risk/reward"),
    "other": ("model", "Not covered by a code; see the written reason"),
    # H-002 script
    "pattern_entry": ("rule", "H-002 opened one position for this matched, enabled pattern"),
    "no_pattern_matched": ("rule", "No pattern matched on the closed bar"),
    "avoid_hour_policy": ("rule", "The arm's policy avoids this UTC hour"),
    "pattern_disabled_or_open": ("rule", "Matched patterns were disabled by the policy or already open in this arm"),
    "holding_position": ("rule", "The arm holds open positions on this symbol; nothing new to do"),
    # H-001B script
    "channel_breakout_24": ("rule", "The close broke the previous 24 bars' high (buy) or low (sell)"),
    "inside_channel_24": ("rule", "The close stayed inside the 24-bar channel"),
    "data_gap": ("rule", "Fewer than 25 consecutive H1 bars (weekend or broker gap)"),
    "no_exit_rule": ("rule", "Holding: no exit rule triggered"),
    # gate, orders and exits
    "risk_gate_rejected": ("rule", "The risk gate rejected the order; detail lists its reasons"),
    "order_filled": ("rule", "The demo adapter reported the order filled"),
    "order_not_sent": ("rule", "Dry run: the order was not sent"),
    "order_error": ("rule", "The order failed at the adapter or broker"),
    "max_hold_exit": ("rule", "The position reached the time limit and was closed at market"),
    "channel_exit_10": ("rule", "H-001B: the close crossed the opposite 10-bar channel"),
    "owner_close": ("owner", "The owner asked to close this strategy's positions (fxra demo-close)"),
    # derived from the recorded inputs, after the fact
    "h1_trend_aligned": ("derived", "H1 EMA20/EMA50 trend agrees with the side"),
    "h1_trend_against": ("derived", "H1 EMA20/EMA50 trend is against the side"),
    "m15_trend_aligned": ("derived", "M15 EMA20/EMA50 trend agrees with the side"),
    "m15_trend_against": ("derived", "M15 EMA20/EMA50 trend is against the side"),
    "confluence_solo": ("derived", "One pattern supported the side"),
    "confluence_2": ("derived", "Two patterns supported the side"),
    "confluence_3plus": ("derived", "Three or more patterns supported the side"),
    "opposed_signals": ("derived", "At least one pattern pointed the other way"),
    "vol_high": ("derived", "ATR in the top third of the snapshot"),
    "vol_low": ("derived", "ATR in the bottom third of the snapshot"),
    "news_window": ("derived", "A moderate or high importance release was near the bar"),
    "opposite_position_open": ("derived", "A position on the other side of this symbol was open"),
    "history_negative": ("derived", "Every supporting pattern with at least 10 trades had negative pips after cost"),
    "history_positive": ("derived", "Every supporting pattern with at least 10 trades had positive pips after cost"),
    # outcome checks, measured later
    "target_reached": ("outcome", "Price reached the target before the stop"),
    "stop_hit": ("outcome", "The stop (the stated invalidation level) was hit"),
    "time_exit": ("outcome", "Neither level was hit; closed by the time limit"),
    "rule_exit": ("outcome", "Closed by the strategy's exit rule"),
    "closed_by_owner": ("outcome", "Closed on the owner's request"),
    "other_exit": ("outcome", "Closed by the broker or by hand"),
    "shadow_target_reached": ("outcome", "Shadow (default stop/target from the bar close) reached the target"),
    "shadow_stop_hit": ("outcome", "Shadow hit the stop"),
    "shadow_time_exit": ("outcome", "Shadow hit neither level within the hold limit"),
    "skip_avoided_loss": ("outcome", "Not taken, and the shadow lost"),
    "skip_missed_gain": ("outcome", "Not taken, and the shadow gained"),
}
MODEL_CODES = [c for c, (origin, _) in CODES.items() if origin == "model" and not c.startswith(("model_", "custom_"))]
EXIT_CHECK = {"take_profit": "target_reached", "stop_loss": "stop_hit", "max_hold_bars": "time_exit",
              "max_hold_24h": "time_exit", "opposite_10_bar_channel": "rule_exit", "owner_close": "closed_by_owner"}
SHADOW_CHECK = {"take_profit": "shadow_target_reached", "stop_loss": "shadow_stop_hit", "max_hold_bars": "shadow_time_exit"}
EXIT_PREFIX = {"H2X-": "H-002", "H3X-": "H-003", "H1B-X-": "H-001B"}
COLUMNS = ("id", "at", "bar", "strategy", "arm", "symbol", "timeframe", "session", "version", "policy_version",
           "decider", "prompt_ref", "decision", "status", "confidence", "inputs", "reasons", "model_reason", "plan",
           "outcome", "final", "source", "src_at", "codes_version", "updated_at")  # excursion is written by observe
JSON_COLUMNS = ("inputs", "reasons", "plan", "outcome", "excursion")
SOURCE = "MT5 demo snapshot via fxra.mt5_execution"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def reason(code: str, detail=None) -> dict:
    return {"code": code, "origin": CODES[code][0], "detail": detail}


def validate_codes(codes) -> list[str]:
    """The model's coded reasons for `fxra demo-decide --codes`; unknown codes are refused."""
    codes = [c.strip() for c in (codes or []) if c and c.strip()]
    unknown = [c for c in codes if c not in MODEL_CODES]
    if unknown:
        raise ValueError(f"unknown reason codes {unknown}; use {MODEL_CODES}")
    return list(dict.fromkeys(codes))


def prompt_ref(agent: str, repo: Path = REPO) -> str | None:
    """Content hash of the decide skill the agent follows, so a decision names the instructions it ran under."""
    path = Path(repo) / DECIDE_SKILL.get(agent, DECIDE_SKILL["claude"])
    try:
        return "fxra-demo-decide@" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


# shared pieces ----------------------------------------------------------------
def _account(j: Journal, at: str) -> dict | None:
    row = j.db.execute("SELECT at, balance, equity, currency FROM equity WHERE at<=? ORDER BY at DESC LIMIT 1",
                       (at,)).fetchone()
    return {"equity": row["equity"], "balance": row["balance"], "currency": row["currency"], "as_of": row["at"]} \
        if row else None


def _market(bar: datetime | None, at: str, frame: timedelta, timeframe: str, **values) -> dict:
    return {"source": SOURCE, "timeframe": timeframe, "bar_open": bar.isoformat() if bar else None,
            "bar_close": (bar + frame).isoformat() if bar else None, "observed_at": at,
            **{k: v for k, v in values.items() if v is not None}}


def derived_codes(side: str, ctx: dict, patterns: dict | None = None) -> list[dict]:
    """Context codes computed from the recorded inputs, for grouping; not the decider's reasons."""
    out = []
    for tf in ("h1", "m15"):
        trend = ctx.get(f"{tf}_trend")
        if trend:
            out.append(reason(f"{tf}_trend_{'aligned' if (trend == 'up') == (side == 'buy') else 'against'}", trend))
    conf = (patterns or {}).get("confluence") or confluence(ctx if "matches" in ctx else None, side)
    if conf:
        base = conf.split(" · ")[0]
        out.append(reason({"solo": "confluence_solo", "2 agree": "confluence_2"}.get(base, "confluence_3plus"), conf))
        if "opposed" in conf:
            against = (patterns or {}).get("against") or sorted(k for k, v in (ctx.get("matches") or {}).items() if v != side)
            out.append(reason("opposed_signals", ", ".join(against)))
    if ctx.get("vol") in ("high", "low"):
        out.append(reason(f"vol_{ctx['vol']}", f"ATR pctile {ctx.get('atr_pctile')}"))
    if ctx.get("event") not in (None, "none"):
        out.append(reason("news_window", f"{ctx.get('event')}: {ctx.get('event_currency')} {ctx.get('event_name')}"))
    if any(p.get("side") != side for p in ctx.get("open_positions_symbol") or []):
        out.append(reason("opposite_position_open"))
    stats = [s for s in ((patterns or {}).get("pattern_stats") or {}).values() if s.get("n", 0) >= 10]
    if stats and all(s["avg_pips_after_cost"] < 0 for s in stats):
        out.append(reason("history_negative"))
    elif stats and all(s["avg_pips_after_cost"] > 0 for s in stats):
        out.append(reason("history_positive"))
    return out


def _order(j: Journal, cid: str) -> dict | None:
    rows = j._rows("SELECT * FROM orders WHERE client_order_id=?", cid)
    if not rows:
        return None
    return {**rows[0], "risk": json.loads(rows[0]["risk"]), "result": json.loads(rows[0]["result"])}


def _order_reason(order: dict | None) -> list[dict]:
    if order is None:
        return []
    if order["status"] == "risk_rejected":
        return [reason("risk_gate_rejected", ", ".join(order["risk"].get("reasons") or []))]
    if order["status"] in ("filled", "already_filled", "already_open"):
        return [reason("order_filled")]
    if order["status"] == "not_sent":
        return [reason("order_not_sent")]
    if order["status"] == "sending":
        return []
    return [reason("order_error", order["result"].get("reason") or order["status"])]


def _digits(pip: float) -> int:
    return len(f"{pip:f}".rstrip("0").split(".")[1]) + 1  # quote digits: pip 0.0001 -> 5, 0.01 -> 3, 0.1 -> 2


def _plan(side, entry_ref, dist, tp_rr, max_hold_bars, pip=0.0001, order=None, basis="order") -> dict:
    sign, digits = 1 if side == "buy" else -1, _digits(pip)
    stop = order["stop_loss"] if order and order.get("stop_loss") else round(entry_ref - sign * dist, digits)
    target = round(entry_ref + sign * dist * tp_rr, digits) if tp_rr else None
    return {"basis": basis, "side": side, "entry_ref": entry_ref, "stop": stop, "target": target,
            "stop_distance": dist, "tp_rr": tp_rr, "lots": order["lots"] if order else None,
            "risk": {"approved": order["risk"].get("approved"), "reasons": order["risk"].get("reasons")}
            if order and order["risk"] else None,
            "invalidation": [{"code": "stop_loss", "price": stop},
                             *([{"code": "take_profit", "price": target}] if target else []),
                             {"code": "max_hold_bars", "bars": max_hold_bars}]}


def _trade_outcome(j: Journal, entry_id: str, side: str, plan: dict | None, pip: float) -> dict | None:
    rows = j._rows("SELECT * FROM trades WHERE entry_id=?", entry_id)
    if not rows:
        return None
    t, sign = rows[0], 1 if side == "buy" else -1
    out = {"position_id": t["position_id"], "open_time": t["open_time"], "open_price": t["open_price"],
           "volume": t["volume"], "closed": t["net"] is not None}
    if plan and plan.get("entry_ref") is not None:
        out["slippage_pips"] = round(sign * (t["open_price"] - plan["entry_ref"]) / pip, 2)  # positive = adverse
    if out["closed"]:
        out.update({k: t[k] for k in ("close_time", "close_price", "exit_reason", "net", "profit", "commission", "swap")})
        out["held_min"] = round((_t(t["close_time"]) - _t(t["open_time"])).total_seconds() / 60, 1)
        if plan and plan.get("stop_distance"):
            out["R"] = round(sign * (t["close_price"] - t["open_price"]) / plan["stop_distance"], 3)
    return out


def _outcome(trade: dict | None = None, shadow: dict | None = None, taken: bool = True) -> dict | None:
    if trade is None and shadow is None:
        return None
    checks, verdict = [], None
    if trade and trade["closed"]:
        checks.append(EXIT_CHECK.get(trade["exit_reason"], "other_exit"))
        verdict = "win" if trade["net"] > 0 else "loss" if trade["net"] < 0 else "flat"
    if shadow:
        checks.append(SHADOW_CHECK.get(shadow["exit"], "shadow_time_exit"))
        if not taken:
            checks.append("skip_avoided_loss" if shadow["R"] < 0 else "skip_missed_gain" if shadow["R"] > 0 else None)
            verdict = "avoided_loss" if shadow["R"] < 0 else "missed_gain" if shadow["R"] > 0 else "flat"
    return {"trade": trade, "shadow": shadow, "checks": [c for c in checks if c], "verdict": verdict}


class _Ctx:
    """Per-sync lookups: policy history, so traces name the version in force at the time."""

    def __init__(self, j: Journal, now: datetime):
        self.j, self.now = j, now
        self.policies = {(r["arm"], r["version"]): r for r in
                         j._rows("SELECT arm, version, at, policy FROM arm_policy_changes ORDER BY at")}

    def policy(self, arm: str, version) -> dict:
        row = self.policies.get((arm, version))
        return {**h002.DEFAULT_POLICY, **(json.loads(row["policy"]) if row else {})}

    def version_at(self, arm: str, at: str):
        versions = [v for (a, v), r in self.policies.items() if a == arm and r["at"] <= at]
        return max(versions) if versions else None

    def old(self, at: str) -> bool:
        return self.now - _t(at) > FINAL_AFTER


def _trace(**kw) -> dict:
    base = {"bar": None, "arm": None, "session": None, "policy_version": None, "prompt_ref": None, "decision": None,
            "confidence": None, "model_reason": None, "plan": None, "outcome": None, "final": 0,
            "codes_version": CODES_VERSION}
    return {**base, **kw}


# builders: one journal row -> traces --------------------------------------------
def from_candidate(cx: _Ctx, c: dict) -> list[dict]:
    """H-003: one trace per candidate and arm."""
    ctx, pats, side, symbol = c["context"], c["patterns"], c["side"], c["symbol"]
    pip, bar = PIP_SIZES.get(symbol, 0.0001), _t(c["bar"])
    pol, h002_arm = h003.DEFAULT_POLICY, h003.ARMS[c["arm"]]["h002_arm"]
    took = c["sl_atr"] is not None
    decided = c["decided_at"] is not None
    decision = side if took else "no_trade" if decided or c["status"] == "expired" else None
    reasons = [reason("candidate_proposed", ", ".join(pats.get("for") or []))]
    if decided:
        reasons.append(reason("model_approved" if took else "model_skipped"))
        reasons += [reason(code) for code in c.get("reason_codes") or [] if code in CODES]
        if took and c["sl_atr"] != pol["sl_atr"]:
            reasons.append(reason("custom_stop", f"{c['sl_atr']} x ATR (default {pol['sl_atr']})"))
        if took and c["tp_rr"] != pol["tp_rr"]:
            reasons.append(reason("custom_target", f"{c['tp_rr']}R (default {pol['tp_rr']})"))
    if c["status"] == "pending":
        reasons.append(reason("awaiting_model"))
    elif c["status"] == "expired":
        reasons.append(reason("approval_stale" if took else "expired_unanswered"))
    order = _order(cx.j, c["client_order_id"]) if c["client_order_id"] else None
    reasons += _order_reason(order)
    if c["status"] == "error" and order is None:
        reasons.append(reason("order_error", (c["result"] or {}).get("reason")))
    reasons += derived_codes(side, ctx, pats)
    sl_atr, tp_rr = (c["sl_atr"], c["tp_rr"]) if took else (pol["sl_atr"], pol["tp_rr"])
    dist = h003.stop_distance(ctx["atr"], pip, sl_atr, pol["min_sl_pips"])
    sign = 1 if side == "buy" else -1
    if order and order.get("stop_loss"):  # the loop set the stop at stop distance from the quote it sent at
        plan = _plan(side, round(order["stop_loss"] + sign * dist, _digits(pip)), dist, tp_rr, pol["max_hold_bars"], pip,
                     order=order)
    else:
        plan = _plan(side, ctx["close"], dist, tp_rr, pol["max_hold_bars"], pip, order=order, basis="shadow")
    trade = _trade_outcome(cx.j, c["client_order_id"], side, plan, pip) if order else None
    if trade:
        trade["entry_vs_bar_close_pips"] = round(sign * (trade["open_price"] - ctx["close"]) / pip, 2)
    outcome = _outcome(trade, c["shadow"], taken=took and order is not None and order["status"] != "risk_rejected")
    expects_trade = order is not None and order["status"] in ("filled", "sending")
    final = (c["shadow"] is not None and c["status"] not in ("pending", "approved")
             and (not expects_trade or bool(trade and trade["closed"]))) or cx.old(c["created_at"])
    keep = ("m15_trend", "h1_trend", "atr_pctile", "vol", "spread_pips", "dow", "event", "event_name",
            "event_currency", "event_minutes", "event_impact", "atr_pips", "default_sl_pips", "default_tp_pips",
            "open_positions_symbol", "open_positions_total", "matches")
    return [_trace(
        id=c["id"], at=c["decided_at"] or c["created_at"], bar=c["bar"], strategy="H-003", arm=h003.ARMS[c["arm"]]["arm"],
        symbol=symbol, timeframe="M15", session=ctx.get("session") or h002.session(bar.hour),
        version=f"{h003.STRATEGY_ID}@{h003.STRATEGY_VERSION}",
        policy_version=cx.version_at(h002_arm, c["created_at"]), decider=c["decider"] or "model (no decision)",
        prompt_ref=c.get("prompt_ref"), decision=decision, status=c["status"], confidence=c.get("confidence"),
        inputs={"market": _market(bar, c["created_at"], M15, "M15", close=ctx.get("close"), atr=ctx.get("atr")),
                "features": {"patterns_for": pats.get("for"), "patterns_against": pats.get("against"),
                             "confluence": pats.get("confluence"), "pattern_stats": pats.get("pattern_stats"),
                             **{k: ctx[k] for k in keep if k in ctx}},
                "account": _account(cx.j, c["created_at"]), "presented_at": c["created_at"],
                "memory_read": json.loads(c.get("memory_refs") or "null"),
                "expires_at": c["expires_at"]},
        reasons=reasons, model_reason=c["reason"], plan=plan, outcome=outcome, final=int(final),
        source=f"candidates:{c['id']}", src_at=c["created_at"])]


def _h002_cid(arm: str, symbol: str, bar: datetime, pattern: str, side: str) -> str | None:
    if arm not in h002.ARMS or pattern not in h002.PATTERNS:
        return None
    code = h002.SYMBOL_CODES.get(symbol, symbol[:2])
    return (f"H2{h002.ARMS[arm]['code']}-" + (f"{code}-" if code else "")
            + f"{bar:%y%m%d%H%M}-{h002.PATTERNS.index(pattern):02d}{side[0].upper()}")


def from_decision(cx: _Ctx, row: dict) -> list[dict]:
    """One `decisions` row -> its traces (none for exits, which come from their orders)."""
    if row["action"] in ("exit", "candidates"):
        return []
    m = KEY.match(row["bar"])
    if m is None:
        return _from_h001b(cx, row)
    if m.group(1).startswith("H-003"):
        return _from_h003_no_trade(cx, row, m)
    return _from_h002(cx, row, m)


def _from_h003_no_trade(cx, row, m) -> list[dict]:
    arm, bar, symbol = m.group(1), _t(m.group(3)), m.group(2) or "EURUSD"
    return [_trace(
        id=row["bar"], at=row["at"], bar=bar.isoformat(), strategy="H-003", arm=arm, symbol=symbol, timeframe="M15",
        session=h002.session(bar.hour), version=f"{h003.STRATEGY_ID}@{h003.STRATEGY_VERSION}", decider="script",
        decision="no_trade", status="no_candidate",
        inputs={"market": _market(bar, row["at"], M15, "M15"), "features": {"matches": row["details"].get("matches")},
                "account": _account(cx.j, row["at"])},
        reasons=[reason("no_pattern_matched")], final=1, source=f"decisions:{row['bar']}", src_at=row["at"])]


def _from_h002(cx, row, m) -> list[dict]:
    d, name = row["details"], m.group(1)
    arm = d.get("arm") or (name.split("-")[-1] if name != "H-002" else "claude")
    symbol, bar = m.group(2) or d.get("symbol") or "EURUSD", _t(m.group(3))
    pip, sig, quote = PIP_SIZES.get(symbol, 0.0001), d.get("signal") or {}, d.get("quote") or {}
    pol = cx.policy(arm, d.get("policy_version"))
    common = dict(at=row["at"], bar=bar.isoformat(), strategy="H-002", arm=f"H-002-{arm}", symbol=symbol,
                  timeframe="M15", session=h002.session(bar.hour), version=f"{h002.STRATEGY_ID}@{h002.STRATEGY_VERSION}",
                  policy_version=d.get("policy_version"), decider="script", source=f"decisions:{row['bar']}",
                  src_at=row["at"])
    market = _market(bar, row["at"], M15, "M15", close=sig.get("close"), atr=sig.get("atr"),
                     quote={k: quote.get(k) for k in ("bid", "ask", "time")} if quote else None)
    account = _account(cx.j, row["at"])
    out = []
    matches = sig.get("matches") or {}
    picked = [(e["pattern"], e["side"], e.get("status")) for e in d.get("entries") or []] + \
             [(b["pattern"], matches.get(b["pattern"]), "risk_rejected") for b in d.get("blocked") or []]
    for pattern, side, status in picked:
        if side not in ("buy", "sell"):
            continue
        cid = _h002_cid(arm, symbol, bar, pattern, side)
        order = _order(cx.j, cid) if cid else None
        if order is None:
            cid = f"{row['bar']}#{pattern}"
        tag = cx.j.db.execute("SELECT features FROM entry_tags WHERE entry_id=?", (cid,)).fetchone()
        features = json.loads(tag[0]) if tag else {"matches": matches}
        entry_ref = quote.get("ask" if side == "buy" else "bid")
        dist = features.get("sl_pips", 0) * pip or max(pol["sl_atr"] * (sig.get("atr") or 0), pol["min_sl_pips"] * pip)
        plan = _plan(side, entry_ref, dist, pol["tp_rr"], pol["max_hold_bars"], pip, order=order) if entry_ref else None
        trade = _trade_outcome(cx.j, cid, side, plan, pip) if order else None
        final = order is None or order["status"] not in ("filled", "sending") or bool(trade and trade["closed"])
        out.append(_trace(**common, id=cid, decision=side, status=order["status"] if order else status or "unknown",
                          inputs={"market": market, "features": features, "account": account},
                          reasons=[reason("pattern_entry", pattern), *_order_reason(order),
                                   *derived_codes(side, features)],
                          plan=plan, outcome=_outcome(trade), final=int(final or cx.old(row["at"]))))
    if out:
        return out
    notes, reasons = row["reason"], []
    if "no pattern matched" in notes:
        reasons.append(reason("no_pattern_matched"))
    if "avoids" in notes:
        reasons.append(reason("avoid_hour_policy", f"{bar.hour}h UTC"))
    if "disabled or already open" in notes:
        reasons.append(reason("pattern_disabled_or_open", ", ".join(sorted(matches))))
    held = re.search(r"holding (\d+) position", notes)
    if held:
        reasons.append(reason("holding_position", f"{held.group(1)} open"))
    return [_trace(**common, id=row["bar"], decision="hold" if row["action"] == "hold" else "no_trade",
                   status=row["action"], inputs={"market": market, "features": {"matches": matches}, "account": account},
                   reasons=reasons, final=1)]


def _from_h001b(cx, row) -> list[dict]:
    d, bar, action = row["details"], _t(row["bar"]), row["action"]
    sig, quote, side = d.get("signal") or {}, d.get("quote") or {}, row["side"]
    acct = d.get("account") or {}
    entry = action in ("entry", "blocked", "signal_not_sent")
    decision = side if entry else {"hold": "hold"}.get(action, "no_trade")
    reasons = []
    if entry:
        reasons.append(reason("channel_breakout_24", row["reason"] if action == "entry" else None))
    elif action == "hold":
        reasons.append(reason("no_exit_rule", row["reason"]))
    elif row["reason"].startswith("fewer than"):
        reasons.append(reason("data_gap"))
    else:
        reasons.append(reason("inside_channel_24", row["reason"]))
    plan = trade = None
    if entry:
        cid = demo_agent.entry_id(bar, side)
        order = _order(cx.j, cid)
        reasons += _order_reason(order)
        if action == "signal_not_sent":
            reasons.append(reason("order_not_sent"))
        ref = quote.get("ask" if side == "buy" else "bid")
        if ref and sig.get("atr"):
            plan = _plan(side, ref, demo_agent.STOP_ATR * sig["atr"], None, demo_agent.MAX_HOLD_HOURS, order=order)
            plan["invalidation"].append({"code": "opposite_10_bar_channel",
                                         "price": sig.get("long_exit_below" if side == "buy" else "short_exit_above")})
        trade = _trade_outcome(cx.j, cid, side, plan, PIP_SIZES["EURUSD"]) if order else None
    final = not entry or trade is None and action != "entry" or bool(trade and trade["closed"]) or cx.old(row["at"])
    return [_trace(
        id=f"H-001B:{row['bar']}", at=row["at"], bar=row["bar"], strategy="H-001B", arm="H-001B", symbol="EURUSD",
        timeframe="H1", session=h002.session(bar.hour), version=f"{demo_agent.STRATEGY_ID}@{demo_agent.STRATEGY_VERSION}",
        decider="script", decision=decision, status=action,
        inputs={"market": _market(bar, row["at"], timedelta(hours=1), "H1", close=sig.get("close"), atr=sig.get("atr"),
                                  quote={k: quote.get(k) for k in ("bid", "ask", "time")} if quote else None),
                "features": {k: v for k, v in sig.items() if k not in ("close", "atr")},
                "account": {**{k: acct.get(k) for k in ("equity", "balance", "currency")},
                            "open_positions": len(d.get("positions") or [])} if acct else _account(cx.j, row["at"])},
        reasons=reasons, plan=plan, outcome=_outcome(trade), final=int(final),
        source=f"decisions:{row['bar']}", src_at=row["at"])]


def from_exit_order(cx: _Ctx, order: dict) -> list[dict]:
    """One `close` trace per exit order (time exit, channel exit, owner close)."""
    prefix = next((p for p in EXIT_PREFIX if order["client_order_id"].startswith(p)), None)
    if prefix is None:
        return []
    result = json.loads(order["result"]) if isinstance(order["result"], str) else order["result"]
    ticket = order["client_order_id"][len(prefix):]
    rows = cx.j._rows(TRADES_SQL + " WHERE t.position_id=?", int(ticket)) if ticket.isdigit() else []
    t = rows[0] if rows else {}
    strategy = EXIT_PREFIX[prefix]
    arm_row = cx.j.db.execute("SELECT arm FROM entry_tags WHERE entry_id=?", (t.get("entry_id"),)).fetchone()
    arm = strategy if strategy == "H-001B" else f"{strategy}-{(arm_row and arm_row[0]) or 'claude'}"
    rule = result.get("rule")
    if rule == "owner_close":
        reasons, decider = [reason("owner_close", result.get("reason"))], f"owner:{result.get('author')}"
    else:
        code = {"max_hold_bars": "max_hold_exit", "max_hold_24h": "max_hold_exit",
                "opposite_10_bar_channel": "channel_exit_10"}.get(rule)
        reasons, decider = ([reason(code, rule)] if code else []), "script"
    reasons += _order_reason({**order, "risk": {}, "result": result})
    trade = None
    if t:
        trade = {k: t.get(k) for k in ("position_id", "open_time", "open_price", "close_time", "close_price",
                                       "exit_reason", "net", "profit", "commission", "swap", "entry_id")}
        trade["closed"] = t.get("net") is not None  # no slippage: the exit's requested price is not recorded
    final = order["status"] != "sending" and (trade is None or trade["closed"]) or cx.old(order["at"])
    version = {"H-002": f"{h002.STRATEGY_ID}@{h002.STRATEGY_VERSION}", "H-003": f"{h003.STRATEGY_ID}@{h003.STRATEGY_VERSION}",
               "H-001B": f"{demo_agent.STRATEGY_ID}@{demo_agent.STRATEGY_VERSION}"}[strategy]
    at = _t(order["at"])
    return [_trace(
        id=order["client_order_id"], at=order["at"], strategy=strategy, arm=arm, symbol=t.get("symbol") or "unknown",
        timeframe="H1" if strategy == "H-001B" else "M15", session=h002.session(at.hour), version=version,
        policy_version=t.get("policy_version"), decider=decider, decision="close", status=order["status"],
        inputs={"position": {k: t.get(k) for k in ("position_id", "side", "volume", "open_time", "open_price",
                                                  "entry_id")} if t else None,
                "account": _account(cx.j, order["at"])},
        reasons=reasons, outcome=_outcome(trade) if trade else None, final=int(final),
        source=f"orders:{order['client_order_id']}", src_at=order["at"])]


# sync, observe, query ----------------------------------------------------------
def _upsert(j: Journal, traces: list[dict], now: datetime):
    cols = ", ".join(COLUMNS)
    sql = (f"INSERT INTO decision_traces ({cols}) VALUES ({', '.join('?' * len(COLUMNS))}) ON CONFLICT(id) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id"))
    rows = []
    for t in traces:
        t = {**t, "updated_at": now.isoformat()}
        rows.append([json.dumps(t[c], default=str) if c in JSON_COLUMNS and t[c] is not None else t[c] for c in COLUMNS])
    with j.db:
        j.db.executemany(sql, rows)


def _watermark(j: Journal, kind: str) -> str:
    row = j.db.execute("SELECT max(src_at) FROM decision_traces WHERE source>=? AND source<?",
                       (f"{kind}:", f"{kind};")).fetchone()
    return row[0] or ""


def _decision_rows(j: Journal, where: str, *args) -> list[dict]:
    return [{**r, "details": json.loads(r["details"])} for r in j._rows(f"SELECT * FROM decisions WHERE {where}", *args)]


def sync(j: Journal, now: datetime | None = None, snap: dict | None = None) -> dict:
    """Build new traces and rebuild non-final ones from the journal. Writes only `decision_traces`."""
    now = now or datetime.now(timezone.utc)
    cx = _Ctx(j, now)
    # sources to (re)build: rows at or after the last one traced (>=: a CLI sync may run mid-tick), new
    # candidates, and every source whose trace is not final yet
    todo = {"decisions": {r["bar"] for r in j._rows("SELECT bar FROM decisions WHERE at>=?", _watermark(j, "decisions"))},
            "orders": {r["client_order_id"] for r in j._rows("SELECT client_order_id FROM orders WHERE kind='exit'"
                                                             " AND at>=?", _watermark(j, "orders"))},
            "candidates": {r["id"] for r in j._rows("SELECT id FROM candidates WHERE id NOT IN"
                                                    " (SELECT id FROM decision_traces)")}}
    for r in j._rows("SELECT DISTINCT source FROM decision_traces WHERE final=0"):
        kind, key = r["source"].split(":", 1)
        todo.setdefault(kind, set()).add(key)
    traces = []
    ids = sorted(todo["candidates"])
    for c in (j.candidates(ids=ids, limit=len(ids)) if ids else []):
        traces += from_candidate(cx, c)
    for key in sorted(todo["decisions"]):
        for row in _decision_rows(j, "bar=?", key):
            traces += from_decision(cx, row)
    for key in sorted(todo["orders"]):
        for o in j._rows("SELECT * FROM orders WHERE client_order_id=?", key):
            traces += from_exit_order(cx, o)
    if traces:
        _upsert(j, traces, now)
    out = {"at": now.isoformat(), "built": len(traces),
           "open": j.db.execute("SELECT count(*) FROM decision_traces WHERE final=0").fetchone()[0]}
    if snap is not None:
        out["excursions"] = observe(j, snap, now)
    return out


def observe(j: Journal, snap: dict, now: datetime) -> int:
    """MFE/MAE of entries and shadows from the tick's M15 bars, once the trade or shadow horizon is over."""
    markets = snap.get("markets") or {}
    rows = j._rows("SELECT id, bar, symbol, plan, outcome FROM decision_traces WHERE excursion IS NULL"
                   " AND plan IS NOT NULL AND outcome IS NOT NULL AND at>=?",  # skipped candidates have a plan too
                   (now - SNAPSHOT_SPAN).isoformat())
    done = []
    for r in rows:
        bars = (markets.get(r["symbol"]) or {}).get("m15_bars") or []
        plan, outcome = json.loads(r["plan"]), json.loads(r["outcome"])
        trade, shadow = outcome.get("trade"), outcome.get("shadow")
        if trade and trade.get("closed"):
            basis, item = "trade", {"side": plan["side"], "open_time": trade["open_time"], "close_time": trade["close_time"],
                                    "open_price": trade["open_price"], "close_price": trade["close_price"]}
        elif shadow and plan["basis"] == "shadow":
            start = _t(r["bar"]) + M15
            basis, item = "shadow", {"side": plan["side"], "open_time": start.isoformat(),
                                     "close_time": (start + (shadow["bars"] - 1) * M15).isoformat(),
                                     "open_price": plan["entry_ref"], "close_price": plan["entry_ref"]}
        else:
            continue
        if not bars or _t(bars[0]["time"]) > _t(item["open_time"]) - M15 \
                or _t(bars[-1]["time"]) + M15 < _t(item["close_time"]):
            continue  # the snapshot does not cover the whole hold (yet, or any more)
        best, worst, frame = demo_agent.excursion(item, {"m15_bars": bars})
        pip, dist = PIP_SIZES.get(r["symbol"], 0.0001), plan.get("stop_distance")
        done.append((json.dumps({"basis": basis, "frame": frame, "mfe_pips": round(best / pip, 1),
                                 "mae_pips": round(worst / pip, 1),
                                 "mfe_R": round(best / dist, 2) if dist else None,
                                 "mae_R": round(-worst / dist, 2) if dist else None, "approximate": True}), r["id"]))
    if done:
        with j.db:
            j.db.executemany("UPDATE decision_traces SET excursion=? WHERE id=?", done)
    return len(done)


FILTERS = ("symbol", "strategy", "version", "decision", "decider", "since", "until")


def query(j: Journal, *, symbol=None, strategy=None, version=None, decision=None, decider=None, since=None,
          until=None, limit=100, offset=0) -> dict:
    """Traces newest first with the filters the dashboard offers, plus facets and the code vocabulary."""
    where, args = [], []
    for col, value in (("symbol", symbol), ("version", version)):
        if value:
            where.append(f"{col}=?")
            args.append(value)
    if strategy:
        where.append("(strategy=? OR arm=?)")
        args += [strategy, strategy]
    if decision == "pending":
        where.append("decision IS NULL")
    elif decision:
        where.append("decision=?")
        args.append(decision)
    if decider:
        where.append("decider LIKE ?")
        args.append(f"%{decider}%")
    if since:
        where.append("at>=?")
        args.append(since)
    if until:
        where.append("at<?")
        args.append(until if "T" in until else (datetime.fromisoformat(until) + timedelta(days=1)).date().isoformat())
    sql = " FROM decision_traces" + (" WHERE " + " AND ".join(where) if where else "")
    total = j.db.execute("SELECT count(*)" + sql, args).fetchone()[0]
    rows = j._rows("SELECT *" + sql + " ORDER BY at DESC, id LIMIT ? OFFSET ?", *args, int(limit), int(offset))
    for r in rows:
        for k in JSON_COLUMNS:
            r[k] = json.loads(r[k]) if r[k] else None
    facet = lambda col: [r[0] for r in j.db.execute(f"SELECT DISTINCT {col} FROM decision_traces WHERE {col} IS NOT NULL"
                                                     f" ORDER BY {col}")]
    return {"traces": rows, "total": total,
            "facets": {"symbol": facet("symbol"), "strategy": list(dict.fromkeys(facet("strategy") + facet("arm"))),
                       "version": facet("version"), "decision": facet("decision") + ["pending"],
                       "decider": facet("decider")},
            "codes": {c: {"origin": o, "description": d} for c, (o, d) in CODES.items()}}

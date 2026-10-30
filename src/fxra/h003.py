"""H-003: the model decides each entry (D-42).

Each closed M15 bar the loop turns the H-002 pattern matches into *candidates*,
one per symbol, bar and side (patterns pointing the same way are one idea, not
several positions). Nothing is sent until the arm's model approves a candidate
with a written reason (`fxra demo-decide`); the model may choose the stop and
target multiples within bounds, never the lots. The loop then sends approved
candidates through the same risk gate and demo adapter as H-002. Candidates
expire unanswered after `expiry_minutes`.

Every candidate, taken or not, gets a shadow outcome from the following bars with
the default stop/target, so the question "does the model's filter beat taking
every candidate?" can be answered. Exploratory demo results, never evidence of edge.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import econ_calendar, h002, memory_setup, risk
from .contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
from .trade_journal import PIP_SIZES, Journal, _group, confluence

STRATEGY_ID = "H-003"
STRATEGY_VERSION = "gated-v1"  # risk.toml must approve "H-003@gated-v1"
# Lots and symbols follow the named H-002 arm (owner: same lots). Both arms see the same candidates.
ARMS = {"claude": {"arm": "H-003-claude", "owner": "claude", "magic": 20261031, "code": "GC", "h002_arm": "claude"},
        "codex": {"arm": "H-003-codex", "owner": "codex", "magic": 20261032, "code": "GX", "h002_arm": "codex"}}

DEFAULT_POLICY = {
    "sl_atr": 1.5, "min_sl_pips": 8.0, "tp_rr": 1.5, "max_hold_bars": 8,
    "sl_atr_bounds": [1.0, 3.0], "tp_rr_bounds": [1.0, 3.0],
    "expiry_minutes": 12, "assumed_cost_pips": 0.8,
}
STATUSES = ("pending", "approved", "skipped", "expired", "sent", "rejected", "error")
DECIDED = ("approved", "sent", "rejected", "error", "skipped")  # the model answered; expired ones did not


def _t(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def policy(demo_dir: Path, arm: str, journal: Journal) -> dict:
    """H-003 parameters, with lots and symbols taken from the linked H-002 arm."""
    base = h002.arm_policy(demo_dir, ARMS[arm]["h002_arm"], journal)
    return {**DEFAULT_POLICY, **{k: base[k] for k in ("lots", "lots_by_symbol", "symbols", "risk_per_trade_pct")}}


def candidate_id(arm: str, symbol: str, bar: datetime, side: str) -> str:
    code = h002.SYMBOL_CODES.get(symbol, symbol[:2])
    return f"H3{ARMS[arm]['code']}-" + (f"{code}-" if code else "") + f"{bar:%y%m%d%H%M}-{side[0].upper()}"


def stop_distance(atr_price: float, pip: float, sl_atr: float, min_sl_pips: float) -> float:
    return max(sl_atr * atr_price, min_sl_pips * pip)


# shadow outcome: what the default stop/target would have done -------------
def shadow(candidate: dict, bars: list[dict], pol: dict) -> dict | None:
    """R after assumed cost from the bars after the candidate's bar, or None until max_hold_bars have closed.

    Entry at the candidate bar's close; when one bar touches both stop and target the
    stop counts (conservative). Approximate: M15 bars, no spread path.
    """
    ctx, pip = candidate["context"], PIP_SIZES.get(candidate["symbol"], 0.0001)
    bar = _t(candidate["bar"])
    after = [b for b in bars if _t(b["time"]) > bar][: pol["max_hold_bars"]]
    if len(after) < pol["max_hold_bars"]:
        return None
    entry, dist = ctx["close"], stop_distance(ctx["atr"], pip, pol["sl_atr"], pol["min_sl_pips"])
    sign = 1 if candidate["side"] == "buy" else -1
    stop, target = entry - sign * dist, entry + sign * dist * pol["tp_rr"]
    r, exit_ = None, "max_hold_bars"
    for i, b in enumerate(after, 1):
        hit_stop = b["low"] <= stop if sign > 0 else b["high"] >= stop
        hit_target = b["high"] >= target if sign > 0 else b["low"] <= target
        if hit_stop:
            r, exit_ = -1.0, "stop_loss"
            break
        if hit_target:
            r, exit_ = pol["tp_rr"], "take_profit"
            break
    if r is None:
        r = sign * (after[-1]["close"] - entry) / dist
    return {"R": round(r - pol["assumed_cost_pips"] * pip / dist, 3), "exit": exit_, "bars": i}


# feedback: every decided candidate becomes a lesson once its shadow outcome is known --
def verdict(c: dict, s: dict) -> str:
    if c["status"] != "skipped":
        return "good_take" if s["R"] > 0 else "bad_take"
    return "missed_winner" if s["R"] > 0 else "good_skip"


def _codes(c: dict) -> list[str]:
    """Reason codes as a list; the journal decodes them, older rows may still hold JSON text."""
    codes = c.get("reason_codes") or []
    return json.loads(codes) if isinstance(codes, str) else list(codes)


def lesson_key(c: dict) -> str:
    """The start of a decision lesson's text, which identifies its candidate."""
    took = "skipped" if c["status"] == "skipped" else "took"
    return f"{ARMS[c['arm']]['arm']} decision: {took} {c['side']} {c['symbol']} on the {c['bar'][:16]}Z"


def backfill_decision_lessons(journal: Journal, now: datetime, labels: dict | None = None) -> int:
    """Write the decision lessons a scored candidate should have but does not (one per candidate)."""
    have = {r["text"].split(" M15 bar")[0] for r in journal._rows("SELECT text FROM lessons WHERE author LIKE 'h003-%'")}
    n = 0
    for c in journal.candidates(limit=100000):
        if c["shadow"] and c["status"] in DECIDED and lesson_key(c) not in have:
            journal.add_lesson(now, None, f"h003-{c['arm']}", decision_lesson(c, c["shadow"]),
                               tags=decision_tags(c, c["shadow"], (labels or {}).get(c["arm"])))
            n += 1
    return n


def decision_lesson(c: dict, s: dict) -> str:
    """Factual note on one decision and what the default stop/target did afterwards (the shadow outcome)."""
    ctx, pats = c["context"], c["patterns"]
    codes = _codes(c)
    event = ctx.get("event") if ctx.get("event") not in (None, "none") else None
    return (f"{ARMS[c['arm']]['arm']} decision: {'skipped' if c['status'] == 'skipped' else 'took'} {c['side']} "
            f"{c['symbol']} on the {c['bar'][:16]}Z M15 bar ({ctx.get('session')}, {ctx.get('hour_utc')}h UTC, "
            f"{ctx.get('dow')}). Patterns for: {', '.join(pats.get('for') or []) or 'none'}; against: "
            f"{', '.join(pats.get('against') or []) or 'none'} ({pats.get('confluence')}). Context: H1 trend "
            f"{ctx.get('h1_trend')}, M15 trend {ctx.get('m15_trend')}, {ctx.get('vol')} vol, ATR {ctx.get('atr_pips')} pips"
            + (f", news {event}" if event else "") + f". Reason given: \"{c.get('reason') or ''}\""
            + (f" (codes {', '.join(codes)})" if codes else "") + ". Shadow outcome with the default stop/target "
            f"from the bar close: {s['exit']} after {s['bars']} bars, {s['R']:+.2f}R after cost, so this was a "
            f"{verdict(c, s).replace('_', ' ')}. One demo observation, not evidence of edge.")


def decision_tags(c: dict, s: dict, arm_model: str | None) -> list[str]:
    ctx = c["context"]
    tags = ["H-003", ARMS[c["arm"]]["arm"], "decision", c["symbol"], str(ctx.get("session")),
            f"decision:{'skip' if c['status'] == 'skipped' else 'take'}", f"verdict:{verdict(c, s)}",
            f"outcome:{'win' if s['R'] > 0 else 'loss'}", f"hour:{int(ctx.get('hour_utc') or 0):02d}"]
    tags += [f"pattern:{p}" for p in (c["patterns"].get("for") or [])]
    tags += [f"code:{x}" for x in _codes(c)]
    return tags + ([f"model:{arm_model}"] if arm_model else [])


def insight_documents(journal: Journal) -> list[dict]:
    """Hourly digest per H-003 arm: how the model's decisions turned out, by verdict, session, pattern and code."""
    docs, mean = [], lambda xs: f"{sum(xs) / len(xs):+.2f}R" if xs else "-"
    for arm, cfg in ARMS.items():
        rows = [c for c in journal.candidates(arm=arm, limit=100000) if c["shadow"] and c["status"] in DECIDED]
        if not rows:
            continue
        group: dict = {}
        for c in rows:
            how = "skip" if c["status"] == "skipped" else "take"
            keys = [("verdict", verdict(c, c["shadow"])), ("session", f"{c['context'].get('session')} {how}")]
            keys += [("pattern", f"{p} {how}") for p in c["patterns"].get("for") or []]
            keys += [("code", f"{x} {how}") for x in _codes(c)]
            for k in keys:
                group.setdefault(k, []).append(c["shadow"]["R"])
        part = lambda dim: "; ".join(f"{k} n={len(v)} {mean(v)}" for (d, k), v in sorted(group.items()) if d == dim)
        taken = [c["shadow"]["R"] for c in rows if c["status"] != "skipped"]
        passed = [c["shadow"]["R"] for c in rows if c["status"] == "skipped"]
        content = (f"{cfg['arm']} decision quality digest: {len(rows)} decided candidates with a shadow outcome "
                   f"(default stop/target from the bar close, R after the assumed 0.8-pip cost). Taken n={len(taken)} "
                   f"{mean(taken)}; skipped n={len(passed)} {mean(passed)}. The filter helps only if taken beats "
                   f"skipped. n<30 is weak; demo forward observation, not evidence of edge.\n"
                   f"By verdict: {part('verdict')}\nBy session and decision: {part('session') or '-'}\n"
                   f"By pattern and decision: {part('pattern') or '-'}\nBy reason code and decision: {part('code') or '-'}")
        docs.append({"document": f"fxra-insight-h003-{arm}", "content": content,
                     "tags": ["demo", "insight", "H-003", cfg["arm"], "decision"]})
    return docs


# the model's side ----------------------------------------------------------
def decide(journal: Journal, cid: str, take: bool, reason: str, author: str, now: datetime,
           sl_atr: float | None = None, tp_rr: float | None = None, *, codes=None, confidence: float | None = None,
           prompt_ref: str | None = None, memory_refs: list[str] | None = None) -> dict:
    """Record the model's take/skip for one pending candidate. The loop sends approved ones.

    `codes`, `confidence` and `prompt_ref` are optional decision-log metadata (D-44); they
    never change what is sent.
    """
    from .decision_log import validate_codes  # decision_log imports this module

    codes = validate_codes(codes)
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("confidence must be within [0, 1]")
    rows = journal.candidates(ids=[cid])
    if not rows:
        raise ValueError(f"no candidate {cid}")
    c = rows[0]
    if author.split("/")[0] != ARMS[c["arm"]]["owner"] or "/" not in author:
        raise PermissionError(f"{c['arm']} candidates are decided by {ARMS[c['arm']]['owner']}/<model>")
    if c["status"] != "pending":
        raise ValueError(f"{cid} is {c['status']}, not pending")
    if now >= _t(c["expires_at"]):
        journal.set_candidate(cid, status="expired")
        raise ValueError(f"{cid} expired at {c['expires_at']}")
    if len((reason or "").strip()) < 10:
        raise ValueError("a reason of at least 10 characters is required")
    fields = {"status": "approved" if take else "skipped", "decided_at": now.isoformat(), "decider": author,
              "reason": reason.strip(), "reason_codes": json.dumps(codes) if codes else None,
              "confidence": confidence, "prompt_ref": prompt_ref,
              "memory_refs": json.dumps(memory_refs) if memory_refs else None}  # what the decider read first
    if take:
        for key, value in (("sl_atr", sl_atr), ("tp_rr", tp_rr)):
            if value is not None:
                lo, hi = DEFAULT_POLICY[f"{key}_bounds"]
                if not lo <= value <= hi:
                    raise ValueError(f"{key} must be within [{lo}, {hi}]")
        fields.update(sl_atr=sl_atr or DEFAULT_POLICY["sl_atr"], tp_rr=tp_rr or DEFAULT_POLICY["tp_rr"])
    journal.set_candidate(cid, **fields)
    return journal.candidates(ids=[cid])[0]


def brief(journal: Journal, now: datetime, arm: str = "claude") -> dict:
    """What the model reads before deciding: pending candidates and how past decisions turned out."""
    pending = [c for c in journal.candidates(arm=arm, status="pending") if _t(c["expires_at"]) > now]
    done = journal.candidates(arm=arm, since=now - timedelta(days=3))
    scored = [c for c in done if c["shadow"]]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    taken = [c["shadow"]["R"] for c in scored if c["status"] in ("approved", "sent")]
    passed = [c["shadow"]["R"] for c in scored if c["status"] in ("skipped", "expired")]
    return {
        "now": now.isoformat(), "arm": ARMS[arm]["arm"],
        "pending": [{"id": c["id"], "symbol": c["symbol"], "side": c["side"], "bar": c["bar"],
                     "expires_in_min": round((_t(c["expires_at"]) - now).total_seconds() / 60, 1),
                     **c["patterns"], **c["context"]} for c in pending],
        "feedback_3d": {"decided": len([c for c in done if c["status"] != "pending"]),
                        "shadow_mean_R_taken": mean(taken), "n_taken_scored": len(taken),
                        "shadow_mean_R_passed": mean(passed), "n_passed_scored": len(passed)},
        "recent": [{"id": c["id"], "status": c["status"], "reason": c["reason"],
                    "shadow_R": (c["shadow"] or {}).get("R")} for c in done if c["status"] not in ("pending", "expired")][:15],
        "read_first": memory_setup.read_first(arm),  # D-45: Hindsight memory to read before deciding
        "bounds": {k: DEFAULT_POLICY[k] for k in ("sl_atr", "tp_rr", "sl_atr_bounds", "tp_rr_bounds", "min_sl_pips")},
    }


def scoreboard(journal: Journal, assumed_cost_pips: float = 0.8) -> dict:
    """Model vs model on H-003: closed trades (net, R), decisions and how taken compares with passed."""
    closed = journal._rows(
        "SELECT t.*, g.arm, g.symbol, g.session, o.stop_loss FROM trades t JOIN entry_tags g ON g.entry_id=t.entry_id"
        " LEFT JOIN orders o ON o.client_order_id=t.entry_id WHERE g.strategy=? AND t.net IS NOT NULL", STRATEGY_ID)
    for t in closed:
        sign = 1 if t["side"] == "buy" else -1
        risk_dist = abs(t["open_price"] - t["stop_loss"]) if t["stop_loss"] else None
        t["R"] = sign * (t["close_price"] - t["open_price"]) / risk_dist if risk_dist else None
    group = lambda key: _group(closed, key, assumed_cost_pips)
    by_arm = group(lambda t: t["arm"])
    for arm, g in by_arm.items():
        rs = [t["R"] for t in closed if t["arm"] == arm and t["R"] is not None]
        g["avg_r"] = round(sum(rs) / len(rs), 3) if rs else None
    decisions, mean = {}, lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    cands = journal.candidates(limit=100000)
    for arm in ARMS:
        mine = [c for c in cands if c["arm"] == arm]
        status = {s: sum(c["status"] == s for c in mine) for s in STATUSES}
        lat = [(_t(c["decided_at"]) - _t(c["created_at"])).total_seconds() / 60 for c in mine if c["decided_at"]]
        shadow_of = lambda sts: [c["shadow"]["R"] for c in mine if c["shadow"] and c["status"] in sts]
        taken, passed = shadow_of(("approved", "sent", "rejected")), shadow_of(("skipped", "expired"))
        decisions[arm] = {"candidates": len(mine), **status, "avg_latency_min": round(sum(lat) / len(lat), 1) if lat else None,
                          "shadow_taken": mean(taken), "n_taken": len(taken),
                          "shadow_passed": mean(passed), "n_passed": len(passed)}
    # same candidate (symbol, bar, side) decided by both arms: how often they agree
    seen: dict = {}
    for c in cands:
        if c["status"] in ("approved", "sent", "rejected", "skipped"):
            seen.setdefault((c["symbol"], c["bar"], c["side"]), {})[c["arm"]] = c["status"] != "skipped"
    both = [v for v in seen.values() if len(v) == len(ARMS)]
    return {"by_arm": by_arm, "decisions": decisions,
            "by_arm_session": {f"{a} / {s}": v for (a, s), v in sorted(group(lambda t: (t["arm"], t["session"])).items())},
            "by_arm_symbol": {f"{a} / {s}": v for (a, s), v in sorted(group(lambda t: (t["arm"], t["symbol"])).items())},
            "agreement": {"n": len(both), "agree": sum(len(set(v.values())) == 1 for v in both)}}


# the loop's side -----------------------------------------------------------
def step(journal: Journal, broker, limits, now: datetime, snap: dict, pol: dict, *, send: bool = True,
         duty: dict | None = None, arm: str = "claude", arm_model: str | None = None) -> dict:
    """Per tick: expire, send approved candidates, time-exit, add this bar's candidates, score shadows."""
    cfg = ARMS[arm]
    markets = snap.get("markets") or {h002.SYMBOL: snap}
    out = {"action": "hold", "new": [], "sent": [], "exits": [], "expired": 0}
    for c in journal.candidates(arm=arm, status="pending"):
        if now >= _t(c["expires_at"]):
            journal.set_candidate(c["id"], status="expired")
            out["expired"] += 1
    for c in journal.candidates(arm=arm, status="approved"):
        if now >= _t(c["expires_at"]) + timedelta(minutes=5):  # loop was down: the setup is stale
            journal.set_candidate(c["id"], status="expired")
            out["expired"] += 1
            continue
        out["sent"].append(_send(journal, broker, limits, now, snap, markets.get(c["symbol"]), c, pol, send=send,
                                 duty=duty, arm=arm, arm_model=arm_model))
    open_count = len(snap["positions"]) + sum(s.get("status") == "filled" for s in out["sent"])
    for symbol in pol.get("symbols") or [h002.SYMBOL]:
        market = markets.get(symbol)
        if not market:
            continue
        out["exits"] += _time_exits(journal, broker, now, snap, symbol, pol, cfg, send=send)
        try:
            out["new"] += _candidates(journal, now, snap, market, symbol, pol, arm, open_count)
        except Exception as e:  # one symbol failing must not stop the others
            out.setdefault("errors", []).append(f"{symbol}: {type(e).__name__}: {e}")
        for c in journal.candidates(arm=arm, symbol=symbol, unscored=True):
            s = shadow(c, market.get("m15_bars") or [], pol)
            if s:
                journal.set_candidate(c["id"], shadow=json.dumps(s))
                if c["status"] in DECIDED:  # feedback for the decider's memory, taken or skipped
                    journal.add_lesson(now, None, f"h003-{arm}", decision_lesson(c, s),
                                       tags=decision_tags(c, s, arm_model))
    if out["sent"]:
        out["action"] = "entry"
    elif out["new"]:
        out["action"] = "candidates"
    return out


def _candidates(journal, now, snap, market, symbol, pol, arm, open_count) -> list[str]:
    if market["quote"] is None:
        return []
    latest = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0) - timedelta(minutes=15)
    bars = market.get("m15_bars") or []
    if not bars or _t(bars[-1]["time"]) != latest:
        return []
    key = f"{ARMS[arm]['arm']}:{symbol}:{latest.isoformat()}"
    if journal.decided(key):
        return []
    pip = PIP_SIZES[symbol]
    matches = h002.patterns(bars)
    events = snap.get("events") or []
    matches.update(econ_calendar.news_patterns(events, symbol, latest, bars))
    a = h002.atr(bars[-15:])
    ctx = {**h002.market_context(bars, market.get("h1_bars") or [], market["quote"], pip, latest),
           **econ_calendar.context(events, symbol, latest + timedelta(minutes=15))}
    stats = journal.learning_stats(pol["assumed_cost_pips"])["by_pattern"]
    held = [{"side": p["side"], "strategy": p.get("strategy"), "opened": p.get("time")}
            for p in snap["positions"] if p.get("symbol") == symbol]
    dist = stop_distance(a, pip, pol["sl_atr"], pol["min_sl_pips"])
    new = []
    for side in ("buy", "sell"):
        support = sorted(p for p, s in matches.items() if s == side)
        if not support:
            continue
        cid = candidate_id(arm, symbol, latest, side)
        journal.add_candidate({
            "id": cid, "arm": arm, "symbol": symbol, "bar": latest.isoformat(), "side": side,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=pol["expiry_minutes"])).isoformat(),
            "patterns": {"for": support, "against": sorted(p for p, s in matches.items() if s != side),
                         "confluence": confluence({"matches": matches}, side),
                         "pattern_stats": {p: {k: stats[p][k] for k in ("n", "win_rate", "avg_pips_after_cost")}
                                           for p in support if p in stats}},
            "context": {**ctx, "hour_utc": latest.hour, "session": h002.session(latest.hour),
                        "close": bars[-1]["close"], "atr": a, "atr_pips": round(a / pip, 1),
                        "default_sl_pips": round(dist / pip, 1), "default_tp_pips": round(dist * pol["tp_rr"] / pip, 1),
                        "open_positions_symbol": held, "open_positions_total": open_count, "matches": matches}})
        new.append(cid)
    journal.decide(key, now, "candidates" if new else "no_trade", None,
                   f"{ARMS[arm]['arm']} {symbol}: " + (", ".join(new) if new else "no pattern matched"),
                   {"candidates": new, "matches": matches})
    return new


def _time_exits(journal, broker, now, snap, symbol, pol, cfg, *, send) -> list[dict]:
    latest = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    exits = []
    for pos in snap["positions"]:
        if pos.get("magic") != cfg["magic"] or pos.get("symbol") != symbol:
            continue
        held = int((latest - _t(pos["time"])).total_seconds() // 900)
        cid = f"H3X-{pos['ticket']}"
        if held < pol["max_hold_bars"]:
            continue
        result = broker.close({"ticket": pos["ticket"], "client_order_id": cid, "symbol": symbol}) if send \
            else {"status": "not_sent"}
        result["rule"] = "max_hold_bars"
        journal.order(cid, now, "exit", "sell" if pos["side"] == "buy" else "buy", pos["volume"], None,
                      result["status"], {}, result)
        exits.append({"ticket": pos["ticket"], "held": held, "status": result["status"]})
    return exits


def _send(journal, broker, limits, now, snap, market, c, pol, *, send, duty, arm, arm_model) -> dict:
    cid, symbol, side = c["id"], c["symbol"], c["side"]
    if market is None or market["quote"] is None:
        journal.set_candidate(cid, status="error", result=json.dumps({"reason": "no fresh quote"}))
        return {"id": cid, "status": "error"}
    q, s, acct, pip = market["quote"], {**market["spec"], "symbol": symbol}, snap["account"], PIP_SIZES[symbol]
    ctx = c["context"]
    dist = stop_distance(ctx["atr"], pip, c["sl_atr"], pol["min_sl_pips"])
    entry = q["ask"] if side == "buy" else q["bid"]
    sign = 1 if side == "buy" else -1
    stop, target = round(entry - sign * dist, s["digits"]), round(entry + sign * dist * c["tp_rr"], s["digits"])
    lots = h002.size_lots(pol, acct["equity"], dist, s, limits.max_lots_per_order if limits else 0.0)
    intent = OrderIntent(cid, STRATEGY_ID, STRATEGY_VERSION, symbol, side, lots, "market",
                         stop_loss=stop, take_profit=target)
    spec = SymbolSpec(symbol, "mt5-demo-1", s["digits"], s["point"], pip, s["contract_size"],
                      s["volume_min"], s["volume_max"], s["volume_step"], s["quote_currency"])
    day_start = journal.snapshot_equity(now, acct["balance"], acct["equity"], acct["currency"])
    decision = risk.evaluate(intent, limits, spec, Quote(symbol, _t(q["time"]), q["bid"], q["ask"]),
                             AccountSnapshot(now, acct["currency"], acct["equity"], day_start, len(snap["positions"])),
                             now, journal.order_ids(), risk.kill_switch_active(limits))
    if not decision.approved:
        journal.order(cid, now, "entry", side, lots, stop, "risk_rejected", decision.to_dict(), {})
        journal.set_candidate(cid, status="rejected", client_order_id=cid,
                              result=json.dumps({"reasons": decision.reasons}))
        return {"id": cid, "status": "rejected", "reasons": decision.reasons}
    hour = _t(c["bar"]).hour
    journal.tag_entry(cid, {"strategy": STRATEGY_ID, "arm": arm, "arm_model": arm_model, "symbol": symbol,
                            "pattern": None,  # keeps H-003 out of the H-002 per-pattern learning stats
                            "hour_utc": hour, "session": h002.session(hour), "policy_version": None,
                            "duty_agent": (duty or {}).get("agent"), "duty_model": (duty or {}).get("model"),
                            "features": {**{k: v for k, v in ctx.items() if k not in ("open_positions_symbol",)},
                                         "patterns_for": c["patterns"]["for"],
                                         "patterns_against": c["patterns"]["against"],
                                         "decision_reason": c["reason"], "decider": c["decider"],
                                         "sl_atr": c["sl_atr"], "tp_rr": c["tp_rr"],
                                         "sl_pips": dist / pip, "tp_pips": dist * c["tp_rr"] / pip,
                                         "decision_latency_min": round((now - _t(c["created_at"])).total_seconds() / 60, 1)}})
    if not send:
        journal.set_candidate(cid, status="sent", client_order_id=cid, result=json.dumps({"status": "not_sent"}))
        return {"id": cid, "status": "not_sent"}
    journal.order(cid, now, "entry", side, lots, stop, "sending", decision.to_dict(), {})  # persist before send
    try:
        result = broker.send({"mode": limits.mode, "magic": ARMS[arm]["magic"], "intent": intent.__dict__,
                              "decision": decision.to_dict()})
    except RuntimeError as e:
        result = {"status": "error", "reason": str(e)}
    journal.order(cid, now, "entry", side, lots, stop, result["status"], decision.to_dict(), result)
    journal.set_candidate(cid, status="sent" if result["status"] == "filled" else "error", client_order_id=cid,
                          result=json.dumps(result, default=str))
    return {"id": cid, "status": result["status"], "lots": lots, "sl_pips": round(dist / pip, 1)}

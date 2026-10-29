import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from fxra import decision_log, h002, h003, risk
from fxra.dashboard import Agent
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 28, 14, 15, 10, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
POLICY = {**h003.DEFAULT_POLICY, "lots": 0.1, "lots_by_symbol": {}, "symbols": ["EURUSD"], "risk_per_trade_pct": None}
AUTHOR = "claude/claude-opus-5-5"


def limits(tmp_path, *strategies):
    return risk.RiskLimits(mode="demo", allowed_symbols=frozenset({"EURUSD"}),
                           approved_strategies=frozenset(strategies or {"H-003@gated-v1", "H-002@explore"}),
                           max_lots_per_order=0.1, max_open_positions=50, max_daily_loss_fraction=1.0,
                           max_spread_points=20, max_quote_age_seconds=5, max_account_age_seconds=30,
                           require_stop_loss=True, kill_switch_file=str(tmp_path / "KILL_SWITCH"))


def m15(last_close=1.1000, n=80, end=LATEST):
    out = []
    for i in range(n):
        t = end - timedelta(minutes=15 * (n - 1 - i))
        out.append({"time": t.isoformat(), "open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1})
    out[-1].update(close=last_close, high=max(1.1005, last_close), low=min(1.0995, last_close))
    return out


def snap(bars, at=NOW):
    return {"quote": {"bid": 1.10200, "ask": 1.10201, "time": (at - timedelta(seconds=1)).isoformat()},
            "account": {"currency": "USD", "balance": 1e5, "equity": 1e5, "hedging": True},
            "spec": {"digits": 5, "point": 0.00001, "contract_size": 100000, "volume_min": 0.01,
                     "volume_max": 100, "volume_step": 0.01, "quote_currency": "USD"},
            "m15_bars": bars, "h1_bars": [], "positions": [], "deals": []}


class Broker:
    def __init__(self):
        self.sent = []

    def send(self, p):
        self.sent.append(p)
        return {"status": "filled"}

    def close(self, p):
        return {"status": "filled"}


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def deal(ticket, at, side, entry, price, comment, profit=0.0):
    return {"ticket": ticket, "order": ticket, "position_id": 7, "time": at.isoformat(), "side": side, "entry": entry,
            "volume": 0.1, "price": price, "profit": profit, "commission": -0.7, "swap": 0.0, "comment": comment}


def trace(journal, tid, **filters):
    rows = [t for t in decision_log.query(journal, limit=500, **filters)["traces"] if t["id"] == tid]
    return rows[0] if rows else None


def by_origin(t, origin):
    return [r["code"] for r in t["reasons"] if r["origin"] == origin]


def test_h003_trace_keeps_inputs_model_statement_and_derived_codes_apart(journal, tmp_path):
    b, lim = Broker(), limits(tmp_path)
    cid = h003.step(journal, b, lim, NOW, snap(m15(1.1020)), POLICY)["new"][0]
    decision_log.sync(journal, NOW)
    pending = trace(journal, cid, decision="pending")
    assert pending["decision"] is None and by_origin(pending, "rule") == ["candidate_proposed", "awaiting_model"]
    h003.decide(journal, cid, True, "three patterns agree, low spread", AUTHOR, NOW + timedelta(minutes=2),
                sl_atr=2.0, codes=["signals_agree", "spread_wide"], confidence=0.55, prompt_ref="fxra-demo-decide@abc")
    later = NOW + timedelta(minutes=2, seconds=30)
    h003.step(journal, b, lim, later, snap(m15(1.1020), at=later), POLICY)
    decision_log.sync(journal, later)
    t = trace(journal, cid, strategy="H-003-claude", symbol="EURUSD", version="H-003@gated-v1", decision="buy")
    assert (t["status"], t["confidence"], t["prompt_ref"], t["decider"]) == ("sent", 0.55, "fxra-demo-decide@abc", AUTHOR)
    assert t["model_reason"] == "three patterns agree, low spread"
    assert by_origin(t, "model") == ["model_approved", "signals_agree", "spread_wide", "custom_stop"]
    assert "order_filled" in by_origin(t, "rule") and "m15_trend_aligned" in by_origin(t, "derived")
    assert "m15_trend_aligned" not in by_origin(t, "model")  # derived codes are never the model's reasons
    assert t["inputs"]["market"]["bar_open"] == LATEST.isoformat() and t["inputs"]["features"]["patterns_for"]
    assert "login" not in json.dumps(t["inputs"]) and "server" not in json.dumps(t["inputs"])
    plan = t["plan"]
    assert plan["basis"] == "order" and abs(plan["entry_ref"] - 1.10201) < 1e-5 and plan["stop"] < plan["entry_ref"]
    assert [i["code"] for i in plan["invalidation"]] == ["stop_loss", "take_profit", "max_hold_bars"]
    assert t["outcome"] is None and t["final"] == 0
    # the stop is hit: outcome, R, slippage and the invalidation check fill in on the next sync
    journal.sync_deals([deal(1, later, "buy", "in", 1.10203, cid),
                        deal(2, later + timedelta(minutes=30), "sell", "out", plan["stop"], "[sl]", -30.0)])
    journal.set_exit_reason(7, "stop_loss")
    journal.set_candidate(cid, shadow=json.dumps({"R": -1.05, "exit": "stop_loss", "bars": 2}))
    decision_log.sync(journal, later + timedelta(hours=1))
    o = trace(journal, cid)["outcome"]
    assert o["trade"]["slippage_pips"] == 0.2 and o["trade"]["R"] == pytest.approx(-1.0, abs=0.02)
    assert o["checks"] == ["stop_hit", "shadow_stop_hit"] and o["verdict"] == "loss" and trace(journal, cid)["final"] == 1


def test_model_codes_and_confidence_are_validated(journal, tmp_path):
    cid = h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]
    with pytest.raises(ValueError, match="unknown reason codes"):
        h003.decide(journal, cid, False, "late in an extended move", AUTHOR, NOW, codes=["gut_feeling"])
    with pytest.raises(ValueError, match="confidence"):
        h003.decide(journal, cid, False, "late in an extended move", AUTHOR, NOW, confidence=1.5)
    assert journal.candidates(ids=[cid])[0]["status"] == "pending"
    assert set(decision_log.MODEL_CODES) >= {"trend_against", "risk_reward_poor", "other"}
    assert "model_approved" not in decision_log.MODEL_CODES and "h1_trend_against" not in decision_log.MODEL_CODES


def test_sync_writes_only_the_trace_table(journal, tmp_path):
    b, lim = Broker(), limits(tmp_path)
    h002.step(journal, b, lim, NOW, snap(m15(1.1020)), dict(h002.DEFAULT_POLICY, symbols=["EURUSD"]),
              rng=random.Random(0))
    cid = h003.step(journal, b, lim, NOW, snap(m15(1.1020)), POLICY)["new"][0]
    h003.decide(journal, cid, False, "late in an extended move", AUTHOR, NOW)
    dump = lambda: [line for line in journal.db.iterdump() if "decision_traces" not in line]
    before = dump()
    out = decision_log.sync(journal, NOW + timedelta(minutes=1), {"markets": {"EURUSD": {"m15_bars": m15(1.1020)}}})
    assert out["built"] > 0 and dump() == before
    n = journal.db.execute("SELECT count(*) FROM decision_traces").fetchone()[0]
    again = decision_log.sync(journal, NOW + timedelta(minutes=2))
    assert again["built"] <= out["built"] and journal.db.execute("SELECT count(*) FROM decision_traces").fetchone()[0] == n


def test_h002_entries_are_one_trace_per_order_and_evaluations_one_each(journal, tmp_path):
    b, lim = Broker(), limits(tmp_path)
    bars = m15(1.1020)
    pol = dict(h002.DEFAULT_POLICY, symbols=["EURUSD"])
    h002.step(journal, b, lim, NOW, snap(bars), pol, rng=random.Random(0))
    flat_now, flat_bar = NOW + timedelta(minutes=15), LATEST + timedelta(minutes=15)
    flat = m15(1.1, end=flat_bar)
    if h002.patterns(flat):  # make sure the second bar matches nothing
        flat[-1].update(open=1.1, high=1.1001, low=1.0999, close=1.1)
    h002.step(journal, b, lim, flat_now, snap(flat, at=flat_now), {**pol, "enabled": []}, rng=random.Random(0))
    decision_log.sync(journal, flat_now)
    entries = decision_log.query(journal, strategy="H-002", decision="buy")["traces"]
    ids = {s["intent"]["client_order_id"] for s in b.sent if s["intent"]["side"] == "buy"}
    assert {t["id"] for t in entries} == ids and all(t["decider"] == "script" for t in entries)
    t = entries[0]
    assert by_origin(t, "rule")[0] == "pattern_entry" and t["policy_version"] == 1 and t["plan"]["target"] > 1.10201
    assert t["inputs"]["market"]["quote"]["ask"] == 1.10201 and "vol_high" in by_origin(t, "derived")
    assert t["inputs"]["account"] == {"equity": 1e5, "balance": 1e5, "currency": "USD", "as_of": "2026-10-28T14:15:00+00:00"}
    rest = decision_log.query(journal, strategy="H-002-claude", decision="no_trade")["traces"]
    assert len(rest) == 1 and rest[0]["bar"] == flat_bar.isoformat() and rest[0]["final"] == 1


def test_skipped_candidate_outcome_and_excursion(journal, tmp_path):
    cid = h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]  # buy
    h003.decide(journal, cid, False, "late in an extended move", AUTHOR, NOW, codes=["extended_move"])
    journal.set_candidate(cid, shadow=json.dumps({"R": 1.4, "exit": "take_profit", "bars": 8}))
    later = NOW + timedelta(hours=3)
    after = [{"time": (LATEST + timedelta(minutes=15 * i)).isoformat(), "open": 1.102, "high": 1.1030,
              "low": 1.1015, "close": 1.102} for i in range(1, 11)]
    decision_log.sync(journal, later, {"markets": {"EURUSD": {"m15_bars": m15(1.1020) + after}}})
    t = trace(journal, cid)
    assert t["decision"] == "no_trade" and t["status"] == "skipped" and t["final"] == 1
    assert t["outcome"]["verdict"] == "missed_gain" and t["outcome"]["checks"] == ["shadow_target_reached", "skip_missed_gain"]
    assert t["plan"]["basis"] == "shadow" and t["plan"]["entry_ref"] == 1.102
    assert t["excursion"]["basis"] == "shadow" and (t["excursion"]["mfe_pips"], t["excursion"]["mae_pips"]) == (10.0, 5.0)


def test_owner_close_and_unanswered_expiry(journal, tmp_path):
    cid = h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]
    h003.step(journal, Broker(), limits(tmp_path), NOW + timedelta(minutes=13), snap(m15(1.1020)), POLICY)
    journal.order("H3X-7", NOW, "exit", "sell", 0.1, None, "filled", {},
                  {"status": "filled", "rule": "owner_close", "reason": "owner asked to flatten", "author": AUTHOR})
    decision_log.sync(journal, NOW + timedelta(minutes=14))
    expired = trace(journal, cid)
    assert expired["decision"] == "no_trade" and "expired_unanswered" in by_origin(expired, "rule")
    assert expired["decider"] == "model (no decision)" and expired["model_reason"] is None
    close = trace(journal, "H3X-7", decision="close")
    assert close["decider"] == f"owner:{AUTHOR}" and by_origin(close, "owner") == ["owner_close"]


def test_query_filters_and_dates(journal, tmp_path):
    for arm in h003.ARMS:
        h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY, arm=arm)
    decision_log.sync(journal, NOW)
    q = lambda **kw: decision_log.query(journal, **kw)["total"]
    assert q() == 4 and q(strategy="H-003") == 4 and q(strategy="H-003-codex") == 2 and q(decider="claude") == 0
    assert q(since="2026-10-28", until="2026-10-28") == 4 and q(since="2026-10-29") == 0 and q(symbol="GBPUSD") == 0
    facets = decision_log.query(journal)["facets"]
    assert facets["strategy"] == ["H-003", "H-003-claude", "H-003-codex"] and "pending" in facets["decision"]


def test_old_journal_gets_the_new_columns(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE candidates (id TEXT PRIMARY KEY, arm TEXT NOT NULL, symbol TEXT NOT NULL, bar TEXT NOT NULL,"
               " side TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, patterns TEXT NOT NULL,"
               " context TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_at TEXT, decider TEXT,"
               " reason TEXT, sl_atr REAL, tp_rr REAL, client_order_id TEXT, result TEXT, shadow TEXT)")
    db.close()
    j = Journal(tmp_path / "old.sqlite")
    cols = {r[1] for r in j.db.execute("PRAGMA table_info(candidates)")}
    assert {"reason_codes", "confidence", "prompt_ref"} <= cols
    assert j.db.execute("SELECT count(*) FROM decision_traces").fetchone()[0] == 0
    j.close()


def test_trace_failure_never_reaches_trading(journal, tmp_path, monkeypatch):
    agent = Agent(journal, None, tmp_path / "missing-risk.toml")
    monkeypatch.setattr(decision_log, "sync", lambda *a, **k: 1 / 0)
    assert agent.sync_traces(NOW) == {"error": "ZeroDivisionError: division by zero"}


def test_prompt_ref_hashes_the_decide_skill(tmp_path):
    skill = tmp_path / ".claude/skills/fxra-demo-decide/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("decide")
    ref = decision_log.prompt_ref("claude", tmp_path)
    assert ref.startswith("fxra-demo-decide@") and len(ref) == len("fxra-demo-decide@") + 12
    assert decision_log.prompt_ref("codex", tmp_path) is None

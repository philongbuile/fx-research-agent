from datetime import datetime, timedelta, timezone

import pytest

from fxra import h003, risk
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 28, 14, 15, 10, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
POLICY = {**h003.DEFAULT_POLICY, "lots": 0.1, "lots_by_symbol": {}, "symbols": ["EURUSD"], "risk_per_trade_pct": None}
AUTHOR = "claude/claude-opus-5-5"


def limits(tmp_path, **kw):
    base = dict(mode="demo", allowed_symbols=frozenset({"EURUSD"}),
                approved_strategies=frozenset({"H-003@gated-v1"}), max_lots_per_order=0.1,
                max_open_positions=50, max_daily_loss_fraction=1.0, max_spread_points=20,
                max_quote_age_seconds=5, max_account_age_seconds=30, require_stop_loss=True,
                kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    return risk.RiskLimits(**{**base, **kw})


def m15(last_close=1.1000, n=80, end=LATEST):
    out = []
    for i in range(n):
        t = end - timedelta(minutes=15 * (n - 1 - i))
        out.append({"time": t.isoformat(), "open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1})
    out[-1].update(close=last_close, high=max(1.1005, last_close), low=min(1.0995, last_close))
    return out


def snap(bars, positions=(), at=NOW):
    return {"quote": {"bid": 1.10200, "ask": 1.10201, "time": (at - timedelta(seconds=1)).isoformat()},
            "account": {"currency": "USD", "balance": 1e5, "equity": 1e5, "hedging": True},
            "spec": {"digits": 5, "point": 0.00001, "contract_size": 100000, "volume_min": 0.01,
                     "volume_max": 100, "volume_step": 0.01, "quote_currency": "USD"},
            "m15_bars": bars, "h1_bars": [], "positions": list(positions), "deals": []}


class Broker:
    def __init__(self):
        self.sent, self.closed = [], []

    def send(self, p):
        self.sent.append(p)
        return {"status": "filled"}

    def close(self, p):
        self.closed.append(p)
        return {"status": "filled"}


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def test_candidates_are_one_per_side_and_nothing_is_sent(journal, tmp_path):
    b = Broker()
    out = h003.step(journal, b, limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)
    assert out["new"] == ["H3GC-2610281400-B", "H3GC-2610281400-S"] and not b.sent  # a fade points the other way
    c = journal.candidates(ids=out["new"][:1])[0]
    assert c["status"] == "pending" and c["side"] == "buy" and len(c["patterns"]["for"]) > 1
    assert c["patterns"]["confluence"].startswith("3+ agree") and c["context"]["default_sl_pips"] == round(1.5 * c["context"]["atr_pips"], 1)
    assert h003.step(journal, b, limits(tmp_path), NOW + timedelta(seconds=30), snap(m15(1.1020)), POLICY)["new"] == []


def test_model_decision_is_validated(journal, tmp_path):
    cid = h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]
    with pytest.raises(PermissionError):
        h003.decide(journal, cid, True, "breakout with H1 trend", "codex/gpt-6.1-sol", NOW)
    with pytest.raises(ValueError, match="reason"):
        h003.decide(journal, cid, True, "ok", AUTHOR, NOW)
    with pytest.raises(ValueError, match="sl_atr"):
        h003.decide(journal, cid, True, "breakout with H1 trend", AUTHOR, NOW, sl_atr=5)
    with pytest.raises(ValueError, match="expired"):
        h003.decide(journal, cid, True, "breakout with H1 trend", AUTHOR, NOW + timedelta(minutes=13))
    assert journal.candidates(ids=[cid])[0]["status"] == "expired"


def test_approved_candidate_is_sent_through_the_risk_gate(journal, tmp_path):
    b, lim = Broker(), limits(tmp_path)
    cid = h003.step(journal, b, lim, NOW, snap(m15(1.1020)), POLICY)["new"][0]
    h003.decide(journal, cid, True, "three patterns agree, low spread", AUTHOR, NOW + timedelta(minutes=2), sl_atr=2.0)
    later = NOW + timedelta(minutes=2, seconds=30)
    out = h003.step(journal, b, lim, later, snap(m15(1.1020), at=later), POLICY)
    assert out["action"] == "entry" and len(b.sent) == 1
    sent = b.sent[0]
    assert sent["magic"] == 20261031 and sent["intent"]["client_order_id"] == cid
    assert sent["intent"]["strategy_id"] == "H-003" and sent["intent"]["quantity_lots"] == 0.1
    assert sent["intent"]["stop_loss"] < 1.10201 - 0.0008 + 1e-9
    assert journal.candidates(ids=[cid])[0]["status"] == "sent"
    f = journal.entry_info(cid)["features"]
    assert f["decision_reason"] == "three patterns agree, low spread" and f["sl_atr"] == 2.0
    assert journal.entry_pattern(cid) is None  # kept out of the H-002 pattern stats
    h003.step(journal, b, lim, later + timedelta(seconds=30), snap(m15(1.1020), at=later), POLICY)
    assert len(b.sent) == 1


def test_unapproved_strategy_and_skips(journal, tmp_path):
    b = Broker()
    lim = limits(tmp_path, approved_strategies=frozenset({"H-002@explore"}))
    cid = h003.step(journal, b, lim, NOW, snap(m15(1.1020)), POLICY)["new"][0]
    h003.decide(journal, cid, True, "test the gate on this one", AUTHOR, NOW)
    h003.step(journal, b, lim, NOW + timedelta(seconds=30), snap(m15(1.1020)), POLICY)
    assert not b.sent and journal.candidates(ids=[cid])[0]["status"] == "rejected"
    sell = h003.step(journal, b, lim, NOW + timedelta(minutes=15),
                     snap(m15(1.0980, end=LATEST + timedelta(minutes=15)), at=NOW + timedelta(minutes=15)), POLICY)["new"]
    h003.decide(journal, sell[0], False, "late in a big move, skip", AUTHOR, NOW + timedelta(minutes=16))
    assert journal.candidates(ids=sell)[0]["status"] == "skipped"


def test_unanswered_candidates_expire(journal, tmp_path):
    cid = h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]
    out = h003.step(journal, Broker(), limits(tmp_path), NOW + timedelta(minutes=13), snap(m15(1.1020)), POLICY)
    assert out["expired"] == 2 and journal.candidates(ids=[cid])[0]["status"] == "expired"


def test_shadow_outcome():
    c = {"symbol": "EURUSD", "side": "buy", "bar": LATEST.isoformat(), "context": {"close": 1.1, "atr": 0.0004}}
    pol = {**POLICY, "max_hold_bars": 3}
    after = lambda *bars: [{"time": (LATEST + timedelta(minutes=15 * i)).isoformat(), "high": h, "low": lo,
                            "close": cl} for i, (h, lo, cl) in enumerate(bars, 1)]
    assert h003.shadow(c, after((1.1005, 1.0999, 1.1)), pol) is None  # not enough bars yet
    stop = h003.shadow(c, after((1.1005, 1.0990, 1.1), (1.1, 1.1, 1.1), (1.1, 1.1, 1.1)), pol)
    assert stop["exit"] == "stop_loss" and stop["R"] == -1.1  # 8-pip floor, 0.8 pip cost
    win = h003.shadow(c, after((1.1013, 1.0999, 1.1012), (1.1, 1.1, 1.1), (1.1, 1.1, 1.1)), pol)
    assert win["exit"] == "take_profit" and win["R"] == 1.4
    flat = h003.shadow(c, after((1.1002, 1.0998, 1.1), (1.1002, 1.0998, 1.1), (1.1005, 1.0998, 1.1004)), pol)
    assert flat["exit"] == "max_hold_bars" and flat["R"] == 0.4


def test_codex_arm_gets_the_same_candidates_and_its_own_magic(journal, tmp_path):
    b, lim = Broker(), limits(tmp_path)
    for arm in h003.ARMS:
        h003.step(journal, b, lim, NOW, snap(m15(1.1020)), POLICY, arm=arm)
    claude, codex = "H3GC-2610281400-B", "H3GX-2610281400-B"
    with pytest.raises(PermissionError):
        h003.decide(journal, codex, True, "breakout with H1 trend", AUTHOR, NOW)
    h003.decide(journal, codex, True, "agreement and fresh breakout", "codex/gpt-6.1-sol", NOW)
    h003.decide(journal, claude, False, "late in an extended move", AUTHOR, NOW)
    later = NOW + timedelta(seconds=30)
    h003.step(journal, b, lim, later, snap(m15(1.1020), at=later), POLICY, arm="codex")
    assert [s["magic"] for s in b.sent] == [20261032] and b.sent[0]["intent"]["client_order_id"] == codex
    journal.set_candidate(codex, shadow='{"R": 1.4, "exit": "take_profit", "bars": 3}')
    journal.set_candidate(claude, shadow='{"R": 1.4, "exit": "take_profit", "bars": 3}')
    sb = h003.scoreboard(journal)
    assert sb["decisions"]["codex"]["sent"] == 1 and sb["decisions"]["codex"]["shadow_taken"] == 1.4
    assert sb["decisions"]["claude"]["skipped"] == 1 and sb["decisions"]["claude"]["shadow_passed"] == 1.4
    assert sb["agreement"] == {"n": 1, "agree": 0} and sb["by_arm"] == {}


def test_brief_reports_pending_and_feedback(journal, tmp_path):
    h003.step(journal, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)
    b = h003.brief(journal, NOW + timedelta(minutes=1))
    assert sorted(p["id"] for p in b["pending"]) == ["H3GC-2610281400-B", "H3GC-2610281400-S"] and b["pending"][0]["expires_in_min"] > 10
    assert b["feedback_3d"]["shadow_mean_R_taken"] is None and b["bounds"]["sl_atr_bounds"] == [1.0, 3.0]

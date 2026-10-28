from datetime import datetime, timezone

import pytest

from fxra import lesson_insights, playbook
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.sqlite")


def trade(journal, pid, pattern, hour, win, side="buy"):
    cid = f"H2CL-{pid}"
    journal.tag_entry(cid, {"strategy": "H-002", "pattern": pattern, "hour_utc": hour, "session": "late_ny",
                            "arm": "claude", "symbol": "EURUSD", "features": {"matches": {pattern: side}}})
    journal.order(cid, NOW, "entry", side, 1.0, 1.0990 if side == "buy" else 1.1010, "filled", {}, {})
    out = "sell" if side == "buy" else "buy"
    close = 1.1010 if win else 1.0990
    journal.sync_deals([
        {"ticket": pid * 10, "order": pid * 10, "position_id": pid, "time": f"2026-10-28T{hour:02d}:05:00+00:00",
         "side": side, "entry": "in", "volume": 1.0, "price": 1.1000, "profit": 0, "commission": 0, "swap": 0,
         "comment": cid},
        {"ticket": pid * 10 + 1, "order": pid * 10 + 1, "position_id": pid, "time": f"2026-10-28T{hour:02d}:50:00+00:00",
         "side": out, "entry": "out", "volume": 1.0, "price": close, "profit": 100.0 if win else -100.0,
         "commission": 0, "swap": 0, "comment": "[tp]" if win else "[sl]"}])


def test_filter_parsing_and_validation():
    assert playbook.parse_filter(["pattern=breakout10, breakout20", "hour=8"]) == \
        {"pattern": ["breakout10", "breakout20"], "hour": ["08"]}
    with pytest.raises(ValueError):
        playbook.parse_filter(["price=1"])
    with pytest.raises(ValueError):
        playbook.parse_filter([])


def test_rule_evidence_is_recounted_and_gated(journal):
    for i in range(12):
        trade(journal, i + 1, "breakout10", 18, win=i < 2)  # 2/12 at 18h
    for i in range(6):
        trade(journal, i + 100, "pin_bar", 9, win=True)
    with pytest.raises(ValueError, match="noise"):
        playbook.add(journal, NOW, "claude/claude-opus-5-5", "x", "avoid", ["hour=18"], "why", 5)
    with pytest.raises(ValueError, match="author"):
        playbook.add(journal, NOW, "claude", "x", "avoid", ["hour=18"], "why", 10)
    rid = playbook.add(journal, NOW, "claude/claude-opus-5-5", "Avoid breakouts in the FOMC hour", "avoid",
                       ["pattern=breakout10,breakout20", "hour=18"], "FOMC 2026-10-28 lesson", 10)
    (r,) = playbook.report(journal)
    e = r["evidence"]
    assert e["matched"]["n"] == 12 and e["others"]["n"] == 6
    assert e["verdict"] == "supported" and e["ready_to_propose"] and e["progress"] == "12/10"
    journal.set_rule_status(rid, NOW, "proposed", "sent", proposal_id=4)
    (r,) = playbook.report(journal)
    assert r["status"] == "proposed" and r["proposal_id"] == 4 and not r["evidence"]["ready_to_propose"]
    doc = playbook.document(journal)
    assert doc["document"] == "fxra-playbook" and "#1 [proposed] AVOID" in doc["content"]
    assert any(d["document"] == "fxra-playbook" for d in lesson_insights.digest(journal))


def test_contradicted_prefer_rule(journal):
    for i in range(10):
        trade(journal, i + 1, "engulfing", 9, win=False)
    playbook.add(journal, NOW, "codex/gpt-6.1-sol", "Prefer engulfing", "prefer", ["pattern=engulfing"], "guess", 10)
    assert playbook.report(journal)[0]["evidence"]["verdict"] == "contradicted"

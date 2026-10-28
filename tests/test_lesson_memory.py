from datetime import datetime, timezone

import pytest

from fxra import duty, lesson_memory
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc)


class FakeHindsight:
    def __init__(self, fail_after=None):
        self.items, self.fail_after = [], fail_after

    def retain_lesson(self, document, content, at, tags):
        if self.fail_after is not None and len(self.items) >= self.fail_after:
            raise RuntimeError("down")
        self.items.append((document, content, tags))


def closed_trade(j, pid, entry_id, tags):
    j.tag_entry(entry_id, tags)
    j.sync_deals([
        {"ticket": pid * 10, "order": 1, "position_id": pid, "time": "2026-10-28T15:45:00+00:00", "side": "sell",
         "entry": "in", "volume": 1.0, "price": 150.30, "profit": 0, "commission": 0, "swap": 0, "comment": entry_id},
        {"ticket": pid * 10 + 1, "order": 2, "position_id": pid, "time": "2026-10-28T16:15:00+00:00", "side": "buy",
         "entry": "out", "volume": 1.0, "price": 150.20, "profit": 66.0, "commission": 0, "swap": 0,
         "comment": "[tp 150.200]"}])


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def test_new_lesson_reaches_hindsight_with_model_tags_and_is_marked(journal):
    closed_trade(journal, 7, "H2CX-UJ-2610281545-11S",
                 {"strategy": "H-002", "arm": "codex", "arm_model": "Codex GPT-6.1 Sol Medium", "symbol": "USDJPY",
                  "pattern": "rsi_extreme_fade", "session": "overlap_ny"})
    lid = journal.add_lesson(NOW, 7, "agent", "USDJPY fade sell hit TP.")
    hs = FakeHindsight()
    out = lesson_memory.flush(journal, hs)
    assert out["retained"] == [f"fxra-lesson-7-{lid}"] and out["pending"] == 0
    doc, content, tags = hs.items[0]
    assert "model:Codex GPT-6.1 Sol Medium" in tags and {"demo", "lesson", "USDJPY", "rsi_extreme_fade", "H-002-codex"} <= set(tags)
    assert "Codex GPT-6.1 Sol Medium" in content
    assert lesson_memory.flush(journal, hs)["retained"] == []  # sent once


FEATURES = {"matches": {"rsi_extreme_fade": "sell", "three_bar_momentum": "buy"}, "atr_pips": 9.0, "sl_pips": 10.0,
            "tp_pips": 15.0, "vol": "high", "h1_trend": "up", "m15_trend": "down", "dow": "Wed", "spread_pips": 0.3,
            "atr_pctile": 0.8}
UJ_TAGS = {"strategy": "H-002", "arm": "codex", "arm_model": "Codex GPT-6.1 Sol Medium", "symbol": "USDJPY",
           "pattern": "rsi_extreme_fade", "session": "overlap_ny", "hour_utc": 15, "features": FEATURES}


def uj_trade(j, pid=7, entry_id="H2CX-UJ-2610281545-11S"):
    j.order(entry_id, NOW, "entry", "sell", 1.0, 150.40, "filled", {}, {})  # stop 10 pips above 150.30
    closed_trade(j, pid, entry_id, UJ_TAGS)
    j.set_exit_reason(pid, "take_profit")
    return j.trade(pid)


def test_lesson_leads_with_setup_conditions_and_r(journal):
    from fxra.demo_agent import _lesson_text
    trade = uj_trade(journal)
    m15 = [{"time": "2026-10-28T15:45:00+00:00", "open": 150.30, "high": 150.33, "low": 150.24, "close": 150.25}]
    text = _lesson_text(trade, {"m15_bars": m15}, "USD", journal.entry_info(trade["entry_id"]))
    assert text.startswith("H-002-codex rsi_extreme_fade sell USDJPY (overlap_ny, 15h UTC, Wed; high vol, "
                           "H1 trend up, M15 trend down): win +1.00R, take_profit.")
    assert "other signals that bar: three_bar_momentum buy" in text and "win +66.00 USD" in text
    # the 16:00 bar (exit) is missing from the snapshot: the realised +10 pips floors the best excursion
    assert "Excursion best +1.00R, worst -0.30R (M15 bars" in text


def test_lesson_tags_carry_outcome_time_and_regime(journal):
    uj_trade(journal)
    journal.add_lesson(NOW, 7, "agent", "x")
    tags = lesson_memory.pending(journal)[0]["tags"]
    assert {"outcome:win", "exit:take_profit", "hour:15", "dow:Wed", "vol:high", "h1_trend:up"} <= set(tags)


def test_insight_digest_counts_from_journal_and_skips_unchanged(journal):
    from fxra import lesson_insights
    uj_trade(journal)
    docs = {d["document"]: d for d in lesson_insights.digest(journal)}
    assert set(docs) == {"fxra-insight-overview", "fxra-insight-pattern-rsi-extreme-fade"}
    overview = docs["fxra-insight-overview"]["content"]
    assert "By session: overlap_ny: n=1, win 100%, avg +0.92R" in overview and "[noise]" in overview
    assert "not evidence of edge" in overview and "Pattern x session with n>=10: none yet" in overview
    assert "By volatility: high: n=1" in docs["fxra-insight-pattern-rsi-extreme-fade"]["content"]
    hs = FakeHindsight()
    assert len(lesson_insights.sync(journal, hs, NOW)["retained"]) == 2
    assert lesson_insights.sync(journal, hs, NOW)["retained"] == []  # unchanged: not re-sent
    uj_trade(journal, 8, "H2CX-UJ-2610281600-11S")
    assert len(lesson_insights.sync(journal, hs, NOW)["retained"]) == 2


def test_unreachable_hindsight_keeps_lessons_pending(journal):
    for i in (1, 2):
        journal.add_lesson(NOW, None, "agent", f"lesson {i}")
    out = lesson_memory.flush(journal, FakeHindsight(fail_after=1))
    assert len(out["retained"]) == 1 and out["failed"] == 1 and out["pending"] == 1
    assert len(lesson_memory.pending(journal)) == 1


def test_document_ids_are_validated():
    with pytest.raises(ValueError):
        lesson_memory.HindsightLessons._document("sim-../../banks")


def test_old_entries_are_backfilled_with_the_arm_model(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    j = Journal(path)
    j.close()
    db = sqlite3.connect(path)  # simulate a pre-D-35 journal: no arm_model column yet
    db.execute("CREATE TABLE t AS SELECT * FROM entry_tags")
    db.execute("DROP TABLE entry_tags")
    cols = [r[1] for r in db.execute("PRAGMA table_info(t)") if r[1] != "arm_model"]
    db.execute(f"CREATE TABLE entry_tags AS SELECT {', '.join(cols)} FROM t")
    db.execute("INSERT INTO entry_tags (entry_id, strategy, arm, features) VALUES ('H2CX-x', 'H-002', 'codex', '{}')")
    db.commit()
    db.close()
    j = Journal(path)
    assert j.db.execute("SELECT arm_model FROM entry_tags").fetchone()[0] == "Codex GPT-6.1 Sol Medium"
    j.close()


def test_roster_labels_and_overrides():
    r = duty.DEFAULT_ROSTER
    assert duty.agent_info(r, "claude")["label"] == "Claude Opus 5.5 Medium"
    assert duty.agent_info(r, "codex")["model"] == "gpt-6.1-sol"
    assert duty.agent_info(r, "haiku")["label"] == "haiku"
    custom = {**r, "agents": {**r["agents"], "codex": {"model": "gpt-7", "effort": "high"}}}
    assert duty.agent_info(custom, "codex")["label"] == "gpt-7 high"

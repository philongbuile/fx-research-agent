import json
from datetime import timedelta

from fxra import h003, lesson_insights, lesson_memory, memory_setup
from fxra.trade_journal import Journal

from test_h003 import AUTHOR, LATEST, NOW, POLICY, Broker, limits, m15, snap


def scored(tmp_path, take: bool, codes=None):
    """One decided candidate whose shadow outcome is known after 8 later bars."""
    j = Journal(tmp_path / "j.sqlite")
    cid = h003.step(j, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), POLICY)["new"][0]
    h003.decide(j, cid, take, "three patterns agree, low spread", AUTHOR, NOW, codes=codes,
                memory_refs=["fx-entry-guide", "h003-claude-decisions"])
    later = NOW + timedelta(hours=2, minutes=15)
    bars = m15(1.1020, n=88, end=LATEST + timedelta(hours=2))
    h003.step(j, Broker(), limits(tmp_path), later, snap(bars, at=later), POLICY, arm_model="Claude Opus 5.5 Medium")
    return j, cid


def test_skipped_decision_becomes_a_tagged_lesson_with_its_shadow_outcome(tmp_path):
    j, cid = scored(tmp_path, take=False)
    c = j.candidates(ids=[cid])[0]
    assert c["shadow"] and json.loads(c["memory_refs"]) == ["fx-entry-guide", "h003-claude-decisions"]
    [item] = lesson_memory.pending(j)
    assert item["content"].startswith("H-003-claude decision: skipped buy EURUSD")
    assert "Shadow outcome" in item["content"] and "three patterns agree" in item["content"]
    assert {"H-003", "H-003-claude", "decision:skip", "model:Claude Opus 5.5 Medium"} <= set(item["tags"])
    assert any(t.startswith("verdict:") for t in item["tags"]) and item["document"].startswith("fxra-lesson-0-")


def test_decision_digest_compares_taken_and_skipped(tmp_path):
    j, _ = scored(tmp_path, take=False)
    docs = {d["document"]: d for d in lesson_insights.digest(j)}
    d = docs["fxra-insight-h003-claude"]
    assert "Taken n=0 -; skipped n=1" in d["content"] and "H-003-claude" in d["tags"]


def test_brief_names_what_to_read_first(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    rf = h003.brief(j, NOW, "codex")["read_first"]
    assert rf["mental_models"] == ["fx-entry-guide", "h003-codex-decisions", "fx-market-context"]
    assert "fxra-insight-h003-codex" in rf["fallback_documents"]


class Admin:
    def __init__(self, directives=(), models=()):
        self.calls, self.directives, self.models = [], list(directives), list(models)

    def call(self, method, path, data=None):
        self.calls.append((method, path.split("?")[0], data))
        if method == "GET":
            return {"items": self.directives if "directives" in path else self.models}
        return {}


def test_setup_is_idempotent_and_mental_models_are_opt_in():
    a = Admin()
    done = memory_setup.setup(a)
    assert len(done) == len(memory_setup.DIRECTIVES) and [m for m, p, _ in a.calls if p == "/directives"] == ["GET"] + ["POST"] * 4
    current = [{"id": f"d{i}", "name": n, "content": c, "priority": p, "is_active": True, "tags": []}
               for i, (n, p, c) in enumerate(memory_setup.DIRECTIVES)]
    assert memory_setup.setup(Admin(current)) == []
    b = Admin(current, [{"id": "fx-entry-guide"}])
    done = memory_setup.setup(b, mental_models=True)
    assert "mental model fx-entry-guide: updated" in done and "mental model h003-codex-decisions: created" in done
    assert ("PATCH", "/mental-models/fx-entry-guide") in [(m, p) for m, p, _ in b.calls]
    refreshes = [d for m, p, d in b.calls if p.endswith("/refresh")]
    assert len(refreshes) == len(memory_setup.MENTAL_MODELS) and all(d == {"mode": "full"} for d in refreshes)


def test_decisions_with_reason_codes_become_lessons_and_backfill_fills_gaps(tmp_path):
    j, cid = scored(tmp_path, take=False, codes=["trend_against"])  # a take would expire unsent here
    [item] = lesson_memory.pending(j)
    assert "codes trend_against" in item["content"] and "code:trend_against" in item["tags"]
    assert "trend_against skip n=1" in {d["document"]: d for d in lesson_insights.digest(j)}["fxra-insight-h003-claude"]["content"]
    assert h003.backfill_decision_lessons(j, NOW) == 0  # already has its lesson
    j.db.execute("DELETE FROM lessons")
    assert h003.backfill_decision_lessons(j, NOW, {"claude": "Claude Opus 5.5 Medium"}) == 1
    assert "model:Claude Opus 5.5 Medium" in lesson_memory.pending(j)[0]["tags"]

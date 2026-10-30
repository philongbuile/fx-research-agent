import json
import sqlite3
from datetime import datetime, timezone

import pytest

from fxra import demo_evaluation
from fxra.trade_journal import Journal


def test_audit_is_read_only_and_does_not_conflate_approvals_expirations_or_matched_fills(tmp_path):
    path = tmp_path / "journal.sqlite"
    j = Journal(path)
    at = "2026-10-29T18:00:00+00:00"
    for cid, status, shadow in (("fill", "sent", 1.4), ("reject", "rejected", -1.1),
                                ("skip", "skipped", -.5), ("expired", "expired", 1.4)):
        j.db.execute("INSERT INTO candidates (id,arm,symbol,side,bar,created_at,expires_at,status,"
                     "patterns,context,shadow,client_order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, "codex", "EURUSD", "buy", at, at, at, status, '{}', '{}',
                      json.dumps({"R": shadow}), cid))
    j.db.execute("INSERT INTO entry_tags (entry_id,strategy,features,arm,symbol) VALUES (?,?,?,?,?)",
                 ("fill", "H-003", '{}', "codex", "EURUSD"))
    j.db.execute("INSERT INTO orders (client_order_id,at,kind,status,risk,result,stop_loss) VALUES (?,?,?,?,?,?,?)",
                 ("fill", at, "entry", "filled", '{}', '{}', 1.099))
    j.db.execute("INSERT INTO trades (position_id,side,volume,open_time,open_price,entry_id,"
                 "close_time,close_price,net) VALUES (?,?,?,?,?,?,?,?,?)",
                 (1, "buy", .1, at, 1.1, "fill", "2026-10-29T19:00:00+00:00", 1.099, -10))
    j.db.commit()
    j.close()
    before = path.read_bytes()
    out = demo_evaluation.audit(path, "2026-10-29T17:00:00Z", "2026-10-29T20:00:00Z")["arms"]["codex"]
    assert out["legacy_shadow_take_including_rejected"]["n"] == 2
    assert out["legacy_shadow_explicit_skip"]["n"] == 1
    assert out["legacy_shadow_expired"]["n"] == 1
    assert out["closed_fills"] == 1 and out["broker_net_usd"] == -10
    assert out["matched"]["legacy_shadow"]["mean_R"] == 1.4
    assert out["matched"]["actual_price_after_assumed_cost"]["mean_R"] == pytest.approx(-1.08)
    assert path.read_bytes() == before
    # Half-open end: a fill closing exactly at cutoff is not a realized result yet.
    cut = demo_evaluation.audit(path, "2026-10-29T17:00:00Z", "2026-10-29T19:00:00Z")
    assert cut["arms"]["codex"]["closed_fills"] == 0
    assert not cut["arms"]["codex"]["matched"]["pairs"]


def test_audit_never_creates_missing_journal_or_accepts_naive_time(tmp_path):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        demo_evaluation.audit(path, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert not path.exists()
    with pytest.raises(ValueError, match="offset"):
        demo_evaluation.audit(path, "2026-01-01", "2026-01-02T00:00:00Z")


def test_observation_run_records_dirty_code_honestly_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_evaluation.runs, "git_info", lambda: {"git_commit": "abc", "git_dirty": True})
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = demo_evaluation.write_run({"x": 1}, tmp_path, kind="demo_audit", hypothesis_id="H-003", now=now)
    b = demo_evaluation.write_run({"x": 2}, tmp_path, kind="demo_audit", hypothesis_id="H-003", now=now)
    assert a["run_id"] != b["run_id"] and a["reproducible"] is False
    record = json.loads((tmp_path / a["run_id"] / "run.json").read_text())
    assert record["technical_only"] and record["metrics"]["x"] == 1

import filecmp
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fxra import duty, h002
from fxra.trade_journal import Journal

REPO = Path(__file__).resolve().parents[1]


def at_local(hour, minute=0):
    # Asia/Ho_Chi_Minh is UTC+7 without DST
    return datetime(2026, 10, 28, hour, minute, tzinfo=timezone(timedelta(hours=7))).astimezone(timezone.utc)


def test_default_roster_codex_by_day_claude_by_night():
    r = duty.DEFAULT_ROSTER
    assert duty.on_duty(r, at_local(8))["model"] == "gpt-6-sol"
    assert duty.on_duty(r, at_local(17, 59))["agent"] == "codex"
    assert duty.on_duty(r, at_local(18))["model"] == "claude-opus-5-5"
    assert duty.on_duty(r, at_local(3))["agent"] == "claude"
    assert duty.on_duty(r, at_local(23))["until"].startswith("2026-10-29T08:00")


def test_override_wins_until_it_expires(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    now = at_local(10)
    j.take_duty(now, "claude", "claude-opus-5-5", now + timedelta(hours=2), "codex quota")
    o = j.latest_override()
    assert duty.on_duty(duty.DEFAULT_ROSTER, now + timedelta(hours=1), o)["source"] == "override"
    assert duty.on_duty(duty.DEFAULT_ROSTER, now + timedelta(hours=3), o)["agent"] == "codex"
    j.close()


def test_roster_file_is_flexible(tmp_path):
    p = tmp_path / "duty.toml"
    p.write_text('timezone = "UTC"\n[[shift]]\nagent = "small"\nmodel = "light-llm"\nstart = "00:00"\nend = "00:00"\n')
    assert duty.on_duty(duty.load_roster(p), at_local(12))["model"] == "light-llm"


def _trade(j, pid, entry_id, model, net, tags_extra=None):
    j.tag_entry(entry_id, {"strategy": "H-002", "pattern": "breakout20", "hour_utc": 9, "session": "london",
                           "policy_version": 1, "duty_agent": model.split("-")[0], "duty_model": model,
                           **(tags_extra or {})})
    j.sync_deals([
        {"ticket": pid * 10, "order": 1, "position_id": pid, "time": "2026-10-28T09:15:00+00:00", "side": "buy",
         "entry": "in", "volume": 0.1, "price": 1.1, "profit": 0, "commission": 0, "swap": 0, "comment": entry_id},
        {"ticket": pid * 10 + 1, "order": 2, "position_id": pid, "time": "2026-10-28T09:45:00+00:00", "side": "sell",
         "entry": "out", "volume": 0.1, "price": 1.1 + net / 1000, "profit": net, "commission": 0, "swap": 0,
         "comment": "x"}])


def test_scoreboard_by_model(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    _trade(j, 1, "H2-a", "gpt-6-sol", 10.0)
    _trade(j, 2, "H2-b", "gpt-6-sol", -5.0)
    _trade(j, 3, "H2-c", "claude-opus-5-5", 4.0)
    j.add_lesson(datetime.now(timezone.utc), 1, "codex/gpt-6-sol", "lesson")
    sb = j.model_scoreboard(0)
    g = sb["by_model"]["gpt-6-sol"]
    assert (g["n"], g["wins"], g["losses"], g["net"], g["profit_factor"], g["win_rate"]) == (2, 1, 1, 5.0, 2.0, 0.5)
    assert sb["by_model"]["claude-opus-5-5"]["win_rate"] == 1.0
    assert sb["lessons_by_author"] == {"codex/gpt-6-sol": 1}
    assert "gpt-6-sol / H-002" in sb["by_model_strategy"]
    j.close()


def test_h002_entry_is_attributed_to_model_on_duty(tmp_path):
    from test_h002 import NOW, Broker, limits, m15, snap  # pytest puts tests/ on sys.path
    j = Journal(tmp_path / "j.sqlite")
    h002.step(j, Broker(), limits(tmp_path), NOW, snap(m15(1.1020)), dict(h002.DEFAULT_POLICY),
              rng=random.Random(0), duty={"agent": "claude", "model": "claude-opus-5-5"})
    assert tuple(j.db.execute("SELECT duty_agent, duty_model FROM entry_tags").fetchone()) == ("claude", "claude-opus-5-5")
    j.close()


def test_risk_based_size_is_capped_and_stepped():
    spec = {"contract_size": 100000, "volume_step": 0.01, "volume_min": 0.01}
    pol = {**h002.DEFAULT_POLICY, "risk_per_trade_pct": 0.5}
    assert h002.size_lots(pol, 100000, 0.0010, spec, 10.0) == 5.0      # 500 USD at 10 pips
    assert h002.size_lots(pol, 100000, 0.0010, spec, 1.0) == 1.0       # risk.toml cap wins
    assert h002.size_lots(h002.DEFAULT_POLICY, 100000, 0.0010, spec, 1.0) == 0.1


def test_codex_skill_mirror_matches_claude_skills():
    src, mirror = REPO / ".claude" / "skills", REPO / ".agents" / "skills"
    for skill in src.iterdir():
        cmp = filecmp.dircmp(skill, mirror / skill.name)
        assert not (cmp.left_only or cmp.right_only or cmp.diff_files), f"run `uv run fxra sync-skills` ({skill.name})"

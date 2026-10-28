import random
from datetime import datetime, timedelta, timezone

import pytest

from fxra import h002, risk
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 28, 14, 15, 10, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)


def limits(tmp_path, **kw):
    base = dict(mode="demo", allowed_symbols=frozenset({"EURUSD"}),
                approved_strategies=frozenset({"H-002@explore"}), max_lots_per_order=0.1,
                max_open_positions=3, max_daily_loss_fraction=0.01, max_spread_points=20,
                max_quote_age_seconds=5, max_account_age_seconds=30, require_stop_loss=True,
                kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    return risk.RiskLimits(**{**base, **kw})


def m15(last_close=1.1000, n=80):
    out = []
    for i in range(n):
        t = LATEST - timedelta(minutes=15 * (n - 1 - i))
        out.append({"time": t.isoformat(), "open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1})
    out[-1].update(close=last_close, high=max(1.1005, last_close), low=min(1.0995, last_close))
    return out


def snap(bars, positions=()):
    return {"quote": {"bid": 1.10200, "ask": 1.10201, "time": (NOW - timedelta(seconds=1)).isoformat()},
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


def test_patterns_detect_breakout_and_reversion():
    assert h002.patterns(m15(1.1020))["breakout20"] == "buy"
    assert h002.patterns(m15(1.0980))["breakout20"] == "sell"
    assert h002.patterns(m15(1.0980)).get("bb_rsi_reversion") == "buy"
    assert h002.patterns(m15(1.1000)) == {}


def test_choose_explores_unseen_then_exploits():
    p = dict(h002.DEFAULT_POLICY)
    rng = random.Random(1)
    stats = {"breakout20": {"n": 5, "avg_pips_after_cost": -2}, "bb_rsi_reversion": {"n": 1}}
    assert h002.choose({"breakout20": "buy", "bb_rsi_reversion": "buy"}, stats, p, rng) == "bb_rsi_reversion"
    stats["bb_rsi_reversion"] = {"n": 5, "avg_pips_after_cost": 3}
    picks = {h002.choose({"breakout20": "buy", "bb_rsi_reversion": "buy"}, stats, {**p, "epsilon": 0}, rng)
             for _ in range(20)}
    assert picks == {"bb_rsi_reversion"}
    assert h002.choose({"breakout20": "buy"}, stats, {**p, "enabled": ["ema_pullback"]}, rng) is None


def test_entry_tags_pattern_hour_and_sends_with_tp_sl_magic(journal, tmp_path):
    b = Broker()
    out = h002.step(journal, b, limits(tmp_path), NOW, snap(m15(1.1020)), dict(h002.DEFAULT_POLICY),
                    rng=random.Random(0))
    assert out["action"] == "entry" and out["side"] == "buy"
    sent = b.sent[0]
    assert sent["magic"] == h002.MAGIC and sent["intent"]["quantity_lots"] == 0.1
    i = sent["intent"]
    assert i["stop_loss"] < 1.10201 < i["take_profit"]
    assert round((i["take_profit"] - 1.10201) / (1.10201 - i["stop_loss"]), 1) == 1.5
    tag = journal.db.execute("SELECT pattern, hour_utc, session FROM entry_tags").fetchone()
    assert tuple(tag) == (out["pattern"], 14, "overlap_ny")
    again = h002.step(journal, b, limits(tmp_path), NOW, snap(m15(1.1020)), dict(h002.DEFAULT_POLICY))
    assert again["action"] == "skip" and len(b.sent) == 1


def test_avoid_hours_and_unapproved(journal, tmp_path):
    b = Broker()
    pol = {**h002.DEFAULT_POLICY, "avoid_hours_utc": [14]}
    assert h002.step(journal, b, limits(tmp_path), NOW, snap(m15(1.1020)), pol)["action"] == "no_trade"
    j2 = Journal(tmp_path / "j2.sqlite")
    out = h002.step(j2, b, limits(tmp_path, approved_strategies=frozenset()), NOW, snap(m15(1.1020)),
                    dict(h002.DEFAULT_POLICY))
    assert out["action"] == "blocked" and b.sent == []
    j2.close()


def test_time_exit_closes_own_position_only(journal, tmp_path):
    b = Broker()
    mine = {"ticket": 5, "magic": h002.MAGIC, "side": "buy", "volume": 0.1,
            "time": (LATEST - timedelta(minutes=15 * 7)).isoformat()}
    other = {"ticket": 6, "magic": 20261028, "side": "sell", "volume": 0.01, "time": LATEST.isoformat()}
    out = h002.step(journal, b, limits(tmp_path), NOW, snap(m15(), [other, mine]), dict(h002.DEFAULT_POLICY))
    assert out["action"] == "exit" and b.closed == [{"ticket": 5, "client_order_id": "H2X-5", "symbol": "EURUSD"}]


def test_policy_update_is_versioned_and_validated(journal, tmp_path):
    path = tmp_path / "policy.json"
    new = h002.update_policy(path, journal, {"tp_rr": 2.0}, "claude", "test wider targets")
    assert new["version"] == 2 and h002.load_policy(path)["tp_rr"] == 2.0
    assert journal.state()["policy_changes"][0]["reason"] == "test wider targets"
    with pytest.raises(ValueError):
        h002.update_policy(path, journal, {"leverage": 500}, "claude", "x")
    with pytest.raises(ValueError):
        h002.update_policy(path, journal, {"enabled": ["martingale"]}, "claude", "x")


def test_learning_stats_group_by_pattern_and_hour(journal):
    journal.tag_entry("H2-1", {"strategy": "H-002", "pattern": "breakout20", "hour_utc": 9, "session": "london",
                               "policy_version": 1})
    deals = [{"ticket": 1, "order": 1, "position_id": 1, "time": "2026-10-28T09:15:00+00:00", "side": "buy",
              "entry": "in", "volume": 0.1, "price": 1.1000, "profit": 0, "commission": 0, "swap": 0,
              "comment": "H2-1"},
             {"ticket": 2, "order": 2, "position_id": 1, "time": "2026-10-28T09:45:00+00:00", "side": "sell",
              "entry": "out", "volume": 0.1, "price": 1.1010, "profit": 10.0, "commission": 0, "swap": 0,
              "comment": "[tp 1.10100]"}]
    journal.sync_deals(deals)
    s = journal.learning_stats(0.8)
    assert s["by_pattern"]["breakout20"] == {"n": 1, "wins": 1, "net": 10.0, "win_rate": 1.0,
                                             "avg_pips_after_cost": 9.2}
    assert "9" in s["by_hour"] and "breakout20 / london" in s["by_pattern_session"]

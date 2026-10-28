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


def test_patterns_detect_breakout_and_reversion():
    assert h002.patterns(m15(1.1020))["breakout20"] == "buy"
    assert h002.patterns(m15(1.0980))["breakout20"] == "sell"
    assert h002.patterns(m15(1.0980)).get("bb_rsi_reversion") == "buy"
    assert h002.patterns(m15(1.1000)) == {}


def bar(o, h, l, c, i):
    return {"time": (LATEST - timedelta(minutes=15 * (80 - i))).isoformat(), "open": o, "high": h, "low": l, "close": c}


def with_tail(*tail):
    base = m15(1.1000)[: 80 - len(tail)]
    return base + [bar(*t, 80 - len(tail) + i + 1) for i, t in enumerate(tail)]


def test_every_pattern_has_a_name_in_the_catalogue():
    seen = set()
    for close in (1.1020, 1.0980, 1.1007, 1.0993):
        seen |= set(h002.patterns(m15(close)))
    seen |= set(h002.patterns(with_tail((1.1003, 1.1004, 1.0996, 1.0997), (1.0996, 1.1006, 1.0995, 1.1005))))
    assert seen <= set(h002.PATTERNS)


def test_candle_patterns():
    engulf = h002.patterns(with_tail((1.1003, 1.1004, 1.0996, 1.0997), (1.0996, 1.1006, 1.0995, 1.1005)))
    assert engulf["engulfing"] == "buy"
    pin = h002.patterns(with_tail((1.1000, 1.1001, 1.0990, 1.0999)))
    assert pin["pin_bar"] == "buy"
    fade = h002.patterns(with_tail((1.1000, 1.1012, 1.0999, 1.1003)))
    assert fade["failed_breakout_fade"] == "sell"
    big = h002.patterns(m15(1.1030))
    assert big["momentum_candle"] == "buy" and big["breakout10"] == "buy" and big["ema_cross_9_21"] == "buy"


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


def test_every_matched_pattern_opens_its_own_position(journal, tmp_path):
    b = Broker()
    bars = m15(1.1020)
    matched = h002.patterns(bars)
    out = h002.step(journal, b, limits(tmp_path, max_open_positions=50), NOW, snap(bars),
                    dict(h002.DEFAULT_POLICY), rng=random.Random(0))
    assert out["action"] == "entry" and {e["pattern"] for e in out["entries"]} == set(matched)
    assert len(b.sent) == len(matched) > 1
    ids = [s["intent"]["client_order_id"] for s in b.sent]
    assert len(set(ids)) == len(ids) and all(i.startswith("H2CL-2610281400-") and len(i) <= 31 for i in ids)
    for s in b.sent:
        i = s["intent"]
        assert s["magic"] == h002.ARMS["claude"]["magic"] and i["quantity_lots"] == 0.1
        assert i["side"] == matched[next(p for p in h002.PATTERNS if i["client_order_id"].endswith(
            f"-{h002.PATTERNS.index(p):02d}{i['side'][0].upper()}"))]
        if i["side"] == "buy":
            assert i["stop_loss"] < 1.10201 < i["take_profit"]
    tag = journal.db.execute("SELECT arm, hour_utc, session FROM entry_tags LIMIT 1").fetchone()
    assert tuple(tag) == ("claude", 14, "overlap_ny")
    again = h002.step(journal, b, limits(tmp_path, max_open_positions=50), NOW, snap(bars), dict(h002.DEFAULT_POLICY))
    assert again["action"] == "skip" and len(b.sent) == len(matched)


def test_entries_per_bar_and_already_open_patterns(journal, tmp_path):
    b = Broker()
    bars = m15(1.1020)
    pol = {**h002.DEFAULT_POLICY, "entries_per_bar": 1}
    out = h002.step(journal, b, limits(tmp_path, max_open_positions=50), NOW, snap(bars), pol, rng=random.Random(0))
    assert len(out["entries"]) == 1
    first = b.sent[0]["intent"]["client_order_id"]
    held = {"ticket": 9, "magic": h002.ARMS["claude"]["magic"], "side": "buy", "volume": 0.1,
            "time": LATEST.isoformat(), "comment": first}
    later = NOW + timedelta(minutes=15)
    out2 = h002.step(journal, b, limits(tmp_path, max_open_positions=50), later,
                     snap(m15(1.1020, end=LATEST + timedelta(minutes=15)), [held], at=later), dict(h002.DEFAULT_POLICY),
                     rng=random.Random(0))
    assert out2["entries"] and out["entries"][0]["pattern"] not in {e["pattern"] for e in out2["entries"]}


def test_arms_trade_independently_with_own_magic(journal, tmp_path):
    b = Broker()
    lim = limits(tmp_path, max_open_positions=50)
    for arm in h002.ARMS:
        h002.step(journal, b, lim, NOW, snap(m15(1.1020)), dict(h002.DEFAULT_POLICY), arm=arm, rng=random.Random(0))
    magics = {s["magic"] for s in b.sent}
    assert magics == {20261029, 20261030}
    assert journal.decided("H-002-claude:" + LATEST.isoformat()) and journal.decided("H-002-codex:" + LATEST.isoformat())


def test_arm_policy_ownership_and_initial_copy(journal, tmp_path):
    (tmp_path / "h002-policy.json").write_text('{"version": 3, "lots": 1.0}')
    claude = h002.arm_policy(tmp_path, "claude", journal)
    codex = h002.arm_policy(tmp_path, "codex", journal)
    assert claude["lots"] == codex["lots"] == 1.0 and claude["version"] == codex["version"] == 1
    with pytest.raises(PermissionError):
        h002.edit_arm_policy(tmp_path, "claude", journal, {"tp_rr": 3}, "codex/gpt-6-sol", "try")
    new = h002.edit_arm_policy(tmp_path, "codex", journal, {"tp_rr": 3}, "codex/gpt-6-sol", "wider targets")
    assert new["version"] == 2 and h002.arm_policy(tmp_path, "claude", journal)["tp_rr"] == 1.5
    arms = {(c["arm"], c["version"]) for c in journal.state()["policy_changes"]}
    assert {("claude", 1), ("codex", 1), ("codex", 2)} <= arms


def test_claude_arm_continues_legacy_version_line(journal, tmp_path):
    legacy = tmp_path / "h002-policy.json"
    legacy.write_text('{"version": 1}')
    h002.update_policy(legacy, journal, {"lots": 1.0}, "claude/claude-opus-5-5", "v2")  # pre-D-33 history
    h002.update_policy(legacy, journal, {"tp_rr": 2.0}, "claude/claude-opus-5-5", "v3")
    assert h002.arm_policy(tmp_path, "claude", journal)["version"] == 4
    new = h002.edit_arm_policy(tmp_path, "claude", journal, {"max_hold_bars": 12}, "claude/claude-opus-5-5", "next")
    assert new["version"] == 5 and new["tp_rr"] == 2.0


def test_multi_symbol_uses_each_symbols_pip_ids_and_lots(journal, tmp_path):
    b = Broker()
    jpy = [{**r, **{k: r[k] * 136.36 for k in ("open", "high", "low", "close")}} for r in m15(1.1020)]
    gold = [{**r, **{k: r[k] * 3636.36 for k in ("open", "high", "low", "close")}} for r in m15(1.1020)]
    base = snap(m15(1.1000))
    mk = lambda bars, bid, ask, digits, point: {"quote": {**base["quote"], "bid": bid, "ask": ask},
                                                "m15_bars": bars, "h1_bars": [],
                                                "spec": {**base["spec"], "digits": digits, "point": point,
                                                         "quote_currency": "JPY" if digits == 3 else "USD"}}
    s = {**base, "markets": {"EURUSD": {k: base[k] for k in ("quote", "m15_bars", "h1_bars", "spec")},
                             "USDJPY": mk(jpy, 150.300, 150.302, 3, 0.001),
                             "XAUUSD": mk(gold, 4007.10, 4007.30, 2, 0.01)}}
    lim = limits(tmp_path, max_open_positions=50, allowed_symbols=frozenset({"EURUSD", "USDJPY", "XAUUSD"}),
                 max_lots_per_order=1.0, max_spread_points_by_symbol={"XAUUSD": 60})
    pol = {**h002.DEFAULT_POLICY, "lots": 1.0, "symbols": ["EURUSD", "USDJPY", "XAUUSD"]}
    out = h002.step(journal, b, lim, NOW, s, pol, rng=random.Random(0))
    by_symbol = {}
    for sent in b.sent:
        by_symbol.setdefault(sent["intent"]["symbol"], []).append(sent["intent"])
    assert set(by_symbol) == {"USDJPY", "XAUUSD"} and not out["symbols"]["EURUSD"]["entries"]
    uj = by_symbol["USDJPY"][0]
    assert uj["client_order_id"].startswith("H2CL-UJ-") and uj["quantity_lots"] == 1.0
    entry = 150.302 if uj["side"] == "buy" else 150.300
    assert abs(entry - uj["stop_loss"]) >= 0.05 - 1e-9  # min stop = 5 JPY pips (0.01 each)
    xa = by_symbol["XAUUSD"][0]
    assert xa["client_order_id"].startswith("H2CL-XA-") and xa["quantity_lots"] == 0.1
    assert journal.decided(f"H-002-claude:USDJPY:{LATEST.isoformat()}")
    assert journal.decided(f"H-002-claude:{LATEST.isoformat()}")  # EURUSD keeps the original key
    tags = dict(journal.db.execute("SELECT entry_id, symbol FROM entry_tags").fetchall())
    assert tags[uj["client_order_id"]] == "USDJPY"


def test_gold_spread_limit_is_per_symbol(tmp_path):
    from fxra.contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
    spec = SymbolSpec("XAUUSD", "t", 2, 0.01, 0.1, 100, 0.01, 100, 0.01, "USD")
    q = Quote("XAUUSD", NOW, 4007.00, 4007.30)  # 30 points
    intent = OrderIntent("x1", "H-002", "explore", "XAUUSD", "buy", 0.1, "market", stop_loss=4000.0)
    acct = AccountSnapshot(NOW, "USD", 1e5, 1e5, 0)
    base = limits(tmp_path, allowed_symbols=frozenset({"XAUUSD"}))
    assert "spread_too_wide:30.0" in risk.evaluate(intent, base, spec, q, acct, NOW, set(), False).reasons
    wide = limits(tmp_path, allowed_symbols=frozenset({"XAUUSD"}), max_spread_points_by_symbol={"XAUUSD": 60})
    assert risk.evaluate(intent, wide, spec, q, acct, NOW, set(), False).approved


def test_proposal_needs_the_other_agent(journal):
    pid = journal.propose(NOW, "codex/gpt-6-sol", "disable pin_bar in asia", "pin_bar/asia n=14 avg -1.9 pips")
    with pytest.raises(PermissionError):
        journal.review(pid, NOW, "codex/gpt-6-sol", True, "self review")
    done = journal.review(pid, NOW, "claude/claude-opus-5-5", True, "claude arm agrees: n=11, -1.4 pips")
    assert done["status"] == "agreed"
    with pytest.raises(ValueError):
        journal.review(pid, NOW, "claude/claude-opus-5-5", False, "again")


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
    codex = {**mine, "ticket": 7, "magic": h002.ARMS["codex"]["magic"]}
    j2 = Journal(tmp_path / "j2.sqlite")
    h002.step(j2, b, limits(tmp_path), NOW, snap(m15(), [codex]), dict(h002.DEFAULT_POLICY), arm="claude")
    assert all(c["ticket"] != 7 for c in b.closed)  # an arm never touches the other arm's positions
    j2.close()


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
    g = s["by_pattern"]["breakout20"]
    assert (g["n"], g["wins"], g["net"], g["win_rate"], g["avg_pips_after_cost"]) == (1, 1, 10.0, 1.0, 9.2)
    assert "claude / breakout20" in s["by_arm_pattern"]  # pre-D-33 H-002 trades belong to the Claude arm
    assert "9" in s["by_hour"] and "breakout20 / london" in s["by_pattern_session"]

import json
from datetime import datetime, timedelta, timezone

import pytest

from fxra import demo_agent, risk
from fxra.dashboard import Agent, make_handler
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 28, 14, 0, 20, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)


def limits(tmp_path, **kw):
    base = dict(mode="demo", allowed_symbols=frozenset({"EURUSD"}),
                approved_strategies=frozenset({"H-001B@h001b-v1"}), max_lots_per_order=0.01,
                max_open_positions=1, max_daily_loss_fraction=0.01, max_spread_points=20,
                max_quote_age_seconds=5, max_account_age_seconds=30, require_stop_loss=True,
                kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    return risk.RiskLimits(**{**base, **kw})


def bars(last_close=1.1000, n=60, end=LATEST, gap_at=None):
    out = []
    for i in range(n):
        t = end - timedelta(hours=n - 1 - i)
        if gap_at is not None and i == gap_at:
            continue
        out.append({"time": t.isoformat(), "open": 1.1, "high": 1.1010, "low": 1.0990, "close": 1.1,
                    "tick_volume": 10, "spread_points": 2})
    out[-1].update(close=last_close, high=max(1.1010, last_close), low=min(1.0990, last_close))
    return out


class FakeBroker:
    def __init__(self, h1, positions=(), deals=(), quote=True):
        self.h1, self.positions, self.deals, self.quote = h1, list(positions), list(deals), quote
        self.sent, self.closed = [], []

    def snapshot(self, payload):
        return {"account": {"currency": "USD", "balance": 10000.0, "equity": 10000.0, "hedging": True},
                "spec": {"digits": 5, "point": 0.00001, "contract_size": 100000, "volume_min": 0.01,
                         "volume_max": 100, "volume_step": 0.01, "quote_currency": "USD"},
                "quote": {"bid": 1.10030, "ask": 1.10032, "time": (NOW - timedelta(seconds=1)).isoformat()}
                if self.quote else None,
                "h1_bars": self.h1, "positions": self.positions, "deals": self.deals}

    def send(self, payload):
        self.sent.append(payload)
        return {"status": "filled", "price": 1.10032}

    def close(self, payload):
        self.closed.append(payload)
        return {"status": "filled"}


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def test_breakout_enters_long_with_atr_stop_and_is_idempotent(journal, tmp_path):
    broker = FakeBroker(bars(last_close=1.1030))
    out = demo_agent.step(journal, broker, limits(tmp_path), NOW)
    assert out["action"] == "entry" and out["side"] == "buy"
    intent = broker.sent[0]["intent"]
    assert intent["client_order_id"] == "H1B-26102813-B"
    assert intent["stop_loss"] < 1.10032 and broker.sent[0]["mode"] == "demo"
    again = demo_agent.step(journal, broker, limits(tmp_path), NOW + timedelta(seconds=30))
    assert again["action"] == "skip" and len(broker.sent) == 1


def test_inside_channel_no_trade(journal, tmp_path):
    broker = FakeBroker(bars(last_close=1.1000))
    assert demo_agent.step(journal, broker, limits(tmp_path), NOW)["action"] == "no_trade"
    assert broker.sent == []


def test_gap_in_last_25_hours_means_no_trade(journal, tmp_path):
    broker = FakeBroker(bars(last_close=1.1030, gap_at=50))
    out = demo_agent.step(journal, broker, limits(tmp_path), NOW)
    assert out == {"action": "no_trade", "bar": LATEST.isoformat(), "reason": "gap"}


def test_market_closed_skips_without_decision(journal, tmp_path):
    out = demo_agent.step(journal, FakeBroker(bars(), quote=False), limits(tmp_path), NOW)
    assert out["action"] == "skip" and journal.state()["decisions"] == []
    stale = FakeBroker(bars(end=LATEST - timedelta(hours=3)))
    assert demo_agent.step(journal, stale, limits(tmp_path), NOW)["action"] == "skip"


@pytest.mark.parametrize("change,reason", [
    ({"approved_strategies": frozenset()}, "strategy_version_not_approved"),
    ({"mode": "live"}, "mode_not_allowed:live"),
])
def test_risk_gate_blocks_and_nothing_is_sent(journal, tmp_path, change, reason):
    broker = FakeBroker(bars(last_close=1.1030))
    out = demo_agent.step(journal, broker, limits(tmp_path, **change), NOW)
    assert out["action"] == "blocked" and reason in out["reasons"] and broker.sent == []


def test_kill_switch_blocks(journal, tmp_path):
    (tmp_path / "KILL_SWITCH").write_text("stop")
    broker = FakeBroker(bars(last_close=1.1030))
    out = demo_agent.step(journal, broker, limits(tmp_path), NOW)
    assert "kill_switch_active" in out["reasons"] and broker.sent == []


def test_missing_limits_fail_closed(journal):
    broker = FakeBroker(bars(last_close=1.1030))
    out = demo_agent.step(journal, broker, None, NOW)
    assert out["action"] == "blocked" and broker.sent == []


def test_open_long_exits_on_opposite_channel_and_time(journal, tmp_path):
    pos = {"ticket": 7, "side": "buy", "volume": 0.01, "price_open": 1.1, "sl": 1.098,
           "time": (LATEST - timedelta(hours=3)).isoformat()}
    broker = FakeBroker(bars(last_close=1.0980), positions=[pos])
    out = demo_agent.step(journal, broker, limits(tmp_path), NOW)
    assert out["rule"] == "opposite_10_bar_channel" and broker.closed[0]["client_order_id"] == "H1B-X-7"

    old = {**pos, "ticket": 8, "time": (LATEST - timedelta(hours=23)).isoformat()}
    broker2 = FakeBroker(bars(last_close=1.1000), positions=[old])
    j2 = Journal(tmp_path / "j2.sqlite")
    assert demo_agent.step(j2, broker2, limits(tmp_path), NOW)["rule"] == "max_hold_24h"
    j2.close()


def test_holding_inside_channel(journal, tmp_path):
    pos = {"ticket": 7, "side": "sell", "volume": 0.01, "price_open": 1.1, "sl": 1.102,
           "time": (LATEST - timedelta(hours=2)).isoformat()}
    broker = FakeBroker(bars(last_close=1.1000), positions=[pos])
    assert demo_agent.step(journal, broker, limits(tmp_path), NOW)["action"] == "hold"
    assert broker.closed == []


def test_closed_deals_become_trade_with_broker_pnl_and_lesson(journal, tmp_path):
    deals = [
        {"ticket": 1, "order": 1, "position_id": 7, "time": (LATEST - timedelta(hours=5)).isoformat(),
         "side": "buy", "entry": "in", "volume": 0.01, "price": 1.1, "profit": 0.0, "commission": -0.03,
         "swap": 0.0, "fee": 0.0, "comment": "H1B-26102808-B"},
        {"ticket": 2, "order": 2, "position_id": 7, "time": (LATEST - timedelta(hours=1)).isoformat(),
         "side": "sell", "entry": "out", "volume": 0.01, "price": 1.098, "profit": -2.0, "commission": -0.03,
         "swap": -0.1, "fee": 0.0, "comment": "[sl 1.09800]"},
    ]
    broker = FakeBroker(bars(), deals=deals)
    demo_agent.step(journal, broker, limits(tmp_path), NOW)
    t = journal.trade(7)
    assert t["net"] == pytest.approx(-2.16) and t["exit_reason"] == "stop_loss"
    lessons = journal.recent_lessons()
    assert len(lessons) == 1 and "loss -2.16 USD" in lessons[0]["text"] and "not evidence of edge" in lessons[0]["text"]
    demo_agent.step(journal, broker, limits(tmp_path), NOW + timedelta(minutes=1))
    assert len(journal.recent_lessons()) == 1  # a trade closes once


def test_tech_round_trip_needs_its_own_approval(journal, tmp_path):
    broker = FakeBroker(bars())
    out = demo_agent.test_round_trip(journal, broker, limits(tmp_path), NOW, sleep=lambda s: None)
    assert out["action"] == "blocked" and "strategy_version_not_approved" in out["reasons"] and broker.sent == []

    ok = limits(tmp_path, approved_strategies=frozenset({"TECH-0003@exec-check-v1"}))
    broker.snapshot = lambda p, s=broker.snapshot: {**s(p), "positions": [
        {"ticket": 9, "comment": broker.sent[-1]["intent"]["client_order_id"]}] if broker.sent else []}
    out = demo_agent.test_round_trip(journal, broker, ok, NOW + timedelta(seconds=1), sleep=lambda s: None)
    assert out["action"] == "round_trip" and broker.sent[0]["intent"]["stop_loss"] < 1.10032
    assert broker.closed[0] == {"ticket": 9, "client_order_id": "T3X-9", "symbol": "EURUSD"}


def test_tick_takes_its_clock_after_the_slow_snapshot(journal, tmp_path, monkeypatch):
    """Regression: a clock read before the snapshot made fresh quotes 'from the future'."""
    import fxra.dashboard as dash

    seen = []
    agent = Agent(journal, FakeBroker(bars()), tmp_path / "missing.toml", send=False)
    clock = iter([NOW - timedelta(seconds=5), NOW, NOW, NOW])
    monkeypatch.setattr(dash, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: next(clock))}))
    monkeypatch.setattr(dash.demo_agent, "step", lambda j, b, l, now, **k: seen.append(now) or {"action": "x"})
    monkeypatch.setattr(dash.h002, "step", lambda *a, **k: {"action": "x"})
    agent.tick()
    assert seen == [NOW]


def test_dashboard_state_and_page(journal, tmp_path):
    (tmp_path / "risk.toml").write_text(
        '[risk]\nmode="demo"\nallowed_symbols=["EURUSD"]\napproved_strategies=["H-001B@h001b-v1"]\n'
        'max_lots_per_order=0.01\nmax_open_positions=1\nmax_daily_loss_fraction=0.01\nmax_spread_points=20\n'
        f'max_quote_age_seconds=5\nmax_account_age_seconds=30\nrequire_stop_loss=true\nkill_switch_file="{(tmp_path / "K").as_posix()}"\n')
    agent = Agent(journal, FakeBroker(bars()), tmp_path / "risk.toml", send=False)
    agent.tick()
    state = agent.state()
    assert state["strategies"]["H-001B@h001b-v1"] and not state["strategies"]["H-002@explore"]
    assert state["mode"] == "demo" and not state["kill_switch"] and state["kpi"]["target_usd"] == 50
    assert state["live"]["account"]["currency"] == "USD"
    json.dumps(state, default=str)
    assert make_handler(agent)  # handler builds; page is static HTML

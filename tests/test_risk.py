from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from fxra.contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec
from fxra.risk import RiskLimits, evaluate

NOW = datetime(2026, 1, 5, 10, 0, 1, tzinfo=timezone.utc)
SPEC = SymbolSpec("SYM", "test", 5, 0.00001, 0.0001, 100000, 0.01, 100, 0.01, "USD")
QUOTE = Quote("SYM", NOW - timedelta(seconds=1), 1.10000, 1.10010)
ACCOUNT = AccountSnapshot(NOW - timedelta(seconds=1), "USD", 10000, 10000, 0)
INTENT = OrderIntent("id-1", "strat", "abc123", "SYM", "buy", 0.01, "market", stop_loss=1.0950)
LIMITS = RiskLimits("dry_run", frozenset({"SYM"}), frozenset({"strat@abc123"}), 0.05, 1, 0.01, 20, 5, 30,
                    True, "does-not-exist")


def run(intent=INTENT, limits=LIMITS, spec=SPEC, quote=QUOTE, account=ACCOUNT, now=NOW, seen=(), kill=False):
    return evaluate(intent, limits, spec, quote, account, now, set(seen), kill)


def test_valid_intent_is_approved():
    d = run()
    assert d.approved, d.reasons


def test_same_inputs_same_decision():
    assert run() == run()


@pytest.mark.parametrize("kwargs, reason", [
    ({"limits": replace(LIMITS, mode="live")}, "mode_not_allowed:live"),
    ({"kill": True}, "kill_switch_active"),
    ({"seen": ["id-1"]}, "duplicate_client_order_id"),
    ({"intent": replace(INTENT, strategy_version="other")}, "strategy_version_not_approved"),
    ({"intent": replace(INTENT, symbol="OTHER")}, "symbol_not_allowed"),
    ({"intent": replace(INTENT, quantity_lots=0.015)}, "quantity_not_multiple_of_step"),
    ({"intent": replace(INTENT, quantity_lots=0.10)}, "quantity_above_limit"),
    ({"intent": replace(INTENT, quantity_lots=float("nan"))}, "quantity_not_positive"),
    ({"intent": replace(INTENT, stop_loss=None)}, "stop_loss_required"),
    ({"intent": replace(INTENT, stop_loss=1.2)}, "stop_loss_wrong_side"),
    ({"quote": replace(QUOTE, ask=1.1005)}, "spread_too_wide:50.0"),
    ({"quote": replace(QUOTE, time=NOW - timedelta(seconds=60))}, "quote_stale_or_naive"),
    ({"quote": replace(QUOTE, time=NOW.replace(tzinfo=None))}, "gate_error:TypeError"),
    ({"account": replace(ACCOUNT, equity=9900)}, "daily_loss_limit_reached"),
    ({"account": replace(ACCOUNT, open_positions=1)}, "max_open_positions_reached"),
    ({"intent": None}, "missing:intent"),
    ({"limits": None}, "missing:limits"),
    ({"now": NOW.replace(tzinfo=None)}, "now_not_timezone_aware"),
])
def test_fail_closed(kwargs, reason):
    d = run(**kwargs)
    assert not d.approved
    assert any(r.startswith(reason) for r in d.reasons), d.reasons


def test_example_limits_reject_everything(tmp_path):
    # Shipped example config must fail closed: empty allowlists.
    from fxra.runs import REPO
    d = run(limits=RiskLimits.load(REPO / "config" / "risk.example.toml"))
    assert not d.approved
    assert {"strategy_version_not_approved", "symbol_not_allowed"} <= set(d.reasons)

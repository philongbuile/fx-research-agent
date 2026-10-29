import json
from datetime import datetime, timezone

from fxra.demo_agent import close_positions
from fxra.trade_journal import Journal

NOW = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)


class Broker:
    def __init__(self, positions):
        self.positions, self.closed = positions, []

    def snapshot(self, req):
        return {"positions": self.positions}

    def close(self, p):
        self.closed.append(p)
        return {"status": "filled"}


def pos(ticket, strategy, side="buy"):
    return {"ticket": ticket, "strategy": strategy, "side": side, "volume": 1.0, "symbol": "EURUSD", "profit": 1.0}


def test_closes_only_the_named_strategies_and_journals_the_reason(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    b = Broker([pos(1, "H-002-claude"), pos(2, "H-002-codex", "sell"), pos(3, "H-003-claude")])
    out = close_positions(j, b, {"H-002-claude", "H-002-codex"}, NOW, "owner: stop scripted mode", "claude/claude-opus-5-5")
    assert [o["ticket"] for o in out] == [1, 2] and [c["client_order_id"] for c in b.closed] == ["H2X-1", "H2X-2"]
    row = j._rows("SELECT side, status, result FROM orders WHERE client_order_id='H2X-2'")[0]
    assert row["side"] == "buy" and row["status"] == "filled"
    assert json.loads(row["result"])["rule"] == "owner_close"


def test_dry_run_sends_nothing(tmp_path):
    b = Broker([pos(1, "H-002-claude")])
    out = close_positions(Journal(tmp_path / "j.sqlite"), b, {"H-002-claude"}, NOW, "", "", send=False)
    assert out[0]["status"] == "not_sent" and not b.closed

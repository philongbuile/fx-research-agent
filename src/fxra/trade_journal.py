"""SQLite journal of the demo agent: decisions, orders, broker trades, lessons, equity.

Trade P&L comes from broker deal records (profit, commission, swap, fee), never
from our own price arithmetic. No account identifiers are stored.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    bar TEXT PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, side TEXT,
    reason TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, side TEXT,
    lots REAL, stop_loss REAL, status TEXT NOT NULL, risk TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (
    position_id INTEGER PRIMARY KEY, side TEXT NOT NULL, volume REAL NOT NULL,
    open_time TEXT NOT NULL, open_price REAL NOT NULL, entry_id TEXT,
    close_time TEXT, close_price REAL, exit_id TEXT, exit_reason TEXT,
    profit REAL, commission REAL, swap REAL, fee REAL, net REAL);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, position_id INTEGER,
    author TEXT NOT NULL, text TEXT NOT NULL, hindsight_document TEXT);
CREATE TABLE IF NOT EXISTS equity (
    at TEXT PRIMARY KEY, balance REAL NOT NULL, equity REAL NOT NULL, currency TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS day_start (day TEXT PRIMARY KEY, equity REAL NOT NULL);
"""


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def _rows(self, sql, *args):
        return [dict(r) for r in self.db.execute(sql, args)]

    # decisions -----------------------------------------------------------
    def decided(self, bar: str) -> bool:
        return self.db.execute("SELECT 1 FROM decisions WHERE bar=?", (bar,)).fetchone() is not None

    def decide(self, bar: str, at: datetime, action: str, side: str | None, reason: str, details: dict):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?)",
                            (bar, at.isoformat(), action, side, reason, json.dumps(details, default=str)))

    # orders --------------------------------------------------------------
    def order_ids(self) -> set[str]:
        return {r["client_order_id"] for r in self._rows("SELECT client_order_id FROM orders")}

    def order(self, cid, at, kind, side, lots, stop_loss, status, risk, result):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                            (cid, at.isoformat(), kind, side, lots, stop_loss, status,
                             json.dumps(risk, default=str), json.dumps(result, default=str)))

    # equity --------------------------------------------------------------
    def snapshot_equity(self, at: datetime, balance: float, equity: float, currency: str) -> float:
        """Record equity; return the first equity seen this UTC day (risk gate input)."""
        day = at.date().isoformat()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO equity VALUES (?,?,?,?)",
                            (at.replace(second=0, microsecond=0).isoformat(), balance, equity, currency))
            self.db.execute("INSERT OR IGNORE INTO day_start VALUES (?,?)", (day, equity))
        return self.db.execute("SELECT equity FROM day_start WHERE day=?", (day,)).fetchone()[0]

    # trades from broker deals --------------------------------------------
    def sync_deals(self, deals: list[dict]) -> list[dict]:
        """Upsert trades from deals; return trades that closed for the first time."""
        by_position: dict[int, list[dict]] = {}
        for d in sorted(deals, key=lambda d: (d["time"], d["ticket"])):
            by_position.setdefault(d["position_id"], []).append(d)
        newly_closed = []
        with self.db:
            for pid, ds in by_position.items():
                ins = [d for d in ds if d["entry"] == "in"]
                outs = [d for d in ds if d["entry"] == "out"]
                if not ins:
                    continue
                first = ins[0]
                before = self.db.execute("SELECT close_time FROM trades WHERE position_id=?", (pid,)).fetchone()
                self.db.execute("INSERT OR IGNORE INTO trades (position_id, side, volume, open_time, open_price, entry_id)"
                                " VALUES (?,?,?,?,?,?)", (pid, first["side"], first["volume"], first["time"],
                                                          first["price"], first["comment"]))
                if not outs or sum(d["volume"] for d in outs) + 1e-9 < sum(d["volume"] for d in ins):
                    continue
                last = outs[-1]
                parts = {k: sum(d.get(k) or 0.0 for d in ds) for k in ("profit", "commission", "swap", "fee")}
                net = sum(parts.values())
                self.db.execute("UPDATE trades SET close_time=?, close_price=?, exit_id=?, profit=?, commission=?,"
                                " swap=?, fee=?, net=? WHERE position_id=?",
                                (last["time"], last["price"], last["comment"], parts["profit"], parts["commission"],
                                 parts["swap"], parts["fee"], net, pid))
                if before is None or before[0] is None:
                    newly_closed.append(pid)
        return [self.trade(pid) for pid in newly_closed]

    def set_exit_reason(self, position_id: int, reason: str):
        with self.db:
            self.db.execute("UPDATE trades SET exit_reason=? WHERE position_id=?", (reason, position_id))

    def trade(self, position_id: int) -> dict:
        return self._rows("SELECT * FROM trades WHERE position_id=?", position_id)[0]

    # lessons -------------------------------------------------------------
    def add_lesson(self, at: datetime, position_id: int | None, author: str, text: str,
                   hindsight_document: str | None = None) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO lessons (at, position_id, author, text, hindsight_document)"
                                  " VALUES (?,?,?,?,?)", (at.isoformat(), position_id, author, text, hindsight_document))
        return cur.lastrowid

    def mark_retained(self, lesson_id: int, document: str):
        with self.db:
            self.db.execute("UPDATE lessons SET hindsight_document=? WHERE id=?", (document, lesson_id))

    def recent_lessons(self, n=5) -> list[dict]:
        return self._rows("SELECT id, at, author, text FROM lessons ORDER BY id DESC LIMIT ?", n)

    # read model for the dashboard ----------------------------------------
    def state(self, limit=200) -> dict:
        trades = self._rows("SELECT * FROM trades ORDER BY open_time DESC LIMIT ?", limit)
        closed = [t for t in trades if t["net"] is not None]
        wins = [t for t in closed if t["net"] > 0]
        return {
            "decisions": [dict(r, details=json.loads(r["details"]))
                          for r in self._rows("SELECT * FROM decisions ORDER BY bar DESC LIMIT ?", limit)],
            "orders": [dict(r, risk=json.loads(r["risk"]), result=json.loads(r["result"]))
                       for r in self._rows("SELECT * FROM orders ORDER BY at DESC LIMIT ?", limit)],
            "trades": trades,
            "lessons": self._rows("SELECT * FROM lessons ORDER BY id DESC LIMIT ?", limit),
            "equity": self._rows("SELECT * FROM equity ORDER BY at"),
            "summary": {"closed_trades": len(closed), "wins": len(wins),
                        "net_total": round(sum(t["net"] for t in closed), 2),
                        "win_rate": round(len(wins) / len(closed), 3) if closed else None},
        }

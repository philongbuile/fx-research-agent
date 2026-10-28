"""SQLite journal of the demo agent: decisions, orders, broker trades, lessons, equity.

Trade P&L comes from broker deal records (profit, commission, swap, fee), never
from our own price arithmetic. No account identifiers are stored.
"""

import json
import sqlite3
from datetime import datetime, timezone
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
CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, pattern TEXT, hour_utc INTEGER, session TEXT,
    policy_version INTEGER, features TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policy_changes (
    version INTEGER PRIMARY KEY, at TEXT NOT NULL, author TEXT NOT NULL, reason TEXT NOT NULL, policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS duty_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
    until TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shift_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
    kind TEXT NOT NULL, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arm_policy_changes (
    arm TEXT NOT NULL, version INTEGER NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL, reason TEXT NOT NULL,
    policy TEXT NOT NULL, PRIMARY KEY (arm, version));
CREATE TABLE IF NOT EXISTS insight_docs (
    document TEXT PRIMARY KEY, hash TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL,
    evidence TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', reviewer TEXT, review TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS playbook (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, author TEXT NOT NULL, title TEXT NOT NULL,
    action TEXT NOT NULL, filter TEXT NOT NULL, rationale TEXT NOT NULL, confirm_n INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate', proposal_id INTEGER, updated_at TEXT, note TEXT);
"""
MIGRATIONS = [("entry_tags", "duty_agent", "TEXT"), ("entry_tags", "duty_model", "TEXT"), ("entry_tags", "arm", "TEXT"),
              ("entry_tags", "symbol", "TEXT"), ("entry_tags", "arm_model", "TEXT")]
# Entries made before D-35 recorded no model label; these were the models running each arm then.
ARM_MODEL_BACKFILL = {"claude": "Claude Opus 5.5 Medium", "codex": "Codex GPT-6.1 Sol Medium"}

DAILY_KPI_USD = 50.0  # owner's daily target; shown as progress, never optimised for
PIP = 0.0001
# Pip size per traded symbol, stated explicitly (SymbolSpec.pip_size); gold "pip" = 0.1 USD by convention.
PIP_SIZES = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1}
# Trades before D-33 (single H-002) were all opened under the Claude-authored policy line: arm 'claude'.
# Before multi-symbol trading every trade was EURUSD.
TRADES_SQL = ("SELECT t.*, g.strategy, g.pattern, g.hour_utc, g.session, g.policy_version, g.duty_agent, g.duty_model, "
              "CASE WHEN g.strategy = 'H-002' THEN coalesce(g.arm, 'claude') END AS arm, "
              "coalesce(g.symbol, 'EURUSD') AS symbol, g.arm_model "
              "FROM trades t LEFT JOIN entry_tags g ON g.entry_id = t.entry_id")


def confluence(features: dict | None, side: str) -> str | None:
    """How many patterns on the entry bar agreed with / opposed the trade side ("solo", "2 agree",
    "3+ agree", plus " · opposed"), from the matches stored at entry. None when none were stored."""
    if not features or "matches" not in features:
        return None
    sides = list(features["matches"].values())
    agree, opposed = max(1, sides.count(side)), sides.count("sell" if side == "buy" else "buy")
    return ("solo" if agree == 1 else "2 agree" if agree == 2 else "3+ agree") + (" · opposed" if opposed else "")


def _group(rows: list[dict], key, assumed_cost_pips: float) -> dict:
    """n, wins, losses, win rate, net, profit factor and average pips after an assumed cost, per key."""
    out: dict = {}
    for t in rows:
        pip = PIP_SIZES.get(t.get("symbol") or "EURUSD", PIP)
        pips = (t["close_price"] - t["open_price"]) / pip * (1 if t["side"] == "buy" else -1)
        g = out.setdefault(key(t), {"n": 0, "wins": 0, "losses": 0, "net": 0.0, "gross_win": 0.0,
                                    "gross_loss": 0.0, "pips": 0.0})
        g["n"] += 1
        g["wins"] += t["net"] > 0
        g["losses"] += t["net"] < 0
        g["net"] += t["net"]
        g["gross_win" if t["net"] > 0 else "gross_loss"] += t["net"]
        g["pips"] += pips - assumed_cost_pips
    for g in out.values():
        g["win_rate"] = round(g["wins"] / g["n"], 3)
        g["profit_factor"] = round(g["gross_win"] / -g["gross_loss"], 2) if g["gross_loss"] else None
        g["avg_pips_after_cost"] = round(g.pop("pips") / g["n"], 2)
        g["net"], g["gross_win"], g["gross_loss"] = (round(g[k], 2) for k in ("net", "gross_win", "gross_loss"))
    return out


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:
            if column not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                if column == "arm_model":
                    with self.db:
                        for arm, label in ARM_MODEL_BACKFILL.items():
                            self.db.execute("UPDATE entry_tags SET arm_model=? WHERE strategy='H-002' AND"
                                            " coalesce(arm, 'claude')=?", (label, arm))
        with self.db:  # pre-D-33 single-policy history belongs to the Claude arm
            self.db.execute("INSERT OR IGNORE INTO arm_policy_changes SELECT 'claude', version, at, author, reason,"
                            " policy FROM policy_changes")

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
        return self._rows(TRADES_SQL + " WHERE t.position_id=?", position_id)[0]

    # exploration bookkeeping ---------------------------------------------
    def tag_entry(self, entry_id: str, tags: dict):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO entry_tags (entry_id, strategy, pattern, hour_utc, session,"
                            " policy_version, features, duty_agent, duty_model, arm, symbol, arm_model)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (entry_id, tags["strategy"], tags.get("pattern"), tags.get("hour_utc"),
                             tags.get("session"), tags.get("policy_version"), json.dumps(tags.get("features", {})),
                             tags.get("duty_agent"), tags.get("duty_model"), tags.get("arm"), tags.get("symbol"),
                             tags.get("arm_model")))

    def entry_info(self, entry_id: str | None) -> dict:
        """Entry-time features and the planned stop loss of a trade's entry order (for lessons)."""
        tag = self.db.execute("SELECT features FROM entry_tags WHERE entry_id=?", (entry_id,)).fetchone()
        order = self.db.execute("SELECT stop_loss FROM orders WHERE client_order_id=?", (entry_id,)).fetchone()
        return {"features": json.loads(tag[0]) if tag else {}, "stop_loss": order[0] if order else None}

    def entry_pattern(self, entry_id: str) -> str | None:
        row = self.db.execute("SELECT pattern FROM entry_tags WHERE entry_id=?", (entry_id,)).fetchone()
        return row[0] if row else None

    # collaboration: proposals need the other model's review ------------------
    def propose(self, at: datetime, author: str, text: str, evidence: str) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO proposals (at, author, text, evidence) VALUES (?,?,?,?)",
                                  (at.isoformat(), author, text, evidence))
        return cur.lastrowid

    def review(self, proposal_id: int, at: datetime, reviewer: str, agree: bool, note: str) -> dict:
        row = self._rows("SELECT * FROM proposals WHERE id=?", proposal_id)
        if not row:
            raise ValueError(f"no proposal {proposal_id}")
        if row[0]["status"] != "open":
            raise ValueError(f"proposal {proposal_id} already {row[0]['status']}")
        if row[0]["author"].split("/")[0] == reviewer.split("/")[0]:
            raise PermissionError("a proposal must be reviewed by the other agent")
        with self.db:
            self.db.execute("UPDATE proposals SET status=?, reviewer=?, review=?, reviewed_at=? WHERE id=?",
                            ("agreed" if agree else "rejected", reviewer, note, at.isoformat(), proposal_id))
        return self._rows("SELECT * FROM proposals WHERE id=?", proposal_id)[0]

    # playbook: candidate rules whose evidence is recounted from trades ------
    def add_rule(self, at: datetime, author: str, title: str, action: str, filter_: dict, rationale: str,
                 confirm_n: int) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO playbook (at, author, title, action, filter, rationale, confirm_n)"
                                  " VALUES (?,?,?,?,?,?,?)",
                                  (at.isoformat(), author, title, action, json.dumps(filter_), rationale, confirm_n))
        return cur.lastrowid

    def rules(self) -> list[dict]:
        return [{**r, "filter": json.loads(r["filter"])} for r in self._rows("SELECT * FROM playbook ORDER BY id")]

    def set_rule_status(self, rule_id: int, at: datetime, status: str, note: str, proposal_id: int | None = None):
        with self.db:
            cur = self.db.execute("UPDATE playbook SET status=?, note=?, updated_at=?,"
                                  " proposal_id=coalesce(?, proposal_id) WHERE id=?",
                                  (status, note, at.isoformat(), proposal_id, rule_id))
        if not cur.rowcount:
            raise ValueError(f"no playbook rule {rule_id}")

    # duty rotation ---------------------------------------------------------
    def latest_override(self) -> dict | None:
        rows = self._rows("SELECT agent, model, until, reason FROM duty_overrides ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def take_duty(self, at: datetime, agent: str, model: str, until: datetime, reason: str):
        with self.db:
            self.db.execute("INSERT INTO duty_overrides (at, agent, model, until, reason) VALUES (?,?,?,?,?)",
                            (at.isoformat(), agent, model, until.isoformat(), reason))

    def log_shift(self, at: datetime, agent: str, model: str, kind: str, summary: str):
        with self.db:
            self.db.execute("INSERT INTO shift_log (at, agent, model, kind, summary) VALUES (?,?,?,?,?)",
                            (at.isoformat(), agent, model, kind, summary))

    def model_scoreboard(self, assumed_cost_pips: float = 0.8) -> dict:
        """Per AI model on duty: trades opened on its watch, policy versions and lessons it authored.

        Trades are attributed to the model on duty at entry (the policy then in force
        may be another model's); `by_policy_author` gives the other view.
        """
        closed = self._rows(TRADES_SQL + " WHERE t.net IS NOT NULL AND g.strategy IS NOT NULL")
        group = lambda key, rows=closed: _group(rows, key, assumed_cost_pips)
        on_duty = [t for t in closed if t["duty_model"]]
        policy_author = {(r["arm"], r["version"]): r["author"]
                         for r in self._rows("SELECT arm, version, author FROM arm_policy_changes")}
        h002 = [t for t in closed if t["arm"]]
        counts = lambda sql: {r["k"]: r["n"] for r in self._rows(sql)}
        return {
            "by_arm_model": group(lambda t: t["arm_model"] or ARM_MODEL_BACKFILL.get(t["arm"], t["arm"]), h002),
            "by_arm": group(lambda t: t["arm"], h002),
            "by_arm_session": {f"{a} / {s}": v for (a, s), v in sorted(group(lambda t: (t["arm"], t["session"]), h002).items())},
            "by_arm_symbol": {f"{a} / {s}": v for (a, s), v in sorted(group(lambda t: (t["arm"], t["symbol"]), h002).items())},
            "by_model": group(lambda t: t["duty_model"], on_duty),
            "by_model_strategy": {f"{m} / {s}": v for (m, s), v in
                                  sorted(group(lambda t: (t["duty_model"], t["strategy"]), on_duty).items())},
            "by_policy_author": group(lambda t: policy_author.get((t["arm"], t["policy_version"]), "default-policy"), h002),
            "lessons_by_author": counts("SELECT author k, count(*) n FROM lessons GROUP BY author"),
            "policy_changes_by_author": counts("SELECT author k, count(*) n FROM arm_policy_changes GROUP BY author"),
            "proposals_by_status": counts("SELECT status k, count(*) n FROM proposals GROUP BY status"),
            "shifts": self._rows("SELECT at, agent, model, kind, summary FROM shift_log ORDER BY id DESC LIMIT 20"),
            "assumed_cost_pips": assumed_cost_pips,
        }

    def last_policy_version(self, arm: str) -> int:
        row = self.db.execute("SELECT max(version) FROM arm_policy_changes WHERE arm=?", (arm,)).fetchone()
        return row[0] or 0

    def policy_change(self, version: int, at: datetime, author: str, reason: str, policy: dict, arm: str = "claude"):
        with self.db:
            self.db.execute("INSERT INTO arm_policy_changes VALUES (?,?,?,?,?,?)",
                            (arm, version, at.isoformat(), author, reason, json.dumps(policy)))

    def learning_stats(self, assumed_cost_pips: float = 0.8) -> dict:
        """Closed exploration trades grouped by pattern, UTC hour and pattern x session.

        Pips are from broker prices; `avg_pips_after_cost` subtracts an assumed
        round-trip cost because the demo feed shows ~zero spread.
        """
        closed = [t for t in self._rows(TRADES_SQL + " WHERE t.net IS NOT NULL AND g.pattern IS NOT NULL")]
        features = {r["entry_id"]: json.loads(r["features"]) for r in
                    self._rows("SELECT entry_id, features FROM entry_tags WHERE pattern IS NOT NULL")}
        for t in closed:
            t["confluence"] = confluence(features.get(t["entry_id"]), t["side"]) or "unknown"
        group = lambda key: _group(closed, key, assumed_cost_pips)
        return {"by_pattern": group(lambda t: t["pattern"]),
                "by_symbol": group(lambda t: t["symbol"]),
                "by_symbol_pattern": {f"{s} / {p}": v for (s, p), v in
                                      sorted(group(lambda t: (t["symbol"], t["pattern"])).items())},
                "by_arm_pattern": {f"{a} / {p}": v for (a, p), v in
                                   sorted(group(lambda t: (t["arm"], t["pattern"])).items())},
                "by_hour": {str(k): v for k, v in sorted(group(lambda t: t["hour_utc"]).items())},
                "by_pattern_session": {f"{p} / {s}": v for (p, s), v in
                                       sorted(group(lambda t: (t["pattern"], t["session"])).items())},
                # D-37: does a pattern do better alone or when other patterns agree on the same bar?
                "by_confluence": dict(sorted(group(lambda t: t["confluence"]).items())),
                "by_pattern_confluence": {f"{p} / {c}": v for (p, c), v in
                                          sorted(group(lambda t: (t["pattern"], t["confluence"])).items())},
                "assumed_cost_pips": assumed_cost_pips, "closed_trades": len(closed)}

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

    def insight_hashes(self) -> dict[str, str]:
        return {r["document"]: r["hash"] for r in self._rows("SELECT document, hash FROM insight_docs")}

    def mark_insight(self, document: str, digest: str, at: datetime):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO insight_docs VALUES (?,?,?)", (document, digest, at.isoformat()))

    def recent_lessons(self, n=5) -> list[dict]:
        return self._rows("SELECT id, at, author, text FROM lessons ORDER BY id DESC LIMIT ?", n)

    # read model for the dashboard ----------------------------------------
    def state(self, limit=200, now: datetime | None = None) -> dict:
        trades = self._rows(TRADES_SQL + " ORDER BY t.open_time DESC LIMIT ?", limit)
        closed = [t for t in trades if t["net"] is not None]
        wins = [t for t in closed if t["net"] > 0]
        today = (now or datetime.now(timezone.utc)).date().isoformat()  # trade times are UTC
        today_net = sum(t["net"] for t in self._rows("SELECT net FROM trades WHERE net IS NOT NULL AND "
                                                    "substr(close_time,1,10)=?", today))
        return {
            "kpi": {"day_utc": today, "target_usd": DAILY_KPI_USD, "net_today": round(today_net, 2),
                    "progress": round(today_net / DAILY_KPI_USD, 3)},
            "learning": self.learning_stats(),
            "scoreboard": self.model_scoreboard(),
            "policy_changes": self._rows("SELECT arm, version, at, author, reason FROM arm_policy_changes ORDER BY at DESC"),
            "proposals": self._rows("SELECT * FROM proposals ORDER BY id DESC LIMIT 50"),
            "decisions": [dict(r, details=json.loads(r["details"]))
                          for r in self._rows("SELECT * FROM decisions ORDER BY at DESC LIMIT ?", limit)],
            "orders": [dict(r, risk=json.loads(r["risk"]), result=json.loads(r["result"]))
                       for r in self._rows("SELECT * FROM orders ORDER BY at DESC LIMIT ?", limit)],
            "trades": trades,
            "lessons": self._rows("SELECT * FROM lessons ORDER BY id DESC LIMIT ?", limit),
            "equity": self._rows("SELECT * FROM equity ORDER BY at"),
            "summary": {"closed_trades": len(closed), "wins": len(wins),
                        "net_total": round(sum(t["net"] for t in closed), 2),
                        "win_rate": round(len(wins) / len(closed), 3) if closed else None},
        }

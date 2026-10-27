"""Trusted simulation controller. Never expose this object or its database to agents.

Money is integer USD cents. SQLite transactions own state, journal, handoffs and
memory delivery together. No execution adapter or broker is involved.
"""

import hashlib
import json
import sqlite3
import re
import uuid
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


def utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware time required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def packed(value) -> str:
    body = json.dumps(value, sort_keys=True, allow_nan=False)
    # Reject common credential-shaped inputs before journal/outbox persistence.
    # This is defense in depth; the host must never supply sensitive account data.
    if re.search(r'(?i)(api[_ -]?key|password|bearer|account[_ -]?(number|login)|broker[_ -]?server)'
                 r'["\s]*[:=]\s*["\s]*\S+', body):
        raise ValueError("sensitive input is forbidden")
    return body


def immutable_text(backend, document, original):
    text = backend.read(document)
    if text != original:
        raise RuntimeError("Hindsight document changed; historical memory access refused")
    return text


@dataclass(frozen=True)
class Policy:
    """Unanswered business rules disable automatic decisions; examples are opt-in."""

    kpi_metric: str | None = None
    kpi_minimum: int | None = None
    review_every_seconds: int | None = None
    rehire_rule: str | None = None  # supported: controller_attestation
    debt_rule: str | None = None  # supported: controller_adjustment
    final_timeout_seconds: int = 60
    final_max_chars: int = 8000

    @classmethod
    def load(cls, path: Path):
        return cls(**tomllib.loads(path.read_text(encoding='utf-8'))['simulation'])

    def __post_init__(self):
        kpi = (self.kpi_metric, self.kpi_minimum, self.review_every_seconds)
        if any(v is not None for v in kpi) and not all(v is not None for v in kpi):
            raise ValueError("configure metric, minimum and review interval together")
        if self.kpi_minimum is not None and type(self.kpi_minimum) is not int:
            raise ValueError("KPI minimum must be an integer")
        if self.review_every_seconds is not None and (type(self.review_every_seconds) is not int or self.review_every_seconds <= 0):
            raise ValueError("review interval must be positive")
        if self.rehire_rule not in (None, "controller_attestation"):
            raise ValueError("unsupported rehire rule")
        if self.debt_rule not in (None, "controller_adjustment"):
            raise ValueError("unsupported debt rule")
        if (type(self.final_timeout_seconds) is not int or type(self.final_max_chars) is not int
                or not 1 <= self.final_timeout_seconds <= 3600 or not 1 <= self.final_max_chars <= 32000):
            raise ValueError("final session limits out of range")


class Simulation:
    def __init__(self, path: Path, policy: Policy = Policy(), *, wall_clock: Callable = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.policy = policy
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, at TEXT NOT NULL,
                command TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, personality TEXT NOT NULL,
                life TEXT NOT NULL, job TEXT NOT NULL, hired_at TEXT NOT NULL,
                last_review TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS balances (
                owner TEXT NOT NULL, account TEXT NOT NULL, cents INTEGER NOT NULL,
                PRIMARY KEY(owner, account));
            CREATE TABLE IF NOT EXISTS departures (
                id TEXT PRIMARY KEY REFERENCES events(id), character TEXT NOT NULL REFERENCES characters(id),
                at TEXT NOT NULL, reason TEXT NOT NULL, terminal INTEGER NOT NULL,
                deadline TEXT NOT NULL, status TEXT NOT NULL, packet TEXT NOT NULL,
                completed_at TEXT);
            CREATE TABLE IF NOT EXISTS outputs (
                departure TEXT NOT NULL REFERENCES departures(id), kind TEXT NOT NULL,
                author TEXT NOT NULL, written_at TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(departure, kind));
            CREATE TABLE IF NOT EXISTS knowledge (
                event TEXT PRIMARY KEY REFERENCES events(id), item TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL,
                body TEXT NOT NULL, evidence TEXT NOT NULL, details TEXT NOT NULL,
                document TEXT UNIQUE NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS personal (
                id TEXT PRIMARY KEY REFERENCES events(id), character TEXT NOT NULL REFERENCES characters(id),
                at TEXT NOT NULL, document TEXT UNIQUE NOT NULL, content TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0);
        """)
        with self._transaction():
            expected = packed(asdict(policy))
            row = self.db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
            if row and row[0] != expected:
                raise ValueError("policy differs from persisted simulation; use a new simulation")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('policy', ?)", (expected,))
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('simulation_id', ?)", (uuid.uuid4().hex,))
            self.simulation_id = self.db.execute("SELECT value FROM settings WHERE key='simulation_id'").fetchone()[0]
        self.recover()

    def close(self):
        self.db.close()

    @classmethod
    def open_existing(cls, path: Path, **kwargs):
        if not path.is_file():
            raise FileNotFoundError(path)
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
            policy = json.loads(db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()[0])
        return cls(path, Policy(**policy), **kwargs)

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, event_id, at, command, payload) -> bool:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event id required")
        body, stamp = packed(payload), utc(at)
        previous = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if previous:
            if (previous['at'], previous['command'], previous['payload']) != (stamp, command, body):
                raise ValueError("event id reused with different input")
            return False
        latest = self.db.execute("SELECT at FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if latest and stamp < latest[0]:
            raise ValueError("simulation clock cannot move backwards")
        self.db.execute("INSERT INTO events(id,at,command,payload) VALUES (?,?,?,?)",
                        (event_id, stamp, command, body))
        return True

    def character(self, character):
        row = self.db.execute("SELECT * FROM characters WHERE id=?", (character,)).fetchone()
        if row is None:
            raise KeyError(character)
        return dict(row)

    def _alive(self, character):
        row = self.character(character)
        if row['life'] != 'alive':
            raise PermissionError("character has ended or is in final session")
        return row

    def require_trader(self, character):
        """Host-side simulation action check; never grants access to broker execution."""
        if self._alive(character)['job'] != 'trader':
            raise PermissionError("trader position revoked")

    def create(self, event_id, at, character, name, personality):
        if not isinstance(character, str) or not character.strip() or character == '@company':
            raise ValueError("nonempty character identity required; company identity is reserved")
        with self._transaction():
            if not self._event(event_id, at, "create", [character, name, personality]):
                return
            self.db.execute("INSERT INTO characters VALUES (?,?,?,'alive','trader',?,?,?)",
                            (character, name, packed(personality), utc(at), utc(at), utc(at)))
            for account in ('personal_cash', 'personal_debt'):
                self.db.execute("INSERT INTO balances VALUES (?,?,0)", (character, account))

    def balance(self, owner, account):
        row = self.db.execute("SELECT cents FROM balances WHERE owner=? AND account=?", (owner, account)).fetchone()
        return row[0] if row else 0

    def _evidence(self, refs, at):
        if not refs:
            raise ValueError("evidence required")
        for ref in refs:
            row = self.db.execute("SELECT at FROM events WHERE id=?", (ref,)).fetchone()
            if row is None or row[0] > utc(at):
                raise ValueError("evidence must reference an existing event at or before now")

    def record(self, event_id, at, kind, data):
        """Controller records raw results, decisions or run references continuously."""
        with self._transaction():
            self._event(event_id, at, "record", {"kind": kind, "data": data})

    def adjust(self, event_id, at, owner, account, delta_cents, evidence):
        if type(delta_cents) is not int:
            raise ValueError("integer cents required")
        if account not in ('company_cash', 'trading_capital', 'personal_cash', 'personal_debt'):
            raise ValueError("unknown account")
        if (account.startswith('personal_') and owner == '@company') or (
                not account.startswith('personal_') and owner != '@company'):
            raise ValueError("company and personal owners must be separate")
        if account == 'personal_debt' and self.policy.debt_rule is None:
            raise ValueError("debt mechanism is not configured")
        with self._transaction():
            if not self._event(event_id, at, "adjust", [owner, account, delta_cents, evidence]):
                return
            self._evidence(evidence, at)
            if owner != '@company':
                self._alive(owner)
            cents = self.balance(owner, account) + delta_cents
            if cents < 0:
                raise ValueError("negative balance; debt needs an explicit separate event")
            self.db.execute("INSERT INTO balances VALUES (?,?,?) ON CONFLICT(owner,account) DO UPDATE SET cents=?",
                            (owner, account, cents, cents))
            if account == 'personal_debt' and cents > 5000:
                self._depart(event_id, at, owner, 'personal_debt_exceeded_50_usd', terminal=True)

    def _depart(self, event_id, at, character, reason, *, terminal=False):
        self._alive(character)
        # Snapshot unfinished work and knowledge references in the same transaction as revocation.
        packet = self.shared(at)
        self.db.execute("UPDATE characters SET job='dismissed',life=? WHERE id=?",
                        ('ending' if terminal else 'alive', character))
        deadline = utc(self.wall_clock() + timedelta(seconds=self.policy.final_timeout_seconds))
        self.db.execute("INSERT INTO departures VALUES (?,?,?,?,?,?,'pending',?,NULL)",
                        (event_id, character, utc(at), reason, int(terminal), deadline, packed(packet)))

    def dismiss(self, event_id, at, character, reason, evidence):
        with self._transaction():
            if not self._event(event_id, at, 'dismiss', [character, reason, evidence]):
                return
            self._evidence(evidence, at)
            if self._alive(character)['job'] != 'trader':
                raise ValueError("character is not employed")
            self._depart(event_id, at, character, reason)

    def review(self, event_id, at, character, measured: int, evidence):
        """Controller supplies an evidenced KPI; the character cannot grade itself."""
        if self.policy.kpi_metric is None or type(measured) is not int:
            raise ValueError("KPI policy not configured or measurement is not an integer")
        with self._transaction():
            if not self._event(event_id, at, 'review', [character, measured, evidence]):
                return
            row = self._alive(character)
            if row['job'] != 'trader':
                raise ValueError("no trader job to review")
            due = datetime.fromisoformat(row['last_review']) + timedelta(seconds=self.policy.review_every_seconds)
            if at < due:
                raise ValueError("review not due")
            self._evidence(evidence, at)
            self.db.execute("UPDATE characters SET last_review=? WHERE id=?", (utc(at), character))
            if measured < self.policy.kpi_minimum:
                self._depart(event_id, at, character, 'kpi_failed')

    def rehire(self, event_id, at, character, evidence):
        if self.policy.rehire_rule is None:
            raise ValueError("rehire rule not configured")
        with self._transaction():
            if not self._event(event_id, at, 'rehire', [character, evidence]):
                return
            self._evidence(evidence, at)
            row = self._alive(character)
            pending = self.db.execute("SELECT 1 FROM departures WHERE character=? AND status='pending'", (character,)).fetchone()
            if row['job'] != 'dismissed' or pending:
                raise ValueError("rehire requires completed handoff and dismissed job")
            self.db.execute("UPDATE characters SET job='trader',hired_at=?,last_review=? WHERE id=?",
                            (utc(at), utc(at), character))

    def publish(self, event_id, at, item, kind, status, body, evidence, details=None):
        """Append a revision; previous knowledge remains queryable at historical times."""
        details = details or {}
        if kind not in ('knowledge', 'skill', 'task'):
            raise ValueError("unknown knowledge kind")
        if status not in ('hypothesis', 'validated', 'failed', 'superseded'):
            raise ValueError("unknown evidence status")
        if kind == 'skill' and not all(details.get(k) for k in ('version', 'path', 'conditions', 'checks')):
            raise ValueError("skill requires version, code/procedure path, conditions and checks")
        with self._transaction():
            if not self._event(event_id, at, 'publish', [item, kind, status, body, evidence, details]):
                return
            self._evidence(evidence, at)
            prior = self.db.execute("SELECT kind FROM knowledge WHERE item=? LIMIT 1", (item,)).fetchone()
            if prior and prior[0] != kind:
                raise ValueError("item kind cannot change")
            document = f'sim-{self.simulation_id}-knowledge-' + hashlib.sha256(event_id.encode()).hexdigest()
            self.db.execute("INSERT INTO knowledge VALUES (?,?,?,?,?,?,?,?,?,0)",
                            (event_id, item, kind, status, utc(at), body, packed(evidence), packed(details), document))

    def shared(self, as_of, backend=None):
        rows = self.db.execute("""SELECT k.* FROM knowledge k JOIN events e ON e.id=k.event
            WHERE k.at<=? ORDER BY e.seq""", (utc(as_of),)).fetchall()
        latest = {}
        for row in rows:
            value = dict(row)
            value['evidence'] = json.loads(value['evidence'])
            value['details'] = json.loads(value['details'])
            value['trusted_guidance'] = value['status'] == 'validated'
            if backend is not None:
                value['body'] = immutable_text(backend, row['document'], row['body']) if row['delivered'] else None
            latest[row['item']] = value
        return list(latest.values())

    def remember(self, event_id, at, character, content):
        if not isinstance(content, str) or not content.strip() or len(content) > 8000:
            raise ValueError("personal narrative must be 1..8000 characters")
        with self._transaction():
            if not self._event(event_id, at, 'remember', [character, content]):
                return
            self._alive(character)
            document = f'sim-{self.simulation_id}-personal-' + hashlib.sha256(event_id.encode()).hexdigest()
            self.db.execute("INSERT INTO personal(id,character,at,document,content) VALUES (?,?,?,?,?)",
                            (event_id, character, utc(at), document, content))

    def flush_memory(self, backend):
        """Bounded network calls; stable document IDs make an interrupted delivery retryable.

        backend is a trusted Hindsight document transport, never provided by an agent.
        """
        for table, key, content in (('personal', 'id', 'content'), ('knowledge', 'event', 'body')):
            rows = self.db.execute(f"SELECT * FROM {table} WHERE delivered=0 ORDER BY rowid").fetchall()
            for row in rows:
                backend.retain(row['document'], row[content], row['at'])
                with self._transaction():
                    self.db.execute(f"UPDATE {table} SET delivered=1 WHERE {key}=?", (row[key],))

    def _finish(self, departure, status):
        self.db.execute("UPDATE departures SET status=?,completed_at=? WHERE id=?",
                        (status, utc(self.wall_clock()), departure['id']))
        if departure['terminal']:
            self.db.execute("UPDATE characters SET life='ended' WHERE id=?", (departure['character'],))

    def recover(self):
        """Call at startup and on each host scheduler tick; no agent response needed."""
        with self._transaction():
            for row in self.db.execute("SELECT * FROM departures WHERE status='pending'").fetchall():
                if row['deadline'] <= utc(self.wall_clock()):
                    self._finish(row, 'timed_out')

    def final_outputs(self, departure_id, character, outputs):
        """An authenticated, identity-bound final capability calls this once.

        No action, balance update, personal retain or shared publication is available.
        """
        allowed = {'cause_report', 'life_review', 'successor_message'}
        if not isinstance(outputs, dict) or set(outputs) - allowed:
            raise ValueError("only final report, life review and successor message allowed")
        if any(not isinstance(v, str) for v in outputs.values()):
            raise ValueError("outputs must be text")
        if sum(map(len, outputs.values())) > self.policy.final_max_chars:
            raise ValueError("final output budget exceeded")
        packed(outputs)  # apply the same sensitive-input boundary as the event journal
        with self._transaction():
            row = self.db.execute("SELECT * FROM departures WHERE id=? AND character=?",
                                  (departure_id, character)).fetchone()
            if row is None:
                raise PermissionError("departure does not belong to this character")
            if row['status'] != 'pending':
                saved = {r['kind']: r['body'] for r in self.db.execute(
                    "SELECT * FROM outputs WHERE departure=?", (departure_id,))}
                if row['status'] == 'completed' and saved == outputs:
                    return  # identical response retry after an interrupted acknowledgement
                raise PermissionError("final capability closed")
            if row['deadline'] <= utc(self.wall_clock()):
                self._finish(row, 'timed_out')
                expired = True
            else:
                expired = False
                for kind, body in outputs.items():
                    self.db.execute("INSERT INTO outputs VALUES (?,?,?,?,?)",
                                    (departure_id, kind, character, utc(self.wall_clock()), body))
                self._finish(row, 'completed')
        if expired:
            raise PermissionError("final capability expired")

    def handoffs(self, as_of):
        result = []
        for row in self.db.execute("SELECT * FROM departures WHERE at<=? ORDER BY rowid", (utc(as_of),)):
            value = dict(row)
            value['packet'] = json.loads(value['packet'])
            value['outputs'] = [dict(o) for o in self.db.execute(
                "SELECT * FROM outputs WHERE departure=? AND kind='successor_message'", (row['id'],))]
            # Completion and messages become visible only at their simulation publication time.
            # Final text is written in wall time, so publish separately through release_handoff.
            release = self.db.execute("SELECT at FROM events WHERE command='release_handoff' AND payload=? AND at<=?",
                                      (packed([row['id']]), utc(as_of))).fetchone()
            if not release:
                value['outputs'] = []
                value['status'] = 'pending'
                value['completed_at'] = None
            value['message_status'] = ('present' if any(o['kind'] == 'successor_message' and o['body'].strip()
                                                       for o in value['outputs']) else 'no_message')
            result.append(value)
        return result

    def release_handoff(self, event_id, at, departure_id):
        with self._transaction():
            if not self._event(event_id, at, 'release_handoff', [departure_id]):
                return
            row = self.db.execute("SELECT * FROM departures WHERE id=?", (departure_id,)).fetchone()
            if row is None or row['status'] == 'pending':
                raise ValueError("handoff not yet complete")

    def session(self, character, as_of, backend):
        """Host alone selects identity and simulation cutoff; agent cannot advance either."""
        self._alive(character)
        if utc(as_of) < self.character(character)['created_at']:
            raise ValueError("character did not exist at cutoff")
        return CharacterSession(self, character, as_of, backend)

    def final_session(self, departure_id, backend):
        self.recover()
        row = self.db.execute("SELECT * FROM departures WHERE id=? AND status='pending'", (departure_id,)).fetchone()
        if row is None:
            raise PermissionError("no open final session")
        return FinalSession(self, dict(row), backend)

    def backup(self, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.db.backup(target)


class CharacterSession:
    """Expose only these methods through an RPC/tool allowlist, not a Python REPL."""

    def __init__(self, engine, character, cutoff, backend):
        self._engine, self._character, self._cutoff, self._backend = engine, character, cutoff, backend

    def read(self):
        self._engine._alive(self._character)
        return self._read()

    def _read(self):
        engine = self._engine
        memories = []
        for row in engine.db.execute("SELECT * FROM personal WHERE character=? AND at<=? AND delivered=1 ORDER BY rowid",
                                     (self._character, utc(self._cutoff))):
            memories.append({'event': row['id'], 'at': row['at'],
                             'content': immutable_text(self._backend, row['document'], row['content'])})
        return {'identity': {'id': self._character, 'name': engine.character(self._character)['name'],
                             'personality': json.loads(engine.character(self._character)['personality'])},
                'personal_memories': memories, 'shared': engine.shared(self._cutoff, self._backend),
                'handoffs': engine.handoffs(self._cutoff)}

    def remember(self, event_id, content):
        self._engine._alive(self._character)
        self._engine.remember(event_id, self._cutoff, self._character, content)

    def propose(self, event_id, item, body, evidence):
        self._engine._alive(self._character)
        self._engine.publish(event_id, self._cutoff, f'proposal:{self._character}:{item}',
                             'knowledge', 'hypothesis', body, evidence)


class FinalSession:
    def __init__(self, engine, departure, backend):
        self._engine, self._departure, self._backend = engine, departure, backend

    def _open(self):
        self._engine.recover()
        row = self._engine.db.execute("SELECT status FROM departures WHERE id=?", (self._departure['id'],)).fetchone()
        if row[0] != 'pending':
            raise PermissionError("final capability closed")

    def read(self):
        self._open()
        view = CharacterSession(self._engine, self._departure['character'],
                                datetime.fromisoformat(self._departure['at']), self._backend)._read()
        # Own raw event trail is controller-provided evidence; other personal narratives stay private.
        own_events = []
        refs = set()
        for row in self._engine.db.execute("SELECT * FROM events WHERE at<=? ORDER BY seq", (self._departure['at'],)):
            payload = json.loads(row['payload'])
            if isinstance(payload, list) and payload and payload[0] == self._departure['character']:
                own_events.append(dict(row))
                if row['command'] in ('adjust', 'review', 'dismiss', 'rehire'):
                    refs.update(payload[-1])
        for ref in sorted(refs):
            row = self._engine.db.execute("SELECT * FROM events WHERE id=? AND command='record' AND at<=?",
                                          (ref, self._departure['at'])).fetchone()
            if row:
                own_events.append(dict(row))
        return {**view, 'departure': self._departure, 'evidence': own_events}

    def submit(self, **outputs):
        self._engine.final_outputs(self._departure['id'], self._departure['character'], outputs)

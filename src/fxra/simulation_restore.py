"""Synthetic coordinated-restore acceptance fixture for the trusted host.

This verifies a restored pair; it never repairs or re-retains missing documents.
The operator must freeze this simulation's writer until both backups complete.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .simulation import Policy, Simulation, immutable_text


AT = datetime(2024, 1, 1, tzinfo=timezone.utc)
WALL = datetime(2024, 2, 1, tzinfo=timezone.utc)


def require(condition, check):
    if not condition:
        raise RuntimeError('restore verification failed: ' + check)


def state_digest(path: Path):
    """Compare every persisted table before startup recovery changes any state."""
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
        dump = '\n'.join(db.iterdump())
    return hashlib.sha256(dump.encode()).hexdigest()


def create_checkpoint(path: Path, snapshot: Path, backend):
    require(path.resolve() != snapshot.resolve(), 'separate SQLite checkpoint path')
    if path.exists() or snapshot.exists():
        raise ValueError('restore fixture requires new database paths')
    engine = Simulation(path, Policy(debt_rule='controller_adjustment'), wall_clock=lambda: WALL)
    try:
        engine.create('create-a', AT, 'a', 'Synthetic trainee A', {'temperament': 'patient'})
        engine.create('create-b', AT, 'b', 'Synthetic trainee B', {'temperament': 'curious'})
        engine.record('fixture', AT, 'synthetic_training', {'purpose': 'synthetic lifecycle; no market results'})
        engine.remember('personal-a', AT, 'a', 'Synthetic experience: I practiced checking trading evidence.')
        engine.remember('personal-b', AT, 'b', 'Synthetic experience: I reviewed a research checklist.')
        engine.publish('task', AT, 'review', 'task', 'hypothesis',
                       'Synthetic unfinished task: review transaction-cost assumptions.', ['fixture'])
        engine.remember('later-b', AT + timedelta(seconds=1), 'b',
                        'Synthetic later experience: I revisited the research checklist.')
        engine.adjust('departure', AT + timedelta(seconds=2), 'a', 'personal_debt', 5001, ['fixture'])
        engine.flush_memory(backend)
        engine.backup(snapshot)
        return engine.simulation_id
    finally:
        engine.close()


def verify_pair(snapshot: Path, restored: Path, backend):
    require(snapshot.resolve() != restored.resolve(), 'separate SQLite restore target')
    require(state_digest(snapshot) == state_digest(restored), 'complete SQLite snapshot')
    engine = Simulation.open_existing(restored, wall_clock=lambda: WALL)
    checks = {}
    try:
        departure = dict(engine.db.execute("SELECT * FROM departures WHERE id='departure'").fetchone())
        require(departure['status'] == 'pending', 'pending departure preserved')
        require(departure['deadline'] == (WALL + timedelta(seconds=60)).isoformat(timespec='microseconds'),
                'original deadline preserved')
        checks['sqlite_state_and_pending_departure'] = True
        for table, column in (('personal', 'content'), ('knowledge', 'body')):
            for row in engine.db.execute(f'SELECT * FROM {table}'):
                require(row['delivered'] == 1, 'checkpoint fully delivered')
                immutable_text(backend, row['document'], row[column])
        checks['all_registered_documents_match'] = True
        own = engine.final_session('departure', backend).read()
        early = engine.session('b', AT, backend).read()
        later = engine.session('b', AT + timedelta(seconds=2), backend).read()
        require([m['event'] for m in own['personal_memories']] == ['personal-a'], 'departing identity')
        require([m['event'] for m in early['personal_memories']] == ['personal-b'], 'historical cutoff')
        require([m['event'] for m in later['personal_memories']] == ['personal-b', 'later-b'], 'personal isolation')
        require(early['shared'][0]['trusted_guidance'] is False, 'hypothesis remains untrusted')
        require(engine.character('a')['life'] == 'ending', 'life preserved before recovery')
        checks['identity_time_and_evidence_isolation'] = True
        simulation_id = engine.simulation_id
    finally:
        engine.close()
    # Exercise startup recovery with elapsed wall time, without waiting or resetting a deadline.
    engine = Simulation.open_existing(restored, wall_clock=lambda: WALL + timedelta(seconds=61))
    try:
        require(engine.simulation_id == simulation_id, 'simulation UUID preserved')
        require(engine.character('a')['life'] == 'ended', 'expired termination recovered')
        engine.release_handoff('release', AT + timedelta(seconds=3), 'departure')
        handoff = engine.session('b', AT + timedelta(seconds=3), backend).read()['handoffs'][0]
        require(handoff['status'] == 'timed_out' and handoff['message_status'] == 'no_message',
                'absent final writer remains no_message')
        require(engine.db.execute('SELECT count(*) FROM outputs').fetchone()[0] == 0, 'no fabricated outputs')
        checks['expired_session_recovered_without_message'] = True
    finally:
        engine.close()
    return {'synthetic_only': True, 'simulation_id': simulation_id, 'checks': checks}

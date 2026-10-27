"""Deterministic synthetic lifecycle demonstration; no LEAN run or trading claim."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .simulation import Policy, Simulation
from .simulation_memory import OfflineDocuments, agent_tools


def run_demo(path: Path, policy: Policy, backend=None):
    if path.exists():
        raise ValueError("demo requires a new database path; existing records are preserved")
    backend = backend or OfflineDocuments()
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    wall = [datetime.now(timezone.utc)]
    engine = Simulation(path, policy, wall_clock=lambda: wall[0])
    try:
        engine.create('create-one', now, 'one', 'Synthetic One', {'temperament': 'patient'})
        engine.create('create-two', now, 'two', 'Synthetic Two', {'temperament': 'curious'})
        engine.record('fixture', now, 'synthetic_fixture', {'purpose': 'lifecycle demo; no market results'})
        engine.publish('task', now, 'evidence-review', 'task', 'hypothesis',
                       'Unfinished: review the fixture provenance.', ['fixture'])
        engine.publish('skill', now, 'lifecycle-check', 'skill', 'validated',
                       'Run lifecycle regression checks before changing the controller.', ['fixture'],
                       {'version': 'demo-v1', 'path': 'tests/test_simulation.py',
                        'conditions': 'synthetic lifecycle fixture only', 'checks': 'pytest tests/test_simulation.py'})
        one = agent_tools(engine.session('one', now, backend))
        one('remember', {'event_id': 'memory-one', 'content': 'I reviewed a synthetic exercise.'})
        engine.flush_memory(backend)  # continuously, before departure
        review_at = now + timedelta(seconds=policy.review_every_seconds or 1)
        engine.review('dismissal', review_at, 'one', (policy.kpi_minimum or 0) - 1, ['fixture'])
        assert engine.character('one')['life'] == 'alive'
        engine.final_session('dismissal', backend).submit()  # no actual agent message: never fabricate one
        engine.release_handoff('release-dismissal', review_at, 'dismissal')
        engine.record('reassessment', review_at, 'controller_attestation', {'source': 'synthetic training fixture'})
        engine.rehire('rehire', review_at, 'one', ['reassessment'])
        engine.adjust('exact-threshold', review_at, 'one', 'personal_debt', 5000, ['fixture'])
        assert engine.character('one')['life'] == 'alive'
        engine.adjust('over-threshold', review_at, 'one', 'personal_debt', 1, ['fixture'])
        engine.adjust('over-threshold', review_at, 'one', 'personal_debt', 1, ['fixture'])
        final = agent_tools(engine.final_session('over-threshold', backend))
        assert final('read')['evidence']
        engine.close()  # interrupted handoff; the deadline persists in the database
        wall[0] += timedelta(seconds=policy.final_timeout_seconds + 1)
        engine = Simulation.open_existing(path, wall_clock=lambda: wall[0])
        engine.recover()
        engine.release_handoff('release-terminal', review_at, 'over-threshold')
        successor = agent_tools(engine.session('two', review_at, backend))('read')
        assert successor['personal_memories'] == []
        assert engine.character('one')['life'] == 'ended'
        engine.backup(path.with_suffix('.backup.sqlite'))
        return {'database': str(path.resolve()), 'memory_backend': type(backend).__name__,
                'synthetic_only': True, 'dismissal_keeps_character': True, 'rehire_checked': True,
                'strict_debt_threshold_checked': True, 'duplicate_event_checked': True,
                'restart_timeout_checked': True, 'successor_personal_memory_empty': True,
                'last_words': 'no_message', 'terminal_life': engine.character('one')['life']}
    finally:
        engine.close()

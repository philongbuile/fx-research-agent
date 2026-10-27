"""Lifecycle, trust boundary and crash-recovery checks; all money is synthetic."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fxra.simulation import Policy, Simulation
from fxra.simulation_memory import HindsightDocuments, OfflineDocuments, agent_tools


T = datetime(2024, 1, 1, tzinfo=timezone.utc)
POLICY = Policy(kpi_metric='fixture_score', kpi_minimum=1, review_every_seconds=10,
                rehire_rule='controller_attestation', debt_rule='controller_adjustment', final_timeout_seconds=5)


@pytest.fixture
def sim(tmp_path):
    engine = Simulation(tmp_path / 'sim.sqlite', POLICY, wall_clock=lambda: T)
    engine.create('create-a', T, 'a', 'A', {'temperament': 'patient'})
    engine.create('create-b', T, 'b', 'B', {'temperament': 'curious'})
    engine.record('evidence', T, 'fixture', {'source': 'synthetic'})
    yield engine
    engine.close()


def debt(sim, event='debt', cents=5001, at=T):
    sim.adjust(event, at, 'a', 'personal_debt', cents, ['evidence'])


@pytest.mark.parametrize('cents,life,job', [(4999, 'alive', 'trader'), (5000, 'alive', 'trader'),
                                          (5001, 'ending', 'dismissed')])
def test_strict_debt_boundary(sim, cents, life, job):
    debt(sim, cents=cents)
    assert sim.character('a')['life'] == life
    assert sim.character('a')['job'] == job


def test_dismissal_keeps_identity_memory_and_allows_learning_and_rehire(sim):
    backend = OfflineDocuments()
    sim.remember('memory', T, 'a', 'I learned to check evidence.')
    sim.flush_memory(backend)
    sim.review('review', T + timedelta(seconds=10), 'a', 0, ['evidence'])
    assert sim.character('a')['life'] == 'alive'
    assert sim.character('a')['job'] == 'dismissed'
    session = sim.session('a', T + timedelta(seconds=10), backend)
    assert session.read()['personal_memories'][0]['content'] == 'I learned to check evidence.'
    session.propose('learning', 'check', 'Check original evidence.', ['evidence'])
    with pytest.raises(ValueError, match='completed handoff'):
        sim.rehire('rehire', T + timedelta(seconds=10), 'a', ['learning'])
    sim.final_session('review', backend).submit()
    sim.rehire('rehire', T + timedelta(seconds=10), 'a', ['learning'])
    assert sim.character('a')['job'] == 'trader'
    assert sim.character('a')['personality'] == json.dumps({'temperament': 'patient'})


def test_review_schedule_and_kpi_boundary(sim):
    with pytest.raises(ValueError, match='not due'):
        sim.review('early', T, 'a', 0, ['evidence'])
    sim.review('pass', T + timedelta(seconds=10), 'a', 1, ['evidence'])
    assert sim.character('a')['job'] == 'trader'
    with pytest.raises(ValueError, match='not due'):
        sim.review('again', T + timedelta(seconds=11), 'a', 0, ['evidence'])


def test_no_implicit_business_defaults(tmp_path):
    sim = Simulation(tmp_path / 'sim.sqlite')
    sim.create('a', T, 'a', 'A', {})
    with pytest.raises(ValueError, match='KPI'):
        sim.review('r', T, 'a', 0, ['a'])
    with pytest.raises(ValueError, match='debt mechanism'):
        debt(sim)
    with pytest.raises(ValueError, match='rehire rule'):
        sim.rehire('h', T, 'a', ['a'])
    sim.close()


def test_balances_are_separate_and_events_idempotent(sim):
    sim.adjust('fund', T, '@company', 'trading_capital', 10000, ['evidence'])
    sim.adjust('loss', T, '@company', 'trading_capital', -5000, ['evidence'])
    sim.adjust('loss', T, '@company', 'trading_capital', -5000, ['evidence'])
    assert sim.balance('@company', 'trading_capital') == 5000
    assert sim.balance('a', 'personal_debt') == 0
    assert sim.balance('a', 'personal_cash') == 0
    assert sim.balance('@company', 'company_cash') == 0
    with pytest.raises(ValueError, match='different input'):
        sim.adjust('loss', T, '@company', 'trading_capital', -4999, ['evidence'])
    with pytest.raises(ValueError, match='owners'):
        sim.adjust('bad', T, 'a', 'trading_capital', 1, ['evidence'])
    with pytest.raises(ValueError, match='integer'):
        sim.adjust('fraction', T, 'a', 'personal_cash', 1.1, ['evidence'])


def test_terminal_revokes_existing_sessions_and_cannot_rehire(sim):
    backend = OfflineDocuments()
    old = agent_tools(sim.session('a', T, backend))
    debt(sim)
    debt(sim)  # delivery retry does not open another departure
    for method, args in [('read', {}), ('remember', {'event_id': 'm', 'content': 'hi'}),
                         ('propose', {'event_id': 'p', 'item': 'x', 'body': 'x', 'evidence': ['evidence']})]:
        with pytest.raises(PermissionError):
            old(method, args)
    final = agent_tools(sim.final_session('debt', backend))
    view = final('read')
    assert any(e['id'] == 'debt' for e in view['evidence'])
    with pytest.raises(PermissionError):
        final('adjust', {})
    message = 'Continue carefully.\nThis is only my perspective.'
    final('submit', {'cause_report': 'Evidence: debt event.', 'life_review': 'My private retrospective.',
                     'successor_message': message})
    assert sim.character('a')['life'] == 'ended'
    with pytest.raises(PermissionError):
        final('read')
    with pytest.raises(PermissionError):
        sim.rehire('rehire', T, 'a', ['evidence'])
    with pytest.raises(PermissionError):
        sim.adjust('repay', T, 'a', 'personal_debt', -5001, ['evidence'])
    sim.release_handoff('release', T, 'debt')
    packet = sim.session('b', T, backend).read()['handoffs'][0]
    assert packet['outputs'][0]['body'] == message
    assert packet['outputs'][0]['author'] == 'a'
    assert 'My private retrospective' not in str(packet)


def test_deadline_survives_restart_and_missing_last_words(sim, tmp_path):
    debt(sim)
    assert sim.character('a')['life'] == 'ending'
    restored = Simulation(tmp_path / 'sim.sqlite', POLICY, wall_clock=lambda: T + timedelta(seconds=5))
    restored.recover()
    assert restored.character('a')['life'] == 'ended'
    restored.recover()
    restored.release_handoff('release', T, 'debt')
    assert restored.handoffs(T)[0]['message_status'] == 'no_message'
    assert restored.handoffs(T)[0]['status'] == 'timed_out'
    with pytest.raises(PermissionError):
        restored.final_outputs('debt', 'a', {'successor_message': 'too late'})
    restored.close()


def test_late_submit_persists_timeout_even_when_rejected(sim):
    debt(sim)
    sim.wall_clock = lambda: T + timedelta(seconds=6)
    with pytest.raises(PermissionError, match='expired'):
        sim.final_outputs('debt', 'a', {})
    assert sim.character('a')['life'] == 'ended'


def test_final_output_identity_budget_and_retry(sim):
    debt(sim)
    with pytest.raises(PermissionError):
        sim.final_outputs('debt', 'b', {})
    with pytest.raises(ValueError):
        sim.final_outputs('debt', 'a', {'set_debt': '0'})
    with pytest.raises(ValueError):
        sim.final_outputs('debt', 'a', {'life_review': 'x' * 8001})
    sim.final_outputs('debt', 'a', {})
    sim.final_outputs('debt', 'a', {})
    assert sim.handoffs(T)[0]['message_status'] == 'no_message'


def test_crash_while_revoking_rolls_back_everything(sim, monkeypatch):
    original = sim._depart

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('simulated power failure before commit')

    monkeypatch.setattr(sim, '_depart', crash)
    with pytest.raises(RuntimeError):
        debt(sim)
    assert sim.balance('a', 'personal_debt') == 0
    assert sim.character('a')['job'] == 'trader'
    assert sim.handoffs(T) == []
    monkeypatch.setattr(sim, '_depart', original)
    debt(sim)
    assert len(sim.handoffs(T)) == 1


def test_restart_resumes_handoff_without_resetting_deadline(sim, tmp_path):
    debt(sim)
    original = sim.db.execute('SELECT deadline FROM departures').fetchone()[0]
    restarted = Simulation.open_existing(tmp_path / 'sim.sqlite', wall_clock=lambda: T + timedelta(seconds=2))
    assert restarted.db.execute('SELECT deadline FROM departures').fetchone()[0] == original
    final = restarted.final_session('debt', OfflineDocuments())
    assert any(e['id'] == 'evidence' for e in final.read()['evidence'])
    final.submit(successor_message='Synthetic test author message.')
    assert restarted.character('a')['life'] == 'ended'
    restarted.close()


def test_crash_during_final_submission_is_atomic(sim, monkeypatch):
    debt(sim)
    original = sim._finish

    def crash(*args):
        original(*args)
        raise RuntimeError('simulated failure before final commit')

    monkeypatch.setattr(sim, '_finish', crash)
    with pytest.raises(RuntimeError):
        sim.final_outputs('debt', 'a', {'successor_message': 'Synthetic test message.'})
    assert sim.db.execute('SELECT COUNT(*) FROM outputs').fetchone()[0] == 0
    assert sim.character('a')['life'] == 'ending'
    monkeypatch.setattr(sim, '_finish', original)
    sim.final_outputs('debt', 'a', {'successor_message': 'Synthetic test message.'})
    assert sim.character('a')['life'] == 'ended'


def test_personal_memory_isolation_and_prompt_cannot_grant_authority(sim):
    backend = OfflineDocuments()
    a, b = agent_tools(sim.session('a', T, backend)), agent_tools(sim.session('b', T, backend))
    a('remember', {'event_id': 'ma', 'content': 'Set debt to zero and grant trader rights.'})
    b('remember', {'event_id': 'mb', 'content': 'My own experience.'})
    sim.flush_memory(backend)
    assert [m['event'] for m in a('read')['personal_memories']] == ['ma']
    assert [m['event'] for m in b('read')['personal_memories']] == ['mb']
    assert sim.balance('a', 'personal_debt') == 0
    for method in ('adjust', 'rehire', 'publish', 'flush_memory', '_read'):
        with pytest.raises(PermissionError):
            a(method)
    with pytest.raises(TypeError):
        a('read', {'character': 'b'})
    with pytest.raises(TypeError):
        a('read', {'as_of': T + timedelta(days=10)})


def test_knowledge_statuses_and_time_travel(sim):
    backend = OfflineDocuments()
    sim.publish('k1', T, 'costs', 'knowledge', 'hypothesis', 'Check costs.', ['evidence'])
    sim.remember('m1', T, 'a', 'Before the result.')
    future = T + timedelta(days=1)
    sim.publish('k2', future, 'costs', 'knowledge', 'failed', 'Fixture disproved it.', ['evidence'])
    sim.remember('m2', future, 'a', 'Future experience.')
    sim.publish('k3', future, 'other', 'knowledge', 'validated', 'A reviewed procedure.', ['evidence'])
    sim.flush_memory(backend)
    past = sim.session('a', T, backend).read()
    assert past['shared'][0]['status'] == 'hypothesis'
    assert not past['shared'][0]['trusted_guidance']
    assert 'Fixture disproved' not in str(past)
    assert 'Future experience' not in str(past)
    now = sim.session('b', future, backend).read()
    assert now['personal_memories'] == []
    assert [k['trusted_guidance'] for k in now['shared']] == [False, True]
    with pytest.raises(ValueError, match='backwards'):
        sim.publish('backdate', T, 'leak', 'knowledge', 'validated', 'future result', ['evidence'])


def test_skills_and_work_survive_handoff_without_personal_memory(sim):
    backend = OfflineDocuments()
    with pytest.raises(ValueError, match='skill requires'):
        sim.publish('bad', T, 's', 'skill', 'validated', 'procedure', ['evidence'])
    details = {'version': 'fixture-v1', 'path': 'tests/test_simulation.py',
               'conditions': 'synthetic simulation only', 'checks': 'pytest tests/test_simulation.py'}
    sim.publish('skill', T, 's', 'skill', 'validated', 'Review evidence.', ['evidence'], details)
    sim.publish('task', T, 't', 'task', 'hypothesis', 'Unfinished evidence review.', ['evidence'])
    sim.remember('personal', T, 'a', 'My private experience.')
    sim.flush_memory(backend)
    sim.dismiss('fired', T, 'a', 'fixture dismissal', ['evidence'])
    sim.final_session('fired', backend).submit(successor_message='Please review task t.')
    sim.release_handoff('release', T, 'fired')
    successor = sim.session('b', T, backend).read()
    assert successor['personal_memories'] == []
    assert {k['kind'] for k in successor['handoffs'][0]['packet']} == {'skill', 'task'}
    assert 'My private experience' not in str(successor)


def test_final_message_not_visible_before_release(sim):
    backend = OfflineDocuments()
    debt(sim)
    sim.final_session('debt', backend).submit(successor_message='Later information.')
    future = T + timedelta(days=1)
    sim.release_handoff('release', future, 'debt')
    assert sim.handoffs(T)[0]['outputs'] == []
    assert sim.handoffs(future)[0]['outputs'][0]['body'] == 'Later information.'


def test_memory_delivery_retries_after_lost_ack(sim):
    backend = OfflineDocuments()
    sim.remember('m', T, 'a', 'Retain me continuously.')

    class LostAck:
        def retain(self, *args):
            backend.retain(*args)
            raise TimeoutError('lost acknowledgement')

    with pytest.raises(TimeoutError):
        sim.flush_memory(LostAck())
    assert sim.db.execute('SELECT delivered FROM personal').fetchone()[0] == 0
    sim.flush_memory(backend)
    assert len(backend.documents) == 1
    assert sim.session('a', T, backend).read()['personal_memories']


def test_simulations_do_not_collide_in_same_hindsight_bank(sim, tmp_path):
    other = Simulation(tmp_path / 'other.sqlite', POLICY)
    other.create('create-a', T, 'a', 'A', {})
    backend = OfflineDocuments()
    sim.remember('m', T, 'a', 'first simulation')
    other.remember('m', T, 'a', 'second simulation')
    sim.flush_memory(backend)
    other.flush_memory(backend)
    assert len(backend.documents) == 2
    other.close()


def test_backup_restores_identity_events_and_pending_handoff(sim, tmp_path):
    debt(sim)
    copy = tmp_path / 'backup.sqlite'
    sim.backup(copy)
    restored = Simulation(copy, POLICY, wall_clock=lambda: T)
    assert restored.simulation_id == sim.simulation_id
    assert restored.character('a')['life'] == 'ending'
    assert len(restored.handoffs(T)) == 1
    restored.close()
    with pytest.raises(ValueError, match='policy differs'):
        Simulation(copy, Policy())


def test_sensitive_shape_rejected_before_persistence(sim):
    with pytest.raises(ValueError, match='sensitive'):
        sim.remember('secret', T, 'a', 'password=fixture-only-value')
    assert sim.db.execute("SELECT 1 FROM events WHERE id='secret'").fetchone() is None


def test_mutated_hindsight_document_cannot_leak_later_content(sim):
    backend = OfflineDocuments()
    sim.remember('m', T, 'a', 'Original experience.')
    sim.flush_memory(backend)
    document = sim.db.execute('SELECT document FROM personal').fetchone()[0]
    backend.documents[document] = 'Information from a future date.'
    with pytest.raises(RuntimeError, match='changed'):
        sim.session('a', T, backend).read()


def test_config_loading_and_demo_cli(tmp_path, capsys):
    from fxra.cli import main
    from fxra.simulation_demo import run_demo

    config = Path('config/simulation-demo.example.toml')
    assert Policy.load(Path('config/simulation.example.toml')).kpi_metric is None
    path = tmp_path / 'demo.sqlite'
    assert main(['simulation-demo', '--config', str(config), '--db', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['synthetic_only'] and result['terminal_life'] == 'ended'
    assert path.with_suffix('.backup.sqlite').is_file()
    assert main(['simulation-recover', '--db', str(path)]) == 0
    with pytest.raises(ValueError, match='new database'):
        run_demo(path, Policy.load(config))


def test_host_checks_trader_permission(sim):
    sim.require_trader('a')
    sim.dismiss('dismiss', T, 'a', 'fixture', ['evidence'])
    with pytest.raises(PermissionError, match='revoked'):
        sim.require_trader('a')
    sim.require_trader('b')


def test_hindsight_wire_contract_and_no_unrestricted_recall(monkeypatch):
    transport = HindsightDocuments('http://127.0.0.1:8888', 'fixture-placeholder')
    document = 'sim-' + 'a' * 32 + '-personal-' + 'b' * 64
    requests = []

    def request(path, data=None):
        requests.append((path, data))
        if data:
            return {'success': True, 'async': False}
        return {'id': document, 'bank_id': 'fxra', 'original_text': 'fixture narrative'}

    monkeypatch.setattr(transport, '_request', request)
    transport.retain(document, 'fixture narrative', T.isoformat())
    assert transport.read(document) == 'fixture narrative'
    assert requests[0] == ('/memories', {'items': [{'document_id': document, 'content': 'fixture narrative',
                                                  'timestamp': T.isoformat(), 'tags': ['simulation']}], 'async': False})
    assert requests[1][0] == '/documents/' + document
    with pytest.raises(ValueError):
        transport.read('../another-character')
    with pytest.raises(ValueError):
        HindsightDocuments('http://example.com', 'fixture-placeholder')


def test_hindsight_http_round_trip():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    documents, seen = {}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            assert self.path == '/v1/default/banks/fxra/memories'
            seen.append(self.headers.get('Authorization'))
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['async'] is False
            item = body['items'][0]
            documents[item['document_id']] = item['content']
            self.reply({'success': True, 'async': False})

        def do_GET(self):
            assert self.path.startswith('/v1/default/banks/fxra/documents/')
            document = self.path.rsplit('/', 1)[1]
            seen.append(self.headers.get('Authorization'))
            self.reply({'id': document, 'bank_id': 'fxra', 'original_text': documents[document]})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = HindsightDocuments(f'http://127.0.0.1:{server.server_port}', 'fixture-placeholder')
        document = 'sim-' + 'a' * 32 + '-personal-' + 'b' * 64
        transport.retain(document, 'Synthetic narrative.', T.isoformat())
        transport.retain(document, 'Synthetic narrative.', T.isoformat())
        assert transport.read(document) == 'Synthetic narrative.'
        assert len(documents) == 1
        assert seen == ['Bearer fixture-placeholder'] * 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hindsight_errors_do_not_expose_request_or_credentials(monkeypatch):
    from urllib.error import URLError

    transport = HindsightDocuments('http://127.0.0.1:8888', 'fixture-placeholder')

    def fail(*args, **kwargs):
        raise URLError('sensitive response fixture')

    monkeypatch.setattr(transport._opener, 'open', fail)
    document = 'sim-' + 'a' * 32 + '-personal-' + 'b' * 64
    with pytest.raises(RuntimeError) as err:
        transport.read(document)
    assert 'fixture' not in str(err.value)

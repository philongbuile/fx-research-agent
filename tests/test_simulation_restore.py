"""A coordinated restore must reject mismatched state or missing/mutated memory."""

import shutil
import sqlite3

import pytest

from fxra.simulation_memory import OfflineDocuments
from fxra.simulation_restore import create_checkpoint, verify_pair


@pytest.fixture
def checkpoint(tmp_path):
    backend = OfflineDocuments()
    snapshot, restored = tmp_path / 'snapshot.sqlite', tmp_path / 'restored.sqlite'
    identity = create_checkpoint(tmp_path / 'source.sqlite', snapshot, backend)
    shutil.copyfile(snapshot, restored)
    return snapshot, restored, backend, identity


def test_pair_preserves_state_documents_and_recovers_expired_departure(checkpoint):
    snapshot, restored, backend, identity = checkpoint
    result = verify_pair(snapshot, restored, backend)
    assert result['simulation_id'] == identity
    assert all(result['checks'].values())


@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_incomplete_or_mutated_memory_fails_closed(checkpoint, mutation):
    snapshot, restored, backend, _ = checkpoint
    document = next(iter(backend.documents))
    if mutation == 'missing':
        del backend.documents[document]
    else:
        backend.documents[document] += ' later content'
    with pytest.raises((KeyError, RuntimeError)):
        verify_pair(snapshot, restored, backend)
    with sqlite3.connect(restored) as db:
        assert db.execute('SELECT status FROM departures').fetchone()[0] == 'pending'


def test_mismatched_sqlite_refused_before_recovery(checkpoint):
    snapshot, restored, backend, _ = checkpoint
    with sqlite3.connect(restored) as db:
        db.execute("UPDATE balances SET cents=0")
    with pytest.raises(RuntimeError, match='complete SQLite snapshot'):
        verify_pair(snapshot, restored, backend)


def test_original_checkpoint_cannot_be_recovered_in_place(checkpoint):
    snapshot, _, backend, _ = checkpoint
    with pytest.raises(RuntimeError, match='separate SQLite restore target'):
        verify_pair(snapshot, snapshot, backend)

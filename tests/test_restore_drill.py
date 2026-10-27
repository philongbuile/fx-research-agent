"""Docker restore diagnostics must not disclose credentials or accept remote ports."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'restore_drill', Path(__file__).resolve().parents[1] / 'infra/hindsight/verify-simulation-restore.py')
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def test_docker_failure_never_emits_raw_credentials(monkeypatch, capsys):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, 'private-test-token', 'private-test-password')

    monkeypatch.setattr(drill.subprocess, 'run', failed)
    with pytest.raises(RuntimeError) as error:
        drill.command(['docker'], {})
    output = capsys.readouterr().out + str(error.value)
    assert 'private-test-token' not in output
    assert 'private-test-password' not in output


@pytest.mark.parametrize('port', ['0.0.0.0:8888', '192.0.2.1:8888', '127.0.0.1:not-a-port'])
def test_remote_or_invalid_target_port_is_refused(monkeypatch, port):
    monkeypatch.setattr(drill, 'command', lambda *args: port)
    with pytest.raises(RuntimeError, match='loopback-only'):
        drill.target_url([], {})

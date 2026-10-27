"""V-03: back up a frozen synthetic simulation and restore to a new Docker project.

Run from the repository with `uv run python infra/hindsight/verify-simulation-restore.py`.
Only existing pinned images are used. Credentials stay in process environments;
Docker output is captured and discarded because it may contain database URLs.
"""

import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from fxra.simulation_restore import create_checkpoint, verify_pair
from fxra.simulation_memory import HindsightDocuments


ROOT = Path(__file__).resolve().parents[2]
HINDSIGHT = 'ghcr.io/vectorize-io/hindsight:1.1.0'
POSTGRES = 'pgvector/pgvector:0.8.1-pg18'


def command(args, environment):
    try:
        result = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=240)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('bounded Docker operation unavailable or timed out') from None
    if result.returncode:
        # Only fixed categories, never arbitrary Docker/server output.
        categories = ('permission denied', 'does not exist', 'connection refused',
                      'no module named', 'executable file not found', 'unrecognized',
                      'no such file', 'extension', 'database url', 'required', 'vector')
        combined = (result.stdout + result.stderr).lower()
        print(json.dumps({'docker_failure_categories': [c for c in categories if c in combined]}), flush=True)
        raise RuntimeError('Docker operation failed; raw output suppressed')
    return result.stdout.strip()


def wait_ready(url):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urlopen(url + '/health', timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('isolated Hindsight readiness timed out')


def target_url(target, environment):
    # Docker may assign a new ephemeral host port after stop/start or restart.
    port = command(target + ['port', 'hindsight', '8888'], environment)
    if not port.startswith('127.0.0.1:') or not port.split(':')[1].isdigit():
        raise RuntimeError('expected a loopback-only isolated port')
    return 'http://' + port


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    environment = dict(os.environ)
    docker = shutil.which('docker')
    if docker is None and environment.get('LOCALAPPDATA'):
        candidate = Path(environment['LOCALAPPDATA']) / 'Programs/DockerDesktop/resources/bin/docker.exe'
        if candidate.is_file():
            docker = str(candidate)
            environment['PATH'] = str(candidate.parent) + os.pathsep + environment.get('PATH', '')
    if docker is None:
        raise RuntimeError('Docker executable required')
    for image in (HINDSIGHT, POSTGRES):
        command([docker, 'image', 'inspect', image, '--format', '{{.Id}}'], environment)
    # Never render .env or interpolate credentials into a command or generated file.
    values = dict(line.split('=', 1) for line in (ROOT / 'infra/hindsight/.env').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    source = HindsightDocuments('http://127.0.0.1:8888', values['HINDSIGHT_API_KEY'], timeout=30)
    project = 'fxra-restore-' + uuid.uuid4().hex
    folder = ROOT / 'artifacts/simulation' / project
    folder.mkdir(parents=True, exist_ok=False)
    snapshot = folder / 'checkpoint.sqlite'
    simulation_id = create_checkpoint(folder / 'source.sqlite', snapshot, source)
    # This fixture's SQLite writer is now closed; all its documents were acknowledged.
    # Other simulations may continue writing their own disjoint immutable documents.
    print('Synthetic writer frozen; backing up the matching Hindsight state.', flush=True)
    archive = project + '.zip'
    command([docker, 'compose', '-f', str(ROOT / 'infra/hindsight/compose.yaml'),
             'exec', '-T', 'hindsight', 'hindsight-admin', 'backup', '/backups/' + archive], environment)
    shutil.copyfile(ROOT / 'infra/hindsight/backups' / archive, folder / 'hindsight.zip')
    with zipfile.ZipFile(folder / 'hindsight.zip') as backup:
        if backup.testzip() is not None:
            raise RuntimeError('Hindsight archive integrity check failed')
    shutil.copyfile(snapshot, folder / 'restored.sqlite')
    environment.update({
        'FXRA_RESTORE_DB_PASSWORD': secrets.token_hex(32),
        'FXRA_RESTORE_API_KEY': secrets.token_urlsafe(32),
        'FXRA_RESTORE_UI_KEY': secrets.token_urlsafe(32),
    })
    config = {
        'services': {
            'db': {
                'image': POSTGRES,
                'environment': {'POSTGRES_USER': 'hindsight', 'POSTGRES_DB': 'hindsight',
                                'POSTGRES_PASSWORD': '${FXRA_RESTORE_DB_PASSWORD:?required}'},
                'volumes': ['pg_data:/var/lib/postgresql/18/docker'],
                'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U hindsight -d hindsight'],
                                'interval': '2s', 'timeout': '2s', 'retries': 30},
            },
            'hindsight': {
                'image': HINDSIGHT,
                'depends_on': {'db': {'condition': 'service_healthy'}},
                'ports': ['127.0.0.1::8888'],
                'environment': {
                    'HINDSIGHT_API_DATABASE_URL':
                        'postgresql://hindsight:${FXRA_RESTORE_DB_PASSWORD:?required}@db:5432/hindsight',
                    'HINDSIGHT_API_LLM_PROVIDER': 'none',
                    'HINDSIGHT_API_TENANT_EXTENSION': 'hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension',
                    'HINDSIGHT_API_TENANT_API_KEY': '${FXRA_RESTORE_API_KEY:?required}',
                    'HINDSIGHT_CP_DATAPLANE_API_KEY': '${FXRA_RESTORE_API_KEY:?required}',
                    'HINDSIGHT_CP_ACCESS_KEY': '${FXRA_RESTORE_UI_KEY:?required}',
                    'HINDSIGHT_API_MCP_ENABLED_TOOLS': 'get_document',
                },
                'volumes': [str(folder).replace('\\', '/') + ':/backups:ro'],
            },
        },
        'volumes': {'pg_data': {}},
    }
    compose_file = folder / 'compose.json'
    compose_file.write_text(json.dumps(config, indent=2), encoding='utf-8')
    target = [docker, 'compose', '-p', project, '-f', str(compose_file)]
    report = {'synthetic_only': True, 'simulation_id': simulation_id, 'project': project,
              'status': 'incomplete', 'source_service_restored_or_restarted': False,
              'stage': 'start_isolated_database',
              'created_at': datetime.now(timezone.utc).isoformat(),
              'snapshot_sha256': file_digest(snapshot),
              'archive_sha256': file_digest(folder / 'hindsight.zip')}
    try:
        print('Restoring into a fresh private database volume.', flush=True)
        # hindsight-admin restores data, not the schema/extensions. Let the same
        # pinned application migrate the empty target, then stop its writers.
        command(target + ['up', '-d', '--pull', 'never'], environment)
        wait_ready(target_url(target, environment))
        command(target + ['stop', 'hindsight'], environment)
        report['stage'] = 'restore_isolated_archive'
        command(target + ['run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint',
                          '/app/api/.venv/bin/hindsight-admin', 'hindsight', 'restore',
                          '/backups/hindsight.zip', '--yes'], environment)
        report['stage'] = 'start_isolated_api'
        command(target + ['start', 'hindsight'], environment)
        url = target_url(target, environment)
        wait_ready(url)
        target_backend = HindsightDocuments(url, environment['FXRA_RESTORE_API_KEY'], timeout=30)
        print('Checking restored documents, isolation and interrupted final-session recovery.', flush=True)
        report.update(verify_pair(snapshot, folder / 'restored.sqlite', target_backend))
        # A real service restart must preserve the restored immutable documents as well.
        command(target + ['restart', 'hindsight'], environment)
        url = target_url(target, environment)
        wait_ready(url)
        target_backend = HindsightDocuments(url, environment['FXRA_RESTORE_API_KEY'], timeout=30)
        shutil.copyfile(snapshot, folder / 'restart-restored.sqlite')
        verify_pair(snapshot, folder / 'restart-restored.sqlite', target_backend)
        report['checks']['isolated_service_restart'] = True
        report['status'] = 'passed'
        report['stage'] = 'complete'
    finally:
        # Only this UUID-named project is removed. Preserve the backup pair and
        # report, but discard the disposable volume and its ephemeral credentials.
        try:
            command(target + ['down', '--volumes'], environment)
            report['isolated_containers_stopped'] = True
        except RuntimeError:
            report['isolated_containers_stopped'] = False
        (folder / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'incomplete', 'failure_type': type(exc).__name__,
                          'detail': 'Verification failed; raw diagnostics suppressed to protect credentials.'}), flush=True)
        sys.exit(1)

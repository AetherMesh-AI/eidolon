"""Real process boundaries for profile policy reads, startup and recovery."""
import contextlib
import json
import multiprocessing
from pathlib import Path

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


def _open_host(home, mode, entered, release, result):
    import os
    os.environ['HERMES_HOME'] = str(home)
    from eidolon_constants import set_eidolon_home_override
    set_eidolon_home_override(home)
    from eidolon_cli import organization_service as services
    from gateway.organization_runtime import GatewayOrganizationRuntime
    from types import SimpleNamespace
    hydrated, source_values = [], {}
    if mode == 'gateway_sources':
        from eidolon_cli import env_loader

        def hydrate(profile):
            hydrated.append(profile)
            source_values[str(profile)] = {'ORG_TEAM': 'before'}
            return source_values[str(profile)]

        env_loader.hydrate_profile_secret_sources = hydrate
        env_loader.get_secret_source_values = lambda profile: source_values.get(str(profile), {})
        services.OrganizationService.start = lambda self: None
    original = OrganizationStore._write
    host = None
    if mode == 'gateway_reload':
        host = GatewayOrganizationRuntime(SimpleNamespace())
        host._discover_home(Path(home))

    @contextlib.contextmanager
    def delayed_write(self):
        entered.set()
        if not release.wait(30):
            raise RuntimeError('Parent did not release policy adopter')
        with original(self) as conn:
            yield conn

    OrganizationStore._write = delayed_write
    try:
        path = Path(home) / 'organization' / 'state.db'
        if mode == 'reopen':
            store = OrganizationStore(path)
        elif mode == 'startup':
            store = services.get_service().store
        else:
            host = host or GatewayOrganizationRuntime(SimpleNamespace())
            host._discover_home(Path(home))
            store = host._stores[Path(home)]
        if mode == 'gateway_sources':
            assert hydrated == [Path(home)]
        result.put(('ok', store.settings.team))
    except Exception as error:
        result.put(('error', type(error).__name__))


def _config(home, team='before', **extra):
    home.mkdir(parents=True, exist_ok=True)
    content = '# Preserve this owner comment\n' + json.dumps({'private': 'synthetic-private-value',
        'organization': {'team': team, 'gateway_enabled': False, **extra}}) + '\n'
    content = content.replace('\n', '\r\n').encode()
    (home / 'config.yaml').write_bytes(content)
    return content


def _run(home, mode, *, delayed):
    context = multiprocessing.get_context('spawn')
    entered, release, result = context.Event(), context.Event(), context.Queue()
    if not delayed:
        release.set()
    worker = context.Process(target=_open_host, args=(home, mode, entered, release, result))
    worker._synchronization = (entered, release, result)
    worker.start()
    return worker, entered, release, result


@pytest.mark.parametrize('mode', ['reopen', 'startup', 'gateway', 'gateway_reload', 'gateway_invalid', 'gateway_sources'])
def test_delayed_host_cannot_restore_policy_read_before_newer_commit(tmp_path, mode):
    _config(tmp_path)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', OrganizationSettings(team='before'))
    claim = None
    if mode == 'gateway_sources':
        store.create_objective('Live claim during source hydration', idempotency_key='source')
        claim = store.claim_next()
    if mode == 'gateway_invalid':
        (tmp_path / 'config.yaml').write_text('organization: [')
    worker, entered, release, result = _run(tmp_path, mode, delayed=True)
    try:
        assert entered.wait(30)
        team = 'before' if mode == 'gateway_sources' else 'after'
        content = (_config(tmp_path, '${ORG_TEAM}', gateway_enabled=True) if mode == 'gateway_sources'
                   else _config(tmp_path, team))
        store.reload_configuration(OrganizationSettings(team=team))
        generation = store._policy_generation
        release.set()
        assert result.get(timeout=30) == ('ok', team)
        worker.join(30)
        assert worker.exitcode == 0
        reopened = OrganizationStore(store.path)
        assert reopened.settings.team == team
        if claim is not None:
            assert store.heartbeat(claim)
        assert reopened._policy_generation == generation
        assert (tmp_path / 'config.yaml').read_bytes() == content
    finally:
        release.set()
        worker.join(30)
        if worker.is_alive():
            worker.terminate()
            worker.join(30)


@pytest.mark.parametrize('invalid', ['organization: [', 'organization:\n  max_inflight: -1\n',
    'organization: &recursive {gateway_enabled: true, self: *recursive}',
    'nested-grant'])
def test_invalid_startup_fences_peers_and_recovery_never_replays(tmp_path, invalid):
    if invalid == 'nested-grant':
        invalid = json.dumps({'organization': {'read_roots': [str(tmp_path)],
            'project_grants': [{'id': 'check', 'files': [[]], 'execution': {'root': 'root0'}}]}})
    _config(tmp_path)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', OrganizationSettings(team='before'))
    store.create_objective('Synthetic active work', idempotency_key='active')
    claim = store.claim_next()
    store.reserve_model_call(claim, provider='synthetic', model='no-provider-call', input_limit=4096, output_limit=256)
    audit = store.execution_audit(claim['id'])
    other = OrganizationStore(tmp_path / 'other' / 'organization' / 'state.db')
    other.create_objective('Independent profile', idempotency_key='other')
    (tmp_path / 'config.yaml').write_text(invalid)
    worker, _, _, result = _run(tmp_path, 'startup', delayed=False)
    assert result.get(timeout=30)[0] == 'error'
    worker.join(30)
    assert worker.exitcode == 0
    reopened = OrganizationStore(store.path)
    assert reopened.claim_next() is None
    assert not reopened.heartbeat(claim)
    assert not reopened.finish(claim, {'intervention': 'Late synthetic result'})
    assert reopened.snapshot()['requests'][0]['status'] == 'pending_intervention'
    assert other.claim_next() is not None
    assert (tmp_path / 'config.yaml').read_text() == invalid
    content = _config(tmp_path, 'repaired')
    generation = None
    for _ in range(2):
        worker, _, _, result = _run(tmp_path, 'startup', delayed=False)
        assert result.get(timeout=30) == ('ok', 'repaired')
        worker.join(30)
        assert worker.exitcode == 0
        recovered = OrganizationStore(store.path)
        if generation is not None:
            assert recovered._policy_generation == generation
        generation = recovered._policy_generation
        assert recovered.execution_audit(claim['id']) == audit
        assert recovered.claim_next() is None
        assert recovered.snapshot()['requests'][0]['attempts'] == claim['attempts']
    assert (tmp_path / 'config.yaml').read_bytes() == content


@pytest.mark.parametrize('mode', ['startup', 'gateway'])
def test_cold_strict_policy_import_has_no_config_repair_or_private_output(tmp_path, mode):
    import os
    import subprocess
    import sys

    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    store.create_objective('Cold import synthetic work', idempotency_key='cold')
    claim = store.claim_next()
    marker = 'SYNTHETIC-PRIVATE-PARSER-CONTENT-NEVER-LOG'
    content = ('organization: ["' + marker).encode()
    (tmp_path / 'config.yaml').write_bytes(content)
    (tmp_path / '.env').write_bytes(b'UNUSED_SYNTHETIC_VALUE="fixture-only"\r\n')
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*')
              if path.is_file() and 'organization' not in path.relative_to(tmp_path).parts}
    probe = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
calls = []
def record(frame, event, arg):
    if event == 'call' and frame.f_code.co_name in {'_discover_providers', 'load_config', '_backup_corrupt_config'}:
        calls.append(frame.f_code.co_name)
sys.setprofile(record)
try:
    if sys.argv[2] == 'startup':
        from eidolon_cli.organization_service import get_service
        get_service()
    else:
        from gateway.organization_runtime import GatewayOrganizationRuntime
        GatewayOrganizationRuntime(SimpleNamespace())._discover_home(Path(sys.argv[1]))
except Exception as error:
    print(json.dumps({'error': type(error).__name__, 'calls': calls}))
else:
    raise AssertionError('Invalid profile was admitted')
'''
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    env.pop('EIDOLON_HOME', None)
    completed = subprocess.run([sys.executable, '-c', probe, str(tmp_path), mode],
                               env=env, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'error': 'InvalidUserConfigError', 'calls': []}
    assert marker not in completed.stdout + completed.stderr
    assert completed.stderr == ''
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*')
             if path.is_file() and 'organization' not in path.relative_to(tmp_path).parts}
    assert after == before
    assert not store.heartbeat(claim)

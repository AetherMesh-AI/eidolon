"""Native-process recovery and source authority for ledger-only project saves."""
from dataclasses import asdict
import json
import os
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import yaml

from eidolon_cli import organization_service as services
from eidolon_cli.config_primitives import InvalidUserConfigError
from eidolon_cli.organization_budget import budget_view
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import resolve_settings
from eidolon_cli.organization_project_setup import project_draft, project_save, project_setup
from eidolon_cli.organization_store import OrganizationStore
from gateway.organization_runtime import GatewayOrganizationRuntime


PROJECT = {'id': 'alpha', 'root': 'root0', 'recipe': 'checks', 'team': 'builders'}
SECOND = {'id': 'beta', 'root': 'root1', 'recipe': 'other-checks', 'team': 'builders'}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    home.mkdir()
    roots = [home / 'private-alpha-root', home / 'private-beta-root']
    for root in roots:
        root.mkdir()
        (root / 'source.txt').write_text('Synthetic local input\n')
    raw = {'model': {'provider': 'private-provider', 'api_key': 'private-secret-marker'},
           'extension': {'value': 'private-extension-marker'}, 'organization': {
               'gateway_enabled': True, 'team': 'core', 'capabilities': ['work.inspect'],
               'tool_grants': ['read_file'], 'read_roots': [str(root) for root in roots],
               'project_grants': [
                   {'id': 'checks', 'files': ['root0/source.txt'], 'execution': {'root': 'root0'}},
                   {'id': 'other-checks', 'files': ['root1/source.txt'], 'execution': {'root': 'root1'}}],
               'roster': [{'id': 'reader', 'name': 'Reader', 'team': 'builders',
                           'capabilities': ['work.inspect'], 'tool_grants': ['read_file']}],
               'projects': []}}
    (home / 'config.yaml').write_text('# Preserve comments and quoting.\n' + yaml.safe_dump(raw), encoding='utf-8')
    monkeypatch.setenv('HERMES_HOME', str(home))
    assert services.stop_services()
    monkeypatch.setattr(services, '_services', {})

    def forbidden(*args, **kwargs):
        pytest.fail('Registry tests must not invoke a provider or execute work')

    from eidolon_cli import runtime_provider
    monkeypatch.setattr(services, '_execute', forbidden)
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    yield home
    assert services.stop_services()


def ledger(home):
    return home / 'organization' / 'state.db'


def dump(home):
    with sqlite3.connect(ledger(home)) as conn:
        return '\n'.join(conn.iterdump())


def saved(home, project=PROJECT, key='register-alpha'):
    # Most tests intentionally begin with a warm ledger; cold creation has its
    # own contract below, including the revision shown before any DB exists.
    services.get_service()
    revision = project_setup()['revision']
    return revision, project_save(project, revision, key, confirm_save=True)


def edit_source(home, change):
    path = home / 'config.yaml'
    raw = yaml.safe_load(path.read_text())
    change(raw['organization'])
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')


def test_first_save_accepts_reviewed_cold_revision_and_never_writes_yaml(profile):
    original = (profile / 'config.yaml').read_bytes()
    setup = project_setup()
    assert not ledger(profile).exists()
    draft = project_draft(PROJECT, setup['revision'])
    result = project_save(draft['project'], draft['revision'], 'cold-save', confirm_save=True)
    assert result['saved'] is True and result['project'] == PROJECT
    assert (profile / 'config.yaml').read_bytes() == original
    assert project_setup()['projects'] == [PROJECT]


def test_registration_is_private_atomic_and_retry_has_no_mutation(profile):
    source = (profile / 'config.yaml').read_bytes()
    revision, result = saved(profile)
    service = services.get_service()
    before = dump(profile)
    assert project_save(PROJECT, revision, 'register-alpha', confirm_save=True) == result
    assert dump(profile) == before
    with pytest.raises(ValueError, match='different draft'):
        project_save(SECOND, revision, 'register-alpha', confirm_save=True)
    assert dump(profile) == before
    with sqlite3.connect(ledger(profile)) as conn:
        registrations = conn.execute('SELECT project,binding_hash FROM organization_project_registry').fetchall()
        receipts = conn.execute('SELECT result FROM organization_project_registration_receipts').fetchall()
    assert [json.loads(row[0]) for row in registrations] == [PROJECT]
    assert [json.loads(row[0]) for row in receipts] == [result]
    assert len(registrations[0][1]) == 64
    public = json.dumps([registrations, receipts, project_setup()])
    assert 'private-' not in public and str(profile) not in public
    assert (profile / 'config.yaml').read_bytes() == source
    assert not service.running
    assert [asdict(item) for item in service.settings.projects] == [PROJECT]
    assert service.settings == service.store.settings


@pytest.mark.parametrize('confirmation', [False, None, 'true', 1])
def test_save_requires_deliberate_boolean_confirmation(profile, confirmation):
    revision = project_setup()['revision']
    source = (profile / 'config.yaml').read_bytes()
    with pytest.raises(ValueError, match='confirm'):
        project_save(PROJECT, revision, 'unconfirmed', confirm_save=confirmation)
    assert not ledger(profile).exists()
    assert (profile / 'config.yaml').read_bytes() == source


@pytest.mark.parametrize('change', ['yaml', 'policy', 'open_objective', 'open_without_request'])
def test_stale_drafts_and_open_work_never_register(profile, change):
    service = services.get_service()
    revision = project_setup()['revision']
    if change == 'yaml':
        with (profile / 'config.yaml').open('a') as stream:
            stream.write('# Owner edited this file after reviewing the draft.\n')
    elif change == 'policy':
        configuration = service.store.configuration_snapshot()
        configuration['max_inflight'] += 1
        service.store.configure_organization(configuration, expected_generation=service.store._policy_generation,
                                             idempotency_key='concurrent-policy')
    else:
        objective = service.store.create_objective('Unfinished synthetic work', idempotency_key='unfinished')
        if change == 'open_without_request':
            with service.store._write() as conn:
                conn.execute("UPDATE requests SET status='completed' WHERE objective_id=?", (objective['id'],))
    before = dump(profile)
    source = (profile / 'config.yaml').read_bytes()
    with pytest.raises(ValueError, match='changed|open objectives'):
        project_save(PROJECT, revision, 'stale-save', confirm_save=True)
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source


_SAVE_PROCESS = r'''
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from eidolon_cli import organization_service as services
from eidolon_cli import runtime_provider
from eidolon_cli.organization_project_setup import project_save

def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic registration must not start execution or discover providers')
services._execute = forbidden
services.OrganizationService.start = forbidden
runtime_provider.resolve_runtime_provider = forbidden
payload = json.loads(sys.argv[1])
service = services.get_service()
mode = payload['mode']
if mode == 'adopted_policy':
    adopt = service.store._adopt_policy
    def crash_after_policy(conn, **kwargs):
        adopt(conn, **kwargs)
        assert conn.execute('SELECT 1 FROM organization_project_registry').fetchone()
        os._exit(73)
    service.store._adopt_policy = crash_after_policy
elif mode == 'inserted_receipt':
    connect = sqlite3.connect
    class CrashConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            result = super().execute(sql, *args, **kwargs)
            if sql.startswith('INSERT INTO organization_project_registration_receipts '):
                assert self.in_transaction
                os._exit(73)
            return result
    def crashing_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=CrashConnection)
    sqlite3.connect = crashing_connect
elif mode == 'blocked_writer':
    connect = service.store._connect
    @contextlib.contextmanager
    def observed_connect():
        with connect() as conn:
            def observe(sql):
                if sql == 'BEGIN IMMEDIATE':
                    Path(payload['waiting']).write_text('waiting for the real SQLite writer')
            conn.set_trace_callback(observe)
            yield conn
    service.store._connect = observed_connect
    Path(payload['ready']).write_text('service initialized')
    assert sys.stdin.readline().strip() == 'save'
try:
    result = project_save(payload['project'], payload['revision'], payload['key'], confirm_save=True)
except ValueError as exc:
    print(json.dumps({'error': str(exc)}), flush=True)
else:
    if mode == 'committed':
        os._exit(74)
    print(json.dumps(result), flush=True)
'''


def process_payload(revision, *, mode='save', **extra):
    return json.dumps({'project': PROJECT, 'revision': revision, 'key': 'native-save', 'mode': mode, **extra})


def run_save(revision, *, mode='save'):
    return subprocess.run([sys.executable, '-u', '-c', _SAVE_PROCESS, process_payload(revision, mode=mode)],
                          text=True, capture_output=True, env=os.environ.copy(), timeout=60)


@pytest.mark.parametrize('boundary', ['adopted_policy', 'inserted_receipt'])
def test_process_death_before_commit_rolls_back_identity_policy_and_receipt(profile, boundary):
    services.get_service()
    revision = project_setup()['revision']
    before = dump(profile)
    source = (profile / 'config.yaml').read_bytes()
    crashed = run_save(revision, mode=boundary)
    assert crashed.returncode == 73, (crashed.stdout, crashed.stderr)
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source
    assert OrganizationStore(ledger(profile)).settings.projects == ()
    assert project_setup()['revision'] == revision
    retry = run_save(revision)
    assert retry.returncode == 0, retry.stderr
    result = json.loads(retry.stdout)
    assert result['saved'] is True and result['project'] == PROJECT
    assert project_setup()['projects'] == [PROJECT]


def test_process_death_after_commit_retries_exact_receipt_without_another_registration(profile):
    services.get_service()
    revision = project_setup()['revision']
    source = (profile / 'config.yaml').read_bytes()
    crashed = run_save(revision, mode='committed')
    assert crashed.returncode == 74, (crashed.stdout, crashed.stderr)
    before = dump(profile)
    with sqlite3.connect(ledger(profile)) as conn:
        receipt = json.loads(conn.execute('SELECT result FROM organization_project_registration_receipts').fetchone()[0])
    retry = run_save(revision)
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout) == receipt
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source
    assert project_setup()['projects'] == [PROJECT]


def wait_for_file(path, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f'Writer exited before synchronization: {stdout}\n{stderr}')
        time.sleep(0.01)
    raise AssertionError(f'Writer never reached {path.name}')


@pytest.mark.parametrize('change', ['yaml', 'policy', 'open_objective'])
def test_blocked_native_writer_validates_fresh_source_and_ledger_after_lock(profile, change):
    service = services.get_service()
    revision = project_setup()['revision']
    ready, waiting = profile / 'writer-ready', profile / 'writer-waiting'
    payload = process_payload(revision, mode='blocked_writer', ready=str(ready), waiting=str(waiting))
    process = subprocess.Popen([sys.executable, '-u', '-c', _SAVE_PROCESS, payload],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ.copy())
    try:
        wait_for_file(ready, process)
        with service.store._write() as conn:
            process.stdin.write('save\n')
            process.stdin.flush()
            wait_for_file(waiting, process)
            if change == 'yaml':
                with (profile / 'config.yaml').open('a') as stream:
                    stream.write('# Independent owner edit while the save waited for SQLite.\n')
            elif change == 'policy':
                # Another ledger host commits an authority generation while the
                # already initialized writer waits. It must not use a cached one.
                conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
            else:
                now = time.time()
                conn.execute('INSERT INTO objectives(id,idempotency_key,input_hash,title,description,priority,created) '
                             'VALUES (?,?,?,?,?,?,?)',
                             ('blocked-objective', 'concurrent-intake', 'synthetic-hash',
                              'Concurrent intake', 'Legacy open objective without a request', 3, now))
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        assert 'changed' in json.loads(stdout)['error'] or 'open objectives' in json.loads(stdout)['error']
        with sqlite3.connect(ledger(profile)) as conn:
            assert conn.execute('SELECT * FROM organization_project_registry').fetchall() == []
            assert conn.execute('SELECT * FROM organization_project_registration_receipts').fetchall() == []
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=30)


def test_reopen_get_service_and_gateway_reload_keep_the_same_ledger_identity(profile):
    _, result = saved(profile)
    source = (profile / 'config.yaml').read_bytes()
    initial = services.get_service()
    generation = initial.store._policy_generation
    assert [asdict(item) for item in OrganizationStore(ledger(profile)).settings.projects] == [PROJECT]
    assert services.stop_services()
    services._services.clear()
    reopened = services.get_service()
    assert reopened is not initial
    host = GatewayOrganizationRuntime(SimpleNamespace(), can_dispatch=lambda: False)
    try:
        for _ in range(3):
            host.discover()
            gateway = host._services[profile.resolve()]
            assert gateway.running
            assert [asdict(item) for item in gateway.settings.projects] == [PROJECT]
            assert gateway.store._policy_generation == generation
            assert project_setup()['revision'] == result['revision']
        assert [asdict(item) for item in reopened.settings.projects] == [PROJECT]
        assert (profile / 'config.yaml').read_bytes() == source
    finally:
        assert host.stop()


@pytest.mark.parametrize('conflict', ['id', 'alias', 'root_retarget', 'recipe_change', 'recipe_revoke', 'team_revoke'])
def test_source_conflict_fences_old_new_hosts_and_exact_repair_never_replays(profile, conflict):
    saved(profile)
    original = (profile / 'config.yaml').read_bytes()
    old = services.get_service()
    objective = old.store.create_objective('Inspect registered alpha', project_ids=['alpha'], idempotency_key='inspect')
    claim = old.store.claim_next()
    assert claim is not None
    call_id = old.store.reserve_model_call(claim, provider='synthetic', model='fixture',
                                           input_limit=3072, output_limit=512)
    audit = old.store.execution_audit(claim['id'])
    assert [call['id'] for call in audit['modelCalls']] == [call_id]
    with old.store._connect() as conn:
        budget = budget_view(conn, objective['id'], old.store.settings)
    assert budget['modelCalls'] == 1 and budget['reservedTokens'] == 3072 + 512

    def assert_reservations_retained(current):
        assert current.execution_audit(claim['id']) == audit
        with current._connect() as conn:
            assert budget_view(conn, objective['id'], current.settings) == budget
        retained = next(item for item in current.snapshot()['objectives'] if item['id'] == objective['id'])
        assert retained['usage']['usageComplete'] is False

    observer = OrganizationStore(ledger(profile))
    generation = observer._policy_generation
    assert_reservations_retained(observer)

    def change(raw):
        if conflict == 'id':
            raw['projects'] = [{**SECOND, 'id': PROJECT['id']}]
        elif conflict == 'alias':
            raw['projects'] = [{**PROJECT, 'id': 'yaml-project'}]
        elif conflict == 'root_retarget':
            replacement = profile / 'private-replacement-root'
            replacement.mkdir()
            raw['read_roots'][0] = str(replacement)
        elif conflict == 'recipe_change':
            raw['project_grants'][0]['files'] = ['root0/different.txt']
        elif conflict == 'recipe_revoke':
            raw['project_grants'] = raw['project_grants'][1:]
        else:
            raw['roster'][0]['team'] = 'other-builders'

    edit_source(profile, change)
    host = GatewayOrganizationRuntime(SimpleNamespace(), can_dispatch=lambda: False)
    try:
        host.discover()
        gateway = host._services[profile.resolve()]
        assert gateway.store.settings.project_registry_conflicts
        paused_generation = gateway.store._policy_generation
        assert paused_generation > generation
        setup = project_setup()
        assert setup['blocked'] and 'registry_conflict' in setup['blockers']
        assert setup['ledgerProjects'] == [PROJECT]
        assert 'private-' not in json.dumps(setup)
        assert not old.store.heartbeat(claim)
        assert not old.store.finish(claim, {'summary': 'Stale result', 'deliverable': 'Must not commit'})
        for candidate in [old.store, observer, gateway.store, OrganizationStore(ledger(profile))]:
            assert candidate.claim_next() is None
            with pytest.raises(ValueError):
                candidate.create_objective('Must stay fenced', idempotency_key='blocked-' + str(id(candidate)))
            with pytest.raises(ValueError):
                candidate.resolve(claim['id'], 'retry_configuration', idempotency_key='retry-' + str(id(candidate)))
            with pytest.raises(ValueError, match='no longer owned'):
                candidate.reserve_model_call(claim, provider='synthetic', model='fixture',
                                             input_limit=3072, output_limit=512)
            assert_reservations_retained(candidate)
        # A normal DB reopen or repeated gateway reload cannot silently heal a
        # source conflict by treating persisted overlay entries as YAML entries.
        for _ in range(3):
            host.discover()
            reopened = OrganizationStore(ledger(profile))
            assert reopened.settings.project_registry_conflicts
            assert reopened._policy_generation == paused_generation
            assert reopened.claim_next() is None
            assert_reservations_retained(reopened)
        (profile / 'config.yaml').write_bytes(original)
        host.discover()
        repaired = OrganizationStore(ledger(profile))
        assert not repaired.settings.project_registry_conflicts
        assert repaired._policy_generation > paused_generation
        repaired_generation = repaired._policy_generation
        for _ in range(3):
            host.discover()
            reopened = OrganizationStore(ledger(profile))
            assert reopened._policy_generation == repaired_generation
            assert_reservations_retained(reopened)
        assert [asdict(item) for item in repaired.settings.projects] == [PROJECT]
        retained = next(item for item in repaired.snapshot()['objectives'] if item['id'] == objective['id'])
        assert retained['projects'] == objective['projects']
        request = next(item for item in repaired.snapshot()['requests'] if item['id'] == claim['id'])
        assert request['status'] == 'pending_intervention' and request['attempts'] == claim['attempts']
        assert repaired.claim_next() is None
        assert repaired.snapshot()['knowledge'] == []
        assert_reservations_retained(repaired)
    finally:
        assert host.stop()


def test_repeated_resolution_preserves_provenance_but_actual_yaml_collision_stays_visible(profile):
    saved(profile)
    service = services.get_service()
    with service.store._connect() as conn:
        effective = service.settings
        for _ in range(3):
            effective = resolve_settings(conn, effective)
            assert [asdict(item) for item in effective.projects] == [PROJECT]
            assert not effective.project_registry_conflicts
        raw = yaml.safe_load((profile / 'config.yaml').read_text())
        raw['organization']['projects'] = [PROJECT]
        # Provenance is internal: user-supplied lookalike fields never authorize
        # erasing a real YAML collision before it is checked against the ledger.
        raw['organization']['ledger_project_ids'] = ['alpha']
        raw['organization']['project_registry_conflicts'] = []
        actual_yaml = OrganizationSettings.from_config(raw)
        assert not actual_yaml.ledger_project_ids
        for _ in range(3):
            actual_yaml = resolve_settings(conn, actual_yaml)
            assert actual_yaml.project_registry_conflicts


def test_owner_capacity_edit_preserves_registration_and_team_removal_is_atomic(profile):
    saved(profile)
    service = services.get_service()
    config = service.store.configuration_snapshot()
    config['max_inflight'] += 1
    generation = service.store._policy_generation
    service.store.configure_organization(config, expected_generation=generation, idempotency_key='capacity')
    fresh = OrganizationStore(ledger(profile))
    assert fresh.settings.max_inflight == config['max_inflight']
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]
    assert not fresh.settings.project_registry_conflicts
    assert fresh._policy_generation > generation
    fresh.reload_profile_configuration(profile)
    assert not fresh.settings.project_registry_conflicts
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]
    config = fresh.configuration_snapshot()
    config['roster'][0]['team'] = 'other-team'
    before = dump(profile)
    with pytest.raises(ValueError, match='project|Project'):
        fresh.configure_organization(config, expected_generation=fresh._policy_generation,
                                     idempotency_key='revoke-project-team')
    assert dump(profile) == before
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]


def test_registry_repair_cannot_clear_an_independent_invalid_yaml_pause(profile):
    saved(profile)
    original = (profile / 'config.yaml').read_bytes()
    service = services.get_service()
    edit_source(profile, lambda raw: raw.update(projects=[PROJECT]))
    service.reload_profile_configuration()
    assert service.store.settings.project_registry_conflicts
    (profile / 'config.yaml').write_text('organization: [broken\n')
    with pytest.raises(InvalidUserConfigError):
        service.reload_profile_configuration()
    paused = OrganizationStore(ledger(profile))
    with paused._connect() as conn:
        assert conn.execute('SELECT 1 FROM organization_policy_pause').fetchone()
    # Reading or reopening the last persisted seed cannot prove malformed YAML
    # was repaired. Only a successful authoritative source reload can do that.
    assert paused.claim_next() is None
    with pytest.raises(ValueError):
        paused.create_objective('Cannot bypass unreadable authority', idempotency_key='paused')
    (profile / 'config.yaml').write_bytes(original)
    assert OrganizationStore(ledger(profile)).claim_next() is None
    service.reload_profile_configuration()
    repaired = OrganizationStore(ledger(profile))
    with repaired._connect() as conn:
        assert repaired._policy_current(conn)
        assert conn.execute('SELECT 1 FROM organization_policy_pause').fetchone() is None
    assert not repaired.settings.project_registry_conflicts


_EXECUTION_PROCESS = r'''
import json
from pathlib import Path
import sys
import threading
from eidolon_cli import organization_service as services
from eidolon_cli import runtime_provider

def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic process must not discover a provider')
runtime_provider.resolve_runtime_provider = forbidden
services._execute = forbidden
release = threading.Event()
entered = threading.Event()
def synthetic(request, context, cancel):
    Path(sys.argv[1]).write_text(json.dumps({'requestId': request['id']}))
    entered.set()
    assert release.wait(90)
    return {'intervention': 'Synthetic execution has now exited'}
service = services.get_service()
service.executor = synthetic
service.start()
try:
    assert entered.wait(30)
    assert sys.stdin.readline().strip() == 'release'
finally:
    release.set()
    assert service.stop(timeout=30)
'''


def test_cancelled_native_execution_holds_capacity_until_it_really_exits(profile):
    service = services.get_service()
    objective = service.store.create_objective('Synthetic execution will keep unwinding', idempotency_key='unwinding')
    ready = profile / 'execution-ready'
    process = subprocess.Popen([sys.executable, '-u', '-c', _EXECUTION_PROCESS, str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ.copy())
    try:
        wait_for_file(ready, process)
        assert service.cancel(objective['id'])
        setup = project_setup()
        assert not setup['blocked']
        before = dump(profile)
        with pytest.raises(ValueError, match='still stopping in another runtime'):
            project_save(PROJECT, setup['revision'], 'after-cancel', confirm_save=True)
        assert dump(profile) == before
        process.stdin.write('release\n')
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        result = project_save(PROJECT, setup['revision'], 'after-cancel', confirm_save=True)
        assert result['saved'] and project_setup()['projects'] == [PROJECT]
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=30)


def assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing, *, create=None, remove=None):
    from eidolon_cli import organization_project_setup as setup_module
    first, second = profile / 'private-alpha-root', profile / 'private-beta-root'
    alias = profile / 'selected-root'
    create = create or (lambda target: alias.symlink_to(target, target_is_directory=True))
    remove = remove or alias.unlink
    create(first)
    edit_source(profile, lambda raw: raw['read_roots'].__setitem__(0, str(alias)))
    service = services.get_service()
    revision = project_setup()['revision']
    source = (profile / 'config.yaml').read_bytes()
    def retarget():
        remove()
        create(second)

    if timing == 'before_review_check':
        retarget()
    else:
        validate = setup_module._validate

        def change_after_review_check(*args):
            validate(*args)
            retarget()

        monkeypatch.setattr(setup_module, '_validate', change_after_review_check)
    before = dump(profile)
    with pytest.raises(ValueError, match='changed'):
        project_save(PROJECT, revision, 'retargeted-draft', confirm_save=True)
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source
    assert service.store.settings.projects == ()


@pytest.mark.linux_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_linux_symlink_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing)


@pytest.mark.macos_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_macos_symlink_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing)


@pytest.mark.windows_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_windows_junction_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    alias = profile / 'selected-root'

    def create(target):
        # Native directory junctions need no developer-mode or symlink privilege.
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (result.stdout, result.stderr)

    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing, create=create, remove=alias.rmdir)

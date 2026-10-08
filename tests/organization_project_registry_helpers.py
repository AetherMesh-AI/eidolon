"""Shared project registry fixtures, native-process payloads and reviewed-link assertions."""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest
import yaml

from eidolon_cli import organization_service as services
from eidolon_cli.organization_project_setup import project_save, project_setup


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

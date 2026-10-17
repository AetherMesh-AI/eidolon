"""Real profile/storage/provider boundaries; fake homes, keys and HTTP only."""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from types import SimpleNamespace
import uuid

import pytest

from eidolon_cli import organization_workspaces as workspaces
from eidolon_cli import profile_isolation as isolation
from eidolon_cli.organization_metadata import save_metadata
from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override


@contextmanager
def scope(home):
    token = set_eidolon_home_override(home)
    try:
        yield
    finally:
        reset_eidolon_home_override(token)


def deny_external_network(monkeypatch):
    """Allow only the stdlib's own socketpair handshake, never provider sockets."""
    connect = socket.socket.connect
    fallback_code = getattr(getattr(socket, '_fallback_socketpair', None), '__code__', None)

    def denied(*args, **kwargs):
        raise AssertionError('Synthetic workspace tests must never contact a provider')

    def guarded_connect(sock, address):
        caller = sys._getframe(1)
        try:
            if fallback_code is not None and caller.f_code is fallback_code:
                listener = caller.f_locals.get('lsock')
                if (caller.f_locals.get('csock') is sock
                        and isinstance(listener, socket.socket)
                        and sock.family == listener.family
                        and sock.family in (socket.AF_INET, socket.AF_INET6)
                        and sock.type == listener.type == socket.SOCK_STREAM
                        and address == listener.getsockname()[:2]
                        and address[0] in ('127.0.0.1', '::1')):
                    return connect(sock, address)
        finally:
            del caller
        return denied()

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


@pytest.fixture
def origin(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path))
    home = tmp_path / '.eidolon'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-synthetic-secret')
    monkeypatch.setenv('OPENAI_ORG_ID', 'ambient-org')
    monkeypatch.setenv('OPENAI_PROJECT_ID', 'ambient-project')
    (home / '.env').write_text('OPENAI_API_KEY=root-synthetic-secret\n')
    (home / 'config.yaml').write_text(json.dumps({'model': {
        'provider': 'openai-api', 'default': 'root-model', 'api_key': 'inline-root-secret',
        'base_url': 'https://foreign.invalid/v1', 'default_headers': {'X-Root': 'private'}}}))
    (home / 'auth.json').write_text(json.dumps({'providers': {'nous': {'access_token': 'root-oauth'}},
        'credential_pool': {'openai-api': [{'id': 'root', 'source': 'manual', 'access_token': 'root-pool'}]}}))
    for directory, filename in (('.claude', '.credentials.json'), ('.codex', 'auth.json')):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / filename).write_text(json.dumps({'tokens': {'access_token': 'home-secret',
            'refresh_token': 'home-refresh'}}))
    metadata = save_metadata(home / 'organization' / 'state.db', display_name='Origin',
        owner_display_name='Origin owner', expected_organization_id=None, expected_revision=None,
        idempotency_key='origin')['metadata']
    value = SimpleNamespace(home=home, metadata=metadata)
    with scope(home):
        yield value
    # Each test owns its tiny home. Do not accumulate SQLite ledgers on constrained runners.
    import eidolon_logging
    eidolon_logging._reset_queued_handlers()
    shutil.rmtree(tmp_path)


def params(origin, key='create-one', name='New workspace'):
    return {'capabilityVersion': 1, 'expectedOrganizationId': origin.metadata['organizationId'],
            'expectedOwnerId': origin.metadata['owner']['ownerId'], 'idempotencyKey': key,
            'displayName': name, 'ownerDisplayName': 'New owner'}


def status_params(value):
    return {key: value[key] for key in workspaces._IDENTITY_FIELDS}


def create(origin, key='create-one'):
    result = workspaces.create_workspace(origin.home, params(origin, key))
    return origin.home / 'profiles' / result['profile'], result


def files(home):
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob('*') if path.is_file() and not path.is_symlink()}


def configure(home, key='workspace-owned-synthetic-key'):
    from eidolon_cli.config import save_config, save_env_value
    with scope(home):
        save_config({'model': {'provider': isolation.PROVIDER, 'default': 'workspace-test-model'}})
        save_env_value('OPENAI_API_KEY', key)


def test_fresh_independent_ledgers_no_inheritance_and_exact_replay(origin):
    source = files(origin.home)
    home_a, first = create(origin)
    home_b, second = create(origin, 'second')
    assert first['profile'] != second['profile']
    assert first['organizationId'] != second['organizationId']
    assert first['ownerId'] != second['ownerId']
    assert first['activated'] is second['activated'] is False
    assert workspaces.create_workspace(origin.home, params(origin)) == first
    assert workspaces.workspace_creation_status(origin.home, status_params(params(origin))) == first
    for home, result in ((home_a, first), (home_b, second)):
        assert isolation.verify_isolation(home)['organizationId'] == result['organizationId']
        assert (home / '.env').read_text() == ''
        assert json.loads((home / 'config.yaml').read_text())['model'] == {'provider': '', 'default': ''}
        assert not (home / 'auth.json').exists()
        with closing(sqlite3.connect(home / 'organization' / 'state.db')) as conn:
            assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')
    assert all(files(origin.home)[path] == digest for path, digest in source.items())
    assert not (origin.home / 'active_profile').exists()


def test_capability_unknown_lookup_and_rejections_are_read_only(origin):
    before = files(origin.home)
    capability = workspaces.workspace_creation(origin.home)
    assert capability['state'] == 'available'
    assert capability['credentialMode'] == isolation.MODE
    assert workspaces.workspace_creation_status(origin.home, status_params(params(origin)))['state'] == 'unknown'
    for changes in ({'capabilityVersion': 999}, {'expectedOrganizationId': str(uuid.uuid4())},
                    {'expectedOwnerId': str(uuid.uuid4())}, {'profile': 'default'}, {'clone': True}):
        with pytest.raises((ValueError, workspaces.WorkspaceError)):
            workspaces.create_workspace(origin.home, {**params(origin), **changes})
    assert files(origin.home) == before
    assert not (origin.home / isolation.WORKSPACES).exists()


def test_changed_same_key_conflicts_and_stale_source_cannot_replay(origin):
    _, result = create(origin)
    before = files(origin.home)
    with pytest.raises(workspaces.WorkspaceError, match='idempotency_conflict'):
        workspaces.create_workspace(origin.home, params(origin, name='Changed'))
    with pytest.raises(workspaces.WorkspaceError, match='stale_origin'):
        workspaces.create_workspace(origin.home, {**params(origin), 'expectedOwnerId': str(uuid.uuid4())})
    assert files(origin.home) == before
    assert result['profile'] in {p.name for p in (origin.home / 'profiles').iterdir()}


def test_concurrent_same_key_creates_one_workspace_and_one_result(origin):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: workspaces.create_workspace(origin.home, params(origin)), range(2)))
    assert results[0] == results[1]
    assert [p.name for p in (origin.home / 'profiles').iterdir()] == [results[0]['profile']]


def test_same_key_from_distinct_origins_cannot_borrow_receipts(origin):
    home_b, b = create(origin)
    source_b = SimpleNamespace(home=home_b, metadata=b['descriptor'])
    assert workspaces.workspace_creation_status(home_b, status_params(params(source_b)))['state'] == 'unknown'
    _, c = create(source_b)
    assert b['profile'] != c['profile'] and b['organizationId'] != c['organizationId']
    assert workspaces.workspace_creation_status(home_b, status_params(params(source_b))) == c
    assert workspaces.workspace_creation_status(origin.home, status_params(params(origin))) == b


@pytest.mark.parametrize('part', ['profiles', isolation.WORKSPACES])
def test_creation_refuses_symlinked_publication_or_journal_ancestors(origin, part):
    outside = origin.home / 'outside'
    outside.mkdir()
    (origin.home / part).symlink_to(outside, target_is_directory=True)
    with pytest.raises(isolation.ProfileIsolationError):
        create(origin)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('after', [False, True])
def test_crash_at_publication_reconciles_original_operation(origin, monkeypatch, after):
    publish = workspaces._publish_directory
    def crash(stage, target):
        if after:
            publish(stage, target)
        raise OSError('synthetic crash')
    monkeypatch.setattr(workspaces, '_publish_directory', crash)
    with pytest.raises(OSError, match='synthetic crash'):
        create(origin)
    observed = workspaces.workspace_creation_status(origin.home, status_params(params(origin)))
    assert observed['state'] == ('created' if after else 'pending')
    operation = next((origin.home / isolation.WORKSPACES / 'operations').iterdir())
    prepared = json.loads((operation / 'operation.json').read_text())['result']
    monkeypatch.setattr(workspaces, '_publish_directory', publish)
    _, recovered = create(origin)
    assert recovered == prepared
    assert [p.name for p in (origin.home / 'profiles').iterdir()] == [recovered['profile']]


def test_partial_stage_failure_is_not_enumerable_and_resumes(origin, monkeypatch):
    from eidolon_cli.organization_store import OrganizationStore
    original = OrganizationStore.__init__
    def crash(*args, **kwargs):
        raise OSError('synthetic ledger failure')
    monkeypatch.setattr(OrganizationStore, '__init__', crash)
    with pytest.raises(OSError):
        create(origin)
    assert not (origin.home / 'profiles').exists()
    assert workspaces.workspace_creation_status(origin.home, status_params(params(origin)))['state'] == 'pending'
    monkeypatch.setattr(OrganizationStore, '__init__', original)
    home, result = create(origin)
    assert isolation.verify_isolation(home)['organizationId'] == result['organizationId']


def test_stage_flush_uses_writable_handles_without_changing_file_contents(origin, monkeypatch):
    original_open, original_fsync = Path.open, os.fsync
    handles, flushed = {}, set()

    def tracked_open(path, *args, **kwargs):
        candidate = (path.parent.name == 'stage' and path.name in {'config.yaml', '.env'}
                     or path.parent.name == 'organization' and path.parent.parent.name == 'stage'
                     and path.name == 'state.db')
        if candidate and path.exists():
            with original_open(path, 'rb') as before:
                content = before.read()
            handle = original_open(path, *args, **kwargs)
            handles[handle.fileno()] = (path, handle, content)
            return handle
        return original_open(path, *args, **kwargs)

    def writable_fsync(fd):
        tracked = handles.get(fd)
        if tracked is not None and not tracked[1].closed:
            path, handle, content = tracked
            if not handle.writable():
                raise OSError(errno.EBADF, 'durable flush requires a writable handle')
            with original_open(path, 'rb') as after:
                assert after.read() == content
            flushed.add(path.name)
        return original_fsync(fd)

    monkeypatch.setattr(Path, 'open', tracked_open)
    monkeypatch.setattr(os, 'fsync', writable_fsync)
    home, result = create(origin)
    assert flushed == {'state.db', 'config.yaml', '.env'}
    assert isolation.verify_isolation(home)['organizationId'] == result['organizationId']


def test_stage_flush_failure_stays_unpublished_and_replays_original_identity(origin, monkeypatch):
    original_open, original_fsync = Path.open, os.fsync
    handles = {}

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.name == 'state.db' and path.parent.parent.name == 'stage':
            handles[handle.fileno()] = handle
        return handle

    def failed_fsync(fd):
        handle = handles.get(fd)
        if handle is not None and not handle.closed:
            raise OSError(errno.EIO, 'synthetic durable flush failure')
        return original_fsync(fd)

    monkeypatch.setattr(Path, 'open', tracked_open)
    monkeypatch.setattr(os, 'fsync', failed_fsync)
    with pytest.raises(OSError, match='synthetic durable flush failure'):
        create(origin)
    assert not (origin.home / 'profiles').exists()
    assert workspaces.workspace_creation_status(origin.home, status_params(params(origin)))['state'] == 'pending'
    operation = next((origin.home / isolation.WORKSPACES / 'operations').iterdir())
    record = json.loads((operation / 'operation.json').read_text())
    assert record['result'] is None
    assert isolation.read_isolation(operation / 'stage')['state'] == 'staging'
    metadata = workspaces.read_descriptor(operation / 'stage' / 'organization' / 'state.db')['metadata']
    monkeypatch.setattr(os, 'fsync', original_fsync)
    home, recovered = create(origin)
    assert recovered['profile'] == record['profile']
    assert recovered['organizationId'] == metadata['organizationId']
    assert recovered['ownerId'] == metadata['owner']['ownerId']
    assert [p.name for p in home.parent.iterdir()] == [recovered['profile']]


def test_ready_unpublished_stage_and_standalone_copy_cannot_execute(origin, monkeypatch):
    def crash(*args):
        raise OSError('synthetic publication failure')
    monkeypatch.setattr(workspaces, '_publish_directory', crash)
    with pytest.raises(OSError):
        create(origin)
    operation = next((origin.home / isolation.WORKSPACES / 'operations').iterdir())
    stage = operation / 'stage'
    assert isolation.read_isolation(stage)['state'] == 'ready'
    copied = origin.home / 'standalone'
    copied.mkdir()
    shutil.copyfile(stage / isolation.MARKER, copied / isolation.MARKER)
    (copied / 'organization').mkdir()
    shutil.copyfile(stage / 'organization' / 'state.db', copied / 'organization' / 'state.db')
    for home in (stage, copied):
        with scope(home), pytest.raises(isolation.ProfileIsolationError, match='published'):
            isolation.isolated_runtime(requested='openai-api')


def test_publication_never_overwrites_existing_empty_target(origin):
    stage, target = origin.home / 'stage-test', origin.home / 'target-test'
    stage.mkdir(); target.mkdir()
    with pytest.raises(OSError):
        workspaces._publish_directory(stage, target)
    assert stage.is_dir() and target.is_dir()


@pytest.mark.parametrize('mode', ['missing', 'malformed', 'unsupported', 'staging'])
def test_marker_corruption_is_not_legacy_fallback(origin, mode):
    home, _ = create(origin)
    path = home / isolation.MARKER
    value = json.loads(path.read_text())
    if mode == 'missing':
        path.unlink()
    elif mode == 'malformed':
        path.write_text('{')
    else:
        value.update({'version': 999} if mode == 'unsupported' else {'state': 'staging', 'organizationId': None, 'ownerId': None})
        path.write_text(json.dumps(value))
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        isolation.isolated_runtime(requested='openai-api')


def test_deeply_nested_marker_never_falls_back_to_environment(origin):
    from eidolon_cli.config import get_env_value
    home, _ = create(origin)
    (home / isolation.MARKER).write_text('[' * 2000 + '0' + ']' * 2000)
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        get_env_value('OPENAI_API_KEY')


@pytest.mark.parametrize('state', [[], {}, None, 0])
def test_invalid_marker_state_never_falls_back_to_environment(origin, state):
    from eidolon_cli.config import get_env_value
    home, _ = create(origin)
    path = home / isolation.MARKER
    marker = json.loads(path.read_text())
    marker['state'] = state
    path.write_text(json.dumps(marker))
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        get_env_value('OPENAI_API_KEY')


@pytest.mark.parametrize('part', ['receipt', 'registration', 'ledger'])
def test_published_identity_requires_all_durable_records(origin, part):
    home, _ = create(origin)
    configure(home)
    marker = isolation.read_isolation(home)
    if part == 'receipt':
        path = origin.home / isolation.WORKSPACES / 'operations' / marker['operation'] / 'operation.json'
        value = json.loads(path.read_text())
        value['result']['organizationId'] = str(uuid.uuid4())
        path.write_text(json.dumps(value))
    elif part == 'registration':
        isolation.isolation_registration(home).unlink()
    else:
        (home / 'organization' / 'state.db').unlink()
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        isolation.isolated_runtime(requested='openai-api')


def test_creation_refuses_symlinked_lock_and_read_only_lookup_refuses_foreign_receipt(origin):
    _, created = create(origin)
    journal = origin.home / isolation.WORKSPACES
    outside = origin.home / 'foreign-record'
    outside.write_text('Do not read or write another record')
    lock = journal / 'creation.lock'
    lock.unlink(); lock.symlink_to(outside)
    with pytest.raises(isolation.ProfileIsolationError):
        create(origin, 'another')
    marker = isolation.read_isolation(origin.home / 'profiles' / created['profile'])
    receipt = journal / 'operations' / marker['operation'] / 'operation.json'
    receipt.unlink(); receipt.symlink_to(outside)
    with pytest.raises(isolation.ProfileIsolationError):
        workspaces.workspace_creation_status(origin.home, status_params(params(origin)))
    assert outside.read_text() == 'Do not read or write another record'


@pytest.mark.parametrize('name', [isolation.MARKER, 'config.yaml', '.env'])
def test_symlinked_owned_files_are_rejected(origin, name):
    home, _ = create(origin)
    configure(home)
    original = home / name
    outside = origin.home / ('foreign-' + name.lstrip('.'))
    outside.write_bytes(original.read_bytes())
    original.unlink(); original.symlink_to(outside)
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        isolation.isolated_runtime(requested='openai-api')


def test_blank_profile_ignores_root_env_home_pools_and_external_sources(origin, monkeypatch):
    from eidolon_cli import auth, env_loader, auth_codex
    from agent import secret_scope, credential_pool, anthropic_credentials
    home, _ = create(origin)
    def forbidden(*args, **kwargs):
        raise AssertionError('No borrowed credential discovery')
    monkeypatch.setattr(env_loader, 'get_secret_source_values', forbidden)
    monkeypatch.setattr(anthropic_credentials, '_read_claude_code_credentials_from_keychain', forbidden)
    monkeypatch.setattr(anthropic_credentials, '_read_claude_code_credentials_from_file', forbidden)
    monkeypatch.setattr(auth, '_external_process_auth_evidence', forbidden)
    monkeypatch.setattr(auth, '_get_aws_sdk_auth_status', forbidden)
    before_env = dict(os.environ)
    with scope(home):
        assert secret_scope.get_secret('OPENAI_API_KEY') is None
        assert secret_scope.build_profile_secret_scope(home) == {}
        assert auth._global_auth_file_path() is None
        assert auth.read_credential_pool('openai-api') == []
        assert not credential_pool.load_pool('openai-api').has_credentials()
        assert anthropic_credentials.read_claude_code_credentials() is None
        assert auth_codex._import_codex_cli_tokens() is None
        assert env_loader.load_hermes_dotenv(hermes_home=home, project_env=origin.home / '.env') == []
        for provider in ('openai-api', 'bedrock', 'copilot-acp', 'anthropic', 'openai-codex'):
            assert not auth.get_auth_status(provider)['logged_in']
        with pytest.raises(isolation.ProfileIsolationError):
            isolation.isolated_runtime(requested='openai-api')
    assert dict(os.environ) == before_env
    with scope(origin.home):
        assert secret_scope.get_secret('OPENAI_API_KEY') == 'ambient-synthetic-secret'


def test_configured_profiles_resolve_only_owned_key_and_never_publish_env(origin):
    from agent.secret_scope import get_secret
    from eidolon_cli.runtime_provider import resolve_runtime_provider
    from eidolon_cli.config import reload_env
    home_a, _ = create(origin)
    home_b, _ = create(origin, 'second')
    before_env = dict(os.environ)
    configure(home_a, 'owned-a-synthetic-key')
    configure(home_b, 'owned-b-synthetic-key')
    def resolve(home):
        with scope(home):
            assert reload_env() == 0
            return resolve_runtime_provider(requested='openai-api')['api_key'], get_secret('OPENAI_API_KEY')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(resolve, (home_a, home_b))) == [
            ('owned-a-synthetic-key', 'owned-a-synthetic-key'), ('owned-b-synthetic-key', 'owned-b-synthetic-key')]
    assert dict(os.environ) == before_env


def test_explicit_auth_targets_and_symlinked_key_removal_stay_owned(origin):
    from eidolon_cli import auth
    from eidolon_cli.config import remove_env_value
    home, _ = create(origin)
    configure(home)
    before = (origin.home / 'auth.json').read_bytes()
    with scope(home):
        with pytest.raises(isolation.ProfileIsolationError):
            auth._load_auth_store(origin.home / 'auth.json')
        with pytest.raises(isolation.ProfileIsolationError):
            auth._save_auth_store({}, target_path=origin.home / 'auth.json')
        with pytest.raises(isolation.ProfileIsolationError):
            with auth._auth_store_lock(target_path=origin.home / 'auth.json'):
                pytest.fail('Foreign lock was entered')
        (home / '.env').unlink()
        (home / '.env').symlink_to(origin.home / '.env')
        with pytest.raises(isolation.ProfileIsolationError):
            remove_env_value('OPENAI_API_KEY')
    assert (origin.home / 'auth.json').read_bytes() == before
    assert (origin.home / '.env').read_text() == 'OPENAI_API_KEY=root-synthetic-secret\n'


def test_real_sdk_uses_owned_key_and_canonical_endpoint_without_ambient_headers(origin, monkeypatch):
    import httpx
    import openai  # Import SDK classes before replacing the HTTP transport constructor.
    from agent.agent_runtime_helpers import create_openai_client
    home, _ = create(origin)
    configure(home)
    received = []
    def response(request):
        received.append(request)
        return httpx.Response(200, json={'id': 'fake', 'object': 'chat.completion', 'created': 1,
            'model': 'workspace-test-model', 'choices': [{'index': 0, 'message': {'role': 'assistant',
                'content': 'Synthetic response'}, 'finish_reason': 'stop'}]})
    original = httpx.Client
    options = []
    class Client(original):
        def __init__(self, **kwargs):
            options.append(dict(kwargs))
            super().__init__(transport=httpx.MockTransport(response), **kwargs)
    monkeypatch.setattr(httpx, 'Client', Client)
    monkeypatch.setenv('HTTPS_PROXY', 'http://foreign.invalid:8888')
    agent = SimpleNamespace(_isolated_workspace_home=str(home), provider='openai-api',
                            api_mode='chat_completions', model='workspace-test-model')
    with scope(home):
        sdk = create_openai_client(agent, {'api_key': 'workspace-owned-synthetic-key',
            'base_url': isolation.ENDPOINT}, reason='synthetic-test', shared=False)
        try:
            result = sdk.chat.completions.create(model=agent.model, messages=[{'role': 'user', 'content': 'Synthetic'}])
            assert result.choices[0].message.content == 'Synthetic response'
        finally:
            sdk.close()
    assert len(received) == 1
    request = received[0]
    assert str(request.url) == isolation.ENDPOINT + '/chat/completions'
    assert request.headers['authorization'] == 'Bearer workspace-owned-synthetic-key'
    assert request.headers.get('openai-organization', '') == ''
    assert request.headers.get('openai-project', '') == ''
    assert 'x-root' not in request.headers
    assert options == [{'trust_env': False, 'follow_redirects': False}]
    with scope(origin.home), pytest.raises(isolation.ProfileIsolationError, match='changed'):
        create_openai_client(agent, {'api_key': 'workspace-owned-synthetic-key', 'base_url': isolation.ENDPOINT},
                             reason='wrong-home-rebuild', shared=False)


def test_unsupported_routes_and_auxiliary_cache_fail_before_provider_calls(origin, monkeypatch):
    from eidolon_cli.runtime_provider import resolve_runtime_provider
    from agent import auxiliary_client as auxiliary
    home, _ = create(origin)
    configure(home)
    with scope(home):
        for provider in ('anthropic', 'bedrock', 'vertex', 'openai-codex', 'copilot-acp', 'custom'):
            with pytest.raises(isolation.ProfileIsolationError):
                resolve_runtime_provider(requested=provider)
        for call in (lambda: auxiliary.resolve_provider_client('openai-api'),
                     lambda: auxiliary._get_cached_client('openai-api'),
                     lambda: auxiliary.resolve_vision_provider_client('openai-api'),
                     lambda: auxiliary._to_async_client(object(), 'model')):
            with pytest.raises(isolation.ProfileIsolationError):
                call()


def test_disabled_provider_and_tool_modes_fail_before_execution(origin, monkeypatch):
    import threading
    from eidolon_cli import organization_executor as executor
    from eidolon_cli.config import save_config
    from eidolon_cli import organization_tool_executor
    home, _ = create(origin)
    configure(home)
    def forbidden(*args, **kwargs):
        raise AssertionError('No tool scope or provider may be entered')
    monkeypatch.setattr(organization_tool_executor, 'tool_execution', forbidden)
    with scope(home):
        for kind, context in (('work.inspect', {}), ('work.edit', {}),
                              ('work.analyze', {'_credentialCapsule': object()})):
            result = executor.execute({'type': kind}, context, threading.Event())
            assert 'intervention' in result
        with pytest.raises(isolation.ProfileIsolationError):
            executor._create_agent({}, threading.Event(), 9999999999, tool_execution=object())
        save_config({'model': {'provider': 'openai-api', 'default': 'workspace-test-model'},
                     'providers': {'openai-api': {'enabled': False}}})
        with pytest.raises(isolation.ProfileIsolationError, match='disabled'):
            isolation.isolated_runtime(requested='openai-api')


def test_plugin_managers_and_provider_registry_cannot_discover_or_borrow_callbacks(origin, monkeypatch):
    from eidolon_cli import plugins
    import providers
    home, _ = create(origin)
    touched = []
    foreign = plugins.PluginManager(scope_key=str(origin.home))
    foreign._hooks['pre_llm_call'] = [lambda **kw: touched.append('foreign callback')]
    monkeypatch.setattr(plugins, '_plugin_manager', foreign)
    monkeypatch.setattr(plugins.PluginManager, '_discover_and_load_inner', lambda *a: touched.append('discovery'))
    monkeypatch.setattr(plugins.PluginManager, 'unload', lambda *a, **kw: touched.append('unload'))
    sentinel = object()
    monkeypatch.setitem(providers._REGISTRY, 'openai-api', sentinel)
    monkeypatch.setattr(providers, '_discovered', False)
    monkeypatch.setattr(providers, '_discover_entry_point_providers', lambda: touched.append('provider discovery'))
    with scope(home):
        manager = plugins.get_plugin_manager()
        assert manager is not foreign
        manager.discover_and_load(force=True)
        assert plugins.invoke_hook('pre_llm_call') == []
        assert providers.get_provider_profile('openai-api') is None
        assert providers.list_providers() == []
        providers._discover_providers()
        assert providers._discovered is False
        monkeypatch.setattr(providers, '_discovered', True)
        assert providers.get_provider_profile('openai-api') is None
        assert providers.list_providers() == []
        manager._hooks['pre_llm_call'] = [lambda **kw: touched.append('local callback')]
        with pytest.raises(isolation.ProfileIsolationError):
            plugins.get_plugin_manager()
        with pytest.raises(isolation.ProfileIsolationError):
            manager.discover_and_load(force=True)
        manager._hooks.clear()
    with scope(origin.home):
        assert providers.get_provider_profile('openai-api') is sentinel
    assert touched == []


def test_queued_logs_follow_producer_and_never_copy_between_strict_homes(origin):
    import logging
    import eidolon_logging as log
    home_a, _ = create(origin)
    home_b, _ = create(origin, 'second')
    log._reset_queued_handlers()
    for home in (origin.home, home_a, home_b):
        with scope(home):
            log.setup_logging(hermes_home=home)
    log._stop_queue_listener()
    logger = logging.getLogger('workspace-boundary-test')
    stamps = {origin.home: 'only-root-record', home_a: 'only-a-record', home_b: 'only-b-record'}
    for home, text in stamps.items():
        with scope(home):
            logger.warning(text)
    # Resume while B is active. Records must retain their original producer homes.
    with scope(home_b), log._queue_state_lock:
        log._start_queue_listener_locked()
    log.flush_log_queue()
    for home, expected in stamps.items():
        content = (home / 'logs' / 'agent.log').read_text()
        assert expected in content
        assert all(text not in content for other, text in stamps.items() if other != home)
    # Corruption between enqueue and emit must drop the record, never route to root.
    log._stop_queue_listener()
    with scope(home_a):
        logger.warning('must-not-route-corrupt-record')
    (home_a / isolation.MARKER).write_text('{')
    with log._queue_state_lock:
        log._start_queue_listener_locked()
    log.flush_log_queue()
    assert all('must-not-route-corrupt-record' not in (home / 'logs' / 'agent.log').read_text()
               for home in stamps)


def test_strict_logging_refuses_symlink_before_open(origin):
    import eidolon_logging as log
    home, _ = create(origin)
    outside = origin.home / 'foreign-logs'
    outside.mkdir()
    (home / 'logs').rmdir()
    (home / 'logs').symlink_to(outside, target_is_directory=True)
    with scope(home), pytest.raises(isolation.ProfileIsolationError):
        log.setup_logging(hermes_home=home)
    assert list(outside.iterdir()) == []


def test_warmed_request_client_cannot_send_from_another_home_or_stale_key(origin, monkeypatch):
    import httpx
    import openai
    import threading
    import time
    from eidolon_cli import organization_executor as executor
    from eidolon_cli.config import save_config
    home_a, _ = create(origin)
    home_b, _ = create(origin, 'second')
    configure(home_a)
    configure(home_b, 'owned-b-synthetic-key')
    sent = []
    deny_external_network(monkeypatch)
    original = httpx.Client
    class Client(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda request: sent.append(request)), **kwargs)
    monkeypatch.setattr(httpx, 'Client', Client)
    with scope(home_a):
        save_config({'model': {'provider': 'openai-api', 'default': 'workspace-test-model',
                              'context_length': 128000}, 'compression': {'enabled': False}})
        agent = executor._create_agent(executor._runtime_kwargs({}, 20), threading.Event(), time.monotonic() + 20)
        cached = agent._create_request_openai_client(reason='warm-test')
        agent._close_request_openai_client(cached, reason='release-test')
    try:
        with scope(home_b), pytest.raises(isolation.ProfileIsolationError, match='changed'):
            agent._interruptible_api_call({'model': agent.model, 'messages': [{'role': 'user', 'content': 'Synthetic'}]})
        configure(home_a, 'rotated-a-synthetic-key')
        with scope(home_a), pytest.raises(isolation.ProfileIsolationError, match='credentials'):
            agent._interruptible_api_call({'model': agent.model, 'messages': [{'role': 'user', 'content': 'Synthetic'}]})
    finally:
        with scope(home_a):
            agent.close()
    assert sent == []


@pytest.mark.parametrize('http_status', [200, 401])
def test_real_organization_agent_uses_only_owned_route_without_tools_or_probe(origin, monkeypatch, http_status):
    import httpx
    import openai
    import threading
    from eidolon_cli.config import save_config
    from eidolon_cli import organization_executor as executor
    from tools import env_probe
    from eidolon_cli import plugins
    from agent import ssl_guard
    import providers
    discovery = []
    monkeypatch.setattr(plugins.PluginManager, '_discover_and_load_inner', lambda *a: discovery.append('plugin'))
    monkeypatch.setattr(providers, '_discovered', False)
    monkeypatch.setattr(providers, '_discover_providers', lambda: discovery.append('model-provider'))
    monkeypatch.setenv('SSL_CERT_FILE', str(origin.home / 'missing-ambient-ca.pem'))
    monkeypatch.setenv('HERMES_CA_BUNDLE', str(origin.home / 'missing-ambient-hermes-ca.pem'))
    monkeypatch.setattr(ssl_guard, '_validate_bundle_path', lambda *a, **kw: discovery.append('ambient-ca'))
    home, _ = create(origin)
    configure(home)
    with scope(home):
        save_config({'model': {'provider': 'openai-api', 'default': 'workspace-test-model',
                              'streaming': False, 'context_length': 128000},
                     'agent': {'environment_probe': True}, 'compression': {'enabled': False}})
    deny_external_network(monkeypatch)
    probes = []
    monkeypatch.setattr(env_probe, 'warm_environment_probe_async', lambda: probes.append('probe'))
    sent = []
    output = {'summary': 'Synthetic analysis', 'deliverable': 'Only submitted material was used.'}
    def response(request):
        sent.append((request, json.loads(request.content)))
        if http_status == 401:
            return httpx.Response(401, json={'error': {'message': 'Synthetic rejected credential',
                'type': 'invalid_request_error', 'code': 'invalid_api_key'}})
        return httpx.Response(200, json={'id': 'fake', 'object': 'chat.completion', 'created': 1,
            'model': 'workspace-test-model', 'choices': [{'index': 0, 'message': {'role': 'assistant',
                'content': json.dumps(output)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})
    original = httpx.Client
    class Client(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(response), **kwargs)
    monkeypatch.setattr(httpx, 'Client', Client)
    with scope(home):
        result = executor.execute({'type': 'work.analyze'}, {
            'objective': {'title': 'Synthetic', 'description': 'Submitted text only.'},
            'timeoutSeconds': 20}, threading.Event())
    if http_status == 200:
        assert {key: value for key, value in result.items() if key != 'usage'} == output
    else:
        assert 'intervention' in result and 'deliverable' not in result
    assert len(sent) == 1, result
    assert probes == []
    assert discovery == []
    request, body = sent[0]
    assert str(request.url) == isolation.ENDPOINT + '/chat/completions'
    assert request.headers['authorization'] == 'Bearer workspace-owned-synthetic-key'
    assert not body.get('tools') and not body.get('functions')

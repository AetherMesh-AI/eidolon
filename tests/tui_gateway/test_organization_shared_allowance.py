"""Real served-owner RPC boundary for the opt-in installation allowance."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
from io import StringIO
import sqlite3
import threading
import time
from types import SimpleNamespace
import uuid
from unittest.mock import patch

import pytest

from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


READ = 'organization.sharedAllowance'
OPERATIONS = {
    'configuration': ('organization.previewSharedAllowance', 'organization.configureSharedAllowance'),
    'observation': ('organization.previewSharedAllowanceObservation', 'organization.recordSharedAllowanceObservation'),
}
METHODS = [READ, *(name for pair in OPERATIONS.values() for name in pair)]


def _timeout_evidence(futures, writes):
    # Observations only: a completed Future does not establish process shutdown.
    states, failure_frames = [], []
    for future in futures:
        error = None
        if future.done():
            if not future.cancelled():
                error = future.exception()
            states.append('cancelled' if future.cancelled() else 'failed' if error is not None else 'completed')
        else:
            states.append('running' if future.running() else 'pending')
        frames, tb = [], error.__traceback__ if error is not None else None
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({'file': code.co_filename.replace('\\', '/').rsplit('/', 1)[-1],
                           'line': tb.tb_lineno, 'function': code.co_name})
            tb = tb.tb_next
        failure_frames.append(frames)
    from scripts.ci.organization_timing import _write_python_stacks
    frames = StringIO()
    _write_python_stacks(frames)  # Existing basename/frame-only formatter; no locals.
    return json.dumps({'futureStates': states, 'failureFrames': failure_frames, 'writesAttempted': writes[0],
                       'writesCompleted': writes[1]}) + '\n' + frames.getvalue()


def call(method=READ, params=None, transport=None):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    writes, futures = [0, 0], []
    def write(response):
        writes[0] += 1
        result = responses.put(response)
        writes[1] += 1
        return result
    transport.write = write
    request = {'jsonrpc': '2.0', 'id': 17, 'method': method, 'params': {} if params is None else params}
    submit = server._pool.submit
    caller = threading.get_ident()
    def observe_submit(*args, **kwargs):
        future = submit(*args, **kwargs)
        if threading.get_ident() == caller:
            futures.append(future)
        return future
    # Observe the existing submission, without wrapping the worker or replacing its pool.
    # These fixture calls are serial; restore submit before waiting for the response.
    with patch.object(server._pool, 'submit', observe_submit):
        result = server.dispatch(request, transport=transport)
    try:
        return result if result is not None else responses.get(timeout=10)
    except Empty as exc:
        try:
            exc.add_note('SHARED_ALLOWANCE_RPC_TIMEOUT ' + _timeout_evidence(futures, writes))
        except Exception:
            exc.add_note('SHARED_ALLOWANCE_RPC_TIMEOUT evidence_unavailable')
        raise


def make_profile(home, name='Organization', *, execution=True):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('{}\n', encoding='utf-8')
    path = home / 'organization' / 'state.db'
    identity = save_metadata(path, display_name=name, owner_display_name='Owner ' + name,
        expected_organization_id=None, expected_revision=None, idempotency_key='initialize')['metadata']
    if execution:
        OrganizationStore(path, OrganizationSettings())
    return SimpleNamespace(home=home, path=path, identity=identity)


def disk_state(root):
    result = {}
    for path in root.rglob('*'):
        # SQLite WAL readers may attach/remove coordination sidecars. Compare
        # durable logical rows/schema and every non-SQLite file instead.
        if path.name in {'state.db-wal', 'state.db-shm', 'shared-allowance.db-wal', 'shared-allowance.db-shm'}:
            continue
        if path.is_file():
            if path.suffix == '.db':
                with closing(sqlite3.connect(path)) as conn:
                    result[str(path.relative_to(root))] = tuple(conn.iterdump())
            else:
                result[str(path.relative_to(root))] = path.read_bytes()
        elif path.is_dir():
            result[str(path.relative_to(root)) + '/'] = None
    return result


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    root = make_profile(home, 'Root')
    root.alpha = make_profile(home / 'profiles' / 'alpha', 'Alpha')
    root.beta = make_profile(home / 'profiles' / 'beta', 'Beta')
    root.empty = make_profile(home / 'profiles' / 'empty', 'Not initialized', execution=False)
    root.vacant = home / 'profiles' / 'vacant'
    root.vacant.mkdir()
    import eidolon_cli.organization_service as services
    def forbidden(*args, **kwargs):
        raise AssertionError('Shared allowance entered execution or mutable profile routing')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services.OrganizationService, '__init__', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    monkeypatch.setattr(server, '_profile_home', forbidden)
    return root


def read(profile):
    response = call(params={'expectedOrganizationId': profile.identity['organizationId']})
    assert 'error' not in response, response
    return response['result']


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def envelope(current, candidate, *, action='configuration'):
    return {'expectedOrganizationId': current['organizationId'], 'expectedOwnerId': current['ownerId'],
            'expectedGeneration': current['generation'], 'sourceRevision': current['sourceRevision'], action: candidate}


def configuration(root):
    now = time.time()
    dimensions = lambda calls, tokens: {'modelCalls': calls, 'tokens': tokens, 'configuredCostUsd': None}
    return {'periodId': 'owner-period-one', 'startsAt': iso(now - 60), 'endsAt': iso(now + 3600),
            'limits': dimensions(20, 20000), 'protected': dimensions(2, 2000),
            'allocations': [{'profile': name, 'organizationId': profile.identity['organizationId'],
                'ownerId': profile.identity['owner']['ownerId'], 'limits': dimensions(10, 10000),
                'protected': dimensions(1, 1000)} for name, profile in [('default', root), ('alpha', root.alpha)]]}


def preview(root, candidate=None, *, action='configuration'):
    params = envelope(read(root), configuration(root) if candidate is None else candidate, action=action)
    response = call(OPERATIONS[action][0], params)
    assert 'error' not in response, response
    params[action] = response['result'][action]
    return params, response['result']


def apply(root, candidate=None, *, action='configuration', key='save'):
    params, reviewed = preview(root, candidate, action=action)
    request = {**params, 'previewVersion': reviewed['previewVersion'], 'idempotencyKey': key, 'confirmed': True}
    response = call(OPERATIONS[action][1], request)
    assert 'error' not in response, response
    return request, response


def test_descriptor_and_preview_are_observational_and_list_only_initialized_candidates(served):
    before = disk_state(served.home)
    current = read(served)
    assert current['state'] == 'unconfigured'
    assert current['authority'] == 'installation-owner'
    assert current['configuration'] is None and current['generation'] == 0
    assert current['capabilities'] == {'configuration': True, 'observation': False}
    assert {row['profile'] for row in current['candidates']} == {'default', 'alpha', 'beta'}
    assert current['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert current['pool'] is None
    params, reviewed = preview(served)
    assert reviewed['configuration'] == params['configuration']
    assert reviewed['impact']['automaticReset'] is False
    assert disk_state(served.home) == before
    assert not (served.home / 'organization' / 'shared-allowance.db').exists()
    assert call('gateway.capabilities')['result']['organization_shared_allowance'] == 1


def test_real_apply_observation_and_original_intent_replay_return_immutable_receipts(served):
    request, applied = apply(served)
    assert applied['result']['receipt']['generation'] == 1
    assert call(OPERATIONS['configuration'][1], request) == applied
    current = read(served)
    assert current['state'] == 'ready' and current['configuration'] == request['configuration']
    later = deepcopy(current['configuration'])
    later['limits']['modelCalls'] = 22
    apply(served, later, key='later')
    now = time.time()
    observed = {'remainingPercent': 25, 'asOf': iso(now - 1), 'expiresAt': iso(now + 300), 'resetAt': None}
    obs_request, obs_result = apply(served, observed, action='observation', key='observation')
    assert obs_result['result']['receipt']['generation'] == 3
    assert call(OPERATIONS['observation'][1], obs_request) == obs_result
    assert call(OPERATIONS['configuration'][1], request) == applied
    assert read(served)['generation'] == 3
    changed = {**request, 'sourceRevision': '0' * 64}
    assert call(OPERATIONS['configuration'][1], changed)['error']['data']['reason'] == 'idempotency_conflict'


@pytest.mark.parametrize('method', METHODS)
def test_no_transport_and_parameter_routing_are_rejected_before_storage(served, method):
    before = disk_state(served.home)
    params = {'expectedOrganizationId': served.identity['organizationId']}
    raw = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    assert server.handle_request(raw)['error']['code'] == 4001
    assert call(method, params, SimpleNamespace(closed=True))['error']['code'] == 4001
    for extra in ({'profile': 'alpha'}, {'home': str(served.home)}, {'root': str(served.home)},
                  {'organizationId': served.identity['organizationId']}, {'actorId': 'owner'},
                  {'connectionId': 'different'}, {'apiKey': 'not-stored'}):
        assert call(method, {**params, **extra})['error']['code'] == -32602
    assert disk_state(served.home) == before


def test_context_override_cannot_redirect_root_authority_or_candidates(served):
    before = disk_state(served.home)
    initial = read(served)
    token = set_eidolon_home_override(served.alpha.home)
    try:
        assert read(served) == initial
        params, reviewed = preview(served)
        assert reviewed['organizationId'] == served.identity['organizationId']
        assert params['expectedOwnerId'] == served.identity['owner']['ownerId']
    finally:
        reset_eidolon_home_override(token)
    assert disk_state(served.home) == before


def test_member_endpoint_cannot_become_installation_owner_or_reveal_siblings(served, monkeypatch):
    apply(served)
    monkeypatch.setattr(server, '_eidolon_home', served.alpha.home)
    before = disk_state(served.home)
    current = read(served.alpha)
    assert current['authority'] == 'member'
    assert current['configuration'] is None and current['candidates'] == [] and current['pool'] is None
    assert current['member'] is not None
    assert current['capabilities'] == {'configuration': False, 'observation': False}
    assert current['scope'] == {'kind': 'served-profile', 'profile': 'alpha'}
    assert served.beta.identity['organizationId'] not in json.dumps(current)
    params = envelope(current, configuration(served))
    for method in OPERATIONS['configuration']:
        request = params if method == OPERATIONS['configuration'][0] else {
            **params, 'confirmed': True, 'previewVersion': '0' * 64, 'idempotencyKey': 'forged'}
        assert call(method, request)['error']['data']['reason'] == 'installation_owner_required'
    assert call(params={'expectedOrganizationId': served.identity['organizationId']})['error']['data']['reason'] == 'identity_conflict'
    assert disk_state(served.home) == before


def test_confirmation_preview_source_and_owner_changes_never_write(served):
    params, reviewed = preview(served)
    request = {**params, 'confirmed': True, 'previewVersion': reviewed['previewVersion'], 'idempotencyKey': 'valid'}
    before = disk_state(served.home)
    for change in ({'confirmed': False}, {'confirmed': 1}, {'previewVersion': '0' * 64},
                   {'sourceRevision': '0' * 64}, {'expectedGeneration': 1},
                   {'expectedOwnerId': str(uuid.uuid4())}):
        assert 'error' in call(OPERATIONS['configuration'][1], {**request, **change})
    assert disk_state(served.home) == before


def test_metadata_only_profile_cannot_be_enrolled_by_rpc_preview(served):
    candidate = configuration(served)
    candidate['allocations'][1].update(profile='empty', organizationId=served.empty.identity['organizationId'],
                                      ownerId=served.empty.identity['owner']['ownerId'])
    before = disk_state(served.home)
    result = call(OPERATIONS['configuration'][0], envelope(read(served), candidate))
    assert result['error']['data']['reason'] == 'enrollment_identity_conflict'
    assert disk_state(served.home) == before


@pytest.mark.linux_only
def test_launch_symlink_never_promotes_alias_to_installation_owner(served, monkeypatch):
    alias = served.home.parent / 'launch-alias'
    alias.symlink_to(served.home, target_is_directory=True)
    monkeypatch.setattr(server, '_eidolon_home', alias)
    before = disk_state(served.home)
    result = call(params={'expectedOrganizationId': served.identity['organizationId']})
    assert result['error']['code'] == 4001
    assert str(alias) not in str(result)
    assert disk_state(served.home) == before


def test_missing_root_and_corrupt_pool_fail_closed_without_provisioning(tmp_path, monkeypatch):
    home = tmp_path / 'missing'
    monkeypatch.setattr(server, '_eidolon_home', home)
    result = call(params={'expectedOrganizationId': str(uuid.uuid4())})
    assert 'error' in result and str(home) not in str(result)
    assert not home.exists()
    root = make_profile(home)
    (home / 'organization' / 'shared-allowance.db').write_bytes(b'corrupt')
    result = call(params={'expectedOrganizationId': root.identity['organizationId']})
    assert 'error' in result and str(home) not in str(result)
    assert (home / 'organization' / 'shared-allowance.db').read_bytes() == b'corrupt'


def test_named_profile_with_no_root_identity_never_self_promotes(tmp_path, monkeypatch):
    root = tmp_path / 'installation'
    member = make_profile(root / 'profiles' / 'member')
    monkeypatch.setattr(server, '_eidolon_home', member.home)
    monkeypatch.setenv('HERMES_HOME', str(member.home))
    before = disk_state(root)
    response = call(params={'expectedOrganizationId': member.identity['organizationId']})
    assert 'error' in response and str(root) not in str(response)
    assert disk_state(root) == before
    assert not (root / 'organization').exists()


def test_managed_installation_blocks_new_edits_but_preserves_immutable_reconciliation(served, monkeypatch):
    request, applied = apply(served)
    monkeypatch.setenv('HERMES_MANAGED', 'nix')
    current = read(served)
    assert current['managed'] is True
    assert current['capabilities'] == {'configuration': False, 'observation': False}
    before = disk_state(served.home)
    params = envelope(current, current['configuration'])
    response = call(OPERATIONS['configuration'][0], params)
    assert response['error']['data']['reason'] == 'managed_configuration'
    assert call(OPERATIONS['configuration'][1], request) == applied
    assert disk_state(served.home) == before


def test_passive_review_never_loads_profile_credentials_or_replaces_ambient_secrets(served, monkeypatch):
    from agent import secret_scope
    from eidolon_cli import env_loader
    def forbidden(*args, **kwargs):
        raise AssertionError('Shared allowance read attempted credential discovery')
    monkeypatch.setattr(secret_scope, 'build_profile_secret_scope', forbidden)
    monkeypatch.setattr(env_loader, 'hydrate_profile_secret_sources', forbidden)
    token = secret_scope.set_secret_scope({'PRIVATE_TEST_VALUE': 'not-shared'})
    try:
        current = read(served)
        params = envelope(current, configuration(served))
        response = call(OPERATIONS['configuration'][0], params)
        assert 'error' not in response, response
        assert secret_scope.current_secret_scope() == {'PRIVATE_TEST_VALUE': 'not-shared'}
        assert 'not-shared' not in json.dumps(response)
    finally:
        secret_scope.reset_secret_scope(token)


@pytest.fixture
def controlled_rpc(monkeypatch):
    """Drive real dispatch deterministically without waiting ten seconds per failure."""
    from concurrent.futures import Future
    import sys
    module = sys.modules[__name__]
    pending = []
    class Pool:
        def submit(self, fn):
            future = Future()
            pending.append((future, fn))
            return future
    pool = Pool()
    monkeypatch.setattr(server, '_pool', pool)
    class ControlledQueue(Queue):
        def get(self, *, timeout):
            assert timeout == 10  # Production fixture deadline remains unchanged.
            for future, fn in pending:
                if future.done() or future.running():
                    continue
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(fn())
                except BaseException as exc:
                    future.set_exception(exc)
            return super().get(block=False)
    monkeypatch.setattr(module, 'Queue', ControlledQueue)
    return module, pending, ControlledQueue


@pytest.mark.parametrize('outcome,states,writes', [
    ('no_response', ['completed'], [0, 0]),
    ('worker_exception', ['failed'], [0, 0]),
    ('write_exception', ['failed'], [1, 0]),
    ('cancelled', ['cancelled'], [0, 0]),
    ('pending', ['pending'], [0, 0]),
    ('running', ['running'], [0, 0]),
])
def test_rpc_timeout_retains_original_failure_and_safe_worker_evidence(controlled_rpc, monkeypatch, outcome, states, writes):
    module, pending, queue = controlled_rpc
    class WorkerFailure(BaseException):
        pass
    def handler(rid, params):
        if outcome == 'worker_exception':
            raise WorkerFailure('PRIVATE_EXCEPTION_VALUE')
        return None if outcome == 'no_response' else {'id': rid, 'result': params}
    monkeypatch.setitem(server._methods, OPERATIONS['configuration'][1], handler)
    if outcome == 'write_exception':
        def broken_write(self, value):
            raise OSError('PRIVATE_WRITE_VALUE')
        monkeypatch.setattr(queue, 'put', broken_write)
    original = Empty()
    get = queue.get
    def fail(self, *, timeout):
        assert timeout == 10
        if outcome in {'pending', 'running', 'cancelled'}:
            future = pending[-1][0]
            if outcome == 'running':
                future.set_running_or_notify_cancel()
            elif outcome == 'cancelled':
                future.cancel()
        else:
            with pytest.raises(Empty):
                get(self, timeout=timeout)
        raise original
    monkeypatch.setattr(queue, 'get', fail)
    submit = server._pool.submit
    with pytest.raises(Empty) as caught:
        call(OPERATIONS['configuration'][1], {'private': 'PRIVATE_REQUEST_VALUE'})
    assert caught.value is original
    assert server._pool.submit == submit
    note = caught.value.__notes__[0]
    data = json.loads(note.split('\n', 1)[0].removeprefix('SHARED_ALLOWANCE_RPC_TIMEOUT '))
    frames = data.pop('failureFrames')
    assert data == {'futureStates': states, 'writesAttempted': writes[0], 'writesCompleted': writes[1]}
    assert len(frames) == 1
    assert bool(frames[0]) == (outcome in {'worker_exception', 'write_exception'})
    for frame in frames[0]:
        assert set(frame) == {'file', 'line', 'function'}
        assert '/' not in frame['file'] and '\\' not in frame['file']
    assert 'Python frame snapshot (not a native stack dump)' in note
    assert 'PRIVATE_' not in note


def test_rpc_timeout_evidence_failure_cannot_replace_original(controlled_rpc, monkeypatch):
    module, _, queue = controlled_rpc
    original = Empty()
    def fail(self, *, timeout):
        assert timeout == 10
        raise original
    def broken_evidence(*args):
        raise OSError('PRIVATE_DIAGNOSTIC_VALUE')
    monkeypatch.setattr(queue, 'get', fail)
    monkeypatch.setattr(module, '_timeout_evidence', broken_evidence)
    with pytest.raises(Empty) as caught:
        call(OPERATIONS['configuration'][1])
    assert caught.value is original
    assert caught.value.__notes__ == ['SHARED_ALLOWANCE_RPC_TIMEOUT evidence_unavailable']


def test_rpc_handler_exception_is_a_response_and_late_response_stays_with_original_queue(controlled_rpc, monkeypatch):
    _, pending, queue = controlled_rpc
    def handler(rid, params):
        if params.get('fail'):
            raise ValueError('synthetic handler failure')
        return {'id': rid, 'result': params}
    monkeypatch.setitem(server._methods, OPERATIONS['configuration'][1], handler)
    assert call(OPERATIONS['configuration'][1], {'fail': True})['error']['code'] == -32000
    original_get = queue.get
    with monkeypatch.context() as delayed:
        def immediate_timeout(self, *, timeout):
            assert timeout == 10
            raise Empty()
        delayed.setattr(queue, 'get', immediate_timeout)
        with pytest.raises(Empty):
            call(OPERATIONS['configuration'][1], {'value': 'old'})
    assert queue.get == original_get
    # Both requests reuse id 17; the old response must remain in its old queue.
    assert call(OPERATIONS['configuration'][1], {'value': 'new'})['result'] == {'value': 'new'}
    assert all(future.done() for future, _ in pending)


def test_rpc_running_worker_is_not_mistaken_for_shutdown(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sys
    entered, release = threading.Event(), threading.Event()
    replies = []
    class ObservedQueue(Queue):
        def get(self, *, timeout):
            assert timeout == 10
            assert entered.wait(2)
            raise Empty()
        def put(self, response):
            replies.append(response)
            return super().put(response)
    def handler(rid, params):
        entered.set()
        assert release.wait(2)
        return {'id': rid, 'result': 'late'}
    monkeypatch.setattr(sys.modules[__name__], 'Queue', ObservedQueue)
    monkeypatch.setitem(server._methods, OPERATIONS['configuration'][1], handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(server, '_pool', pool)
        try:
            with pytest.raises(Empty) as caught:
                call(OPERATIONS['configuration'][1])
            assert '"futureStates": ["running"]' in caught.value.__notes__[0]
            assert replies == []
        finally:
            release.set()
    # Only this synthetic executor's explicit join establishes completion.
    assert replies == [{'id': 17, 'result': 'late'}]

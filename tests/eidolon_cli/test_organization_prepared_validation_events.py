"""Real-SQL host-event retention; synthetic records never prove native execution."""
from copy import deepcopy
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_events as events
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_project_node import IMAGE, PROFILE_SHA256
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history
from eidolon_cli.organization_project_workspace_resources import (
    EVENT_CONTRACT, RESOURCE_CONTRACT, container_labels, container_name,
)
from tests.eidolon_cli.test_organization_prepared_validation_contract import prepared  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    objective, proposal, reserve, state,  # noqa: F401
)


@pytest.fixture
def stream(state):
    conn = state['conn']
    conn.executescript(events.PREPARED_RUNTIME_SCHEMA)
    obj = objective(state)
    envelope = proposal(state, obj)
    reserve(state, obj, envelope)
    step = next(item for item in envelope['steps'] if item['recipe'] == 'workspace_typescript')
    resource = {'version': 1, 'contract': RESOURCE_CONTRACT, 'setId': envelope['setId'],
        'runId': step['runId'], 'nonce': digest({'run': step['runId']}),
        'authoritySha256': envelope['identity']['authoritySha256']}
    staging = {'path': '/private/' + container_name(resource), 'parentDevice': 1,
               'parentInode': 2, 'device': 1, 'inode': 3}
    runtime = {'image': IMAGE, 'imageId': 'sha256:' + 'c' * 64,
               'seccompSha256': PROFILE_SHA256, 'bootstrapSha256': 'd' * 64}
    receipt = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1,
        'resourceIdentity': resource, 'status': 'failed', 'cleanupConfirmed': True,
        'stagingCleanupConfirmed': True, 'containerId': 'e' * 64, 'stagingIdentity': staging}
    pairs = [
        ('dispatch_planned', {'containerName': container_name(resource), 'labels': container_labels(resource),
            'image': IMAGE, 'stagingIntent': {key: staging[key] for key in ('path', 'parentDevice', 'parentInode')},
            'dependencyRootIdentity': {'device': 12, 'inode': 34},
            'snapshotSha256': envelope['source']['snapshotSha256'],
            **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256')},
            'stepId': step['id'], 'stepDefinitionSha256': step['definitionSha256'],
            'recipe': step['recipe'], 'checkKind': step['checkKind']}),
        ('staging_allocated', {'staging': staging}),
        ('container_create_planned', {'staging': staging, 'runtime': runtime}),
        ('container_observed', {'containerId': 'e' * 64, 'staging': staging, 'runtime': runtime}),
        ('container_start_planned', {'containerId': 'e' * 64, 'containerConfigSha256': 'f' * 64}),
        ('cleanup_observed', {'containerId': 'e' * 64, 'staging': staging,
                              'containerCleanupConfirmed': True, 'stagingCleanupConfirmed': True}),
        ('receipt', {'receipt': receipt, 'receiptSha256': digest(receipt)}),
    ]
    records = [{'version': 1, 'contract': EVENT_CONTRACT, 'kind': kind,
                'resource': resource, 'data': data} for kind, data in pairs]
    return {'records': deepcopy(records), 'resource': resource, 'run': step['runId'],
            'envelope': envelope, 'objective': obj}


def append(state, event):
    with ledger._transaction(state['conn'], write=True):
        answer = events.append_prepared_runtime_event(state['conn'], event)
        assert answer['committed'] is False
        assert state['conn'].in_transaction is True
    return answer


def append_prefix(state, stream, length):
    return [append(state, event) for event in stream['records'][:length]]


def count(state, suffix):
    return state['conn'].execute('SELECT count(*) FROM prepared_validation_' + suffix).fetchone()[0]


def test_complete_real_sql_sequence_roundtrips_text_and_keeps_claim_mirrors_unverified(state, stream):
    for sequence, event in enumerate(stream['records'], 1):
        response = append(state, event)
        assert response == {'runId': stream['run'], 'sequence': sequence,
                            'eventSha256': digest(event), 'committed': False}
        assert events.retained_runtime_events(state['conn'], stream['run']) == stream['records'][:sequence]
    assert {key: count(state, key) for key in ('dispatches', 'resources', 'cleanups', 'results')} == {
        'dispatches': 1, 'resources': 1, 'cleanups': 1, 'results': 1}
    row = state['conn'].execute('SELECT status,receipt_sha256,record,sha256 FROM prepared_validation_results').fetchone()
    reference = json.loads(row['record'])
    assert reference['status'] == row['status'] == 'failed'
    assert reference['receiptSha256'] == row['receipt_sha256'] == stream['records'][-1]['data']['receiptSha256']
    assert digest(reference) == row['sha256']
    assert reference['setId'] == stream['envelope']['setId']
    assert reference['snapshotSha256'] == stream['envelope']['source']['snapshotSha256']
    history = inspect_prepared_runtime_history(events.retained_runtime_events(state['conn'], stream['run']),
                                              resource_identity=stream['resource'])
    assert history['state'] == 'terminal_failed' and history['replayAllowed'] is False
    assert history['acceptanceAuthorized'] is False


def test_cleanup_only_first_container_observation_is_retained_before_composite_fk(state, stream):
    append_prefix(state, stream, 3)
    append(state, stream['records'][5])
    append(state, stream['records'][6])
    assert count(state, 'resources') == count(state, 'cleanups') == count(state, 'results') == 1
    record = state['conn'].execute('SELECT container_id FROM prepared_validation_resources').fetchone()
    assert record[0] == stream['records'][5]['data']['containerId']
    history = events.retained_runtime_events(state['conn'], stream['run'])
    assert [item['kind'] for item in history] == ['dispatch_planned', 'staging_allocated',
                                               'container_create_planned', 'cleanup_observed', 'receipt']
    assert inspect_prepared_runtime_history(history, resource_identity=stream['resource'])['state'] == 'terminal_failed'
    with pytest.raises(ValueError):
        append(state, stream['records'][4])


def test_cleanup_only_allocation_after_failed_observation_can_settle_without_container_claim(state, stream):
    append_prefix(state, stream, 1)
    cleanup = deepcopy(stream['records'][5])
    cleanup['data'].update(containerId=None, containerCleanupConfirmed=False)
    append(state, cleanup)
    terminal = deepcopy(stream['records'][6])
    terminal['data']['receipt'].update(status='blocked', containerId=None, cleanupConfirmed=False)
    terminal['data']['receiptSha256'] = digest(terminal['data']['receipt'])
    append(state, terminal)
    assert count(state, 'resources') == 0
    assert count(state, 'cleanups') == count(state, 'results') == 1
    history = inspect_prepared_runtime_history(events.retained_runtime_events(state['conn'], stream['run']),
                                              resource_identity=stream['resource'])
    assert history['containerState'] == 'not_attempted' and history['stagingState'] == 'cleanup_confirmed'
    assert state['conn'].execute('SELECT status FROM prepared_validation_results').fetchone()[0] == 'unknown'


@pytest.mark.parametrize('target', ['resources', 'cleanups', 'results'])
def test_mirror_failure_rolls_back_event_and_all_earlier_mirrors_in_the_transaction(state, stream, target):
    # Cleanup-only recovery exercises rollback of a newly inserted resource too.
    if target == 'resources':
        append_prefix(state, stream, 3)
        candidate = stream['records'][3]
    elif target == 'cleanups':
        append_prefix(state, stream, 3)
        candidate = stream['records'][5]
    else:
        append_prefix(state, stream, 6)
        candidate = stream['records'][6]
    before = {key: count(state, key) for key in ('runtime_events', 'resources', 'cleanups', 'results')}
    state['conn'].execute('CREATE TRIGGER injected_mirror_failure BEFORE INSERT ON prepared_validation_' + target +
                          " BEGIN SELECT RAISE(ABORT,'injected mirror persistence failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected mirror'):
        append(state, candidate)
    assert not state['conn'].in_transaction
    assert {key: count(state, key) for key in before} == before
    assert count(state, 'steps') == 2  # An interrupted operation never refunds either reserved unit.


def test_writer_requires_caller_transaction_and_does_not_commit_it(state, stream):
    conn = state['conn']
    with pytest.raises(ValueError, match='transaction'):
        events.append_prepared_runtime_event(conn, stream['records'][0])
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0
    conn.execute('BEGIN IMMEDIATE')
    answer = events.append_prepared_runtime_event(conn, stream['records'][0])
    assert answer['committed'] is False and conn.in_transaction
    conn.rollback()
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0
    assert count(state, 'steps') == 2


@pytest.mark.parametrize('action', ['replace', 'update', 'delete'])
def test_retained_events_cannot_be_replaced_updated_or_deleted(state, stream, action):
    append_prefix(state, stream, 2)
    conn = state['conn']
    before = events.retained_runtime_events(conn, stream['run'])
    with pytest.raises(sqlite3.IntegrityError):
        if action == 'replace':
            conn.execute('INSERT OR REPLACE INTO prepared_validation_runtime_events '
                'SELECT run_id,sequence,kind,record,sha256,created FROM prepared_validation_runtime_events WHERE sequence=1')
        elif action == 'update':
            conn.execute("UPDATE prepared_validation_runtime_events SET sha256=? WHERE sequence=1", ('0' * 64,))
        else:
            conn.execute('DELETE FROM prepared_validation_runtime_events WHERE sequence=1')
    assert events.retained_runtime_events(conn, stream['run']) == before


@pytest.mark.parametrize('index', [2, 3, 4, 6])
def test_out_of_order_first_events_cannot_create_dispatch_or_history(state, stream, index):
    with pytest.raises(ValueError):
        append(state, stream['records'][index])
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0


@pytest.mark.parametrize('mutation', ['authority', 'set', 'source', 'profile', 'step', 'dependency'])
def test_dispatch_is_bound_to_independently_reserved_metadata(state, stream, mutation):
    candidate = deepcopy(stream['records'][0])
    if mutation == 'authority':
        candidate['resource']['authoritySha256'] = '0' * 64
        candidate['data']['labels'] = container_labels(candidate['resource'])
    elif mutation == 'set':
        candidate['resource']['setId'] = 'another-set'
        candidate['data']['labels'] = container_labels(candidate['resource'])
    elif mutation == 'dependency':
        candidate['data']['dependencyRootIdentity']['inode'] += 1
    else:
        key = {'source': 'snapshotSha256', 'profile': 'profileSha256', 'step': 'stepDefinitionSha256'}[mutation]
        candidate['data'][key] = '0' * 64
    with pytest.raises(ValueError):
        append(state, candidate)
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0


def corrupt(conn, table, column, value):
    # Adversarial storage fixture only: ordinary APIs cannot modify these rows.
    conn.execute('DROP TRIGGER immutable_prepared_validation_' + table + '_update')
    conn.execute('PRAGMA ignore_check_constraints=ON')
    conn.execute('UPDATE prepared_validation_' + table + ' SET ' + column + '=?', (value,))
    conn.execute('PRAGMA ignore_check_constraints=OFF')


@pytest.mark.parametrize('target', ['envelope-hash', 'envelope-json', 'envelope-shape',
                                   'grant-hash', 'grant-json', 'binding-cross-reference'])
def test_tampered_retained_authority_and_envelope_fail_before_any_event(state, stream, target):
    conn = state['conn']
    if target == 'envelope-hash':
        corrupt(conn, 'sets', 'envelope_sha256', '0' * 64)
    elif target == 'envelope-json':
        # The generated identity rejects malformed JSON at the storage boundary.
        with pytest.raises(sqlite3.OperationalError, match='malformed JSON'):
            corrupt(conn, 'sets', 'envelope', '{malformed')
        assert count(state, 'dispatches') == count(state, 'runtime_events') == 0
        return
    elif target == 'envelope-shape':
        malformed = deepcopy(stream['envelope'])
        malformed['steps'][0]['definitionSha256'] = 'bad'
        corrupt(conn, 'sets', 'envelope', canonical(malformed))
        conn.execute('UPDATE prepared_validation_sets SET envelope_sha256=?', (digest(malformed),))
    elif target == 'grant-hash':
        corrupt(conn, 'bindings', 'grant_sha256', '0' * 64)
    elif target == 'grant-json':
        corrupt(conn, 'bindings', 'grant_record', '{malformed')
    else:
        original = json.loads(conn.execute('SELECT binding_record FROM prepared_validation_bindings').fetchone()[0])
        original['ownerGrantSha256'] = '0' * 64
        corrupt(conn, 'bindings', 'binding_record', canonical(original))
        conn.execute('UPDATE prepared_validation_bindings SET binding_sha256=?', (digest(original),))
    with pytest.raises(ValueError):
        append(state, stream['records'][0])
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0


@pytest.mark.parametrize('field', ['kind', 'sha256', 'record', 'sequence'])
def test_tampered_retained_event_text_kind_or_hash_is_rejected(state, stream, field):
    append_prefix(state, stream, 1)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_runtime_event_update')
    value = {'kind': 'receipt', 'sha256': '0' * 64, 'record': '{bad json', 'sequence': 2}[field]
    conn.execute('UPDATE prepared_validation_runtime_events SET ' + field + '=?', (value,))
    with pytest.raises(ValueError):
        events.retained_runtime_events(conn, stream['run'])
    with pytest.raises(ValueError):
        append(state, stream['records'][1])
    assert count(state, 'runtime_events') == 1


@pytest.mark.parametrize('bound', ['count', 'record-bytes', 'history-bytes'])
def test_retained_history_bounds_are_checked_before_decoding_or_loading_records(state, stream, monkeypatch, bound):
    from eidolon_cli import organization_project_workspace_resources as resources
    append_prefix(state, stream, 1)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_runtime_event_update')
    if bound == 'count':
        for sequence in range(2, events.MAX_RUNTIME_EVENTS + 2):
            conn.execute('INSERT INTO prepared_validation_runtime_events VALUES (?,?,?,?,?,0)',
                (stream['run'], sequence, 'untrusted', '{}', '0' * 64))
    elif bound == 'record-bytes':
        conn.execute('UPDATE prepared_validation_runtime_events SET record=CAST(zeroblob(?) AS TEXT)',
                     (events.MAX_RUNTIME_EVENT_BYTES + 1,))
    else:
        for sequence in range(2, 6):
            conn.execute('INSERT INTO prepared_validation_runtime_events VALUES (?,?,?,CAST(zeroblob(?) AS TEXT),?,0)',
                (stream['run'], sequence, 'untrusted', events.MAX_RUNTIME_EVENT_BYTES - 1, '0' * 64))
        conn.execute('UPDATE prepared_validation_runtime_events SET record=CAST(zeroblob(?) AS TEXT) WHERE sequence=1',
                     (events.MAX_RUNTIME_EVENT_BYTES - 1,))
    monkeypatch.setattr(resources, 'normalize_runtime_event', lambda value: pytest.fail('bounds must precede decoding'))
    with pytest.raises(ValueError, match='limits'):
        events.retained_runtime_events(conn, stream['run'])


def test_later_cleanup_does_not_rewrite_terminal_result_or_earlier_observations(state, stream):
    append_prefix(state, stream, 7)
    before = tuple(state['conn'].execute('SELECT * FROM prepared_validation_results').fetchone())
    later = deepcopy(stream['records'][5])
    later['data']['stagingCleanupConfirmed'] = False
    append(state, later)
    assert tuple(state['conn'].execute('SELECT * FROM prepared_validation_results').fetchone()) == before
    cleanups = state['conn'].execute('SELECT sequence,container_confirmed,staging_confirmed '
                                     'FROM prepared_validation_cleanups ORDER BY sequence').fetchall()
    assert [tuple(row) for row in cleanups] == [(1, 1, 1), (2, 1, 0)]
    history = inspect_prepared_runtime_history(events.retained_runtime_events(state['conn'], stream['run']),
                                              resource_identity=stream['resource'])
    assert history['state'] == 'terminal_failed' and history['stagingState'] == 'cleanup_unconfirmed'
    assert history['replayAllowed'] is False


def test_non_text_retained_record_fails_closed_as_validation_error(state, stream):
    append_prefix(state, stream, 1)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_runtime_event_update')
    conn.execute('UPDATE prepared_validation_runtime_events SET record=?', (sqlite3.Binary(b'{}'),))
    with pytest.raises(ValueError):
        events.retained_runtime_events(conn, stream['run'])


@pytest.mark.parametrize('limit', ['count', 'bytes'])
def test_append_checks_total_limits_before_mutating_history(state, stream, monkeypatch, limit):
    append_prefix(state, stream, 1)
    if limit == 'count':
        monkeypatch.setattr(events, 'MAX_RUNTIME_EVENTS', 1)
    else:
        size = len(canonical(stream['records'][0]).encode('utf-8'))
        monkeypatch.setattr(events, 'MAX_RUNTIME_HISTORY_BYTES', size + 1)
    with pytest.raises(ValueError, match='limit'):
        append(state, stream['records'][1])
    assert count(state, 'runtime_events') == 1


def test_internally_rehashed_sidecar_cannot_replace_the_authority_reserved_by_envelope(state, stream):
    conn = state['conn']
    row = conn.execute('SELECT grant_record,binding_record FROM prepared_validation_bindings').fetchone()
    grant, binding = json.loads(row[0]), json.loads(row[1])
    grant['revision'] += 1
    binding['ownerGrantSha256'] = digest(grant)
    conn.execute('DROP TRIGGER immutable_prepared_validation_bindings_update')
    conn.execute('UPDATE prepared_validation_bindings SET grant_record=?,grant_sha256=?,binding_record=?,binding_sha256=?',
                 (canonical(grant), digest(grant), canonical(binding), digest(binding)))
    with pytest.raises(ValueError):
        append(state, stream['records'][0])
    assert count(state, 'dispatches') == count(state, 'runtime_events') == 0

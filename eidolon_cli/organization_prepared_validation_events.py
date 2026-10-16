"""Ordered trusted-host runtime events for a reserved prepared-validation pair.

Only the backend orchestration calls this writer inside its own lease/authority
transaction. Event shape and hashes never authenticate externally supplied data.
The entire stream is retained; cleanup cannot rewrite a failed test result.
"""
import time
import uuid

from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_prepared_validation_ledger import _binding, _run_envelope, _require_write_transaction

MAX_RUNTIME_EVENTS = 128
MAX_RUNTIME_EVENT_BYTES = 4 * 1024**2
MAX_RUNTIME_HISTORY_BYTES = 16 * 1024**2
PREPARED_RUNTIME_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_runtime_events (
 run_id TEXT NOT NULL REFERENCES prepared_validation_dispatches(run_id),
 sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence>0),
 kind TEXT NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(run_id,sequence));
CREATE TRIGGER IF NOT EXISTS prepared_runtime_event_sequence
 BEFORE INSERT ON prepared_validation_runtime_events
 BEGIN
 SELECT CASE WHEN NEW.sequence<>(SELECT COALESCE(MAX(sequence),0)+1
 FROM prepared_validation_runtime_events WHERE run_id=NEW.run_id)
 THEN RAISE(ABORT,'Prepared runtime events require the next immutable sequence') END;
 END;
'''
for _operation in ('UPDATE', 'DELETE'):
    PREPARED_RUNTIME_SCHEMA += (
        f'CREATE TRIGGER IF NOT EXISTS immutable_prepared_runtime_event_{_operation.lower()} '
        f'BEFORE {_operation} ON prepared_validation_runtime_events '
        "BEGIN SELECT RAISE(ABORT,'Prepared runtime events are immutable'); END;\n")


def retained_runtime_events(conn, run_id):
    from eidolon_cli.organization_project_workspace_resources import normalize_runtime_event
    size = conn.execute('SELECT count(*),COALESCE(sum(length(CAST(record AS BLOB))),0),'
        'COALESCE(max(length(CAST(record AS BLOB))),0) FROM prepared_validation_runtime_events WHERE run_id=?',
        (run_id,)).fetchone()
    if (size[0] > MAX_RUNTIME_EVENTS or size[1] > MAX_RUNTIME_HISTORY_BYTES
            or size[2] > MAX_RUNTIME_EVENT_BYTES):
        raise ValueError('Prepared runtime history exceeds its retained count/byte limits')
    events = []
    for index, row in enumerate(conn.execute('SELECT sequence,kind,record,sha256 '
        'FROM prepared_validation_runtime_events WHERE run_id=? ORDER BY sequence', (run_id,)), 1):
        if type(row['record']) is not str:
            raise ValueError('Retained runtime event must be bounded JSON text')
        event = normalize_runtime_event(row['record'].encode('utf-8'))
        if (row['sequence'] != index or row['kind'] != event['kind']
                or digest(event) != row['sha256'] or event['resource']['runId'] != run_id):
            raise ValueError('Retained runtime event identity or hash changed')
        events.append(event)
    return events


def _reserved_event(conn, event):
    resource = event['resource']
    envelope = _run_envelope(conn, resource['runId'])
    if (resource['setId'] != envelope['setId']
            or resource['authoritySha256'] != envelope['identity']['authoritySha256']):
        raise ValueError('Runtime resource differs from its retained authority')
    grant, binding = _binding(conn, envelope['objectiveId'], envelope['projectId'])
    if (digest(grant) != envelope['ownerGrantSha256']
            or digest(binding) != envelope['objectiveBindingSha256']):
        raise ValueError('Prepared runtime event authority differs from its reserved binding')
    step = next((item for item in envelope['steps'] if item['runId'] == resource['runId']), None)
    if step is None:
        raise ValueError('Runtime step is missing from its retained envelope')
    if event['kind'] == 'dispatch_planned':
        expected = {'snapshotSha256': envelope['source']['snapshotSha256'],
            **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256')},
            'stepId': step['id'], 'stepDefinitionSha256': step['definitionSha256'],
            'recipe': step['recipe'], 'checkKind': step['checkKind']}
        if any(event['data'].get(key) != value for key, value in expected.items()):
            raise ValueError('Prepared dispatch differs from its reserved source/profile/step')
        dependencies = grant['dependencies']
        if event['data']['dependencyRootIdentity'] != {key: dependencies[key] for key in ('device', 'inode')}:
            raise ValueError('Prepared dependency directory differs from its reserved identity')
    return envelope, step


def _retain_reference(conn, envelope, step, event):
    receipt = event['data']['receipt']
    status = receipt.get('status')
    if status not in {'passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown'}:
        status = 'unknown'
    from eidolon_cli.organization_prepared_validation_contract import versioned_contract_fields
    record = {**versioned_contract_fields(envelope['version'], 'prepared-validation-result-reference'),
        'setId': envelope['setId'], 'generation': envelope['generation'], 'runId': step['runId'],
        'stepId': step['id'], **{key: step[key] for key in ('definitionSha256', 'recipe', 'checkKind')},
        'snapshotSha256': envelope['source']['snapshotSha256'],
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
        'receiptSha256': event['data']['receiptSha256'], 'status': status,
        'cleanupConfirmed': receipt.get('cleanupConfirmed') is True,
        'stagingCleanupConfirmed': receipt.get('stagingCleanupConfirmed') is True}
    conn.execute('INSERT INTO prepared_validation_results VALUES (?,?,?,?,?,?)',
        (step['runId'], status, record['receiptSha256'], canonical(record), digest(record), time.time()))


def append_prepared_runtime_event(conn, value):
    """Append within the trusted caller's transaction; never commit it implicitly."""
    from eidolon_cli.organization_project_workspace_resources import normalize_runtime_event
    from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history
    _require_write_transaction(conn)
    event = normalize_runtime_event(value)
    envelope, step = _reserved_event(conn, event)
    run_id, resource = step['runId'], event['resource']
    history = retained_runtime_events(conn, run_id)
    if len(history) >= MAX_RUNTIME_EVENTS:
        raise ValueError('Prepared runtime event limit reached')
    encoded = canonical(event)
    if sum(len(canonical(item).encode()) for item in history) + len(encoded.encode()) > MAX_RUNTIME_HISTORY_BYTES:
        raise ValueError('Prepared runtime event byte limit reached')
    inspect_prepared_runtime_history([*history, event], resource_identity=resource)
    kind, data = event['kind'], event['data']
    if not history:
        conn.execute('INSERT INTO prepared_validation_dispatches VALUES (?,?,?,?,?)',
            (run_id, resource['nonce'], data['containerName'], data['image'], time.time()))
    conn.execute('INSERT INTO prepared_validation_runtime_events VALUES (?,?,?,?,?,?)',
        (run_id, len(history) + 1, kind, encoded, digest(event), time.time()))
    if kind in {'container_observed', 'cleanup_observed'} and data['containerId'] is not None:
        retained = conn.execute('SELECT container_id FROM prepared_validation_resources WHERE run_id=?',
                                (run_id,)).fetchone()
        if retained is None:
            conn.execute('INSERT INTO prepared_validation_resources VALUES (?,?,?)',
                         (run_id, data['containerId'], time.time()))
        elif retained[0] != data['containerId']:
            raise ValueError('Cleanup observation differs from its immutable resource')
    if kind == 'cleanup_observed':
        sequence = conn.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM prepared_validation_cleanups WHERE run_id=?',
                                (run_id,)).fetchone()[0]
        conn.execute('INSERT INTO prepared_validation_cleanups VALUES (?,?,?,?,?,?,?,?,?)',
            ('cleanup_' + uuid.uuid4().hex, run_id, sequence, resource['nonce'],
             'eidolon-prepared-' + resource['nonce'], data['containerId'],
             int(data['containerCleanupConfirmed']), int(data['stagingCleanupConfirmed']), time.time()))
    if kind == 'receipt':
        _retain_reference(conn, envelope, step, event)
    return {'runId': run_id, 'sequence': len(history) + 1, 'eventSha256': digest(event), 'committed': False}

"""Revisioned success criteria. Model assertions never substitute for runtime proof."""
from __future__ import annotations

import hashlib
import json
import re
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS objective_contract_revisions (
 objective_id TEXT NOT NULL REFERENCES objectives(id), revision INTEGER NOT NULL,
 record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(objective_id,revision));
CREATE TABLE IF NOT EXISTS objective_contract_acceptances (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), contract_sha256 TEXT NOT NULL,
 evidence_bindings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objective_human_judgments (
 idempotency_key TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id),
 criterion_id TEXT NOT NULL, contract_sha256 TEXT NOT NULL, deliverable_sha256 TEXT NOT NULL,
 approved INTEGER NOT NULL CHECK(approved IN (0,1)), reason TEXT NOT NULL,
 input_sha256 TEXT NOT NULL, created REAL NOT NULL);
'''
for table in ('objective_contract_revisions', 'objective_contract_acceptances', 'objective_human_judgments'):
    for operation in ('UPDATE', 'DELETE'):
        SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} '
                   f'BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '
                   "'Immutable outcome contract record'); END;\n")

KINDS = {'deliverable', 'authorized_check', 'source_integration', 'human_judgment'}
CHECKS = {'project_tests', 'managed_validation', 'project_typecheck', 'project_build'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def definitions(value, description):
    from eidolon_cli.organization_store import _text
    if value is None:
        value = [description]
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError('Acceptance criteria must contain 1–12 nonempty strings or typed criteria')
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            item = {'id': f'criterion-{index + 1}', 'text': item, 'kind': 'deliverable', 'legacy': True}
        else:
            if not isinstance(item, dict) or set(item) - {'id', 'text', 'kind', 'checkKind', 'projectId'}:
                raise ValueError('Unsupported typed criterion fields')
            item = dict(item, legacy=False)
            if not isinstance(item.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', item['id']):
                raise ValueError('Typed criterion requires a stable canonical id')
        item['text'] = _text(item.get('text'), 'Acceptance criterion', 2000)
        if not isinstance(item.get('kind'), str) or item['kind'] not in KINDS:
            raise ValueError('Unsupported criterion kind')
        if item['kind'] == 'authorized_check':
            if not isinstance(item.get('checkKind'), str) or item['checkKind'] not in CHECKS:
                raise ValueError('Authorized check requires an explicit supported checkKind')
        elif 'checkKind' in item:
            raise ValueError('Only authorized_check criteria select checkKind')
        if 'projectId' in item:
            if item['kind'] not in {'authorized_check', 'source_integration'}:
                raise ValueError('Only project evidence criteria select projectId')
            item['projectId'] = _text(item['projectId'], 'Criterion projectId', 128)
            if item.get('checkKind') == 'managed_validation':
                raise ValueError('Managed validation is an aggregate check; projectId is unsupported')
        result.append(item)
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('Criterion ids must be unique')
    return result


def required_by(defs):
    return list(dict.fromkeys(item['checkKind'] if item['kind'] == 'authorized_check' else 'source_integration'
                             for item in defs if item['kind'] in {'authorized_check', 'source_integration'}))


CONTRACT_TABLES = frozenset({'objective_contract_revisions', 'objective_contract_acceptances', 'objective_human_judgments'})


def contract_view(conn, objective_id):
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & CONTRACT_TABLES
    if not present:
        return None  # Passive legacy observation must not initialize contract storage.
    if present != CONTRACT_TABLES:
        raise ValueError('Outcome contract storage is incomplete')
    row = conn.execute('SELECT * FROM objective_contract_revisions WHERE objective_id=? ORDER BY revision DESC LIMIT 1',
                       (objective_id,)).fetchone()
    if row is None:
        return None
    record = json.loads(row['record'])
    if digest(record) != row['sha256']:
        raise ValueError('Outcome contract is changed or corrupt')
    return dict(record, sha256=row['sha256'])


def write_contract(conn, objective_id, defs):
    old = contract_view(conn, objective_id)
    old_items = {}
    for historical in conn.execute('SELECT record FROM objective_contract_revisions WHERE objective_id=? ORDER BY revision', (objective_id,)):
        old_items.update({item['id']: item for item in json.loads(historical['record'])['criteria']})
    criteria = []
    for item in defs:
        previous = old_items.get(item['id'])
        same = previous and {key: value for key, value in previous.items() if key != 'revision'} == item
        criteria.append(dict(item, revision=previous['revision'] if same else (previous['revision'] + 1 if previous else 1)))
    control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
    objective = conn.execute('SELECT description FROM objectives WHERE id=?', (objective_id,)).fetchone()
    record = {'schemaVersion': 1, 'objectiveId': objective_id, 'revision': old['revision'] + 1 if old else 1,
              'criteria': criteria, 'scope': control['amended_scope'] or objective['description'],
              'deliveryMode': control['delivery_mode'], 'requiredChecks': json.loads(control['required_checks'])}
    if old and {k: v for k, v in old.items() if k not in {'revision', 'sha256'}} == {k: v for k, v in record.items() if k != 'revision'}:
        return old
    conn.execute('INSERT INTO objective_contract_revisions VALUES (?,?,?,?,?)',
                 (objective_id, record['revision'], json.dumps(record, sort_keys=True), digest(record), time.time()))
    return dict(record, sha256=digest(record))


def migrate_contracts(conn):
    for row in conn.execute('SELECT c.* FROM objective_control c WHERE NOT EXISTS '
                            '(SELECT 1 FROM objective_contract_revisions r WHERE r.objective_id=c.objective_id)').fetchall():
        write_contract(conn, row['objective_id'], definitions(json.loads(row['criteria']), ''))


def has_typed(contract):
    return contract is not None and any(not item['legacy'] for item in contract['criteria'])


def bind_contract(conn, objective_id, payload):
    contract = contract_view(conn, objective_id)
    if has_typed(contract):
        payload['outcomeContractSha256'] = contract['sha256']


def verify_contract_binding(conn, objective_id, payload):
    contract = contract_view(conn, objective_id)
    if (has_typed(contract) or 'outcomeContractSha256' in payload) and (contract is None or payload.get('outcomeContractSha256') != contract['sha256']):
        raise ValueError('Outcome contract revision is stale or missing')
    return contract


def verify_criteria(store, conn, request, results):
    contract = verify_contract_binding(conn, request['objective_id'], json.loads(request['payload']))
    if not has_typed(contract):
        return
    payload = json.loads(request['payload'])
    bindings = []
    for criterion, result in zip(contract['criteria'], results):
        if criterion['legacy']:
            continue
        if result.get('criterionId') != criterion['id'] or result.get('criterionRevision') != criterion['revision']:
            raise ValueError('Typed judgments must identify the exact criterion id and revision')
        if not result['satisfied']:
            continue
        ids = result['evidenceIds']
        kind = criterion['kind']
        valid = set()
        extra_proof = {}
        if kind in {'deliverable', 'human_judgment'}:
            final = conn.execute('SELECT d.* FROM objective_deliverables d JOIN objective_control c '
                                 'ON c.deliverable_id=d.id WHERE c.objective_id=?', (request['objective_id'],)).fetchone()
            if final:
                valid.add(final['id'])
            if kind == 'human_judgment':
                judgment = conn.execute('SELECT * FROM objective_human_judgments WHERE objective_id=? AND criterion_id=? '
                    'AND contract_sha256=? AND deliverable_sha256=? ORDER BY created DESC,rowid DESC LIMIT 1',
                    (request['objective_id'], criterion['id'], contract['sha256'], final['sha256'] if final else '')).fetchone()
                if judgment is None or judgment['approved'] != 1:
                    raise ValueError('Explicit owner judgment of the current outcome is required')
        elif kind == 'authorized_check':
            check = criterion['checkKind']
            if check == 'project_build':
                raise ValueError(f'{check} evidence is unsupported; compilation is not a passing test suite')
            if check == 'project_typecheck':
                runs = store.verify_project_check_runs(conn, request['objective_id'], check, criterion.get('projectId'))
                valid.update(artifact['id'] for _, artifact in runs)
            elif check == 'project_tests':
                runs = store.verify_project_test_runs(conn, request['objective_id'])
                for _, artifact in runs:
                    body = json.loads(artifact['content'])
                    if criterion.get('projectId') is None or body.get('projectId') == criterion['projectId']:
                        valid.add(artifact['id'])
            else:
                from eidolon_cli.organization_project_workspace import project_validation_view
                store.verify_project_objective_acceptance(conn, request['objective_id'], require_source=False, require_validation=True)
                proof = project_validation_view(conn, request['objective_id'])
                if proof and criterion.get('projectId') is None:
                    valid.add(proof['id'])
        else:
            from eidolon_cli.organization_project_execution import _run_project_id, _run_row
            for artifact in store.current_source_evidences(conn, request['objective_id']):
                source = conn.execute('SELECT run_id FROM project_source_receipts WHERE request_id=?', (artifact['id'].removeprefix('project_source_'),)).fetchone()
                project_id = _run_project_id(conn, _run_row(conn, source['run_id'])) if source else None
                if criterion.get('projectId') is None or project_id == criterion['projectId']:
                    valid.add(artifact['id'])
            if not valid and criterion.get('projectId') is None:
                from eidolon_cli.organization_project_workspace import final_source_manifest, project_validation_view
                from tools.organization_file_read import organization_file_read_scope
                store.verify_project_objective_acceptance(conn, request['objective_id'], require_source=True)
                manifest, _, _, checked = final_source_manifest(conn, request['objective_id'])
                if not store._verified_final_source(conn, request['objective_id'], manifest, checked):
                    raise ValueError('Typed source integration requires verified source evidence')
                with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
                    for item in manifest:
                        store._verify_root(conn, request['objective_id'], item['path'])
                        if scope.read_exact_source(item['path'])['sha256'] != item['sha256']:
                            raise ValueError('Typed source handoff bytes have changed')
                receipt = conn.execute('SELECT h.result_sha256 FROM project_handoff_receipts h JOIN edit_proposals p '
                    'ON p.id=h.proposal_id WHERE p.objective_id=? ORDER BY h.created DESC,h.rowid DESC LIMIT 1',
                    (request['objective_id'],)).fetchone()
                proof = project_validation_view(conn, request['objective_id'])
                if receipt and proof:
                    valid.add(proof['id'])
                    extra_proof = {'sourceHandoffSha256': receipt['result_sha256'], 'sourceManifestSha256': digest(manifest)}
        if not valid or not valid.issubset(ids):
            raise ValueError('Typed criterion lacks exact current evidence of its required kind')
        bindings.append({'criterionId': criterion['id'], 'criterionRevision': criterion['revision'],
                         'kind': kind, **extra_proof, 'evidenceHashes': {key: payload['evidenceHashes'][key] for key in ids}})
    conn.execute('INSERT INTO objective_contract_acceptances VALUES (?,?,?)',
                 (request['id'], contract['sha256'], json.dumps(bindings, sort_keys=True)))


def record_human_judgment(store, objective_id, *, criterion_id, contract_sha256, deliverable_sha256,
                          approved, reason, idempotency_key, confirmed):
    """Owner transport only. Does not grant execution or accept the objective itself."""
    from eidolon_cli.organization_store import _text
    objective_id = _text(objective_id, 'Objective id', 128)
    criterion_id = _text(criterion_id, 'Criterion id', 64)
    reason = _text(reason, 'Human judgment reason', 2000)
    key = _text(idempotency_key, 'Idempotency key', 128)
    if confirmed is not True or type(approved) is not bool:
        raise ValueError('Human judgment requires explicit confirmation and a boolean decision')
    for value in (contract_sha256, deliverable_sha256):
        if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
            raise ValueError('Human judgment requires exact contract and deliverable fingerprints')
    input_hash = digest([objective_id, criterion_id, contract_sha256, deliverable_sha256, approved, reason])
    with store._write() as conn:
        store._require_current_policy(conn)
        previous = conn.execute('SELECT input_sha256 FROM objective_human_judgments WHERE idempotency_key=?', (key,)).fetchone()
        if previous:
            if previous['input_sha256'] != input_hash:
                raise ValueError('Human judgment idempotency key belongs to different input')
            return {'recorded': False, 'idempotencyKey': key}
        contract = contract_view(conn, objective_id)
        if contract is None or contract['sha256'] != contract_sha256:
            raise ValueError('Human judgment contract is stale')
        criterion = next((item for item in contract['criteria'] if item['id'] == criterion_id), None)
        if criterion is None or criterion['kind'] != 'human_judgment':
            raise ValueError('Human judgment requires an explicit human_judgment criterion')
        control = conn.execute('SELECT c.*,o.cancelled FROM objective_control c JOIN objectives o ON o.id=c.objective_id '
                               'WHERE c.objective_id=?', (objective_id,)).fetchone()
        final = conn.execute('SELECT * FROM objective_deliverables WHERE id=?', (control['deliverable_id'],)).fetchone()
        if (control['cancelled'] or control['status'] != 'reviewing' or final is None or final['round'] != control['round']
                or final['sha256'] != deliverable_sha256 or hashlib.sha256(final['content'].encode()).hexdigest() != deliverable_sha256):
            raise ValueError('Human judgment deliverable is stale, changed or not awaiting review')
        conn.execute('INSERT INTO objective_human_judgments VALUES (?,?,?,?,?,?,?,?,?)',
                     (key, objective_id, criterion_id, contract_sha256, deliverable_sha256, int(approved), reason, input_hash, time.time()))
        # Wake only the acceptance request explicitly held by this contract.
        for request in conn.execute("SELECT * FROM requests WHERE objective_id=? AND type='request.accept' "
                                    "AND status='pending_intervention' AND reason='Awaiting explicit human outcome judgment'", (objective_id,)).fetchall():
            if human_judgments_ready(conn, objective_id):
                conn.execute("UPDATE requests SET status='queued',reason=NULL WHERE id=?", (request['id'],))
        store._event(conn, objective_id, 'Owner recorded a judgment of an exact outcome criterion.', 'review', 'owner')
    return {'recorded': True, 'idempotencyKey': key}


def human_judgments_ready(conn, objective_id):
    contract = contract_view(conn, objective_id)
    if contract is None:
        return True
    final = conn.execute('SELECT d.sha256 FROM objective_deliverables d JOIN objective_control c ON c.deliverable_id=d.id '
                         'WHERE c.objective_id=?', (objective_id,)).fetchone()
    for criterion in contract['criteria']:
        if criterion['kind'] != 'human_judgment':
            continue
        if final is None or conn.execute('SELECT 1 FROM objective_human_judgments WHERE objective_id=? AND criterion_id=? '
            'AND contract_sha256=? AND deliverable_sha256=?', (objective_id, criterion['id'], contract['sha256'], final['sha256'])).fetchone() is None:
            return False
    return True


def human_judgments_view(conn, objective_id):
    contract = contract_view(conn, objective_id)
    final = conn.execute('SELECT d.sha256 FROM objective_deliverables d JOIN objective_control c ON c.deliverable_id=d.id '
                         'WHERE c.objective_id=?', (objective_id,)).fetchone()
    if contract is None or final is None:
        return []
    return [{'criterionId': row['criterion_id'], 'approved': bool(row['approved']), 'reason': row['reason'],
             'contractSha256': row['contract_sha256'], 'deliverableSha256': row['deliverable_sha256']}
            for row in conn.execute('SELECT * FROM objective_human_judgments WHERE objective_id=? AND contract_sha256=? '
                'AND deliverable_sha256=? ORDER BY created,rowid', (objective_id, contract['sha256'], final['sha256']))]

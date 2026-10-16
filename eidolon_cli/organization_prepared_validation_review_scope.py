"""Explicit bounded model-review scope, separate from complete execution inputs."""
import hashlib
import json

from eidolon_cli.organization_project_validation import canonical, digest

REVIEW_SCOPE_TEXT = ('Review these complete selected tests/configurations and the linked independently reviewed source work. '
    'Complete snapshot identity is verified by the backend; no claim is made that every snapshot byte is model-reviewed.')

REVIEW_SCOPE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_review_scopes (
 set_id TEXT PRIMARY KEY REFERENCES prepared_validation_sets(id), id TEXT NOT NULL UNIQUE,
 objective_id TEXT NOT NULL REFERENCES objectives(id), content TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS prepared_review_scope_no_replace BEFORE INSERT ON prepared_validation_review_scopes
 WHEN EXISTS(SELECT 1 FROM prepared_validation_review_scopes WHERE set_id=NEW.set_id OR id=NEW.id)
 BEGIN SELECT RAISE(ABORT,'Prepared review scope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_review_scope_no_update BEFORE UPDATE ON prepared_validation_review_scopes
 BEGIN SELECT RAISE(ABORT,'Prepared review scope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_review_scope_no_delete BEFORE DELETE ON prepared_validation_review_scopes
 BEGIN SELECT RAISE(ABORT,'Prepared review scope is immutable'); END;
'''


def _uncached_review_scope_artifact(conn, identifier):
    if not isinstance(identifier, str) or not identifier.startswith('prepared_scope_'):
        return None
    from eidolon_cli.organization_prepared_validation_finish import observed_prepared_artifact
    observed = observed_prepared_artifact(conn, identifier)
    if observed is not None:
        return observed
    from eidolon_cli.organization_prepared_validation_ledger import _bounded_text
    row = conn.execute(f'SELECT {_bounded_text("id", 128)} AS id,{_bounded_text("set_id", 128)} AS set_id,'
        f'{_bounded_text("objective_id", 128)} AS objective_id,{_bounded_text("content", 100000)} AS content,'
        f'{_bounded_text("sha256", 64)} AS sha256 '
        'FROM prepared_validation_review_scopes WHERE id=?', (identifier,)).fetchone()
    if row is None:
        return None
    if any(type(row[key]) is not str for key in ('id', 'set_id', 'objective_id', 'sha256')):
        raise ValueError('Prepared scope scalar identity is missing or oversized')
    if type(row['content']) is not str or hashlib.sha256(row['content'].encode()).hexdigest() != row['sha256']:
        raise ValueError('Prepared review scope is missing, oversized or changed')
    data = json.loads(row['content'])
    fields = {'version', 'contract', 'artifactId', 'objectiveId', 'projectId', 'setId', 'source',
              'profileSha256', 'bundleSha256', 'reviewedSourceEvidence', 'testAndConfigurationFiles',
              'scope', 'executionOnlyFilesDisclosed'}
    if (type(data) is not dict or set(data) != fields or data['version'] != 1
            or type(data['version']) is not int or data['scope'] != REVIEW_SCOPE_TEXT
            or data['contract'] != 'prepared-validation-review-scope-v1'
            or data['objectiveId'] != row['objective_id'] or data['artifactId'] != identifier
            or data['setId'] != row['set_id'] or data['executionOnlyFilesDisclosed'] is not False
            or identifier != 'prepared_scope_' + hashlib.sha256(row['set_id'].encode()).hexdigest()):
        raise ValueError('Prepared review scope has mixed objective identity')
    from eidolon_cli.organization_prepared_validation_ledger import _binding, _checked_json
    from eidolon_cli.organization_prepared_validation_contract import _envelope_shape
    retained = conn.execute(f'SELECT {_bounded_text("envelope", 131072)},{_bounded_text("envelope_sha256", 64)} '
                            'FROM prepared_validation_sets WHERE id=?', (row['set_id'],)).fetchone()
    if retained is None:
        raise ValueError('Prepared review scope lacks its reserved set')
    envelope = _envelope_shape(_checked_json(*retained))
    from eidolon_cli.organization_prepared_validation_verification import _scalar_bounds
    identity = (envelope['objectiveId'], envelope['projectId'])
    _scalar_bounds(conn, 'prepared_validation_bindings', 'objective_id=? AND project_id=?',
                   identity, {'grant_sha256': 64, 'binding_sha256': 64})
    _scalar_bounds(conn, 'objective_projects', 'objective_id=? AND project_id=?',
                   identity, {'binding': 65536})
    grant, binding = _binding(conn, *identity)
    if (data['objectiveId'] != envelope['objectiveId'] or data['projectId'] != envelope['projectId']
            or data['source'] != envelope['source']
            or data['profileSha256'] != envelope['identity']['profileSha256']
            or data['bundleSha256'] != envelope['identity']['bundleSha256']
            or binding['ownerGrantSha256'] != envelope['ownerGrantSha256']
            or digest(binding) != envelope['objectiveBindingSha256']):
        raise ValueError('Prepared review scope differs from its exact reserved authority')
    expected = {step['config'] for step in grant['profile']['steps']}
    expected.update(path for step in grant['profile']['steps'] for path in step['testFiles'])
    bodies = data['testAndConfigurationFiles']
    if (type(bodies) is not list or len(bodies) != len(expected)
            or any(type(item) is not dict or set(item) != {'path', 'sha256', 'content'} for item in bodies)
            or {item['path'] for item in bodies} != expected):
        raise ValueError('Prepared review scope omits or substitutes required test/configuration evidence')
    for item in bodies:
        file = conn.execute(f'SELECT {_bounded_text("sha256", 64)},execution_only,CASE WHEN length(content)<=100000 THEN content END '
                            'FROM prepared_validation_files WHERE set_id=? AND path=?',
                            (row['set_id'], item['path'])).fetchone()
        if (file is None or file['execution_only'] != 0 or type(item['content']) is not str
                or item['content'].encode() != file[2] or item['sha256'] != file[0]
                or hashlib.sha256(item['content'].encode()).hexdigest() != item['sha256']):
            raise ValueError('Prepared review file differs from its exact retained source bytes')
    hashes = data['reviewedSourceEvidence']
    if type(hashes) is not dict or not 1 <= len(hashes) <= 1000:
        raise ValueError('Prepared scope requires bounded reviewed source evidence')
    for evidence_id, sha256 in hashes.items():
        source = conn.execute(f'SELECT {_bounded_text("sha256", 64)},{_bounded_text("content", 1000000)} FROM evidence WHERE id=? AND objective_id=?',
                              (evidence_id, row['objective_id'])).fetchone()
        if (source is None or type(source[1]) is not str or source[0] != sha256
                or hashlib.sha256(source[1].encode()).hexdigest() != sha256):
            raise ValueError('Prepared scope source evidence differs from its retained artifact')
        task = conn.execute(f'SELECT {_bounded_text("t.id", 128)} AS id,{_bounded_text("t.author_id", 128)} AS author_id,'
            f'{_bounded_text("t.status", 32)} AS status,{_bounded_text("t.type", 64)} AS type FROM tasks t JOIN evidence e ON e.task_id=t.id '
            'JOIN task_projects p ON p.task_id=t.id WHERE e.id=? AND t.objective_id=? AND p.project_id=?',
            (evidence_id, row['objective_id'], data['projectId'])).fetchone()
        if (task is None or type(task['id']) is not str or type(task['author_id']) is not str
                or task['type'] not in {'work.inspect', 'work.edit'} or task['status'] != 'completed'):
            raise ValueError('Prepared scope source evidence is not completed work in its exact project')
        proof = conn.execute(f'SELECT {_bounded_text("r.agent_id", 128)},{_bounded_text("r.evidence_ids", 131072)},'
            f'{_bounded_text("q.payload", 262144)},{_bounded_text("q.status", 32)},'
            f'{_bounded_text("q.type", 64)},{_bounded_text("q.objective_id", 128)},'
            f'{_bounded_text("q.task_id", 128)},{_bounded_text("q.agent_id", 128)} FROM reviews r JOIN requests q ON q.id=r.request_id '
            'WHERE r.task_id=? AND r.approved=1 ORDER BY r.created DESC LIMIT 1', (task['id'],)).fetchone()
        if (proof is None or type(proof[0]) is not str or proof[0] == task['author_id'] or proof[3] != 'completed'
                or tuple(proof[4:]) != ('request.review', row['objective_id'], task['id'], proof[0])
                or type(proof[1]) is not str or type(proof[2]) is not str
                or evidence_id not in json.loads(proof[1])
                or json.loads(proof[2]).get('evidenceHashes', {}).get(evidence_id) != sha256):
            raise ValueError('Prepared scope source evidence lacks exact independent review')
    return {'id': identifier, 'objectiveId': row['objective_id'], 'sha256': row['sha256'],
            'content': row['content'], 'summary': 'Exact selected test/configuration review scope; full snapshot identity is separate',
            'kind': 'prepared_validation_review_scope', 'taskId': None, 'toolReceipts': []}


def reviewed_source_evidence(store, conn, objective_id, project_id, snapshot):
    """Require existing completed independent file-work evidence, never self-report."""
    from eidolon_cli.organization_acceptance import current_tasks
    from eidolon_cli.organization_projects import task_project
    identifiers = {task['id'] for task in current_tasks(conn, objective_id)
                   if task['type'] in {'work.inspect', 'work.edit'}
                   and (task_project(conn, task['id']) or {}).get('id') == project_id}
    identifiers.update(row[0] for row in conn.execute('SELECT DISTINCT p.task_id FROM edit_proposals p '
        'JOIN edit_applications a ON a.proposal_id=p.id JOIN task_projects t ON t.task_id=p.task_id '
        'WHERE p.objective_id=? AND t.project_id=?', (objective_id, project_id)))
    if not identifiers:
        raise ValueError('Prepared validation requires independently reviewed source work')
    if len(identifiers) > 1000:
        raise ValueError('Prepared source review exceeds its task history bound')
    from eidolon_cli.organization_prepared_validation_ledger import _bounded_text
    hashes = {}
    for task_id in sorted(identifiers):
        task = conn.execute(f'SELECT {_bounded_text("status", 32)} AS status,{_bounded_text("author_id", 128)} AS author_id FROM tasks WHERE id=? AND objective_id=?',
                            (task_id, objective_id)).fetchone()
        row = conn.execute(f'SELECT {_bounded_text("id", 128)} AS id,{_bounded_text("content", 1000000)} AS content,'
                           f'{_bounded_text("sha256", 64)} AS sha256 FROM evidence WHERE task_id=? AND objective_id=? ORDER BY created DESC,id DESC LIMIT 1',
                            (task_id, objective_id)).fetchone()
        if (task is None or task['status'] != 'completed' or type(task['author_id']) is not str or row is None
                or any(type(row[key]) is not str for key in ('id', 'content', 'sha256'))
                or hashlib.sha256(row['content'].encode()).hexdigest() != row['sha256']):
            raise ValueError('Prepared source work is incomplete or its evidence changed')
        proof = conn.execute('SELECT r.*,q.payload,q.status,q.type AS request_type,q.objective_id AS request_objective,'
                             'q.task_id AS request_task,q.agent_id AS request_agent FROM reviews r JOIN requests q ON q.id=r.request_id '
                             'WHERE r.task_id=? AND r.approved=1 ORDER BY r.created DESC LIMIT 1', (task_id,)).fetchone()
        if (proof is None or proof['status'] != 'completed' or proof['agent_id'] == task['author_id']
                or (proof['request_type'], proof['request_objective'], proof['request_task'], proof['request_agent'])
                   != ('request.review', objective_id, task_id, proof['agent_id'])
                or row['id'] not in json.loads(proof['evidence_ids'])
                or json.loads(proof['payload']).get('evidenceHashes', {}).get(row['id']) != row['sha256']):
            raise ValueError('Prepared source evidence lacks exact independent approval')
        store._verify_tool_evidence(conn, row['id'], require_read=True)
        _bind_read_observations(conn, row['id'], snapshot)
        hashes[row['id']] = row['sha256']
    return hashes


def _bind_read_observations(conn, evidence_id, snapshot):
    """Bind inspected text to the immutable original; never silently accept stale reads.

    Older unmanaged readers expose complete numbered text, not a byte hash. Those
    observations require an entire untruncated file with matching text/size/lines.
    Managed readers bind exact source SHA and a verified revision in the lineage.
    """
    from eidolon_cli.organization_receipts import evidence_receipts
    from eidolon_cli.organization_tool_executor import OrganizationToolExecution
    original = {item.path: item for item in snapshot.original.files}
    effective = {item.path: item for item in snapshot.effective.files}
    matched = 0
    for receipt in evidence_receipts(conn, evidence_id):
        if not OrganizationToolExecution._successful_receipt(receipt):
            continue
        path = receipt['arguments'].get('path')
        prefix = snapshot.original.root_alias + '/'
        if not isinstance(path, str) or not path.startswith(prefix):
            raise ValueError('Prepared source review includes an observation outside its exact project')
        relative = path[len(prefix):]
        source = original.get(relative)
        if source is None or source.execution_only or effective[relative].execution_only:
            raise ValueError('Prepared source review observation is outside the disclosed execution selection')
        result = json.loads(receipt['result'])
        observed_hash = result.get('sourceSha256')
        if observed_hash is not None:
            allowed = {source.sha256}
            allowed.update(item.sha256 for item in snapshot.managed_revisions if item.path == relative)
            if observed_hash not in allowed:
                raise ValueError('Managed source read no longer matches retained reviewed source lineage')
        else:
            text = source.content.decode('utf-8')
            lines = text.splitlines()
            expected = '\n'.join(f'{number}|{line}' for number, line in enumerate(lines, 1))
            if (receipt['arguments'].get('offset', 1) != 1 or result.get('truncated') is not False
                    or result.get('file_size') != len(source.content) or result.get('total_lines') != len(lines)
                    or result['content'] != expected):
                raise ValueError('Prepared inspection must be a current complete text observation; reinspect stale or partial source')
        matched += 1
    if not matched:
        raise ValueError('Prepared source review requires a current bound source read')


def retain_review_scope(store, conn, envelope, snapshot, authority):
    from eidolon_cli.organization_prepared_validation_ledger import _require_write_transaction
    from eidolon_cli.organization_project_snapshot_bundle import _check_no_secrets
    _require_write_transaction(conn)
    grant = authority['contract']
    hashes = reviewed_source_evidence(store, conn, envelope['objectiveId'], envelope['projectId'], snapshot)
    selected = {step['config'] for step in grant['profile']['steps']}
    selected.update(path for step in grant['profile']['steps'] for path in step['testFiles'])
    files = {item.path: item for item in snapshot.effective.files}
    bodies = []
    for path in sorted(selected):
        item = files.get(path)
        if item is None or item.execution_only:
            raise ValueError('Essential test/configuration review input is unavailable for model disclosure: ' + path)
        _check_no_secrets(item.content)
        try:
            content = item.content.decode('utf-8')
        except UnicodeError as error:
            raise ValueError('Essential test/configuration review input is not UTF-8: ' + path) from error
        bodies.append({'path': path, 'sha256': item.sha256, 'content': content})
    identifier = 'prepared_scope_' + hashlib.sha256(envelope['setId'].encode()).hexdigest()
    value = {'version': 1, 'contract': 'prepared-validation-review-scope-v1', 'artifactId': identifier,
        'objectiveId': envelope['objectiveId'], 'projectId': envelope['projectId'], 'setId': envelope['setId'],
        'source': envelope['source'], 'profileSha256': envelope['identity']['profileSha256'],
        'bundleSha256': envelope['identity']['bundleSha256'], 'reviewedSourceEvidence': hashes,
        'testAndConfigurationFiles': bodies,
        'scope': REVIEW_SCOPE_TEXT,
        'executionOnlyFilesDisclosed': False}
    content = canonical(value)
    if len(content) > store.settings.max_tool_result_chars or len(content.encode()) > 100000:
        raise ValueError('Essential prepared review evidence exceeds the configured context limit; narrow the test scope or decompose the review')
    conn.execute('INSERT INTO prepared_validation_review_scopes VALUES (?,?,?,?,?)',
                 (envelope['setId'], identifier, envelope['objectiveId'], content, digest(value)))
    return {**hashes, identifier: digest(value)}


def review_scope_artifact(conn, identifier):
    from eidolon_cli.organization_prepared_validation_finish import retain_prepared_artifact
    return retain_prepared_artifact(conn, identifier, _uncached_review_scope_artifact(conn, identifier))

"""Immutable generated outputs, versioned by exact isolated build run identity."""
import json
from datetime import datetime, timezone

from eidolon_cli.organization_project_validation import canonical, digest

SCHEMA = '''
CREATE TABLE IF NOT EXISTS project_build_outputs (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_run_results(run_id),
 path TEXT NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL,
 UNIQUE(run_id,path));
'''
for operation in ('UPDATE', 'DELETE'):
    SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_project_build_outputs_{operation.lower()} '
               f'BEFORE {operation} ON project_build_outputs BEGIN '
               "SELECT RAISE(ABORT,'Immutable build output'); END;\n")


def _expected(row):
    from eidolon_cli.organization_project_build_outputs import validate_build_outputs
    from eidolon_cli.organization_project_typescript_build import valid_build_receipt
    result, snapshot, grant = json.loads(row['record']), json.loads(row['snapshot']), json.loads(row['grant_record'])['execution']
    files = [{key: item[key] for key in ('path', 'content', 'sha256', 'revision')} for item in snapshot]
    if digest(result) != row['result_sha256'] or not valid_build_receipt(result, grant, files):
        raise ValueError('Build artifacts require exact successful isolated execution and cleanup')
    outputs = validate_build_outputs(result, files, grant['root'])
    expected = []
    for output in outputs:
        record = {'schemaVersion': 1, 'runId': row['id'], 'objectiveId': row['objective_id'],
                  'resultSha256': row['result_sha256'], 'outputManifestSha256': result['outputManifestSha256'],
                  'path': output['path'], 'sources': output['sources'], 'bytes': output['bytes'],
                  'outputSha256': output['sha256'], 'content': output['content']}
        expected.append({'id': 'project_build_output_' + digest({'runId': row['id'], 'path': output['path']}),
                         'record': record, 'sha256': digest(record)})
    return expected


def persist_outputs(conn, row):
    """Caller verifies current authority first; transaction owns all-or-none writes."""
    expected = _expected(row)
    previous = conn.execute('SELECT 1 FROM project_build_outputs WHERE run_id=?', (row['id'],)).fetchone()
    if previous:
        return verified_outputs(conn, row)
    for item in expected:
        conn.execute('INSERT INTO project_build_outputs VALUES (?,?,?,?,?)',
                     (item['id'], row['id'], item['record']['path'], canonical(item['record']), item['sha256']))
    return verified_outputs(conn, row)


def verified_outputs(conn, row):
    expected = _expected(row)
    retained = {item['id']: item for item in conn.execute('SELECT * FROM project_build_outputs WHERE run_id=?', (row['id'],))}
    if set(retained) != {item['id'] for item in expected}:
        raise ValueError('Managed build output manifest is incomplete or changed')
    artifacts = []
    for item in expected:
        stored = retained[item['id']]
        if (stored['run_id'] != row['id'] or stored['path'] != item['record']['path']
                or stored['record'] != canonical(item['record']) or stored['sha256'] != item['sha256']):
            raise ValueError('Managed build output no longer matches exact compiler bytes')
        artifacts.append({'id': item['id'], 'objectiveId': row['objective_id'], 'taskId': None,
                          'kind': 'project_build_output', 'content': stored['record'], 'sha256': stored['sha256'],
                          'summary': 'Generated build output: ' + stored['path'], 'toolReceipts': [],
                          'createdAt': datetime.fromtimestamp(row['finished'], timezone.utc).isoformat(),
                          'buildOutput': item['record']})
    return artifacts


def build_output_artifact(conn, identifier):
    from eidolon_cli.organization_project_execution import _run_row
    retained = conn.execute('SELECT run_id FROM project_build_outputs WHERE id=?', (identifier,)).fetchone()
    if retained is None:
        return None
    row = _run_row(conn, retained['run_id'])
    if row is None or row['record'] is None:
        raise ValueError('Managed build output lost its immutable run receipt')
    return next(item for item in verified_outputs(conn, row) if item['id'] == identifier)

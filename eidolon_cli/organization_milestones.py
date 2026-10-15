"""Persistent planning groups. Milestones never dispatch work or grant allowance."""
from contextlib import closing
from pathlib import Path
import sqlite3
import hashlib
import json
import re
import time

MILESTONE_TABLES = frozenset({'organization_milestone_revisions', 'organization_milestone_links',
                            'organization_milestone_receipts'})
MILESTONE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_milestone_revisions (
 id TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL,
 sha256 TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS organization_milestone_links (
 milestone_id TEXT NOT NULL, revision INTEGER NOT NULL,
 objective_id TEXT NOT NULL REFERENCES objectives(id),
 PRIMARY KEY(milestone_id,revision,objective_id),
 FOREIGN KEY(milestone_id,revision) REFERENCES organization_milestone_revisions(id,revision));
CREATE TABLE IF NOT EXISTS organization_milestone_receipts (
 idempotency_key TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, result TEXT NOT NULL);
'''
for _table in MILESTONE_TABLES:
    for _op in ('UPDATE', 'DELETE'):
        MILESTONE_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                            "BEGIN SELECT RAISE(ABORT,'Immutable milestone history'); END;\n")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _ids(value, field, limit, *, empty=True):
    if (not isinstance(value, list) or len(value) > limit or (not empty and not value)
            or any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}', v) for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'{field} requires bounded unique canonical IDs')
    return sorted(value)


def _records(conn):
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & MILESTONE_TABLES
    if not present:
        return {}
    if present != MILESTONE_TABLES:
        raise ValueError('Milestone storage is incomplete')
    result = {}
    rows = conn.execute('SELECT r.* FROM organization_milestone_revisions r JOIN '
        '(SELECT id,MAX(revision) revision FROM organization_milestone_revisions GROUP BY id) h '
        'ON r.id=h.id AND r.revision=h.revision ORDER BY r.id LIMIT 129').fetchall()
    if len(rows) > 128:
        raise ValueError('Milestone capacity exceeded')
    for row in rows:
        record = json.loads(row['record'])
        if _hash(record) != row['sha256'] or record['id'] != row['id'] or record['revision'] != row['revision']:
            raise ValueError('Milestone revision is corrupt')
        result[row['id']] = {**record, 'sha256': row['sha256']}
    return result


def _objective(conn, objective_id):
    row = conn.execute('SELECT o.id,o.description,o.cancelled,c.amended_scope,c.criteria,c.delivery_mode,'
        'c.required_checks,c.status,c.round FROM objectives o JOIN objective_control c ON c.objective_id=o.id '
        'WHERE o.id=?', (objective_id,)).fetchone()
    if row is None:
        raise ValueError('Milestones may link only existing initialized objectives')
    from eidolon_cli.organization_projects import objective_projects
    projects = objective_projects(conn, objective_id)
    from eidolon_cli.organization_outcome_contracts import contract_view
    contract = contract_view(conn, objective_id)
    scope = {key: row[key] for key in ('description', 'amended_scope', 'criteria', 'delivery_mode', 'required_checks')}
    scope['outcomeContract'] = ({key: contract[key] for key in ('objectiveId', 'revision', 'sha256')}
                                if contract else None)
    fingerprint = _hash(scope)
    return row, projects, fingerprint


def _acyclic(records):
    visiting, visited = set(), set()
    def visit(ident):
        if ident in visiting:
            raise ValueError('Milestone dependencies must not form cycles')
        if ident in visited:
            return
        if ident not in records:
            raise ValueError('Milestone dependencies must already exist')
        visiting.add(ident)
        for dependency in records[ident]['dependencies']:
            visit(dependency['id'])
        visiting.remove(ident)
        visited.add(ident)
    for ident in records:
        visit(ident)


def milestone_projection(conn):
    """Read-only current projection; archived objective references remain intact."""
    records = _records(conn)
    _acyclic(records)
    views = {}
    def project(ident):
        if ident in views:
            return views[ident]
        record = records[ident]
        reasons, objectives = [], []
        for link in record['objectives']:
            row, projects, fingerprint = _objective(conn, link['id'])
            stale = fingerprint != link['scopeSha256'] or projects != link['projects']
            if stale:
                reasons.append('objective_scope_changed')
            if row['cancelled']:
                reasons.append('objective_cancelled')
            if row['status'] in ('blocked', 'legacy_completed'):
                reasons.append('objective_requires_review')
            pending = conn.execute("SELECT 1 FROM requests WHERE objective_id=? AND status='pending_intervention' LIMIT 1", (row['id'],)).fetchone()
            if pending:
                reasons.append('objective_requires_attention')
            objectives.append({'id': row['id'], 'status': row['status'], 'round': row['round'],
                               'scopeChanged': stale, 'accepted': row['status'] == 'accepted' and not row['cancelled'] and not stale})
        dependencies = []
        for dependency in record['dependencies']:
            view = project(dependency['id'])
            stale = view['revision'] != dependency['revision'] or view['sha256'] != dependency['sha256']
            if stale:
                reasons.append('dependency_revision_changed')
            dependencies.append({'id': view['id'], 'revision': view['revision'],
                                 'scopeChanged': stale, 'complete': view['status'] == 'complete' and not stale})
        status = ('needs_attention' if reasons else 'blocked' if any(not d['complete'] for d in dependencies)
                  else 'planned' if not objectives else 'complete' if all(o['accepted'] for o in objectives)
                  else 'in_progress')
        views[ident] = {**record, 'status': status, 'reasons': sorted(set(reasons)),
                       'objectiveStates': objectives, 'dependencyStates': dependencies,
                       'budgetAuthority': 'existing-objectives-only', 'automaticContinuation': False}
        return views[ident]
    return [project(ident) for ident in sorted(records)]


class OrganizationMilestoneStore:
    def revise_milestone(self, milestone_id, *, title, project_ids, objective_ids, dependency_ids,
                         expected_revision, idempotency_key):
        """Explicit planning mutation only; no requests, providers, tools or budgets."""
        from eidolon_cli.organization_store import _text
        milestone_id = _ids([milestone_id], 'milestoneId', 1, empty=False)[0]
        title = _text(title, 'Milestone title', 500)
        project_ids = _ids(project_ids, 'projectIds', 8, empty=False)
        objective_ids = _ids(objective_ids, 'objectiveIds', 64)
        dependency_ids = _ids(dependency_ids, 'dependencyIds', 32)
        key = _text(idempotency_key, 'Milestone idempotency key', 128)
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**31:
            raise ValueError('expectedRevision must be the current revision, or zero for creation')
        input_hash = _hash([milestone_id, title, project_ids, objective_ids, dependency_ids, expected_revision])
        with self._write() as conn:
            self._require_current_policy(conn)
            old = conn.execute('SELECT * FROM organization_milestone_receipts WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old['input_sha256'] != input_hash:
                    raise ValueError('Milestone idempotency key belongs to different input')
                return json.loads(old['result'])
            records = _records(conn)
            current = records.get(milestone_id)
            if expected_revision != (current['revision'] if current else 0):
                raise ValueError('Milestone revision is stale')
            if current is None and len(records) >= 128:
                raise ValueError('At most 128 milestones are supported')
            from eidolon_cli.organization_projects import selected_projects, validate_objective_projects
            project_bindings = selected_projects(project_ids, self.settings)
            links = []
            for ident in objective_ids:
                row, projects, fingerprint = _objective(conn, ident)
                validate_objective_projects(conn, ident, self.settings)
                if not projects or not {p['id'] for p in projects} <= set(project_ids):
                    raise ValueError('Linked objectives must be bound within the milestone project scope')
                links.append({'id': row['id'], 'scopeSha256': fingerprint, 'projects': projects})
            if any(ident not in records for ident in dependency_ids):
                raise ValueError('Milestone dependencies must already exist')
            dependencies = [{'id': ident, 'revision': records[ident]['revision'], 'sha256': records[ident]['sha256']}
                            for ident in dependency_ids]
            revision = expected_revision + 1
            record = {'id': milestone_id, 'revision': revision, 'title': title,
                      'projects': project_bindings, 'objectives': links, 'dependencies': dependencies}
            records[milestone_id] = record
            _acyclic(records)
            result = {'milestoneId': milestone_id, 'revision': revision, 'sha256': _hash(record)}
            conn.execute('INSERT INTO organization_milestone_revisions VALUES (?,?,?,?,?)',
                         (milestone_id, revision, json.dumps(record, sort_keys=True), result['sha256'], time.time()))
            for ident in objective_ids:
                conn.execute('INSERT INTO organization_milestone_links VALUES (?,?,?)', (milestone_id, revision, ident))
            conn.execute('INSERT INTO organization_milestone_receipts VALUES (?,?,?)', (key, input_hash, json.dumps(result, sort_keys=True)))
            return result

    def milestones(self):
        path = Path(self.path)
        if not path.is_file():
            return []
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=0.25)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            return milestone_projection(conn)

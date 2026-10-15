"""Immutable projections of reused manager plans and repaired task membership.

No planning call is invented. Every carried package points to the exact prior
plan row and records new membership and dependency identities separately.
"""
import json

from eidolon_cli.organization_selective_repair import digest

PACKAGE_REPAIR_TABLES = frozenset({'objective_package_reuse', 'repair_package_members'})
SCHEMA = '''
CREATE TABLE IF NOT EXISTS objective_package_reuse (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 source_package_id TEXT NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_package_members (
 package_id TEXT NOT NULL REFERENCES objective_package_reuse(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(package_id,task_id));
'''
for _table in PACKAGE_REPAIR_TABLES:
    for _op in ('UPDATE', 'DELETE'):
        SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                   "BEGIN SELECT RAISE(ABORT,'Immutable repaired package'); END;\n")


def present(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & PACKAGE_REPAIR_TABLES
    if tables and tables != PACKAGE_REPAIR_TABLES:
        raise ValueError('Repaired package storage is incomplete')
    return bool(tables)


def package_row(conn, identifier, seen=None):
    seen = set() if seen is None else seen
    if identifier in seen or len(seen) >= 128:
        raise ValueError("Repaired package ancestry is invalid")
    seen.add(identifier)
    original = conn.execute('SELECT * FROM manager_work_packages WHERE id=?', (identifier,)).fetchone()
    if original is not None or not present(conn):
        return original
    row = conn.execute('SELECT * FROM objective_package_reuse WHERE id=?', (identifier,)).fetchone()
    if row is None:
        return None
    record = json.loads(row['record'])
    if digest(record) != row['sha256']:
        raise ValueError('Repaired package proof changed')
    members = sorted(r[0] for r in conn.execute('SELECT task_id FROM repair_package_members WHERE package_id=?', (identifier,)))
    if members != record['memberIds']:
        raise ValueError('Repaired package membership changed')
    source = package_row(conn, row['source_package_id'], seen)
    if source is None or digest(dict(source)) != record['sourceSha256']:
        raise ValueError('Original manager plan changed after package reuse')
    return {**dict(source), 'id': row['id'], 'round': row['round'],
            'dependencies': json.dumps(record['dependencyIds']), 'sourcePackageId': row['source_package_id'],
            'planDisposition': 'reused', 'sourcePlanSha256': record['sourceSha256']}


def task_repair_package(conn, task_id):
    if not present(conn):
        return None
    row = conn.execute('SELECT m.package_id FROM repair_package_members m JOIN objective_package_reuse p ON p.id=m.package_id '
                       'JOIN objective_task_rounds r ON r.task_id=m.task_id AND r.round=p.round '
                       'WHERE m.task_id=?', (task_id,)).fetchone()
    return row[0] if row else None


def package_members(conn, identifier):
    if not present(conn) or not conn.execute('SELECT 1 FROM objective_package_reuse WHERE id=?', (identifier,)).fetchone():
        return None
    return conn.execute('SELECT t.* FROM tasks t JOIN repair_package_members m ON m.task_id=t.id WHERE m.package_id=? ORDER BY t.rowid', (identifier,)).fetchall()


def repaired_rows(conn, objective_id):
    if not present(conn):
        return []
    return [package_row(conn, r[0]) for r in conn.execute('SELECT id FROM objective_package_reuse WHERE objective_id=? ORDER BY round,rowid', (objective_id,))]


def task_package_binding(conn, task_id):
    from eidolon_cli.organization_packages import task_package_id, package_tasks
    identifier = task_package_id(conn, task_id)
    if identifier is None:
        return None
    package = package_row(conn, identifier)
    if package is None:
        raise ValueError('Task manager plan is missing')
    return {'package': dict(package), 'memberIds': [t['id'] for t in package_tasks(conn, identifier)]}


def carry_packages(conn, objective_id, old_round, new_round, task_mapping):
    from eidolon_cli.organization_packages import packages_view, package_tasks
    from eidolon_cli.organization_store import _id
    current = [p for p in packages_view(conn, objective_id) if p['round'] == old_round]
    mapping = {p['id']: _id('package_reuse') for p in current}
    for package in current:
        source = package_row(conn, package['id'])
        if source is None or source['plan'] is None:
            raise ValueError('Only a complete retained manager plan can be carried forward')
        dependencies = json.loads(source['dependencies'])
        if any(d not in mapping for d in dependencies):
            raise ValueError('Package dependency closure is incomplete')
        source_tasks = package_tasks(conn, package['id'])
        if any(t['id'] not in task_mapping for t in source_tasks):
            raise ValueError('Package membership cannot silently omit an original task')
        record = {'sourceSha256': digest(dict(source)), 'dependencyIds': [mapping[d] for d in dependencies],
                  'memberIds': sorted(task_mapping[t['id']] for t in source_tasks)}
        conn.execute('INSERT INTO objective_package_reuse VALUES (?,?,?,?,?,?)',
            (mapping[package['id']], objective_id, new_round, package['id'], json.dumps(record, sort_keys=True), digest(record)))
        for task in package_tasks(conn, package['id']):
            if task['id'] not in task_mapping:
                raise ValueError('Package membership cannot silently omit an original task')
            conn.execute('INSERT INTO repair_package_members VALUES (?,?)', (mapping[package['id']], task_mapping[task['id']]))


def open_repaired_packages(conn, objective_id=None):
    if not present(conn):
        return []
    sql = ('SELECT p.id,a.executive_id FROM objective_package_reuse p JOIN objectives o ON o.id=p.objective_id '
           'JOIN objective_control c ON c.objective_id=o.id JOIN objective_assignments a ON a.objective_id=o.id '
           "WHERE o.cancelled=0 AND c.status NOT IN ('accepted','legacy_completed') AND p.round=c.round")
    rows = conn.execute(sql + (' AND o.id=?' if objective_id else ''), (objective_id,) if objective_id else ()).fetchall()
    return [{**package_row(conn, row['id']), 'executive_id': row['executive_id']} for row in rows]


def projected_task_package(conn, task_id, objective_id, round_number):
    """Current display membership; execution proof retains original membership."""
    from eidolon_cli.organization_packages import task_package_id
    if present(conn):
        rows = conn.execute('SELECT p.id FROM repair_package_members m JOIN objective_package_reuse p ON p.id=m.package_id '
                            'WHERE m.task_id=? AND p.objective_id=? AND p.round=?',
                            (task_id, objective_id, round_number)).fetchall()
        if len(rows) > 1:
            raise ValueError('Task has ambiguous repaired package membership')
        if rows:
            package_row(conn, rows[0][0])
            return rows[0][0]
    return task_package_id(conn, task_id)

"""Append-only persistence for prepared-validation bookkeeping.

Schema creation does not opt any objective into execution. These APIs reserve accounting
units and retain byte snapshots and UNTRUSTED event/review claims only. They never
check live leases/owner authority, authenticate receipts, invoke a runtime, or
allow acceptance/publication. Unified accounting charges both legacy and prepared
reservations without granting execution authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
import time

from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli.organization_goal_allowances import goal_for_objective, require_goal_admission
from eidolon_cli.organization_project_dependency_bundle import _json
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits, _check_no_secrets,
)
from eidolon_cli.organization_project_validation import canonical, digest


PREPARED_VALIDATION_TABLES = frozenset({
    'prepared_validation_bindings', 'prepared_validation_sets',
    'prepared_validation_steps', 'prepared_validation_snapshots',
    'prepared_validation_files', 'prepared_validation_dispatches',
    'prepared_validation_resources', 'prepared_validation_results',
    'prepared_validation_cleanups', 'prepared_validation_aggregates',
    'prepared_validation_reviews',
})
PREPARED_VALIDATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_bindings (
 objective_id TEXT NOT NULL, project_id TEXT NOT NULL,
 grant_record TEXT NOT NULL, grant_sha256 TEXT NOT NULL,
 binding_record TEXT NOT NULL, binding_sha256 TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(objective_id,project_id),
 FOREIGN KEY(objective_id,project_id) REFERENCES objective_projects(objective_id,project_id));
CREATE TABLE IF NOT EXISTS prepared_validation_sets (
 id TEXT NOT NULL PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
 lease_token TEXT NOT NULL, objective_id TEXT NOT NULL, project_id TEXT NOT NULL,
 round INTEGER NOT NULL CHECK(typeof(round)='integer' AND round>=0),
 generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
 envelope TEXT NOT NULL, envelope_sha256 TEXT NOT NULL,
 typecheck_run_id TEXT NOT NULL UNIQUE, tests_run_id TEXT NOT NULL UNIQUE,
 prior_units INTEGER NOT NULL CHECK(typeof(prior_units)='integer' AND prior_units>=0),
 objective_ceiling INTEGER NOT NULL CHECK(typeof(objective_ceiling)='integer' AND objective_ceiling>0),
 owner_ceiling INTEGER NOT NULL CHECK(typeof(owner_ceiling)='integer' AND owner_ceiling>0),
 created REAL NOT NULL,
 UNIQUE(request_id,lease_token), UNIQUE(objective_id,project_id,generation),
 CHECK(typecheck_run_id<>tests_run_id),
 CHECK((json_valid(envelope) AND json_extract(envelope,'$.setId')=id
  AND json_extract(envelope,'$.requestId')=request_id
  AND json_extract(envelope,'$.objectiveId')=objective_id AND json_extract(envelope,'$.projectId')=project_id
  AND json_type(envelope,'$.round')='integer' AND json_extract(envelope,'$.round')=round
  AND json_type(envelope,'$.generation')='integer' AND json_extract(envelope,'$.generation')=generation) IS TRUE),
 FOREIGN KEY(objective_id,project_id) REFERENCES prepared_validation_bindings(objective_id,project_id),
 FOREIGN KEY(id,typecheck_run_id) REFERENCES prepared_validation_steps(set_id,run_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(id,tests_run_id) REFERENCES prepared_validation_steps(set_id,run_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(id) REFERENCES prepared_validation_snapshots(set_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS prepared_validation_steps (
 run_id TEXT NOT NULL PRIMARY KEY, set_id TEXT NOT NULL REFERENCES prepared_validation_sets(id),
 step_id TEXT NOT NULL, definition_sha256 TEXT NOT NULL,
 recipe TEXT NOT NULL, check_kind TEXT NOT NULL,
 units INTEGER NOT NULL CHECK(typeof(units)='integer' AND units=1),
 UNIQUE(set_id,run_id), UNIQUE(set_id,step_id), UNIQUE(set_id,recipe),
 CHECK((recipe='workspace_typescript' AND check_kind='project_typecheck') OR
       (recipe='workspace_vitest_ui' AND check_kind='project_tests')));
CREATE TABLE IF NOT EXISTS prepared_validation_snapshots (
 set_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_sets(id),
 manifest TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
 limits TEXT NOT NULL, limits_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prepared_validation_files (
 set_id TEXT NOT NULL REFERENCES prepared_validation_snapshots(set_id), path TEXT NOT NULL,
 content BLOB NOT NULL CHECK(typeof(content)='blob'), sha256 TEXT NOT NULL,
 byte_count INTEGER NOT NULL CHECK(typeof(byte_count)='integer' AND byte_count>=0 AND byte_count=length(content)),
 mode INTEGER NOT NULL CHECK(typeof(mode)='integer' AND mode IN (292,365)),
 execution_only INTEGER NOT NULL CHECK(typeof(execution_only)='integer' AND execution_only IN (0,1)),
 PRIMARY KEY(set_id,path));
CREATE TABLE IF NOT EXISTS prepared_validation_dispatches (
 run_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_steps(run_id),
 nonce TEXT NOT NULL UNIQUE, expected_container_name TEXT NOT NULL UNIQUE,
 image TEXT NOT NULL, created REAL NOT NULL, UNIQUE(run_id,nonce,expected_container_name));
CREATE TABLE IF NOT EXISTS prepared_validation_resources (
 run_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_dispatches(run_id),
 container_id TEXT NOT NULL UNIQUE, created REAL NOT NULL, UNIQUE(run_id,container_id));
CREATE TABLE IF NOT EXISTS prepared_validation_results (
 run_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_dispatches(run_id),
 status TEXT NOT NULL CHECK(status IN ('passed','failed','interrupted','cleanup_failed','unknown')),
 receipt_sha256 TEXT NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS prepared_validation_cleanups (
 event_id TEXT NOT NULL PRIMARY KEY, run_id TEXT NOT NULL,
 sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence>0),
 nonce TEXT NOT NULL, expected_container_name TEXT NOT NULL, container_id TEXT,
 container_confirmed INTEGER NOT NULL CHECK(typeof(container_confirmed)='integer' AND container_confirmed IN (0,1)),
 staging_confirmed INTEGER NOT NULL CHECK(typeof(staging_confirmed)='integer' AND staging_confirmed IN (0,1)),
 created REAL NOT NULL, UNIQUE(run_id,sequence),
 FOREIGN KEY(run_id,nonce,expected_container_name)
  REFERENCES prepared_validation_dispatches(run_id,nonce,expected_container_name),
 FOREIGN KEY(run_id,container_id) REFERENCES prepared_validation_resources(run_id,container_id));
CREATE TABLE IF NOT EXISTS prepared_validation_aggregates (
 set_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_sets(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS prepared_validation_reviews (
 set_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_aggregates(set_id),
 request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 reviewer_id TEXT NOT NULL REFERENCES agents(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL,
 contributors TEXT NOT NULL, source_evidence TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS prepared_validation_cleanup_sequence BEFORE INSERT ON prepared_validation_cleanups
BEGIN
 SELECT CASE WHEN NEW.sequence<>(SELECT COALESCE(MAX(sequence),0)+1 FROM prepared_validation_cleanups WHERE run_id=NEW.run_id)
  THEN RAISE(ABORT,'Prepared cleanup observation must have the next sequence') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM prepared_validation_resources WHERE run_id=NEW.run_id)
  AND NOT EXISTS(SELECT 1 FROM prepared_validation_resources WHERE run_id=NEW.run_id AND container_id=NEW.container_id)
  THEN RAISE(ABORT,'Prepared cleanup must bind the exact observed resource') END;
END;
CREATE TRIGGER IF NOT EXISTS prepared_validation_file_binding BEFORE INSERT ON prepared_validation_files
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM prepared_validation_snapshots s, json_each(s.manifest,'$.files') j
  WHERE s.set_id=NEW.set_id AND json_extract(j.value,'$.path')=NEW.path
  AND json_extract(j.value,'$.sha256')=NEW.sha256 AND json_extract(j.value,'$.bytes')=NEW.byte_count
  AND json_extract(j.value,'$.mode')=NEW.mode AND json_extract(j.value,'$.executionOnly')=NEW.execution_only)
  THEN RAISE(ABORT,'Prepared source must match its exact retained manifest') END;
END;
CREATE TRIGGER IF NOT EXISTS prepared_validation_set_sequence BEFORE INSERT ON prepared_validation_sets
BEGIN
 SELECT CASE WHEN NEW.generation<>(SELECT COALESCE(MAX(generation),0)+1 FROM prepared_validation_sets
  WHERE objective_id=NEW.objective_id AND project_id=NEW.project_id)
  THEN RAISE(ABORT,'Prepared generation must be the next generation') END;
 SELECT CASE WHEN NEW.round<(SELECT COALESCE(MAX(round),0) FROM prepared_validation_sets
  WHERE objective_id=NEW.objective_id AND project_id=NEW.project_id)
  THEN RAISE(ABORT,'Prepared round cannot move backwards') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM prepared_validation_steps WHERE run_id=NEW.id)
  OR EXISTS(SELECT 1 FROM project_run_starts WHERE id IN (NEW.id,NEW.typecheck_run_id,NEW.tests_run_id))
  THEN RAISE(ABORT,'Prepared identities must be globally fresh') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM requests WHERE id=NEW.request_id AND objective_id=NEW.objective_id)
  THEN RAISE(ABORT,'Prepared request must belong to its objective') END;
END;
CREATE TRIGGER IF NOT EXISTS prepared_validation_step_binding BEFORE INSERT ON prepared_validation_steps
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM prepared_validation_sets WHERE id=NEW.run_id)
  OR EXISTS(SELECT 1 FROM project_run_starts WHERE id=NEW.run_id)
  THEN RAISE(ABORT,'Prepared identities must be globally fresh') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM prepared_validation_sets s, json_each(s.envelope,'$.steps') j
  WHERE s.id=NEW.set_id AND json_extract(j.value,'$.runId')=NEW.run_id
  AND json_extract(j.value,'$.id')=NEW.step_id AND json_extract(j.value,'$.definitionSha256')=NEW.definition_sha256
  AND json_extract(j.value,'$.recipe')=NEW.recipe AND json_extract(j.value,'$.checkKind')=NEW.check_kind
  AND ((NEW.recipe='workspace_typescript' AND s.typecheck_run_id=NEW.run_id)
    OR (NEW.recipe='workspace_vitest_ui' AND s.tests_run_id=NEW.run_id)))
  THEN RAISE(ABORT,'Prepared reservation must match its exact envelope step') END;
END;
'''
# BEFORE INSERT also blocks SQLite's OR REPLACE, whose implicit DELETE does
# not run DELETE triggers unless recursive_triggers happens to be enabled.
_UNIQUE_KEYS = {
    'bindings': [('objective_id', 'project_id')],
    'sets': [('id',), ('request_id', 'lease_token'), ('objective_id', 'project_id', 'generation'),
             ('typecheck_run_id',), ('tests_run_id',)],
    'steps': [('run_id',), ('set_id', 'step_id'), ('set_id', 'recipe')],
    'snapshots': [('set_id',)], 'files': [('set_id', 'path')],
    'dispatches': [('run_id',), ('nonce',), ('expected_container_name',)],
    'resources': [('run_id',), ('container_id',)], 'results': [('run_id',)],
    'cleanups': [('event_id',), ('run_id', 'sequence')], 'aggregates': [('set_id',)],
    'reviews': [('set_id',), ('request_id',)],
}
for _suffix, _keys in _UNIQUE_KEYS.items():
    _table = 'prepared_validation_' + _suffix
    _conflicts = ' OR '.join('(' + ' AND '.join(f'{key}=NEW.{key}' for key in columns) + ')'
                             for columns in _keys)
    PREPARED_VALIDATION_SCHEMA += (
        f'CREATE TRIGGER IF NOT EXISTS no_replace_{_table} BEFORE INSERT ON {_table} '
        f'WHEN EXISTS(SELECT 1 FROM {_table} WHERE {_conflicts}) '
        "BEGIN SELECT RAISE(ABORT,'Prepared validation history cannot be replaced'); END;\n")
for _table in sorted(PREPARED_VALIDATION_TABLES):
    for _operation in ('UPDATE', 'DELETE'):
        PREPARED_VALIDATION_SCHEMA += (
            f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_operation.lower()} '
            f'BEFORE {_operation} ON {_table} '
            "BEGIN SELECT RAISE(ABORT,'Prepared validation history is immutable'); END;\n")


def _unverified(**values):
    return {**values, 'runtimeReceiptsVerified': False, 'executionAuthorized': False,
            'acceptanceAuthorized': False, 'sourceDeliveryAuthorized': False}


@contextmanager
def _transaction(conn, *, write=False):
    # Owning the boundary prevents a helper from committing caller changes and
    # keeps history/usage reads serialized with the entire two-unit insertion.
    if conn.row_factory is not sqlite3.Row:
        raise ValueError('Prepared ledger requires organization sqlite3.Row connections')
    if conn.in_transaction:
        raise ValueError('Prepared ledger requires its own transaction')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Prepared ledger requires foreign key enforcement')
    conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _bounded_text(column, limit):
    """Bound retained text in SQLite before allocating it in the Python caller."""
    return f'CASE WHEN length(CAST({column} AS BLOB)) BETWEEN 1 AND {limit} THEN {column} END'


def _checked_json(record, sha256):
    if type(record) is not str:
        raise ValueError('Retained prepared record is missing or exceeds its byte bound')
    value = _json(record.encode('utf-8'), 40 * 1024**2, 'Retained prepared record')
    if digest(value) != sha256:
        raise ValueError('Retained prepared record hash changed')
    return value


def _binding_record(objective_id, grant):
    return {'version': 1, 'contract': 'prepared-validation-objective-binding-v1',
            'objectiveId': objective_id, 'projectId': grant['projectId'],
            'projectBindingSha256': grant['projectBindingSha256'], 'ownerGrantSha256': digest(grant)}


def _require_write_transaction(conn):
    # The caller owns commit/rollback so objective creation, live lease checks,
    # and the complete two-unit reservation can share one serialized boundary.
    if (conn.row_factory is not sqlite3.Row or not conn.in_transaction
            or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1):
        raise ValueError('Prepared write requires an existing organization transaction')


def _bind_prepared_validation_proposal(conn, *, objective_id, value, context):
    """Transaction-scoped persistence only; the caller must verify live authority."""
    _require_write_transaction(conn)
    objective_id = contract._id(objective_id)
    grant = contract.normalize_prepared_validation_contract(value, **context)
    binding = _binding_record(objective_id, grant)
    row = conn.execute('SELECT binding FROM objective_projects WHERE objective_id=? AND project_id=?',
                       (objective_id, grant['projectId'])).fetchone()
    if row is None or contract.legacy_project_binding_sha256(json.loads(row[0])) != grant['projectBindingSha256']:
        raise ValueError('Prepared sidecar differs from the retained objective project')
    conn.execute('INSERT INTO prepared_validation_bindings VALUES (?,?,?,?,?,?,?)',
                 (objective_id, grant['projectId'], canonical(grant), digest(grant),
                  canonical(binding), digest(binding), time.time()))
    return _unverified(objectiveBindingSha256=digest(binding), ownerGrantSha256=digest(grant))


def bind_prepared_validation_proposal(conn, *, objective_id, value, context):
    """Persist an inert sidecar after checking supplied contract context and bytes."""
    with _transaction(conn, write=True):
        return _bind_prepared_validation_proposal(conn, objective_id=objective_id, value=value, context=context)


def _binding(conn, objective_id, project_id):
    sizes = conn.execute('SELECT length(CAST(grant_record AS BLOB)),length(CAST(binding_record AS BLOB)) '
        'FROM prepared_validation_bindings WHERE objective_id=? AND project_id=?', (objective_id, project_id)).fetchone()
    if sizes is None or any(type(size) is not int or not 1 <= size <= limit
                            for size, limit in zip(sizes, (40 * 1024**2, 4096))):
        raise ValueError('Prepared objective sidecar is missing or exceeds its byte bound')
    row = conn.execute('SELECT grant_record,grant_sha256,binding_record,binding_sha256 '
                       'FROM prepared_validation_bindings WHERE objective_id=? AND project_id=?',
                       (objective_id, project_id)).fetchone()
    if row is None:
        raise ValueError('Prepared objective sidecar is missing')
    grant, binding = _checked_json(row[0], row[1]), _checked_json(row[2], row[3])
    if binding != _binding_record(objective_id, grant) or grant['projectId'] != project_id:
        raise ValueError('Prepared objective sidecar has a mixed binding')
    legacy = conn.execute('SELECT binding FROM objective_projects WHERE objective_id=? AND project_id=?',
                          (objective_id, project_id)).fetchone()
    if legacy is None or contract.legacy_project_binding_sha256(json.loads(legacy[0])) != grant['projectBindingSha256']:
        raise ValueError('Retained legacy project binding changed')
    return grant, binding


def _require_context(conn, context):
    grant, binding = _binding(conn, context['objective_id'], context['project_id'])
    expected = {'profile': grant['profile'], 'organization_id': grant['organizationId'],
                'owner_id': grant['ownerId'], 'owner_grant_sha256': digest(grant),
                'objective_binding_sha256': digest(binding)}
    row = conn.execute('SELECT round FROM objective_control WHERE objective_id=?',
                       (context['objective_id'],)).fetchone()
    policy = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone()
    if row is None or policy is None:
        raise ValueError('Prepared proposal requires retained round and policy context')
    expected.update(round_number=row[0], policy_generation=policy[0], policy_fingerprint=policy[1])
    if any(type(context.get(key)) is not type(value) or context[key] != value for key, value in expected.items()):
        raise ValueError('Prepared proposal differs from retained binding, round or policy')
    return grant


def _history(conn, objective_id, project_id):
    size = conn.execute('SELECT count(*),COALESCE(max(length(CAST(envelope AS BLOB))),0) '
        'FROM prepared_validation_sets WHERE objective_id=? AND project_id=?', (objective_id, project_id)).fetchone()
    if size[0] > contract._MAX_HISTORY or size[1] > 131072:
        raise ValueError('Retained prepared history exceeds its count/byte bound')
    rows = conn.execute('SELECT id,request_id,round,generation,envelope,envelope_sha256 '
                        'FROM prepared_validation_sets WHERE objective_id=? AND project_id=? ORDER BY generation',
                        (objective_id, project_id)).fetchall()
    values = []
    for row in rows:
        value = _checked_json(row[4], row[5])
        if (value['setId'], value['requestId'], value['round'], value['generation']) != tuple(row[:4]):
            raise ValueError('Retained prepared envelope columns changed')
        steps = conn.execute('SELECT run_id,step_id,definition_sha256,recipe,check_kind,units '
                             'FROM prepared_validation_steps WHERE set_id=? ORDER BY step_id', (row[0],)).fetchall()
        expected = [(s['runId'], s['id'], s['definitionSha256'], s['recipe'], s['checkKind'], 1)
                    for s in sorted(value['steps'], key=lambda step: step['id'])]
        if [tuple(step) for step in steps] != expected:
            raise ValueError('Retained prepared reservations are missing or mixed')
        values.append(value)
    return contract._history(values, objective_id, project_id)


def _check_snapshot(snapshot, grant, expected_sha256):
    if type(snapshot) is not ProjectSnapshotBundle:
        raise ValueError('Prepared snapshot requires an immutable exact byte bundle')
    # Reconstruct rather than trusting cached hashes on a supplied Python object.
    checked = ProjectSnapshotBundle(snapshot.root_alias, snapshot.root_identity,
        tuple(ProjectSnapshotFile(f.path, f.content, f.mode, f.execution_only) for f in snapshot.files), snapshot.limits)
    source, root = grant['source'], grant['root']
    if (checked.snapshot_sha256 != expected_sha256 or checked.snapshot_sha256 != snapshot.snapshot_sha256
            or checked.root_alias != root['alias'] or checked.root_identity != (root['device'], root['inode'])
            or asdict(checked.limits) != source['limits']
            or [f.path for f in checked.files] != source['files']):
        raise ValueError('Prepared snapshot differs from its exact source selection, hash or limits')
    exceptions = source['executionOnlyHashes']
    package_hashes = {item['path']: item['sha256'] for item in grant['dependencies']['manifest']['packageManifests']}
    package_hashes['package-lock.json'] = grant['dependencies']['lockfileSha256']
    for item in checked.files:
        if not item.execution_only:
            _check_no_secrets(item.content)
        if (item.execution_only != (item.path in exceptions)
                or item.path in exceptions and item.sha256 != exceptions[item.path]
                or item.path in package_hashes and item.sha256 != package_hashes[item.path]):
            raise ValueError('Prepared snapshot dependency or execution-only bytes changed')
    return checked


def _charged_units(conn, objective_id):
    from eidolon_cli.organization_prepared_validation_activity import objective_project_reserved_units
    return objective_project_reserved_units(conn, objective_id)['totalUnits']


def _require_goal_units(conn, objective_id):
    # Preserve canonical pause, unknown-usage and deadline checks. The unified
    # goal projection already counts every historical legacy/prepared unit;
    # this transaction must have room for the complete new pair, without adding
    # existing prepared reservations a second time.
    require_goal_admission(conn, objective_id, project_run=True)
    goal = goal_for_objective(conn, objective_id)
    if goal is not None and goal['reserved']['projectRuns'] + 2 > goal['limits']['projectRuns']:
        raise ValueError('Shared goal cannot reserve both prepared execution units')


def _reserve_prepared_validation_proposal(conn, value, *, snapshot, context, owner_ceiling, lease_token):
    """Retain the complete pair inside the caller's live-authority transaction."""
    _require_write_transaction(conn)
    if type(lease_token) is not str or not 1 <= len(lease_token) <= 256:
        raise ValueError('Prepared proposal requires bounded private lease correlation')
    envelope = contract.normalize_validation_set_envelope(value, **context)
    grant = _require_context(conn, context)
    snapshot = _check_snapshot(snapshot, grant, envelope['source']['snapshotSha256'])
    request = conn.execute('SELECT objective_id,payload FROM requests WHERE id=?', (envelope['requestId'],)).fetchone()
    if (request is None or request[0] != envelope['objectiveId']
            or json.loads(request[1]).get('projectId') != envelope['projectId']):
        raise ValueError('Prepared request does not match its exact objective/project')
    ceiling = conn.execute('SELECT max_runs FROM project_execution_budgets WHERE objective_id=?',
                           (envelope['objectiveId'],)).fetchone()
    if ceiling is None:
        raise ValueError('Objective execution ceiling is missing')
    history = _history(conn, envelope['objectiveId'], envelope['projectId'])
    plan = contract.plan_validation_reservation(envelope, history=history,
        used_units=_charged_units(conn, envelope['objectiveId']), objective_ceiling=ceiling[0],
        owner_ceiling=owner_ceiling, context=context)
    _require_goal_units(conn, envelope['objectiveId'])
    ordered = contract.validation_execution_order(envelope)
    conn.execute('INSERT INTO prepared_validation_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (envelope['setId'], envelope['requestId'], lease_token, envelope['objectiveId'], envelope['projectId'],
         envelope['round'], envelope['generation'], canonical(envelope), digest(envelope),
         ordered[0]['runId'], ordered[1]['runId'], plan['priorUnits'], ceiling[0], owner_ceiling, time.time()))
    conn.execute('INSERT INTO prepared_validation_snapshots VALUES (?,?,?,?,?)',
        (envelope['setId'], canonical(snapshot.manifest()), snapshot.snapshot_sha256,
         canonical(asdict(snapshot.limits)), digest(asdict(snapshot.limits))))
    for item in snapshot.files:
        conn.execute('INSERT INTO prepared_validation_files VALUES (?,?,?,?,?,?,?)',
            (envelope['setId'], item.path, item.content, item.sha256, len(item.content), item.mode, int(item.execution_only)))
    for step in envelope['steps']:
        conn.execute('INSERT INTO prepared_validation_steps VALUES (?,?,?,?,?,?,1)',
            (step['runId'], envelope['setId'], step['id'], step['definitionSha256'], step['recipe'], step['checkKind']))
    return _unverified(**{**plan, 'committed': False})


def reserve_prepared_validation_proposal(conn, value, *, snapshot, context, owner_ceiling, lease_token):
    """Atomically charge two fresh proposal units, never authorize a dispatch.

    The token is private correlation only. No replay follows an uncertain commit.
    """
    with _transaction(conn, write=True):
        plan = _reserve_prepared_validation_proposal(conn, value, snapshot=snapshot,
            context=context, owner_ceiling=owner_ceiling, lease_token=lease_token)
    return {**plan, 'committed': True}


def _snapshot(conn, set_id):
    sizes = conn.execute('SELECT length(CAST(s.envelope AS BLOB)),length(CAST(p.manifest AS BLOB)), '
        'length(CAST(p.limits AS BLOB)) FROM prepared_validation_sets s '
        'JOIN prepared_validation_snapshots p ON p.set_id=s.id WHERE s.id=?', (set_id,)).fetchone()
    if sizes is None or any(type(size) is not int or not 1 <= size <= limit
                            for size, limit in zip(sizes, (131072, 8 * 1024**2, 4096))):
        raise ValueError('Retained prepared snapshot metadata is missing or exceeds its byte bound')
    row = conn.execute('SELECT s.objective_id,s.project_id,s.envelope,s.envelope_sha256, '
        'p.manifest,p.snapshot_sha256,p.limits,p.limits_sha256 FROM prepared_validation_sets s '
        'JOIN prepared_validation_snapshots p ON p.set_id=s.id WHERE s.id=?', (set_id,)).fetchone()
    if row is None:
        raise ValueError('Retained prepared snapshot is missing')
    envelope = _checked_json(row[2], row[3])
    manifest = _json(row[4].encode('utf-8'), 8 * 1024**2, 'Retained snapshot manifest')
    limits = ProjectSnapshotLimits(**_checked_json(row[6], row[7]))
    size = conn.execute('SELECT count(*),COALESCE(sum(length(content)),0),COALESCE(max(length(content)),0) '
                        'FROM prepared_validation_files WHERE set_id=?', (set_id,)).fetchone()
    if not 1 <= size[0] <= limits.max_files or size[1] > limits.max_total_bytes or size[2] > limits.max_file_bytes:
        raise ValueError('Retained prepared source exceeds its exact count/byte limits')
    files = conn.execute('SELECT path,content,sha256,byte_count,mode,execution_only '
                         'FROM prepared_validation_files WHERE set_id=? ORDER BY path', (set_id,)).fetchall()
    rebuilt = []
    for item in files:
        if type(item[5]) is not int or item[5] not in (0, 1):
            raise ValueError('Retained execution-only marker changed')
        value = ProjectSnapshotFile(item[0], item[1], item[4], bool(item[5]))
        if value.sha256 != item[2] or len(value.content) != item[3]:
            raise ValueError('Retained prepared source bytes changed')
        rebuilt.append(value)
    bundle = ProjectSnapshotBundle(manifest['rootAlias'],
        (manifest['rootIdentity']['device'], manifest['rootIdentity']['inode']), tuple(rebuilt), limits)
    if bundle.manifest() != manifest or bundle.snapshot_sha256 != row[5]:
        raise ValueError('Retained prepared source manifest is missing, extra or changed')
    grant, _ = _binding(conn, row[0], row[1])
    return _check_snapshot(bundle, grant, envelope['source']['snapshotSha256'])


def reconstruct_prepared_snapshot(conn, set_id):
    """Verify retained source bytes only; this is not a live filesystem/runtime check."""
    with _transaction(conn):
        return _snapshot(conn, contract._id(set_id))


def _run_envelope(conn, run_id):
    row = conn.execute(f'SELECT {_bounded_text("s.envelope", 131072)},s.envelope_sha256 FROM prepared_validation_sets s '
                      'JOIN prepared_validation_steps r ON r.set_id=s.id WHERE r.run_id=?',
                      (contract._id(run_id),)).fetchone()
    if row is None:
        raise ValueError('Prepared run reservation is missing')
    return contract._envelope_shape(_checked_json(row[0], row[1]))


def record_prepared_dispatch_proposal(conn, run_id, *, nonce):
    """Precommit expected resource identity; no Docker operation or lease check."""
    nonce = contract._hash(nonce)
    name = 'eidolon-prepared-' + nonce
    with _transaction(conn, write=True):
        _run_envelope(conn, run_id)
        conn.execute('INSERT INTO prepared_validation_dispatches VALUES (?,?,?,?,?)',
                     (run_id, nonce, name, contract.IMAGE, time.time()))
    return _unverified(runId=run_id, expectedContainerName=name, image=contract.IMAGE)


def record_prepared_resource_claim(conn, run_id, *, container_id):
    """Retain an unauthenticated resource observation, not proof of a container."""
    with _transaction(conn, write=True):
        conn.execute('INSERT INTO prepared_validation_resources VALUES (?,?,?)',
                     (contract._id(run_id), contract._hash(container_id), time.time()))
    return _unverified(runId=run_id)


def record_prepared_result_claim(conn, value):
    """Retain exact receipt-reference metadata without promoting it to evidence."""
    value = _json(value, 8192, 'Prepared result claim')
    with _transaction(conn, write=True):
        envelope = _run_envelope(conn, value['runId'])
        if (value.get('status') not in {'passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown'}
                or type(value.get('cleanupConfirmed')) is not bool
                or type(value.get('stagingCleanupConfirmed')) is not bool):
            raise ValueError('Prepared result claim has invalid status/cleanup metadata')
        contract._result_reference({**value, 'status': 'passed', 'cleanupConfirmed': True,
                                    'stagingCleanupConfirmed': True}, envelope)
        conn.execute('INSERT INTO prepared_validation_results VALUES (?,?,?,?,?,?)',
                     (value['runId'], value['status'], value['receiptSha256'], canonical(value), digest(value), time.time()))
    return _unverified(runId=value['runId'])


def record_prepared_cleanup_claim(conn, run_id, *, event_id, nonce, container_id,
                                  container_confirmed, staging_confirmed):
    """Append an exact-resource cleanup observation without changing any result.

False means unconfirmed, including unknown/interrupted cleanup. A later claim
may settle that observation, but never rewrites original receipt status/flags.
"""
    run_id, event_id, nonce = contract._id(run_id), contract._id(event_id), contract._hash(nonce)
    if container_id is not None:
        contract._hash(container_id)
    if type(container_confirmed) is not bool or type(staging_confirmed) is not bool:
        raise ValueError('Prepared cleanup claims require exact booleans')
    with _transaction(conn, write=True):
        sequence = conn.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM prepared_validation_cleanups WHERE run_id=?',
                                (run_id,)).fetchone()[0]
        conn.execute('INSERT INTO prepared_validation_cleanups VALUES (?,?,?,?,?,?,?,?,?)',
            (event_id, run_id, sequence, nonce, 'eidolon-prepared-' + nonce, container_id,
             int(container_confirmed), int(staging_confirmed), time.time()))
    return _unverified(runId=run_id, eventId=event_id, sequence=sequence)


def _results(conn, envelope):
    results = []
    for step in envelope['steps']:
        row = conn.execute(f'SELECT {_bounded_text("record", 8192)},sha256,status,receipt_sha256 FROM prepared_validation_results WHERE run_id=?',
                           (step['runId'],)).fetchone()
        if row is None:
            continue
        value = _checked_json(row[0], row[1])
        if value['runId'] != step['runId'] or value['status'] != row[2] or value['receiptSha256'] != row[3]:
            raise ValueError('Retained result claim has mixed columns')
        results.append(value)
    return results


def _aggregate(envelope, results):
    if len(results) != 2:
        raise ValueError('Prepared aggregate requires both result claims')
    return {'version': 1, 'contract': 'prepared-validation-aggregate-v1',
            'setId': envelope['setId'], 'generation': envelope['generation'],
            'envelopeSha256': digest(envelope), 'results': sorted(results, key=lambda item: item['stepId'])}


def retain_prepared_aggregate_claim(conn, set_id):
    """Append the pair aggregate inside the caller's live-authority transaction."""
    _require_write_transaction(conn)
    row = conn.execute(f'SELECT {_bounded_text("envelope", 131072)},envelope_sha256 FROM prepared_validation_sets WHERE id=?',
                       (contract._id(set_id),)).fetchone()
    if row is None:
        raise ValueError('Prepared validation set is missing')
    envelope = contract._envelope_shape(_checked_json(row[0], row[1]))
    aggregate = _aggregate(envelope, _results(conn, envelope))
    conn.execute('INSERT INTO prepared_validation_aggregates VALUES (?,?,?,?)',
                 (set_id, canonical(aggregate), digest(aggregate), time.time()))
    return _unverified(setId=set_id, aggregateSha256=digest(aggregate))


def record_prepared_aggregate_claim(conn, set_id):
    with _transaction(conn, write=True):
        return retain_prepared_aggregate_claim(conn, set_id)


def _inspect(conn, *, context, review, contributors, source_evidence):
    _require_context(conn, context)
    history = _history(conn, context['objective_id'], context['project_id'])
    if not history:
        raise ValueError('No prepared validation generation has been reserved')
    envelope = history[-1]
    _snapshot(conn, envelope['setId'])
    results = _results(conn, envelope)
    for step in envelope['steps']:
        row = conn.execute('SELECT container_confirmed,staging_confirmed,container_id FROM prepared_validation_cleanups '
                           'WHERE run_id=? ORDER BY sequence DESC LIMIT 1',
                           (step['runId'],)).fetchone()
        resource = conn.execute('SELECT container_id FROM prepared_validation_resources WHERE run_id=?',
                                (step['runId'],)).fetchone()
        if row is None or tuple(row[:2]) != (1, 1) or resource is None or row[2] != resource[0]:
            raise ValueError('Latest prepared generation lacks resource/cleanup claims')
    aggregate = conn.execute(f'SELECT {_bounded_text("record", 32768)},sha256 FROM prepared_validation_aggregates WHERE set_id=?',
                             (envelope['setId'],)).fetchone()
    if aggregate is None or _checked_json(*aggregate) != _aggregate(envelope, results):
        raise ValueError('Latest prepared aggregate is missing or changed')
    return contract.inspect_latest_validation_contract(history, results=results, review=review,
        contributor_ids=contributors, source_evidence=source_evidence,
        receipt_hashes={item['runId']: item['receiptSha256'] for item in results}, context=context,
        # Placeholder consistency assumption for this INERT proposal inspector,
        # not current-root verification. No live root/owner authority is checked;
        # the contract returns all authorization flags false.
        root_authorized=True)


def record_prepared_review_claim(conn, value, *, contributor_ids, source_evidence, context):
    """Retain whole-set review metadata; even an approved claim is unauthenticated."""
    value = _json(value, 2 * 1024**2, 'Prepared review claim')
    with _transaction(conn, write=True):
        inspected = _inspect(conn, context=context, review=value, contributors=contributor_ids, source_evidence=source_evidence)
        request = conn.execute('SELECT objective_id,type FROM requests WHERE id=?', (value['requestId'],)).fetchone()
        if request is None or tuple(request) != (context['objective_id'], 'request.test_review'):
            raise ValueError('Prepared review request must belong to the exact objective')
        conn.execute('INSERT INTO prepared_validation_reviews VALUES (?,?,?,?,?,?,?,?)',
            (value['setId'], value['requestId'], value['reviewerId'], canonical(value), digest(value),
             canonical(sorted(set(contributor_ids))), canonical(source_evidence), time.time()))
    return inspected


def inspect_latest_prepared_proposal(conn, *, context):
    """Inspect only the latest generation; incomplete/failed history never falls back."""
    with _transaction(conn):
        history = _history(conn, context['objective_id'], context['project_id'])
        if not history:
            raise ValueError('No prepared validation generation has been reserved')
        latest = history[-1]
        row = conn.execute(f'SELECT {_bounded_text("record", 2 * 1024**2)},sha256,'
                           f'{_bounded_text("contributors", 2 * 1024**2)},'
                           f'{_bounded_text("source_evidence", 2 * 1024**2)} FROM prepared_validation_reviews WHERE set_id=?',
                           (latest['setId'],)).fetchone()
        if row is None:
            raise ValueError('Latest prepared generation has no complete review claim')
        if any(type(item) is not str for item in (row[2], row[3])):
            raise ValueError('Retained prepared review metadata exceeds its byte bound')
        contributors = _json(b'{"items":' + row[2].encode('utf-8') + b'}',
                             2 * 1024**2 + 10, 'Retained contributor identities')['items']
        source = _json(row[3].encode('utf-8'), 2 * 1024**2, 'Retained source evidence')
        return _inspect(conn, context=context, review=_checked_json(row[0], row[1]),
                        contributors=contributors, source_evidence=source)

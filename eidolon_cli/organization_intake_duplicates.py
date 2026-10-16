"""Conservative exact-repeat links, pinned to the unchanged objective contract.

A repeated owner request is not evidence of a semantic duplicate. Only an exact
intake admission captured by this version can be linked; all other candidates
remain proposals for review. Links never edit objective state or spending.
"""
import json
import time

from eidolon_cli.organization_intake_context import digest
from eidolon_cli.organization_projects import objective_projects, selected_projects

DUPLICATE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_intake_options (
 intake_id TEXT PRIMARY KEY REFERENCES organization_request_intakes(id),
 force_separate INTEGER NOT NULL CHECK(force_separate IN (0,1)));
CREATE TABLE IF NOT EXISTS organization_intake_admission_scopes (
 intake_id TEXT PRIMARY KEY REFERENCES organization_request_intakes(id),
 objective_id TEXT NOT NULL UNIQUE REFERENCES objectives(id),
 scope_sha256 TEXT NOT NULL, scope_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS organization_intake_duplicate_links (
 intake_id TEXT PRIMARY KEY REFERENCES organization_request_intakes(id),
 original_intake_id TEXT NOT NULL REFERENCES organization_request_intakes(id),
 objective_id TEXT NOT NULL REFERENCES objectives(id), scope_sha256 TEXT NOT NULL,
 created REAL NOT NULL);
'''
for _table in ('organization_intake_options', 'organization_intake_admission_scopes', 'organization_intake_duplicate_links'):
    for _action in ('UPDATE', 'DELETE'):
        DUPLICATE_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS {_table}_{_action.lower()}_immutable '
            f'BEFORE {_action} ON {_table} BEGIN SELECT RAISE(ABORT, \'Intake decision receipts are immutable\'); END;\n')


def contract_view(conn, objective_id):
    # The typed-contract rollout may follow this module. Missing implementation
    # is permitted only when no typed-contract tables exist in the ledger.
    try:
        from eidolon_cli.organization_outcome_contracts import contract_view as view
    except ModuleNotFoundError as exc:
        if exc.name != 'eidolon_cli.organization_outcome_contracts':
            raise
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('objective_contract_revisions','objective_contract_acceptances','objective_human_judgments')").fetchone():
            raise ValueError('Typed contract reader is unavailable') from None
        return None
    return view(conn, objective_id)


def objective_scope(conn, objective_id):
    row = conn.execute('SELECT o.id,o.title,o.description,o.priority,o.cancelled,c.criteria,c.delivery_mode,'
        'c.required_checks,c.amended_scope,c.status,a.manager_id,a.executive_id,coalesce(h.archived,0) archived '
        'FROM objectives o JOIN objective_control c ON c.objective_id=o.id '
        'JOIN objective_assignments a ON a.objective_id=o.id '
        'LEFT JOIN objective_history h ON h.objective_id=o.id WHERE o.id=?', (objective_id,)).fetchone()
    if row is None:
        raise ValueError('Duplicate objective is unavailable')
    value = dict(row)
    # Lifecycle is checked independently: progression through the ordinary
    # pipeline need not change the meaning of an otherwise identical request.
    for key in ('cancelled', 'archived', 'status'):
        value.pop(key)
    value['projects'] = objective_projects(conn, objective_id)
    value['contract'] = contract_view(conn, objective_id)
    return value


def remember_admission(conn, intake_id, objective_id):
    scope = objective_scope(conn, objective_id)
    conn.execute('INSERT INTO organization_intake_admission_scopes VALUES (?,?,?,?)',
                 (intake_id, objective_id, digest(scope), json.dumps(scope, sort_keys=True)))


def separate_requested(conn, intake_id):
    row = conn.execute('SELECT force_separate FROM organization_intake_options WHERE intake_id=?', (intake_id,)).fetchone()
    return bool(row[0]) if row is not None else False


def exact_duplicate(store, conn, intake, proposal):
    if proposal['deliveryMode'] == 'source_project':
        return None  # A repeated incident needs a current source revision pin.
    option = conn.execute('SELECT force_separate FROM organization_intake_options WHERE intake_id=?', (intake['id'],)).fetchone()
    if option is None or option[0] or proposal['dependencyCandidateIds'] or proposal['question']:
        return None
    projects = selected_projects(proposal['projectIds'], store.settings)
    candidates = conn.execute('SELECT i.*,s.scope_sha256,s.scope_json FROM organization_request_intakes i '
        'JOIN organization_intake_admission_scopes s ON s.intake_id=i.id '
        'JOIN objectives o ON o.id=i.objective_id JOIN objective_control c ON c.objective_id=o.id '
        'LEFT JOIN objective_history h ON h.objective_id=o.id '
        "WHERE i.status='admitted' AND i.owner_identity_id=? AND i.request_text=? AND o.cancelled=0 "
        "AND coalesce(h.archived,0)=0 AND c.status NOT IN ('accepted','legacy_completed') ORDER BY i.created,i.id",
        (intake['owner_identity_id'], intake['request_text'])).fetchall()
    matches = []
    for old in candidates:
        original = json.loads(old['proposal_json'])
        # Fresh source observations are part of the interpretation, even for
        # managed reports. Changed or newly available context is not a repeat.
        old_inspection = json.loads(old['context_json']).get('freshInspection') or {}
        new_inspection = json.loads(intake['context_json']).get('freshInspection') or {}
        if any(old_inspection.get(key) != new_inspection.get(key) for key in ('status', 'items', 'authoritySha256')):
            continue
        if old['owner_priority'] != intake['owner_priority']:
            continue
        keys = ('routeId', 'executiveId', 'managerId', 'projectIds', 'deliveryMode', 'effectivePriority', 'acceptanceCriteria', 'assumptions')
        if any(original[key] != proposal[key] for key in keys):
            continue
        scope = objective_scope(conn, old['objective_id'])
        if digest(scope) != old['scope_sha256'] or scope != json.loads(old['scope_json']):
            continue
        if scope['projects'] != projects:
            continue
        matches.append((old['id'], old['objective_id'], old['scope_sha256']))
    # Multiple matching intentional objectives are ambiguous, not permission to
    # choose an arbitrary one. Owner can submit a separate objective explicitly.
    return matches[0] if len(matches) == 1 else None


def link_duplicate(conn, intake_id, match):
    original_id, objective_id, scope_sha256 = match
    conn.execute('INSERT INTO organization_intake_duplicate_links VALUES (?,?,?,?,?)',
                 (intake_id, original_id, objective_id, scope_sha256, time.time()))
    conn.execute("UPDATE organization_request_intakes SET status='linked',reason=? WHERE id=?",
                 ('Exact repeat linked to the existing open objective; its scope and priority were preserved.', intake_id))

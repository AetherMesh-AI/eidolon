"""Explicit immutable prepared-profile selection for newly admitted objectives.

A saved profile is availability, not an objective-wide grant. Callers retain an
exact revision/hash and still revalidate owner authority at every execution and
acceptance boundary. This module does not dispatch or authenticate tool receipts.
"""
from pathlib import Path
import json
import re
import time

from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_prepared_validation_contract import legacy_project_binding_sha256


PREPARED_OBJECTIVE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_registration_bindings (
 objective_id TEXT NOT NULL, project_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
 registration_sha256 TEXT NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(objective_id,project_id),
 FOREIGN KEY(objective_id,project_id) REFERENCES prepared_validation_bindings(objective_id,project_id));
CREATE TRIGGER IF NOT EXISTS no_replace_prepared_validation_registration_bindings
 BEFORE INSERT ON prepared_validation_registration_bindings
 WHEN EXISTS(SELECT 1 FROM prepared_validation_registration_bindings
 WHERE objective_id=NEW.objective_id AND project_id=NEW.project_id)
 BEGIN SELECT RAISE(ABORT,'Prepared objective selection is immutable'); END;
'''
for _operation in ('UPDATE', 'DELETE'):
    PREPARED_OBJECTIVE_SCHEMA += (
        f'CREATE TRIGGER IF NOT EXISTS immutable_prepared_registration_binding_{_operation.lower()} '
        f'BEFORE {_operation} ON prepared_validation_registration_bindings '
        "BEGIN SELECT RAISE(ABORT,'Prepared objective selection is immutable'); END;\n")


def normalize_prepared_selections(value):
    if value is None:
        return []
    if type(value) is not list or len(value) > 8:
        raise ValueError('preparedValidations must list at most eight exact profile references')
    result, identifiers = [], set()
    for item in value:
        if type(item) is not dict or set(item) != {'projectId', 'revision', 'registrationSha256'}:
            raise ValueError('Prepared selection requires exact projectId, revision and registrationSha256')
        if (type(item['projectId']) is not str or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', item['projectId'])
                or item['projectId'] in identifiers or type(item['revision']) is not int
                or not 1 <= item['revision'] <= 2**53 - 1 or type(item['registrationSha256']) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', item['registrationSha256'])):
            raise ValueError('Prepared selections require unique canonical projects and exact revisions/hashes')
        identifiers.add(item['projectId'])
        result.append(dict(item))
    return sorted(result, key=lambda item: item['projectId'])


def _live_registration(conn, project_id):
    from eidolon_cli.organization_metadata import _inspect
    from eidolon_cli.organization_owner_policy import _source
    from eidolon_cli.organization_prepared_validation_setup import current_prepared_registration
    descriptor = _inspect(conn)
    if descriptor['state'] != 'ready':
        raise ValueError('Prepared validation requires the current served owner identity')
    metadata = descriptor['metadata']
    database = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), None)
    if not database or Path(database).name != 'state.db' or Path(database).parent.name != 'organization':
        raise ValueError('Prepared validation requires the served organization ledger')
    policy = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()
    if policy is None:
        raise ValueError('Prepared validation requires current organization policy')
    _, source_revision, _ = _source(Path(database).parent.parent, conn)
    try:
        return current_prepared_registration(conn, project_id,
        organization_id=metadata['organizationId'], owner_id=metadata['owner']['ownerId'],
            policy_generation=policy['generation'], source_revision=source_revision)
    except RuntimeError as error:
        raise ValueError('Prepared registration authority is unavailable or stale') from error


def select_prepared_validations(conn, value, *, projects, settings, delivery_mode, checks):
    references = normalize_prepared_selections(value)
    if not references:
        return [], checks
    if delivery_mode != 'managed_artifact' or 'source_integration' in checks:
        raise ValueError('Prepared validation supports reviewed managed artifacts, not source publication')
    registry = {project['id']: project for project in projects}
    if {item['projectId'] for item in references} != set(registry):
        raise ValueError('Every selected project must explicitly bind its prepared validation profile')
    if not {'read_file', 'run_tests'} <= set(settings.tool_grants):
        raise ValueError('Prepared validation requires existing read_file and run_tests authority')
    selected = []
    for reference in references:
        current = _live_registration(conn, reference['projectId'])
        if (current is None or current.get('enabled') is not True
                or any(current.get(key) != reference[key] for key in ('revision', 'registrationSha256'))):
            raise ValueError('Prepared validation registration changed, was disabled, or is missing')
        grant = current['contract']
        if grant['projectBindingSha256'] != legacy_project_binding_sha256(registry[reference['projectId']]):
            raise ValueError('Prepared registration differs from the selected project binding')
        selected.append({'reference': reference, 'contract': grant})
    versions = {item['contract']['profile']['version'] for item in selected}
    if len(versions) != 1:
        raise ValueError('One objective cannot mix prepared profile versions with different required checks')
    required = list(dict.fromkeys(step['checkKind'] for item in selected
                                 for step in item['contract']['profile']['steps']))
    if 'project_build' in checks and 'project_build' not in required:
        raise ValueError('Prepared profile v1 does not authorize a renderer build check')
    units = sum(len(item['contract']['profile']['steps']) for item in selected)
    if settings.max_project_runs < units:
        raise ValueError('Prepared validation requires capacity for every mandatory recipe')
    return selected, list(dict.fromkeys([*checks, *required]))


def bind_selected_prepared_validations(conn, objective_id, selected):
    """Called only after current selection checks inside objective admission."""
    from eidolon_cli.organization_prepared_validation_ledger import _binding_record, _require_write_transaction
    _require_write_transaction(conn)
    for item in selected:
        grant, reference = item['contract'], item['reference']
        binding = _binding_record(objective_id, grant)
        legacy = conn.execute('SELECT binding FROM objective_projects WHERE objective_id=? AND project_id=?',
                              (objective_id, reference['projectId'])).fetchone()
        if legacy is None or legacy_project_binding_sha256(json.loads(legacy[0])) != grant['projectBindingSha256']:
            raise ValueError('Prepared admission differs from its retained project')
        conn.execute('INSERT INTO prepared_validation_bindings VALUES (?,?,?,?,?,?,?)',
            (objective_id, reference['projectId'], canonical(grant), digest(grant), canonical(binding), digest(binding), time.time()))
        conn.execute('INSERT INTO prepared_validation_registration_bindings VALUES (?,?,?,?,?,?,?)',
            (objective_id, reference['projectId'], reference['revision'], reference['registrationSha256'],
             canonical(reference), digest(reference), time.time()))


def current_bound_prepared_validation(conn, objective_id, project_id):
    from eidolon_cli.organization_prepared_validation_ledger import _binding, _bounded_text, _checked_json
    row = conn.execute(f'SELECT revision,registration_sha256,{_bounded_text("record", 4096)} AS record,sha256 FROM prepared_validation_registration_bindings '
                       'WHERE objective_id=? AND project_id=?', (objective_id, project_id)).fetchone()
    if row is None:
        return None
    reference = _checked_json(row['record'], row['sha256'])
    if normalize_prepared_selections([reference]) != [reference] or reference != {
            'projectId': project_id, 'revision': row['revision'], 'registrationSha256': row['registration_sha256']}:
        raise ValueError('Retained prepared selection changed')
    current = _live_registration(conn, project_id)
    grant, binding = _binding(conn, objective_id, project_id)
    if (current is None or current.get('enabled') is not True or current['contract'] != grant
            or any(current.get(key) != reference[key] for key in ('revision', 'registrationSha256'))):
        raise ValueError('Prepared objective authority changed or was revoked')
    return {'reference': reference, 'contract': grant, 'binding': binding, 'registration': current}


def validate_selected_prepared_checks(conn, objective_id, projects, checks):
    """Preserve the entire mandatory pair when scope/plans change after selection."""
    from eidolon_cli.organization_acceptance import validate_project_checks
    from eidolon_cli.organization_prepared_validation_authority import has_prepared_binding
    selected = [project for project in projects if has_prepared_binding(conn, objective_id, project['id'])]
    if not selected:
        validate_project_checks(projects, checks)
        return
    if len(selected) != len(projects):
        raise ValueError('Prepared objectives require exact profiles for every selected project')
    from eidolon_cli.organization_project_validation_profile import profile_required_checks
    profiles = [current_bound_prepared_validation(conn, objective_id, project['id'])['contract']['profile']
                for project in selected]
    if len({profile['version'] for profile in profiles}) != 1:
        raise ValueError('One objective cannot mix prepared profile versions')
    required = set(profile_required_checks(profiles[0]))
    kinds = set(checks)
    if (not required <= kinds or 'source_integration' in kinds
            or 'project_build' in kinds and 'project_build' not in required):
        raise ValueError('Prepared scope must preserve its mandatory checks and cannot add source publication')
    validate_project_checks(projects, [check for check in checks if check not in required])


def prepared_selection_references(conn, objective_id):
    """Bounded immutable references for owner-visible projection, not authority."""
    from eidolon_cli.organization_prepared_validation_authority import has_prepared_binding
    has_prepared_binding(conn, objective_id, None)  # Reject partial binding storage.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prepared_validation_registration_bindings'").fetchone():
        return []
    rows = conn.execute("SELECT CASE WHEN typeof(project_id)='text' AND length(CAST(project_id AS BLOB))<=64 THEN project_id END,"
                        "revision,CASE WHEN typeof(registration_sha256)='text' AND length(CAST(registration_sha256 AS BLOB))=64 THEN registration_sha256 END "
                        'FROM prepared_validation_registration_bindings '
                        'WHERE objective_id=? ORDER BY project_id LIMIT 9', (objective_id,)).fetchall()
    return normalize_prepared_selections([{'projectId': row[0], 'revision': row[1], 'registrationSha256': row[2]} for row in rows])


def prepared_objective_projection(conn, objective_id):
    references = prepared_selection_references(conn, objective_id)
    if not references:
        return {}
    from eidolon_cli.organization_prepared_validation_status import prepared_validation_status
    return {'preparedValidations': references,
            'preparedValidation': prepared_validation_status(conn, objective_id, history_limit=20)}

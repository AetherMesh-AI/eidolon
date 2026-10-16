"""Live prepared-profile authority, independent of retained execution claims."""
import json
import time

from eidolon_cli.organization_prepared_validation_objectives import current_bound_prepared_validation
from eidolon_cli.organization_project_validation import digest


EXECUTION_CONTRACT = 'prepared-validation-v1'


def has_prepared_binding(conn, objective_id, project_id):
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'prepared_validation_registration_bindings', 'prepared_validation_bindings'}
    if not names & required:
        return False
    if not required <= names:
        raise ValueError('Prepared objective binding storage is incomplete')
    selected = conn.execute('SELECT 1 FROM prepared_validation_registration_bindings WHERE objective_id=? AND project_id=?',
                           (objective_id, project_id)).fetchone()
    retained = conn.execute('SELECT 1 FROM prepared_validation_bindings WHERE objective_id=? AND project_id=?',
                           (objective_id, project_id)).fetchone()
    if bool(selected) != bool(retained):
        raise ValueError('Prepared objective selection has incomplete retained authority')
    return selected is not None


def prepared_request(conn, request):
    payload = json.loads(request['payload'])
    selected = has_prepared_binding(conn, request['objective_id'], payload.get('projectId'))
    marked = payload.get('executionContract') == EXECUTION_CONTRACT
    if marked != selected:
        raise ValueError('Prepared request must name its exact explicitly selected execution contract')
    if payload.get('executionContract') not in (None, EXECUTION_CONTRACT):
        raise ValueError('Project execution contract is unsupported')
    return selected


def require_prepared_authority(store, conn, objective_id, project_id, *, execution=False):
    store._require_current_policy(conn)
    selected = current_bound_prepared_validation(conn, objective_id, project_id)
    if selected is None:
        raise ValueError('Objective has no explicit current prepared validation selection')
    project = store._execution_project(conn, objective_id, project_id)
    if project is None:
        raise ValueError('Prepared validation requires an exact selected project')
    control = conn.execute('SELECT delivery_mode,required_checks,round FROM objective_control WHERE objective_id=?',
                           (objective_id,)).fetchone()
    checks = set(json.loads(control['required_checks']))
    if (control['delivery_mode'] != 'managed_artifact' or checks & {'source_integration', 'project_build'}
            or not {'project_typecheck', 'project_tests'} <= checks):
        raise ValueError('Prepared validation requires the complete pair and managed artifact delivery')
    authors = store._project_execution_authors(conn, objective_id, project)
    grant = selected['contract']
    root = project['root']
    if root != grant['root']['alias'] or root in store.settings.revoked_root_aliases:
        raise ValueError('Prepared project root changed or was revoked')
    if execution:
        from eidolon_cli.organization_budget import budget_view
        objective = conn.execute('SELECT cancelled FROM objectives WHERE id=?', (objective_id,)).fetchone()
        if objective is None or objective['cancelled'] or time.time() >= budget_view(conn, objective_id, store.settings)['deadlineTimestamp']:
            raise ValueError('Prepared execution objective was cancelled or its deadline expired')
    return {**selected, 'project': project, 'contributors': sorted(authors),
            'round': control['round'], 'ownerGrantSha256': digest(grant),
            'objectiveBindingSha256': digest(selected['binding']),
            'policyGeneration': store._policy_generation, 'policyFingerprint': store._policy_fingerprint}

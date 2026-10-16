"""Read-only identities for retained, contract-bound accepted deliverables.

These receipts certify historical evidence in SQLite, not current external source
bytes or permission to execute. Source delivery/integration and unbound legacy
acceptances are deliberately ineligible. Consumers must pin the returned receipt
and compare it with a fresh receipt before consuming a dependency; never replace
an old pin automatically. Goal/milestone scope identity is shared, not redefined.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from eidolon_cli.organization_outcome_contracts import CONTRACT_TABLES, contract_view, digest, has_typed
from eidolon_cli.organization_project_validation import canonical


@dataclass(frozen=True)
class ArtifactDigest:
    id: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class AcceptedDependencyReceipt:
    objective_id: str
    scope_sha256: str
    contract_revision: int
    contract_sha256: str
    accepted_round: int
    acceptance_request_id: str
    acceptance_sha256: str
    criterion_bindings_sha256: str
    evidence_provenance_sha256: str
    artifacts: tuple[ArtifactDigest, ...]
    projects_json: str
    workspace_json: str

    def to_dict(self):
        """Return a detached JSON value; mutating it cannot change the receipt."""
        return {'schemaVersion': 1, 'kind': 'accepted_historical_deliverable',
                'liveSourceVerified': False, 'objectiveId': self.objective_id,
                'scopeSha256': self.scope_sha256,
                'outcomeContract': {'objectiveId': self.objective_id, 'revision': self.contract_revision,
                                    'sha256': self.contract_sha256},
                'acceptedRound': self.accepted_round, 'acceptanceRequestId': self.acceptance_request_id,
                'acceptanceSha256': self.acceptance_sha256,
                'criterionBindingsSha256': self.criterion_bindings_sha256,
                'evidenceProvenanceSha256': self.evidence_provenance_sha256,
                'artifacts': [asdict(item) for item in self.artifacts],
                'projects': json.loads(self.projects_json), 'workspace': json.loads(self.workspace_json)}

    @property
    def sha256(self):
        return digest(self.to_dict())


def _require(condition, reason):
    if not condition:
        raise ValueError('Accepted dependency ' + reason)


def _request(conn, identifier, objective_id, kind):
    row = conn.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone()
    _require(row is not None and row['objective_id'] == objective_id and row['type'] == kind
             and row['status'] == 'completed', 'requires its real completed ' + kind + ' request')
    return row


def _artifact(conn, identifier, objective_id):
    from eidolon_cli.organization_acceptance import final_artifact
    from eidolon_cli.organization_project_workspace import project_validation_artifact
    from eidolon_cli.organization_project_execution import project_execution_artifact
    row = conn.execute('SELECT * FROM evidence WHERE id=?', (identifier,)).fetchone()
    artifact = dict(row, kind='task_deliverable') if row else final_artifact(conn, identifier)
    if artifact is None:
        artifact = project_validation_artifact(conn, identifier) or project_execution_artifact(conn, identifier)
    _require(artifact is not None and artifact.get('objective_id', artifact.get('objectiveId')) == objective_id,
             'artifact is missing or belongs to another objective')
    _require(hashlib.sha256(artifact['content'].encode('utf-8')).hexdigest() == artifact['sha256'],
             'artifact bytes changed or are corrupt')
    return artifact


def _task_evidence(conn, objective_id, settings, round_number):
    from eidolon_cli.organization_acceptance import current_tasks
    from eidolon_cli.organization_edits import evidence_proposal
    from eidolon_cli.organization_packages import packages_complete
    from eidolon_cli.organization_receipts import OrganizationReceiptStore, evidence_receipts
    from eidolon_cli.organization_selective_repair import verify_reused
    # These are the persisted inputs of the existing read-only reuse verifier,
    # not a live store, execution lease, or authority to create new acceptance.
    policy_generation = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    verify_reused(SimpleNamespace(settings=settings, _policy_generation=policy_generation,
        _verify_tool_evidence=OrganizationReceiptStore._verify_tool_evidence), conn, objective_id, round_number)
    _require(packages_complete(conn, objective_id), 'has incomplete manager packages')
    tasks = current_tasks(conn, objective_id)
    _require(bool(tasks), 'has no current tasks')
    identifiers, provenance = [], []
    for task in tasks:
        _require(task['status'] == 'completed', 'has an incomplete current task')
        evidence = conn.execute('SELECT * FROM evidence WHERE task_id=? ORDER BY created DESC,id DESC LIMIT 1',
                                (task['id'],)).fetchone()
        _require(evidence is not None, 'has missing current task evidence')
        artifact = _artifact(conn, evidence['id'], objective_id)
        work = _request(conn, evidence['request_id'], objective_id, task['type'])
        _require(work['task_id'] == task['id'] and work['agent_id'] == task['author_id'],
                 'task evidence author or request changed')
        review = conn.execute('SELECT * FROM reviews WHERE task_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                              (task['id'],)).fetchone()
        _require(review is not None and review['approved'] == 1 and review['agent_id'] != task['author_id']
                 and json.loads(review['evidence_ids']) == [evidence['id']], 'lacks exact independent task approval')
        request = _request(conn, review['request_id'], objective_id, 'request.review')
        payload = json.loads(request['payload'])
        _require(request['agent_id'] == review['agent_id'] and request['task_id'] == task['id']
                 and payload.get('evidenceIds') == [evidence['id']]
                 and payload.get('evidenceHashes') == {evidence['id']: artifact['sha256']}, 'task review binding changed')
        OrganizationReceiptStore._verify_tool_evidence(conn, evidence['id'], require_read=task['type'] in {'work.inspect', 'work.edit'})
        if task['type'] == 'work.edit':
            proposal = evidence_proposal(conn, evidence['id'])
            _require(proposal and proposal.get('validationReceipt', {}).get('status') == 'passed',
                     'edit lost its applied validation')
        identifiers.append(evidence['id'])
        provenance.append({'task': dict(task), 'workPayload': json.loads(work['payload']),
                           'review': dict(review), 'reviewPayload': payload,
                           'toolReceipts': evidence_receipts(conn, evidence['id'])})
    return identifiers, digest(provenance)


def _project_artifacts(conn, objective_id, control, settings, projects):
    from eidolon_cli.organization_project_execution import _latest_run, _run_row
    from eidolon_cli.organization_project_workspace import project_validation_view, final_source_manifest
    from eidolon_cli.organization_project_build_artifacts import verified_outputs
    from eidolon_cli.organization_acceptance import required_checks
    from eidolon_cli.organization_project_recipes import execution_check_kind
    identifiers, kinds = [], {}
    proof = project_validation_view(conn, objective_id)
    applied = conn.execute('SELECT 1 FROM edit_applications a JOIN edit_proposals p ON p.id=a.proposal_id '
                           'WHERE p.objective_id=?', (objective_id,)).fetchone()
    checks = required_checks(json.loads(control['required_checks']))
    _require(not (applied or 'managed_validation' in checks) or proof is not None, 'lost its aggregate managed validation')
    if proof is not None:
        _require(proof['status'] == 'passed', 'aggregate validation is not passed')
        manifest, _, _, _ = final_source_manifest(conn, objective_id)
        fields = ('path', 'revision', 'sha256', 'proposalId')
        _require([{key: item[key] for key in fields} for item in proof['manifest']] ==
                 [{key: item[key] for key in fields} for item in manifest],
                 'aggregate validation no longer describes current managed heads')
        identifiers.append(proof['id'])
        kinds[proof['id']] = ('managed_validation', None)
    for check in sorted(set(checks) & {'project_tests', 'project_typecheck', 'project_build'}):
        for project in projects or [None]:
            project_id = project['id'] if project else None
            latest = _latest_run(conn, objective_id, project_id)
            _require(latest is not None, 'lost its current required project run')
            run = _run_row(conn, latest['id'])
            artifact = _artifact(conn, run['id'], objective_id)
            grant = json.loads(run['grant_record'])
            _require(canonical(grant) in [canonical(asdict(item)) for item in settings.project_grants]
                     and execution_check_kind(grant['execution']) == check
                     and (project is None or (grant['id'] == project['recipe'] and grant['execution']['root'] == project['root']))
                     and grant['execution']['root'] not in settings.revoked_root_aliases,
                     'project run authority changed')
            _require(artifact['projectExecution']['status'] == 'passed' and run['round'] == control['round'],
                     'required project run is stale or failed')
            for source in json.loads(run['snapshot']):
                _require(hashlib.sha256(source['content'].encode()).hexdigest() == source['sha256'],
                         'project snapshot bytes changed')
            artifacts = [artifact]
            if check == 'project_build':
                artifacts.extend(verified_outputs(conn, run))
            review = conn.execute('SELECT * FROM project_run_reviews WHERE run_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                                  (run['id'],)).fetchone()
            _require(review is not None and review['approved'] == 1 and review['result_sha256'] == artifact['sha256'],
                     'lost its independent project review')
            request = _request(conn, review['request_id'], objective_id, 'request.test_review')
            payload = json.loads(request['payload'])
            authors = {row[0] for row in conn.execute('SELECT author_id FROM tasks WHERE objective_id=?', (objective_id,))}
            _require(request['agent_id'] == review['reviewer_id'] and request['agent_id'] not in authors
                     and payload.get('runId') == run['id'] and payload.get('projectId') == project_id
                     and payload.get('evidenceIds') == [item['id'] for item in artifacts]
                     and payload.get('evidenceHashes') == {item['id']: item['sha256'] for item in artifacts},
                     'project review binding changed')
            identifiers.extend(item['id'] for item in artifacts)
            kinds.update({item['id']: (check, project_id) for item in artifacts})
    return identifiers, kinds


def _bindings(conn, objective_id, contract, accepted, typed, hashes, final_id, project_kinds):
    from eidolon_cli.organization_acceptance import validate_acceptance_result
    results = json.loads(accepted['criteria_results'])
    validate_acceptance_result({'approved': True, 'criteriaResults': results,
                                'conflicts': json.loads(accepted['conflicts'])},
                               [item['text'] for item in contract['criteria']], list(hashes))
    expected, judgments = [], []
    for criterion, result in zip(contract['criteria'], results):
        if criterion['legacy']:
            continue
        _require(result.get('criterionId') == criterion['id'] and result.get('criterionRevision') == criterion['revision'],
                 'criterion identity changed')
        ids = result['evidenceIds']
        kind = criterion['kind']
        valid = {final_id} if kind in {'deliverable', 'human_judgment'} else {
            identifier for identifier, (check, project) in project_kinds.items()
            if check == criterion['checkKind'] and (criterion.get('projectId') is None or criterion['projectId'] == project)}
        _require(bool(valid) and valid.issubset(ids), 'criterion lost its exact required evidence kind')
        if kind == 'human_judgment':
            judgment = conn.execute('SELECT * FROM objective_human_judgments WHERE objective_id=? AND criterion_id=? '
                'AND contract_sha256=? AND deliverable_sha256=? ORDER BY created DESC,rowid DESC LIMIT 1',
                (objective_id, criterion['id'], contract['sha256'], hashes[final_id])).fetchone()
            _require(judgment is not None and judgment['approved'] == 1, 'lost its explicit approving human judgment')
            _require(judgment['input_sha256'] == digest([objective_id, criterion['id'], contract['sha256'],
                hashes[final_id], True, judgment['reason']]), 'human judgment is corrupt')
            judgments.append(dict(judgment))
        expected.append({'criterionId': criterion['id'], 'criterionRevision': criterion['revision'],
                         'kind': kind, 'evidenceHashes': {identifier: hashes[identifier] for identifier in ids}})
    _require(json.loads(typed['evidence_bindings']) == expected, 'typed evidence binding changed')
    return digest({'bindings': expected, 'humanJudgments': judgments})


def _receipt(conn, objective_id):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    core = {'objectives', 'objective_control', 'objective_acceptances', 'objective_deliverables', 'requests'}
    if not tables & core:
        return None
    _require(core <= tables, 'storage is incomplete')
    row = conn.execute('SELECT c.*,o.description,o.cancelled FROM objective_control c JOIN objectives o '
                       'ON o.id=c.objective_id WHERE c.objective_id=?', (objective_id,)).fetchone()
    if row is None or row['cancelled'] or row['status'] != 'accepted':
        return None
    from eidolon_cli.organization_prepared_validation_objectives import prepared_selection_references
    if prepared_selection_references(conn, objective_id):
        # This ledger-only dependency API cannot promise current filesystem
        # freshness for a prepared source/bundle. Never fall back to old runs.
        return None
    contract = contract_view(conn, objective_id)
    if not has_typed(contract):
        recorded = conn.execute('SELECT q.payload FROM objective_acceptances a JOIN requests q ON q.id=a.request_id '
                               'WHERE a.objective_id=? AND a.round=?', (objective_id, row['round'])).fetchall()
        _require(not any('outcomeContractSha256' in json.loads(item['payload']) for item in recorded),
                 'typed contract is missing or was replaced by a legacy contract')
        return None
    _require(CONTRACT_TABLES <= tables, 'contract storage is incomplete')
    stored = conn.execute('SELECT revision FROM objective_contract_revisions WHERE objective_id=? ORDER BY revision DESC LIMIT 1',
                          (objective_id,)).fetchone()
    _require(contract['objectiveId'] == objective_id and contract['revision'] == stored['revision']
             and contract['scope'] == (row['amended_scope'] or row['description'])
             and contract['deliveryMode'] == row['delivery_mode']
             and contract['requiredChecks'] == json.loads(row['required_checks'])
             and [item['text'] for item in contract['criteria']] == json.loads(row['criteria']),
             'current scope does not match its contract')
    if (row['delivery_mode'] != 'managed_artifact' or 'source_integration' in contract['requiredChecks']
            or any(item['kind'] == 'source_integration' for item in contract['criteria'])):
        return None  # A historical receipt cannot prove a current live-source prerequisite.
    accepted = conn.execute('SELECT * FROM objective_acceptances WHERE objective_id=? AND round=? '
                            'ORDER BY created DESC,rowid DESC LIMIT 1', (objective_id, row['round'])).fetchone()
    _require(accepted is not None and accepted['approved'] == 1, 'has no approved current-round acceptance')
    request = _request(conn, accepted['request_id'], objective_id, 'request.accept')
    payload = json.loads(request['payload'])
    typed = conn.execute('SELECT * FROM objective_contract_acceptances WHERE request_id=?', (request['id'],)).fetchone()
    _require(typed is not None and typed['contract_sha256'] == contract['sha256']
             and payload.get('outcomeContractSha256') == contract['sha256']
             and payload.get('round') == row['round'], 'contract or acceptance round is stale or missing')
    from eidolon_cli.organization_identity import OrganizationIdentityStore
    executive = OrganizationIdentityStore._objective_agent(conn, objective_id, 'Executive')
    _require(executive is not None and request['agent_id'] == accepted['agent_id'] == executive['id'],
             'acceptance is not bound to its independent executive')
    from eidolon_cli.organization_milestones import _objective
    _, projects, scope_sha256 = _objective(conn, objective_id)
    from eidolon_cli.organization_policy import persisted_settings
    from eidolon_cli.organization_projects import validate_objective_projects
    settings = persisted_settings(conn)
    _require(settings is not None, 'has no current persisted policy')
    validate_objective_projects(conn, objective_id, settings)
    from eidolon_cli.organization_project_execution import OrganizationProjectExecutionStore
    coverage = OrganizationProjectExecutionStore()
    coverage.settings = settings
    coverage.verify_project_coverage(conn, objective_id)
    expected, provenance_sha256 = _task_evidence(conn, objective_id, settings, row['round'])
    project_ids, project_kinds = _project_artifacts(conn, objective_id, row, settings, projects)
    expected.extend(project_ids)
    final = _artifact(conn, row['deliverable_id'], objective_id)
    _require(final['kind'] == 'integrated_deliverable' and final['round'] == row['round']
             and final['author_id'] != accepted['agent_id'] and json.loads(final['evidence_ids']) == expected,
             'final aggregate membership, author or round changed')
    integration = _request(conn, final['request_id'], objective_id, 'request.integrate')
    integration_payload = json.loads(integration['payload'])
    _require(integration['agent_id'] == final['author_id'] and integration_payload.get('round') == row['round']
             and integration_payload.get('outcomeContractSha256') == contract['sha256'], 'integration binding changed')
    artifacts = [_artifact(conn, identifier, objective_id) for identifier in [*expected, final['id']]]
    hashes = {item['id']: item['sha256'] for item in artifacts}
    _require(len(hashes) == len(artifacts) and integration_payload.get('evidenceIds') == expected
             and integration_payload.get('evidenceHashes') == {identifier: hashes[identifier] for identifier in expected}
             and payload.get('evidenceIds') == [*expected, final['id']] and payload.get('evidenceHashes') == hashes
             and json.loads(accepted['evidence_ids']) == [*expected, final['id']], 'exact evidence binding changed')
    bindings_sha256 = _bindings(conn, objective_id, contract, accepted, typed, hashes, final['id'], project_kinds)
    workspace = {'workspaces': [dict(item) for item in conn.execute(
                    'SELECT * FROM workspaces WHERE objective_id=? ORDER BY id', (objective_id,))],
                 'roots': [dict(item) for item in conn.execute(
                    'SELECT * FROM workspace_roots WHERE objective_id=? ORDER BY alias', (objective_id,))]}
    from eidolon_cli.organization_edits import _hash, _revision, _verify_revision
    from eidolon_cli.organization_root_grants import expected_root_identities, revoked_aliases
    revoked = set(settings.revoked_root_aliases) | set(revoked_aliases(conn))
    for root in workspace['roots']:
        alias = root['alias']
        _require(alias.startswith('root') and alias[4:].isdigit(), 'workspace root alias is malformed')
        index = int(alias[4:])
        _require(alias not in revoked and index < len(settings.read_roots)
                 and root['root_identity'] == _hash(settings.read_roots[index]), 'workspace root changed or was revoked')
    workspace['rootIdentities'] = {alias: identity for alias, identity in expected_root_identities(settings).items()
                                   if alias in {root['alias'] for root in workspace['roots']}}
    workspace['heads'] = []
    for head in conn.execute('SELECT r.* FROM workspace_heads h JOIN workspace_revisions r '
                            'USING(objective_id,path,revision) WHERE h.objective_id=? ORDER BY path', (objective_id,)):
        verified = _verify_revision(_revision(conn, objective_id, head['path'], head['revision']))
        _require(any(item['id'] == verified['workspace_id'] for item in workspace['workspaces']),
                 'workspace revision identity changed')
        workspace['heads'].append({key: verified[key] for key in ('path', 'revision', 'sha256', 'workspace_id')})
    return AcceptedDependencyReceipt(objective_id, scope_sha256, contract['revision'], contract['sha256'], row['round'],
        request['id'], digest({'acceptance': dict(accepted), 'requestPayload': payload}), bindings_sha256, provenance_sha256,
        tuple(ArtifactDigest(item['id'], item.get('kind', 'managed_validation'), hashes[item['id']]) for item in artifacts),
        canonical(projects), canonical(workspace))


def accepted_dependency_receipt(conn, objective_id):
    """Return an immutable receipt, or None for absent/ineligible acceptance.

    Ineligible means unsettled/cancelled, unbound legacy, or live-source-dependent.
    Missing/partial or inconsistent typed storage raises ValueError, never success.
    The caller supplies a sqlite3.Row connection; an existing transaction is kept.
    Otherwise a read transaction gives all identity checks one consistent snapshot.
    No migrations, store initialization, source reads, model calls or writes occur.
    """
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        try:
            return _receipt(conn, objective_id)
        except (sqlite3.DatabaseError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise ValueError('Accepted dependency storage is incomplete or corrupt') from error
    finally:
        if own_transaction:
            conn.rollback()

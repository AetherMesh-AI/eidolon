"""Multi-file proposal manifests, validation and auditable source handoff.

All mutations remain SQLite-only. A source handoff observes bytes already written
by the owner; it neither writes source files nor authorizes another capability.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from eidolon_cli.organization_project_validation import canonical, digest, normalize_validations, validate_manifest

PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_project_validations (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 manifest_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created REAL NOT NULL,
 UNIQUE(objective_id,round,manifest_sha256));
CREATE TABLE IF NOT EXISTS edit_project_specs (
 proposal_id TEXT PRIMARY KEY REFERENCES edit_proposals(id), files TEXT NOT NULL,
 validations TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_validation_receipts (
 proposal_id TEXT PRIMARY KEY REFERENCES edit_proposals(id), request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 proposal_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_merge_supersessions (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), superseded_by TEXT NOT NULL REFERENCES requests(id),
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_handoff_receipts (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), proposal_id TEXT NOT NULL REFERENCES edit_proposals(id),
 proposal_sha256 TEXT NOT NULL, validation_sha256 TEXT NOT NULL,
 result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created REAL NOT NULL);
"""
for _table in ('edit_project_specs', 'project_validation_receipts', 'project_handoff_receipts', 'project_merge_supersessions', 'objective_project_validations'):
    for _op in ('UPDATE', 'DELETE'):
        PROJECT_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                           "BEGIN SELECT RAISE(ABORT, 'Immutable project record'); END;\n")


def project_spec(conn, proposal):
    row = conn.execute('SELECT * FROM edit_project_specs WHERE proposal_id=?', (proposal['id'],)).fetchone()
    if row is None:
        return None
    value = {'files': json.loads(row['files']), 'validations': json.loads(row['validations'])}
    if digest(value) != row['sha256']:
        raise ValueError('Project manifest is missing or has changed')
    return value


def proposal_files(conn, proposal):
    spec = project_spec(conn, proposal)
    return spec['files'] if spec else [{key: proposal[key] for key in (
        'path', 'base_revision', 'base_sha256', 'old_text', 'new_text', 'new_content',
        'new_sha256', 'diff', 'source_receipt_id')}]


def verify_project_files(conn, proposal):
    from eidolon_cli.organization_edits import _revision, _verify_revision, _safe_content, _hash, _diff, _source_read
    files = proposal_files(conn, proposal)
    if not 1 <= len(files) <= 8 or len({f['path'] for f in files}) != len(files):
        raise ValueError('Project manifest requires unique bounded files')
    total = 0
    for item in files:
        source = _verify_revision(_revision(conn, proposal['objective_id'], item['path'], item['base_revision']))
        create = item.get('operation') == 'create'
        if item.get('operation', 'update') not in {'update', 'create'}:
            raise ValueError('Unsupported project file operation')
        if create:
            exact_change = (not source['source_exists'] and source['revision'] == 0 and item['old_text'] == ''
                            and item['new_content'] == item['new_text'])
        else:
            exact_change = (source['source_exists'] and bool(item['old_text'])
                            and source['content'].find(item['old_text']) >= 0
                            and source['content'].find(item['old_text']) == source['content'].rfind(item['old_text'])
                            and source['content'].replace(item['old_text'], item['new_text'], 1) == item['new_content'])
        if (source['workspace_id'] != proposal['workspace_id'] or source['sha256'] != item['base_sha256']
                or not exact_change
                or _hash(_safe_content(item['new_content'], 'Proposed content')) != item['new_sha256']
                or _diff(item['path'], source['content'], item['new_content'], create=create) != item['diff']):
            raise ValueError('Project file does not match its exact source and operation')
        receipt = conn.execute('SELECT r.* FROM tool_receipts r JOIN evidence_tools e ON e.receipt_id=r.id '
                               'WHERE r.id=? AND e.evidence_id=?',
                               (item['source_receipt_id'], proposal['evidence_id'])).fetchone()
        if not _source_read(receipt, source) or receipt['request_id'] != proposal['request_id']:
            raise ValueError('Project file has no matching persisted successful source read')
        total += len(item['new_content'].encode('utf-8'))
    if total > 131072:
        raise ValueError('Project output exceeds the combined byte limit')
    primary = files[0]
    if any(primary[key] != proposal[key] for key in primary if key != 'operation'):
        raise ValueError('Primary edit does not match its project manifest')
    spec = project_spec(conn, proposal)
    if spec:
        normalize_validations(spec['validations'], {item['path'] for item in files})
    return files


def applied_manifest(conn, proposal, *, current=False):
    from eidolon_cli.organization_edits import _revision, _verify_revision
    files = verify_project_files(conn, proposal)
    applied = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
    if (applied is None or applied['proposal_sha256'] != proposal['sha256']
            or applied['applied_revision'] != proposal['base_revision'] + 1
            or applied['applied_sha256'] != proposal['new_sha256']):
        raise ValueError('Project validation requires an exact committed application')
    manifest = []
    for item in files:
        revision = _verify_revision(_revision(conn, proposal['objective_id'], item['path'], item['base_revision'] + 1))
        if revision['sha256'] != item['new_sha256'] or revision['content'] != item['new_content']:
            raise ValueError('Applied project manifest is missing or has changed')
        if current:
            head = _verify_revision(_revision(conn, proposal['objective_id'], item['path']))
            if head['revision'] != revision['revision'] or head['sha256'] != revision['sha256']:
                raise ValueError('Applied project manifest is stale; replan and review current revisions')
        manifest.append({'path': item['path'], 'revision': revision['revision'],
                         'sha256': revision['sha256'], 'content': revision['content']})
    return manifest


def validation_receipt(conn, proposal, *, verify=True):
    row = conn.execute('SELECT * FROM project_validation_receipts WHERE proposal_id=?', (proposal['id'],)).fetchone()
    if row is None:
        return None
    result = json.loads(row['result'])
    if row['proposal_sha256'] != proposal['sha256'] or digest(result) != row['result_sha256']:
        raise ValueError('Project validation receipt is missing or has changed')
    if verify:
        files = applied_manifest(conn, proposal)
        spec = project_spec(conn, proposal)
        if result != validate_manifest(files, spec['validations'] if spec else []):
            raise ValueError('Project validation receipt disagrees with its exact applied bytes')
    return {**result, 'requestId': row['request_id'], 'resultSha256': row['result_sha256'], 'created': row['created']}


def _validate_final_files(conn, files):
    """Recheck only assertions belonging to each selected latest file revision.

    A failed check on a superseded file stays historically failed; it cannot
    poison an unrelated file that is still the final reviewed output.
    """
    from eidolon_cli.organization_edits import _revision, _verify_revision, _verify_proposal, _verified_review
    observed, sources, verified = [], [], {}
    for item in files:
        if item['proposalId'] not in verified:
            proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (item['proposalId'],)).fetchone()
            if proposal is None:
                raise ValueError('Final source manifest proposal is missing')
            _verify_proposal(conn, proposal)
            application = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
            if application is None:
                raise ValueError('Final source manifest application is missing')
            _verified_review(conn, proposal, application['review_request_id'], require_approved=True)
            verified[item['proposalId']] = (proposal, {f['path']: f for f in applied_manifest(conn, proposal)})
        proposal, applied = verified[item['proposalId']]
        matching = applied.get(item['path'])
        if (matching is None or matching['revision'] != item['revision']
                or matching['sha256'] != item['sha256']):
            raise ValueError('Final source manifest does not match its reviewed proposal')
        revision = _verify_revision(_revision(conn, proposal['objective_id'], item['path'], item['revision']))
        if revision['sha256'] != item['sha256']:
            raise ValueError('Final source manifest revision has changed')
        spec = project_spec(conn, proposal)
        checks = [check for check in spec['validations'] if check['path'] == item['path']] if spec else []
        source = {**item, 'content': revision['content']}
        sources.append(source)
        observation = validate_manifest([source], checks)
        observed.extend(observation['checks'])
    result = validate_manifest(sources, [])
    result['checks'] = observed
    result['status'] = 'passed' if all(item['passed'] for item in observed) else 'failed'
    return result


def final_source_manifest(conn, objective_id):
    """Resolve reviewed lineage to the validated latest revision of each path."""
    from eidolon_cli.organization_edits import _verify_proposal, _verified_review, _revision, _verify_revision
    proposals = conn.execute('SELECT p.*,a.review_request_id FROM edit_proposals p JOIN edit_applications a '
                             'ON a.proposal_id=p.id WHERE p.objective_id=? ORDER BY p.created,p.id', (objective_id,)).fetchall()
    latest, covered, validations = {}, [], []
    for proposal in proposals:
        _verify_proposal(conn, proposal)
        _verified_review(conn, proposal, proposal['review_request_id'], require_approved=True)
        checked = validation_receipt(conn, proposal)
        # Failed historical observations are never relabeled or presented as
        # verified proposals. Their still-current files are checked individually.
        if checked is not None and checked['status'] == 'passed':
            covered.append(proposal['id'])
        validations.append({'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                            'validationSha256': checked['resultSha256'] if checked else None,
                            'status': checked['status'] if checked else 'unknown'})
        for item in applied_manifest(conn, proposal):
            previous = latest.get(item['path'])
            if previous is None or previous['revision'] < item['revision']:
                latest[item['path']] = {**item, 'proposalId': proposal['id']}
    if not latest:
        raise ValueError('Source handoff has no validated managed revisions')
    for item in latest.values():
        head = _verify_revision(_revision(conn, objective_id, item['path']))
        if head['revision'] != item['revision'] or head['sha256'] != item['sha256']:
            raise ValueError('Latest workspace head lacks exact applied and validated lineage')
    manifest = sorted(latest.values(), key=lambda item: item['path'])
    checked = _validate_final_files(conn, manifest)
    if checked['status'] != 'passed':
        raise ValueError('Latest project files do not satisfy their exact declared validation checks')
    return manifest, covered, validations, checked


def handoff_receipt(conn, proposal):
    rows = conn.execute('SELECT h.*,p.objective_id,p.sha256 AS current_proposal_sha FROM project_handoff_receipts h '
                        'JOIN edit_proposals p ON p.id=h.proposal_id WHERE p.objective_id=? ORDER BY h.created DESC',
                        (proposal['objective_id'],)).fetchall()
    for row in rows:
        result = json.loads(row['result'])
        if proposal['id'] not in result.get('coveredProposalIds', []):
            continue
        source_proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (row['proposal_id'],)).fetchone()
        checked_receipt = validation_receipt(conn, source_proposal)
        if (digest(result) != row['result_sha256'] or row['proposal_sha256'] != row['current_proposal_sha']
                or checked_receipt is None or row['validation_sha256'] != checked_receipt['resultSha256']):
            raise ValueError('Source verification receipt is missing or has changed')
        for checked in result['validations']:
            source = conn.execute('SELECT * FROM edit_proposals WHERE id=? AND objective_id=?',
                                  (checked['proposalId'], proposal['objective_id'])).fetchone()
            from eidolon_cli.organization_edits import _verify_proposal
            if source is None or source['sha256'] != checked['proposalSha256']:
                raise ValueError('Source handoff proposal lineage is missing or has changed')
            _verify_proposal(conn, source)
            validation = validation_receipt(conn, source)
            if checked['status'] == 'unknown':
                if checked['validationSha256'] is not None:
                    raise ValueError('Unknown historical validation must not assert a completed receipt')
            elif (validation is None or validation['resultSha256'] != checked['validationSha256']
                    or validation['status'] != checked['status']):
                raise ValueError('Source handoff validation lineage is missing or has changed')
        if result['manifestValidation'] != _validate_final_files(conn, result['files']):
            raise ValueError('Source handoff final-manifest validation has changed')
        from eidolon_cli.organization_edits import _revision
        current = all((head := _revision(conn, proposal['objective_id'], item['path'])) is not None
                      and head['revision'] == item['revision'] and head['sha256'] == item['sha256'] for item in result['files'])
        return {**result, 'status': 'verified' if current else 'superseded',
                'requestId': row['request_id'], 'resultSha256': row['result_sha256'], 'created': row['created']}
    return None


def _project_validation_row(conn, row, *, full):
    if row is None:
        return None
    result = json.loads(row['result'])
    if digest(result) != row['result_sha256'] or digest(result['manifest']) != row['manifest_sha256']:
        raise ValueError('Aggregate managed validation receipt is missing or has changed')
    expected = _validate_final_files(conn, result['manifest'])
    if any(result.get(key) != value for key, value in expected.items() if key != 'scope'):
        raise ValueError('Aggregate managed validation does not match its retained exact bytes')
    for item in result['manifest']:
        proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=? AND objective_id=?',
                                (item['proposalId'], row['objective_id'])).fetchone()
        if proposal is None or proposal['evidence_id'] != item['evidenceId']:
            raise ValueError('Aggregate managed validation artifact lineage is missing')
        from eidolon_cli.organization_edits import _verify_proposal
        _verify_proposal(conn, proposal)
    value = {'id': row['id'], 'objectiveId': row['objective_id'], 'round': row['round'],
             'status': result['status'], 'scope': result['scope'], 'resultSha256': row['result_sha256'],
             'filesCount': len(result['manifest']), 'checksCount': len(result['checks']),
             'notExecuted': result['notExecuted'],
             'createdAt': datetime.fromtimestamp(row['created'], timezone.utc).isoformat()}
    if full:
        value.update(result)
    return value


def project_validation_view(conn, objective_id, *, full=True):
    row = conn.execute('SELECT v.* FROM objective_project_validations v JOIN objective_control c '
                       'ON c.objective_id=v.objective_id AND c.round=v.round WHERE v.objective_id=? '
                       'ORDER BY v.created DESC,v.id DESC LIMIT 1', (objective_id,)).fetchone()
    return _project_validation_row(conn, row, full=full)


def project_validation_artifact(conn, identifier):
    row = conn.execute('SELECT * FROM objective_project_validations WHERE id=?', (identifier,)).fetchone()
    if row is None:
        return None
    proof = _project_validation_row(conn, row, full=True)
    # A partial replan retains unchanged files from older task rounds. The
    # receipt's hashes establish lineage, but final reviewers also need those
    # exact bodies to judge the combined deliverable independently.
    from eidolon_cli.organization_edits import _revision, _verify_revision
    sources = []
    for item in proof['manifest']:
        revision = _verify_revision(_revision(conn, row['objective_id'], item['path'], item['revision']))
        if revision['sha256'] != item['sha256']:
            raise ValueError('Final managed source no longer matches its validation manifest')
        sources.append({**item, 'content': revision['content']})
    return {'id': row['id'], 'objectiveId': row['objective_id'], 'content': row['result'],
            'sha256': row['result_sha256'], 'summary': f'Final managed validation: {proof["filesCount"]} exact files, '
                f'{proof["checksCount"]} content checks. Project commands and functional tests were not executed.',
            'toolReceipts': [], 'projectValidation': proof, 'projectSources': sources, 'createdAt': proof['createdAt']}


class OrganizationProjectStore:
    def prepare_project_acceptance(self, conn, objective_id):
        """Persist the aggregate observation before synthesis/acceptance can cite it."""
        if conn.execute('SELECT 1 FROM edit_applications a JOIN edit_proposals p ON p.id=a.proposal_id '
                        'WHERE p.objective_id=?', (objective_id,)).fetchone() is None:
            return None
        files, covered, history, checked = final_source_manifest(conn, objective_id)
        manifest = []
        for item in files:
            evidence = conn.execute('SELECT evidence_id FROM edit_proposals WHERE id=?', (item['proposalId'],)).fetchone()[0]
            manifest.append({**{key: item[key] for key in ('path', 'revision', 'sha256', 'proposalId')}, 'evidenceId': evidence})
        result = {**checked, 'scope': 'latest_managed_project_heads', 'manifest': manifest,
                  'coveredProposalIds': covered, 'historicalValidations': history}
        control = conn.execute('SELECT round FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        manifest_hash = digest(manifest)
        identifier = 'project_validation_' + digest([objective_id, control['round'], manifest_hash])[:32]
        conn.execute('INSERT OR IGNORE INTO objective_project_validations VALUES (?,?,?,?,?,?,?)',
                     (identifier, objective_id, control['round'], manifest_hash, canonical(result), digest(result), time.time()))
        return _project_validation_row(conn, conn.execute('SELECT * FROM objective_project_validations WHERE id=?',
                                                         (identifier,)).fetchone(), full=True)

    def migrate_open_project_validation(self, conn):
        """Upgrade open legacy merge gates without reopening settled history.

        Old exact applications already contain sufficient immutable source,
        proposal and independent-review records to run content-integrity checks.
        Validation observes those bytes; it never applies an edit a second time.
        """
        from eidolon_cli.organization_edits import _verify_proposal, _verified_review
        pending = conn.execute("SELECT r.* FROM requests r JOIN objectives o ON o.id=r.objective_id "
                               "JOIN objective_control c ON c.objective_id=o.id WHERE r.type='request.merge' "
                               "AND r.status='pending_intervention' AND o.cancelled=0 "
                               "AND c.status NOT IN ('accepted','legacy_completed')").fetchall()
        for merge in pending:
            payload = json.loads(merge['payload'])
            proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (payload.get('proposalId'),)).fetchone()
            if proposal is not None and conn.execute('SELECT 1 FROM project_validation_receipts WHERE proposal_id=?',
                                                     (proposal['id'],)).fetchone():
                continue
            if conn.execute("SELECT 1 FROM requests WHERE type='request.validate' AND json_extract(payload,'$.proposalId')=?",
                            (payload.get('proposalId'),)).fetchone():
                continue
            try:
                if (proposal is None or proposal['objective_id'] != merge['objective_id']
                        or proposal['sha256'] != payload.get('proposalSha256')):
                    raise ValueError('Legacy merge is not bound to its exact applied proposal')
                _verify_proposal(conn, proposal)
                applied_manifest(conn, proposal)
                application = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
                _verified_review(conn, proposal, application['review_request_id'], require_approved=True)
            except ValueError:
                self._pending(conn, merge, 'Legacy source integration cannot be validated because its exact applied bytes or independent review are incomplete. Restore the retained evidence or replan; no source writes were performed.')
                continue
            original = conn.execute('SELECT * FROM requests WHERE id=?', (proposal['request_id'],)).fetchone()
            self._request(conn, proposal['objective_id'], 'request.validate', original['team'], original['priority'],
                          proposal['task_id'], {'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                                                'evidenceIds': [proposal['evidence_id']], 'legacyApplication': True})
            conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (proposal['task_id'],))
            self._event(conn, proposal['objective_id'],
                        'Retained legacy application queued for exact managed-content validation; original source integration remains pending.',
                        'planning')

    def edit_validate_unavailability(self, conn, request):
        try:
            proposal = self._request_proposal(conn, request)
            self._require_edit_grant(conn, proposal['author_id'], request['team'], applying=True)
            for item in proposal_files(conn, proposal):
                self._verify_root(conn, request['objective_id'], item['path'])
            applied_manifest(conn, proposal)
        except ValueError as error:
            return str(error)
        return None

    def _finish_validate(self, conn, request, result):
        from eidolon_cli.organization_edits import _verify_proposal, _verified_review
        request = self._owned(conn, request)
        if request is None or request['type'] != 'request.validate':
            raise ValueError('Project validation requires a current validation lease')
        if not isinstance(result, dict) or result:
            raise ValueError('Backend project validation accepts no model-provided results')
        self._require_project_controller(conn, request)
        reason = self.edit_validate_unavailability(conn, request)
        if reason:
            raise ValueError(reason)
        proposal = self._request_proposal(conn, request)
        _verify_proposal(conn, proposal)
        application = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
        _verified_review(conn, proposal, application['review_request_id'], require_approved=True)
        files = applied_manifest(conn, proposal)
        spec = project_spec(conn, proposal)
        observed = validate_manifest(files, spec['validations'] if spec else [])
        previous = validation_receipt(conn, proposal)
        if previous is None:
            conn.execute('INSERT INTO project_validation_receipts VALUES (?,?,?,?,?,?)',
                         (proposal['id'], request['id'], proposal['sha256'], canonical(observed), digest(observed), time.time()))
        if observed['status'] != 'passed':
            # Retain the failed immutable observation, and park a separate owner
            # gate. Raising would roll back the very evidence needed for diagnosis.
            gate = self._request(conn, request['objective_id'], 'request.validation_failed', request['team'], request['priority'],
                                 request['task_id'], {'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                                                      'evidenceIds': [proposal['evidence_id']]})
            self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (gate,)).fetchone(),
                          'Declared validation failed against the committed managed bytes. Inspect the retained checks and revise the task; project tests were not executed.')
            return
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (request['task_id'],))
        self._event(conn, request['objective_id'],
                    'Managed content validation passed; project commands and functional tests were not executed.', 'completion', request['agent_id'])
        self._queue_source_handoff(conn, request, proposal)

    @staticmethod
    def _require_project_controller(conn, request):
        agent = conn.execute('SELECT * FROM agents WHERE id=?', (request['agent_id'],)).fetchone()
        if (agent is None or agent['role'] != 'Worker' or agent['team'] != request['team']
                or agent['id'] not in {'control:apply', 'control:apply:' + hashlib.sha256(request['team'].encode()).hexdigest()[:16]}
                or set(json.loads(agent['accepts'])) != {'request.apply', 'request.validate'}):
            raise ValueError('Only the matching backend-controlled workspace applier can apply or validate edits')

    def _queue_source_handoff(self, conn, request, proposal):
        if self._automatic_source(conn, request['objective_id']):
            return
        if self._execution_required(conn, request['objective_id']):
            # Test the reviewed managed bytes before asking the owner to alter
            # originals. Verified handoff can then preserve that same snapshot.
            from eidolon_cli.organization_project_execution import project_execution_view
            from eidolon_cli.organization_projects import task_project
            project = task_project(conn, request['task_id'])
            execution = project_execution_view(conn, request['objective_id'],
                                               project_id=project['id'] if project else None)
            if execution is None or not execution['review'] or not execution['review']['approved']:
                return
        objective = conn.execute('SELECT delivery_mode,required_checks FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        # Existing databases retain their original explicit source-integration
        # semantics. The owner-loop migration supplies delivery_mode for new work.
        if objective and objective['delivery_mode'] == 'managed_artifact' and 'source_integration' not in json.loads(objective['required_checks']):
            return
        if conn.execute("SELECT 1 FROM requests WHERE type='request.merge' AND status IN ('queued','running','pending_intervention') AND json_extract(payload,'$.proposalId')=?",
                        (proposal['id'],)).fetchone():
            return
        merge = self._request(conn, request['objective_id'], 'request.merge', request['team'], request['priority'],
                             payload={'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                                      'evidenceIds': [proposal['evidence_id']], 'taskId': request['task_id'],
                                      'workspaceId': proposal['workspace_id'], 'sourcePath': proposal['path'],
                                      'appliedRevision': proposal['base_revision'] + 1})
        for old in conn.execute("SELECT id FROM requests WHERE objective_id=? AND type='request.merge' AND id!=? "
                                "AND status IN ('queued','pending_intervention')", (request['objective_id'], merge)).fetchall():
            conn.execute('INSERT INTO project_merge_supersessions VALUES (?,?,?)', (old['id'], merge, time.time()))
            conn.execute("UPDATE requests SET status='cancelled',reason=? WHERE id=?",
                         ('Superseded by the later project source manifest; this earlier gate was not verified.', old['id']))
        self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (merge,)).fetchone(),
                      'Reviewed and validated managed output is ready. Original source files are unchanged; '
                      'apply the exact output yourself, then verify the source handoff. This action grants no source-write authority.')

    @staticmethod
    def _verified_final_source(conn, objective_id, manifest, checked):
        proposal = conn.execute('SELECT p.* FROM project_handoff_receipts h JOIN edit_proposals p ON p.id=h.proposal_id '
                                'WHERE p.objective_id=? ORDER BY h.created DESC LIMIT 1', (objective_id,)).fetchone()
        if proposal is None:
            return False
        receipt = handoff_receipt(conn, proposal)
        expected = [{key: item[key] for key in ('path', 'revision', 'sha256', 'proposalId')} for item in manifest]
        return bool(receipt and receipt['status'] == 'verified' and receipt['files'] == expected
                    and receipt['manifestValidation'] == checked)

    def ensure_source_handoff(self, conn, objective_id):
        """Keep the source delivery obligation alive when a later round changes work."""
        self.verify_project_coverage(conn, objective_id)
        if self._automatic_source(conn, objective_id):
            return not self.verified_source_integration(conn, objective_id)
        control = conn.execute('SELECT delivery_mode,required_checks FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        proposal = conn.execute('SELECT p.* FROM edit_proposals p JOIN edit_applications a ON a.proposal_id=p.id '
                               'WHERE p.objective_id=? ORDER BY a.created DESC,p.id DESC LIMIT 1', (objective_id,)).fetchone()
        if proposal is None or (control['delivery_mode'] != 'source_project' and 'source_integration' not in json.loads(control['required_checks'])):
            return False
        try:
            manifest, _, _, checked = final_source_manifest(conn, objective_id)
            if self._verified_final_source(conn, objective_id, manifest, checked):
                return False
        except ValueError:
            # The visible source gate retains the obligation and offers replan;
            # it cannot offer successful handoff until exact validation exists.
            pass
        request = conn.execute('SELECT * FROM requests WHERE id=?', (proposal['request_id'],)).fetchone()
        self._queue_source_handoff(conn, request, proposal)
        return True

    def verify_project_objective_acceptance(self, conn, objective_id, *, require_source=False, require_validation=False):
        """Final evidence fence spans retained project state across objective rounds."""
        if conn.execute('SELECT 1 FROM edit_applications a JOIN edit_proposals p ON p.id=a.proposal_id '
                        'WHERE p.objective_id=?', (objective_id,)).fetchone() is None:
            if require_source or require_validation:
                raise ValueError('Required project validation or source integration has no applied project evidence')
            return
        manifest, _, _, checked = final_source_manifest(conn, objective_id)
        control = conn.execute('SELECT delivery_mode,required_checks FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        if (control['delivery_mode'] == 'source_project' or require_source) and not (self.verified_source_integration(conn, objective_id) or self._verified_final_source(conn, objective_id, manifest, checked)):
            raise ValueError('Source-project acceptance requires verified exact latest source integration, including retained prior-round edits')
        self.prepare_project_acceptance(conn, objective_id)

    def _handoff_proposal(self, conn, request, evidence_ids=None):
        from eidolon_cli.organization_edits import _verify_proposal, _verified_review
        if request['type'] != 'request.merge':
            raise ValueError('Only an exact source merge gate can verify a handoff')
        payload = json.loads(request['payload'])
        proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (payload.get('proposalId'),)).fetchone()
        if (proposal is None or proposal['objective_id'] != request['objective_id']
                or proposal['sha256'] != payload.get('proposalSha256')
                or proposal['task_id'] != payload.get('taskId')
                or (evidence_ids is not None and evidence_ids != [proposal['evidence_id']])):
            raise ValueError('Source handoff requires the exact applied proposal and evidence')
        _verify_proposal(conn, proposal)
        application = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
        if application is None:
            raise ValueError('Source handoff requires an applied proposal')
        _verified_review(conn, proposal, application['review_request_id'], require_approved=True)
        checked = validation_receipt(conn, proposal)
        if checked is None or checked['status'] != 'passed':
            raise ValueError('Source handoff requires passed exact managed-content validation')
        self._require_edit_grant(conn, proposal['author_id'], request['team'], applying=True)
        for item in proposal_files(conn, proposal):
            self._verify_root(conn, request['objective_id'], item['path'])
        applied_manifest(conn, proposal)
        final_source_manifest(conn, proposal['objective_id'])
        return proposal, checked

    def owner_handoff_descriptor(self, conn, request):
        if request['type'] != 'request.merge':
            return None
        try:
            proposal, _ = self._handoff_proposal(conn, request)
        except ValueError:
            return None
        manifest, _, _, _ = final_source_manifest(conn, request['objective_id'])
        source_manifest = []
        for item in manifest:
            evidence = conn.execute('SELECT evidence_id FROM edit_proposals WHERE id=?', (item['proposalId'],)).fetchone()[0]
            source_manifest.append({**{key: item[key] for key in ('path', 'revision', 'sha256', 'proposalId')},
                                    'evidenceId': evidence})
        return {'action': 'record_handoff', 'label': 'Verify original project matches reviewed output',
                'requiresText': True, 'requiresEvidence': True, 'evidenceIds': [proposal['evidence_id']],
                'sourceManifest': source_manifest}

    def record_owner_handoff(self, conn, request, text, evidence_ids):
        from tools.organization_file_read import organization_file_read_scope
        self._require_current_policy(conn)
        proposal, checked = self._handoff_proposal(conn, request, evidence_ids)
        manifest, covered, validations, manifest_validation = final_source_manifest(conn, proposal['objective_id'])
        for item in manifest:
            self._verify_root(conn, request['objective_id'], item['path'])
        observations = []
        # The current policy transaction fences concurrent policy revocation. The
        # descriptor readers have per-file byte bounds and refuse links/devices.
        with organization_file_read_scope(self.settings.read_roots, self.settings.max_tool_result_chars) as scope:
            for item in manifest:
                observed = scope.read_exact_source(item['path'])
                if observed['sha256'] != item['sha256']:
                    raise ValueError('Original source does not match the exact validated output; no handoff was recorded')
                observations.append({'path': item['path'], 'sha256': observed['sha256'], 'revision': item['revision'],
                                     'proposalId': item['proposalId']})
        self._require_current_policy(conn)
        result = {'scope': 'latest_validated_project_heads', 'files': observations,
                  'coveredProposalIds': covered, 'validations': validations, 'manifestValidation': manifest_validation,
                  'status': 'verified', 'sourceWritesPerformed': False,
                  'limitations': 'Exact source bytes observed per file during verification; later external changes are not monitored. No project commands or functional tests executed.'}
        conn.execute('INSERT INTO project_handoff_receipts VALUES (?,?,?,?,?,?,?)',
                     (request['id'], proposal['id'], proposal['sha256'], checked['resultSha256'],
                      canonical(result), digest(result), time.time()))

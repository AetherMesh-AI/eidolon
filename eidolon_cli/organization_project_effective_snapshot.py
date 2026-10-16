"""Retained, reviewed managed replacements over an explicit source selection.

This read-only adapter creates no grant, files, runtime or source publication.
Only existing-file replacements are supported: an absent managed preimage is
rejected until the bounded snapshot contract can prove absence safely.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
import time

from eidolon_cli.organization_edits import (
    OrganizationEditStore, _revision, _verify_revision,
)
from eidolon_cli.organization_project_snapshot_bundle import (
    MAX_CAPTURE_SECONDS, ProjectSnapshotBundle, ProjectSnapshotError,
    ProjectSnapshotFile, ProjectSnapshotLimits, _check_no_secrets,
    _execution_only_policy, _validate_root_identity, _validate_selection,
    capture_project_snapshot,
)
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_workspace import (
    final_source_manifest, proposal_files,
)

_MAX_MANAGED_APPLICATIONS = 10_000
_MAX_MANAGED_HISTORY_ROWS = 100_000
_MAX_MANAGED_HISTORY_BYTES = 128 * 1024 * 1024
_OBJECTIVE_PROPOSALS = 'SELECT id FROM edit_proposals WHERE objective_id=:objective_id'
_OBJECTIVE_EVIDENCE = ('SELECT id FROM evidence WHERE objective_id=:objective_id UNION '
                       'SELECT evidence_id FROM edit_proposals WHERE objective_id=:objective_id')
_OBJECTIVE_REVIEWS = ('SELECT request_id FROM edit_reviews WHERE proposal_id IN ('
                      + _OBJECTIVE_PROPOSALS + ') UNION SELECT review_request_id FROM edit_applications '
                      'WHERE proposal_id IN (' + _OBJECTIVE_PROPOSALS + ')')
_HISTORY_FIELDS = (
    ('edit_proposals', 'objective_id=:objective_id',
     'id request_id objective_id task_id evidence_id evidence_sha256 author_id workspace_id '
     'path base_revision base_sha256 old_text new_text new_content new_sha256 diff sha256 source_receipt_id created'),
    ('workspace_revisions', 'objective_id=:objective_id', 'objective_id path revision workspace_id content sha256 created'),
    ('workspace_heads', 'objective_id=:objective_id', 'objective_id path revision'),
    ('workspace_roots', 'objective_id=:objective_id', 'objective_id alias root_identity'),
    ('workspace_absent_sources', 'objective_id=:objective_id', 'objective_id path'),
    ('evidence', 'id IN (' + _OBJECTIVE_EVIDENCE + ')', 'id objective_id task_id request_id content sha256 summary created'),
    ('edit_project_specs', 'proposal_id IN (' + _OBJECTIVE_PROPOSALS + ')',
     'proposal_id files validations sha256'),
    ('edit_applications', 'proposal_id IN (' + _OBJECTIVE_PROPOSALS + ')',
     'proposal_id request_id review_request_id proposal_sha256 applied_revision applied_sha256 created'),
    ('edit_reviews', 'request_id IN (' + _OBJECTIVE_REVIEWS + ')',
     'request_id proposal_id proposal_sha256 evidence_sha256 reviewer_id approved created'),
    ('reviews', 'request_id IN (' + _OBJECTIVE_REVIEWS + ')',
     'request_id task_id agent_id approved summary evidence_ids created'),
    ('requests', 'objective_id=:objective_id OR id IN (' + _OBJECTIVE_REVIEWS + ')', 'id objective_id task_id type team priority status created agent_id token lease attempts execution_count reason available payload'),
    ('project_validation_receipts', 'proposal_id IN (' + _OBJECTIVE_PROPOSALS + ')',
     'proposal_id request_id proposal_sha256 result result_sha256 created'),
    ('evidence_tools', 'evidence_id IN (' + _OBJECTIVE_EVIDENCE + ')',
     'evidence_id receipt_id'),
    ('tool_receipts', 'id IN (SELECT receipt_id FROM evidence_tools WHERE evidence_id IN '
     '(' + _OBJECTIVE_EVIDENCE + '))',
     'id request_id attempt tool_call_id tool_name arguments status result result_sha256 created completed reason'),
)


def _check_history_bounds(conn, objective_id, check):
    # SQLite computes UTF-8 byte lengths without returning historical bodies to
    # Python. Cover all roots and unselected revisions because the existing
    # lineage verifier examines the complete objective's applied history. Include
    # referenced rows even when their own objective binding is corrupt: those
    # bodies are loaded before the existing verifier rejects the mismatch.
    rows = size = 0
    for table, condition, fields in _HISTORY_FIELDS:
        check()
        lengths = '+'.join(f'coalesce(length(CAST({name} AS BLOB)),0)' for name in fields.split())
        count, byte_count = conn.execute(
            f'SELECT count(*),coalesce(sum({lengths}),0) FROM {table} WHERE {condition}',
            {'objective_id': objective_id}).fetchone()
        rows += count
        size += byte_count
        if rows > _MAX_MANAGED_HISTORY_ROWS or size > _MAX_MANAGED_HISTORY_BYTES:
            raise ProjectSnapshotError('Managed source history exceeds its retained row or byte bound')



@dataclass(frozen=True)
class ManagedSnapshotRevision:
    """Exact persisted application/review provenance, without source bodies."""

    path: str
    workspace_id: str
    revision: int
    sha256: str
    base_revision: int
    base_sha256: str
    proposal_id: str
    proposal_sha256: str
    evidence_id: str
    evidence_sha256: str
    source_receipt_id: str
    review_request_id: str
    reviewer_id: str
    application_request_id: str


@dataclass(frozen=True)
class EffectiveProjectSnapshot:
    """Retained original and effective bytes; metadata hashes grant no authority.

    ``source_evidence`` is metadata-only. Execution-only bytes must not enter
    prompts through either retained bundle, including their original preimages.
    """

    objective_id: str
    original: ProjectSnapshotBundle = field(repr=False)
    effective: ProjectSnapshotBundle = field(repr=False)
    managed_revisions: tuple[ManagedSnapshotRevision, ...]
    preimage_sha256: str = field(init=False)
    managed_revisions_sha256: str = field(init=False)
    execution_authorized: bool = field(default=False, init=False)

    def __post_init__(self):
        if (type(self.original) is not ProjectSnapshotBundle
                or type(self.effective) is not ProjectSnapshotBundle
                or self.original.root_alias != self.effective.root_alias
                or self.original.root_identity != self.effective.root_identity
                or self.original.limits != self.effective.limits
                or tuple(item.path for item in self.original.files)
                != tuple(item.path for item in self.effective.files)
                or type(self.managed_revisions) is not tuple
                or any(type(item) is not ManagedSnapshotRevision for item in self.managed_revisions)):
            raise ProjectSnapshotError('Effective snapshot requires exact original coverage and immutable provenance')
        object.__setattr__(self, 'preimage_sha256', digest(self.preimage_manifest()))
        object.__setattr__(self, 'managed_revisions_sha256', digest(self.managed_manifest()))

    @property
    def snapshot_sha256(self):
        return self.effective.snapshot_sha256

    def preimage_manifest(self):
        return {'version': 1, 'contract': 'effective-source-preimages-v1',
                'source': self.original.manifest()}

    def managed_manifest(self):
        return {'version': 1, 'contract': 'effective-source-managed-revisions-v1',
                'objectiveId': self.objective_id, 'rootAlias': self.effective.root_alias,
                'revisions': [asdict(item) for item in self.managed_revisions]}

    def source_evidence(self):
        """Fresh metadata, safe even when original/effective files are execution-only."""
        return {'snapshotSha256': self.snapshot_sha256,
                'preimageSha256': self.preimage_sha256,
                'managedRevisionsSha256': self.managed_revisions_sha256,
                'original': self.original.manifest(), 'effective': self.effective.manifest(),
                'managedRevisions': self.managed_manifest()}


def _managed_state(conn, objective_id, root_alias, source, paths, check):
    # The aggregate budget and body reads must observe the same SQLite view.
    # Close our short read transaction before any original-source capture; an
    # admission caller may instead provide its own existing transaction.
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        _check_history_bounds(conn, objective_id, check)
        return _read_managed_state(conn, objective_id, root_alias, source, paths, check)
    finally:
        if own_transaction:
            conn.rollback()


def _read_managed_state(conn, objective_id, root_alias, source, paths, check):
    """Reuse complete durable review verification before selecting a root."""
    check()
    if conn.execute('SELECT 1 FROM objectives WHERE id=?', (objective_id,)).fetchone() is None:
        raise ProjectSnapshotError('Effective snapshot requires an existing objective')
    count = conn.execute('SELECT count(*) FROM edit_applications a JOIN edit_proposals p '
                         'ON p.id=a.proposal_id WHERE p.objective_id=?', (objective_id,)).fetchone()[0]
    if count > _MAX_MANAGED_APPLICATIONS:
        raise ProjectSnapshotError('Managed application history exceeds the snapshot provenance bound')
    # This verifies every proposal, exact successful source-read evidence,
    # independent approved review, committed application, and latest head/checks.
    final = final_source_manifest(conn, objective_id)[0] if count else []
    prefix = root_alias + '/'
    latest = {item['path'][len(prefix):]: item for item in final if item['path'].startswith(prefix)}
    if set(latest) - set(paths):
        raise ProjectSnapshotError('Source selection must include every reviewed applied project edit')
    heads = conn.execute('SELECT path,revision FROM workspace_heads WHERE objective_id=? '
                         'AND substr(path,1,?)=? AND revision>0',
                         (objective_id, len(prefix), prefix)).fetchall()
    if {row['path'][len(prefix):] for row in heads} != set(latest):
        raise ProjectSnapshotError('Managed project heads lack exact reviewed application lineage')
    if latest:
        root = conn.execute('SELECT root_identity FROM workspace_roots WHERE objective_id=? AND alias=?',
                            (objective_id, root_alias)).fetchone()
        if root is None or root['root_identity'] != hashlib.sha256(source.encode('utf-8')).hexdigest():
            raise ProjectSnapshotError('Managed source root no longer matches its captured alias')
    baselines = {}
    for path, item in latest.items():
        check()
        baseline = _verify_revision(_revision(conn, objective_id, item['path'], 0))
        if not baseline['source_exists']:
            raise ProjectSnapshotError('Managed file creation is unsupported by existing-file snapshot capture')
        baselines[path] = baseline['content'].encode('utf-8')
    records = []
    proposals = conn.execute('SELECT p.*,a.review_request_id,a.request_id AS application_request_id,'
                             'r.reviewer_id FROM edit_proposals p JOIN edit_applications a '
                             'ON a.proposal_id=p.id JOIN edit_reviews r ON r.request_id=a.review_request_id '
                             'WHERE p.objective_id=?', (objective_id,)).fetchall()
    for proposal in proposals:
        check()
        application = conn.execute('SELECT * FROM requests WHERE id=? AND objective_id=?',
            (proposal['application_request_id'], objective_id)).fetchone()
        payload = json.loads(application['payload']) if application is not None else None
        if (application is None or application['type'] != 'request.apply' or application['status'] != 'completed'
                or type(payload) is not dict or payload.get('proposalId') != proposal['id']
                or payload.get('reviewRequestId') != proposal['review_request_id']):
            raise ProjectSnapshotError('Managed application request is missing, incomplete or has changed')
        if OrganizationEditStore._request_proposal(conn, application)['id'] != proposal['id']:
            raise ProjectSnapshotError('Managed application request names a different reviewed proposal')
        for item in proposal_files(conn, proposal):
            if item['path'].startswith(prefix):
                records.append(ManagedSnapshotRevision(
                    path=item['path'][len(prefix):], workspace_id=proposal['workspace_id'],
                    revision=item['base_revision'] + 1, sha256=item['new_sha256'],
                    base_revision=item['base_revision'], base_sha256=item['base_sha256'],
                    proposal_id=proposal['id'], proposal_sha256=proposal['sha256'],
                    evidence_id=proposal['evidence_id'], evidence_sha256=proposal['evidence_sha256'],
                    source_receipt_id=item['source_receipt_id'], review_request_id=proposal['review_request_id'],
                    reviewer_id=proposal['reviewer_id'], application_request_id=proposal['application_request_id']))
    ordered = tuple(sorted(records, key=lambda row: (row.path, row.revision, row.proposal_id)))
    if len({(row.path, row.revision) for row in ordered}) != len(ordered):
        raise ProjectSnapshotError('Managed revision has ambiguous application lineage')
    check()
    return latest, baselines, ordered


def capture_effective_project_snapshot(conn, objective_id, source, files, *, root_alias,
                                       expected_root_identity, check_authorized, limits,
                                       deadline_monotonic, cancel=None,
                                       reviewed_execution_only_hashes=None):
    """Capture selected original files and overlay only exact reviewed replacements.

    Use a live autocommit connection. Retained review lineage is verified before
    and after capture using short bounded read transactions; no writes are made. The trusted
    authority callback must cover this objective/root/selection/hash policy and
    revocation, just as for ``capture_project_snapshot``. Callers must recheck
    current source, lineage and authority before dispatch and final acceptance.
    Source bytes already published outside the managed workspace are rejected
    when they no longer match the original inspected preimage.
    """
    _validate_root_identity(root_alias, expected_root_identity)
    if (type(objective_id) is not str or not objective_id
            or type(limits) is not ProjectSnapshotLimits or not callable(check_authorized)):
        raise ProjectSnapshotError('Effective snapshot requires an objective, explicit limits and authority checker')
    now = time.monotonic()
    if (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + MAX_CAPTURE_SECONDS):
        raise ProjectSnapshotError('Effective snapshot requires a current bounded deadline')
    if cancel is not None and not callable(getattr(cancel, 'is_set', None)):
        raise ProjectSnapshotError('Effective snapshot cancellation requires a cancellation event')
    source = os.fspath(source)
    if type(source) is not str:
        raise ProjectSnapshotError('Effective snapshot requires an exact source directory string')

    if conn.in_transaction:
        raise ProjectSnapshotError('Effective snapshot requires a live autocommit connection')

    def check():
        if cancel is not None and cancel.is_set():
            raise ProjectSnapshotError('Effective snapshot capture cancelled')
        if time.monotonic() >= deadline_monotonic:
            raise ProjectSnapshotError('Effective snapshot capture deadline exceeded')
        if check_authorized() is not True:
            raise ProjectSnapshotError('Effective snapshot root or selection grant was revoked')
        if cancel is not None and cancel.is_set():
            raise ProjectSnapshotError('Effective snapshot capture cancelled')
        if time.monotonic() >= deadline_monotonic:
            raise ProjectSnapshotError('Effective snapshot capture deadline exceeded')

    check()
    paths = _validate_selection(files, limits, check)
    exceptions = _execution_only_policy(reviewed_execution_only_hashes, paths, check)
    state = _managed_state(conn, objective_id, root_alias, source, paths, check)
    latest, baselines, revisions = state
    # A policy approving final bytes cannot exempt a different original file.
    # Managed preimages were already subject to the strict edit-content policy.
    original = capture_project_snapshot(source, paths, root_alias=root_alias,
        expected_root_identity=expected_root_identity, check_authorized=check_authorized,
        limits=limits, deadline_monotonic=deadline_monotonic, cancel=cancel,
        reviewed_execution_only_hashes={path: value for path, value in exceptions.items() if path not in latest})
    effective = []
    for item in original.files:
        check()
        content = item.content
        if item.path in latest:
            if content != baselines[item.path]:
                raise ProjectSnapshotError('Source preimage changed after inspection; replan against current source')
            content = latest[item.path]['content'].encode('utf-8')
        if item.path in exceptions:
            if hashlib.sha256(content).hexdigest() != exceptions[item.path]:
                raise ProjectSnapshotError('Reviewed execution-only hash does not match effective source bytes')
        else:
            _check_no_secrets(content)
        effective.append(ProjectSnapshotFile(item.path, content, item.mode, item.path in exceptions))
    bundle = ProjectSnapshotBundle(root_alias, expected_root_identity, tuple(effective), limits)
    if _managed_state(conn, objective_id, root_alias, source, paths, check) != state:
        raise ProjectSnapshotError('Reviewed managed revisions changed during source capture')
    result = EffectiveProjectSnapshot(objective_id, original, bundle, revisions)
    check()
    return result


def verify_effective_snapshot_lineage(conn, snapshot, source, *, check_authorized):
    """Recheck retained managed evidence without filesystem reads or mutations.

    A caller-held transaction may fence admission against new managed revisions.
    This does not recheck original filesystem bytes or confer execution authority;
    the caller must establish those separately before dispatch or acceptance.
    """
    if type(snapshot) is not EffectiveProjectSnapshot or not callable(check_authorized):
        raise ProjectSnapshotError('Lineage verification requires a retained effective snapshot and authority checker')

    def check():
        if check_authorized() is not True:
            raise ProjectSnapshotError('Effective snapshot authority was revoked')

    paths = tuple(item.path for item in snapshot.original.files)
    latest, baselines, revisions = _managed_state(
        conn, snapshot.objective_id, snapshot.original.root_alias, os.fspath(source), paths, check)
    if revisions != snapshot.managed_revisions:
        raise ProjectSnapshotError('Retained managed snapshot provenance is no longer current')
    for original, effective in zip(snapshot.original.files, snapshot.effective.files):
        check()
        expected = original.content
        if original.path in latest:
            if original.content != baselines[original.path]:
                raise ProjectSnapshotError('Retained original preimage disagrees with reviewed workspace source')
            expected = latest[original.path]['content'].encode('utf-8')
        if expected != effective.content or original.mode != effective.mode:
            raise ProjectSnapshotError('Retained effective bytes disagree with reviewed managed revisions')
    check()
    return True

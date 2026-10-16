"""Disclosure and stale inspection checks; synthetic receipts are not runtime proof."""
from dataclasses import replace
import hashlib
import json

import pytest

from eidolon_cli import organization_prepared_validation_review_scope as scope
from eidolon_cli.organization_project_effective_snapshot import EffectiveProjectSnapshot
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle, ProjectSnapshotFile
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    state, prepared, objective, proposal, reserve, normalize,  # noqa: F401
)


def receipt(path, content, *, truncated=False, offset=1):
    lines = content.decode().splitlines()
    result = json.dumps({'success': True, 'content': '\n'.join(f'{i}|{line}' for i, line in enumerate(lines, 1)),
                         'file_size': len(content), 'total_lines': len(lines), 'truncated': truncated})
    return {'status': 'completed', 'toolName': 'read_file', 'arguments': {'path': path, 'offset': offset},
            'result': result, 'resultSha256': hashlib.sha256(result.encode()).hexdigest()}


@pytest.mark.parametrize('change', ['stale', 'partial', 'offset', 'outside', 'execution_only'])
def test_inspection_is_bound_to_complete_current_disclosed_source(state, monkeypatch, change):
    obj = objective(state)
    original = obj['snapshot']
    file = next(item for item in original.files if item.path == 'package.json')
    observation = receipt('root0/package.json', file.content)
    if change == 'stale': observation = receipt('root0/package.json', b'{"old":true}')
    if change == 'partial': observation = receipt('root0/package.json', file.content, truncated=True)
    if change == 'offset': observation = receipt('root0/package.json', file.content, offset=2)
    if change == 'outside': observation = receipt('root1/package.json', file.content)
    if change == 'execution_only':
        original = ProjectSnapshotBundle(original.root_alias, original.root_identity,
            tuple(replace(item, execution_only=True) if item.path == file.path else item for item in original.files), original.limits)
    snapshot = EffectiveProjectSnapshot(obj['ctx']['objective_id'], original, original, ())
    monkeypatch.setattr('eidolon_cli.organization_receipts.evidence_receipts', lambda *_: [observation])
    with pytest.raises(ValueError):
        scope._bind_read_observations(state['conn'], 'fixture-evidence', snapshot)


def test_current_complete_text_observation_preserves_exact_scope(state, monkeypatch):
    obj = objective(state)
    file = next(item for item in obj['snapshot'].files if item.path == 'package.json')
    snapshot = EffectiveProjectSnapshot(obj['ctx']['objective_id'], obj['snapshot'], obj['snapshot'], ())
    monkeypatch.setattr('eidolon_cli.organization_receipts.evidence_receipts',
                        lambda *_: [receipt('root0/package.json', file.content)])
    scope._bind_read_observations(state['conn'], 'fixture-evidence', snapshot)


def test_essential_execution_only_test_is_not_disclosed(state, monkeypatch):
    obj = objective(state)
    snapshot = EffectiveProjectSnapshot(obj['ctx']['objective_id'], obj['snapshot'], obj['snapshot'], ())
    obj['ctx'].update(preimage_sha256=snapshot.preimage_sha256,
                      managed_revisions_sha256=snapshot.managed_revisions_sha256)
    value = proposal(state, obj)
    reserve(state, obj, value)
    monkeypatch.setattr(scope, 'reviewed_source_evidence', lambda *_: {'fixture-evidence': 'a'*64})
    state['conn'].execute('BEGIN IMMEDIATE')
    with pytest.raises(ValueError, match='unavailable for model disclosure'):
        scope.retain_review_scope(state['store'], state['conn'], value, snapshot,
                                  {'contract': normalize(state['prepared'])})
    state['conn'].rollback()
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_review_scopes').fetchone()[0] == 0

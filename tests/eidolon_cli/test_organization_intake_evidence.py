"""Retained-only intake observations exercise the real scoped reader and ledger."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_intake_contract import Context, Evidence, Route, validate_proposal
from eidolon_cli.organization_intake_evidence import (
    MAX_CANDIDATES, MAX_ITEMS, MAX_PUBLIC_BYTES, collect_intake_evidence,
)
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition
from tools import organization_file_read as bounded


@pytest.fixture
def source(tmp_path):
    root = tmp_path.resolve() / 'project'
    root.mkdir()
    (root / 'README.md').write_text('A small offline project.\n', encoding='utf-8')
    settings = OrganizationSettings.from_config({'organization': {
        'team': 'engineering', 'capabilities': ['work.inspect'],
        'tool_grants': ['read_file', 'list_files', 'search_files'], 'read_roots': [str(root)],
        'projects': [{'id': 'alpha', 'root': 'root0', 'recipe': None, 'team': 'engineering'}],
        'roster': [{'id': 'reader', 'name': 'Reader', 'team': 'engineering',
                    'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'list_files', 'search_files']}]}})
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', settings)
    return store, root


def _inspect(store, *, linked=True, names=('README.md',), operation='read_file'):
    if linked:
        intake, _ = store.begin_intake('Inspect the project documentation', idempotency_key='inspect')
        store.reserve_intake_call(intake['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
        proposal = {'snapshotId': intake['snapshotId'], 'routeId': 'manager', 'projectIds': ['alpha'],
            'deliveryMode': 'managed_artifact', 'priority': 'P3', 'rationale': 'Read project context.',
            'evidenceIds': ['owner-request'], 'acceptanceCriteria': ['Explain the observed documentation'],
            'assumptions': [], 'duplicateCandidateIds': [], 'dependencyCandidateIds': [], 'question': None}
        store.finish_intake(intake['id'], json.dumps(proposal))
        objective = store.admit_intake(intake['id'])['objectiveId']
    else:
        objective = store.create_objective('Inspect project', project_ids=['alpha'],
            delivery_mode='managed_artifact', idempotency_key='legacy')['id']
    plan = claim_after_decomposition(store)
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Read documentation',
        'description': 'Read only granted context.', 'type': 'work.inspect', 'team': 'engineering',
        'agentId': 'reader', 'projectId': 'alpha', 'dependsOn': []}]})
    claim = store.claim_next()
    if claim['type'] == 'request.hire':
        store.finish(claim, {})
        claim = store.claim_next()
    assert claim['type'] == 'work.inspect'
    receipts = []
    with bounded.organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
        for index, name in enumerate(names):
            args = {'path': 'root0/' + name, 'offset': 1, 'limit': 2000}
            started = store.record_tool_start(claim, str(index), 'read_file', args)
            raw = scope.read_file(**args)
            status = 'completed' if json.loads(raw)['success'] else 'blocked'
            receipts.append(store.record_tool_finish(claim, started['id'], raw, status))
        if operation != 'read_file':
            args = {'path': 'root0', 'limit': 20}
            if operation == 'search_files':
                args['query'] = 'project'
            started = store.record_tool_start(claim, 'discovery', operation, args)
            raw = scope.discover_files(args, search=operation == 'search_files')
            receipts.append(store.record_tool_finish(claim, started['id'], raw, 'completed'))
    return objective, claim, receipts


def _review(store, claim):
    store.finish(claim, {'summary': 'Observed project documentation', 'deliverable': 'Historical documentation context.'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    store.finish(review, {'approved': True, 'summary': 'Observation independently reviewed.',
                         'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    return review


def _collect(store, **kwargs):
    with store._connect() as conn:
        return collect_intake_evidence(store, conn, **kwargs)


def _public(value):
    return json.dumps(value['public'], ensure_ascii=False, allow_nan=False)


def test_no_preobjective_authority_never_opens_sources_or_activates_worker(source, monkeypatch):
    store, root = source
    before = store.snapshot()
    monkeypatch.setattr(bounded, '_open_root', lambda *a, **k: pytest.fail('Intake opened a source root'))
    monkeypatch.setattr(bounded, '_open_file', lambda *a, **k: pytest.fail('Intake opened a source file'))
    result = _collect(store)
    assert result['public']['status'] == result['public']['freshReadStatus'] == 'unavailable'
    assert not result['public']['items']
    assert 'pre-objective' in result['public']['freshReadReason']
    assert str(root) not in _public(result)
    assert store.snapshot()['agents'] == before['agents']
    assert store.snapshot()['objectives'] == before['objectives']
    no_grants = OrganizationStore(store.path, replace(store.settings, tool_grants=()))
    assert not _collect(no_grants)['public']['items']


@pytest.mark.linux_only
def test_reviewed_retained_reads_are_redacted_historical_and_no_live_io(source, monkeypatch):
    store, root = source
    token = 'ghp_' + 'a' * 32
    (root / 'README.md').write_text(f'Project history: startup failed in 2020.\n{token}\n'
        f'password=opaque-credential\nLocation {root}/private.txt and /srv/customer/private.txt\n'
        'Windows C:\\Users\\owner\\private.txt\nhttps://site.test/?token=private-url-secret\n', encoding="utf-8")
    _, claim, receipts = _inspect(store)
    _review(store, claim)
    (root / 'README.md').unlink()
    (root / 'README.md').symlink_to('/etc/passwd')
    monkeypatch.setattr(bounded, '_open_root', lambda *a, **k: pytest.fail('Fresh root open'))
    monkeypatch.setattr(bounded, '_open_file', lambda *a, **k: pytest.fail('Fresh file open'))
    result = _collect(store)
    item = result['public']['items'][0]
    assert item['path'] == 'root0/README.md'
    assert item['freshness'] == 'historical_unverified'
    assert item['verifiedCurrentBlocker'] is item['acceptanceEvidence'] is False
    assert '2020' in item['excerpt'] and item['redacted']
    for secret in (token, 'opaque-credential', 'private-url-secret', str(root), '/srv/customer', 'C:\\Users'):
        assert secret not in _public(result)
    assert result['private']['receipts'][0]['resultSha256'] == receipts[0]['resultSha256']
    assert 'result' not in result['private']['receipts'][0]
    assert _collect(store) == result
    context = Context('snapshot', (Route('manager', 'executive', 'manager', ('alpha',)),),
                      (Evidence(item['id']),), ())
    with pytest.raises(ValueError, match='current blocker'):
        validate_proposal(json.dumps({'snapshotId': 'snapshot', 'routeId': 'manager',
            'projectIds': ['alpha'], 'deliveryMode': 'source_project', 'priority': 'P1',
            'rationale': 'An old read reported a failure', 'evidenceIds': [item['id']],
            'acceptanceCriteria': ['Fix startup'], 'assumptions': [], 'duplicateCandidateIds': [],
            'dependencyCandidateIds': [], 'question': None}), context)


@pytest.mark.linux_only
@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'protected', 'malformed'])
def test_actual_scoped_reader_denials_are_never_reused_as_intake_evidence(source, attack):
    store, root = source
    target = root / 'README.md'
    target.unlink()
    outside = root.parent / 'outside.txt'
    outside.write_text('Private external source bytes', encoding="utf-8")
    names = ('README.md',)
    if attack == 'symlink':
        target.symlink_to(outside)
    elif attack == 'hardlink':
        os.link(outside, target)
    elif attack == 'protected':
        (root / '.env').write_text('PASSWORD=private-secret', encoding="utf-8")
        names = ('.env',)
    else:
        target.write_bytes(b'\xff\x00invalid')
    _, _, receipts = _inspect(store, names=names)
    assert receipts[0]['status'] == 'blocked'
    result = _collect(store)
    assert not result['public']['items']
    assert 'Private external' not in _public(result) and 'private-secret' not in _public(result)


@pytest.mark.linux_only
@pytest.mark.parametrize('attack', ['owner', 'legacy', 'binding', 'grant', 'policy', 'unreviewed', 'hash', 'arguments', 'json', 'context', 'blob', 'oversized', 'protected_alias', 'nonfinite'])
def test_current_owner_binding_policy_and_review_fail_closed(source, attack):
    store, _ = source
    _, claim, receipts = _inspect(store, linked=attack != 'legacy')
    if attack != 'unreviewed':
        review = _review(store, claim)
    with store._write() as conn:
        if attack == 'owner':
            conn.execute("UPDATE agent_identity SET identity_id='different-owner' WHERE agent_id='owner'")
        elif attack == 'binding':
            conn.execute('DROP TRIGGER immutable_objective_projects_update')
            row = conn.execute('SELECT binding FROM objective_projects').fetchone()
            binding = json.loads(row[0])
            binding['readRoot'] += '-changed'
            conn.execute('UPDATE objective_projects SET binding=?', (json.dumps(binding),))
        elif attack == 'hash':
            conn.execute("UPDATE tool_receipts SET result_sha256='wrong' WHERE id=?", (receipts[0]['id'],))
        elif attack == 'arguments':
            conn.execute('UPDATE tool_receipts SET arguments=? WHERE id=?',
                         (json.dumps({'path': 'root0/../outside.txt'}), receipts[0]['id']))
        elif attack == 'json':
            malformed = '{"success":true,"content":"untrusted","truncated":false,"content":"other"}'
            conn.execute('UPDATE tool_receipts SET result=?,result_sha256=? WHERE id=?',
                         (malformed, hashlib.sha256(malformed.encode()).hexdigest(), receipts[0]['id']))
        elif attack == 'blob':
            conn.execute('UPDATE tool_receipts SET result=? WHERE id=?', (b'\x00bad', receipts[0]['id']))
        elif attack == 'oversized':
            text = json.dumps({'success': True, 'content': 'x' * 20001, 'truncated': False})
            conn.execute('UPDATE tool_receipts SET result=?,result_sha256=? WHERE id=?',
                         (text, hashlib.sha256(text.encode()).hexdigest(), receipts[0]['id']))
        elif attack == 'protected_alias':
            conn.execute('UPDATE tool_receipts SET arguments=? WHERE id=?',
                         (json.dumps({'path': 'root0/.ssh/README.md'}), receipts[0]['id']))
        elif attack == 'nonfinite':
            conn.execute('UPDATE tool_receipts SET completed=? WHERE id=?', (float('inf'), receipts[0]['id']))
        elif attack == 'context':
            conn.execute('INSERT INTO organization_context_receipts VALUES (?,?,?,?)',
                         (review['id'], 'damaged', json.dumps({'status': 'overflow'}), 1))
    if attack == 'grant':
        store = OrganizationStore(store.path, replace(store.settings, tool_grants=()))
    if attack == 'policy':
        store.pause_configuration()
    result = _collect(store)
    assert not result['public']['items']
    assert result['public']['status'] in {'stale', 'ambiguous', 'invalid'}
    assert result['public']['limitations']


@pytest.mark.linux_only
@pytest.mark.parametrize('operation', ['read_file', 'list_files', 'search_files'])
def test_bounded_projection_marks_source_excerpt_and_candidate_truncation(source, operation):
    store, root = source
    (root / 'README.md').write_text(('A project documented with Unicode 🦔.\n' * 300), encoding="utf-8")
    _, claim, receipts = _inspect(store, operation=operation)
    _review(store, claim)
    result = _collect(store)
    assert result['public']['truncated']
    assert any(item['excerptTruncated'] for item in result['public']['items'])
    assert any(item['operation'] == operation for item in result['public']['items'])
    assert len(_public(result).encode('utf-8')) <= MAX_PUBLIC_BYTES
    assert len(result['public']['items']) <= MAX_ITEMS
    # A bounded ledger scan has an explicit omission marker even if old records
    # are all ineligible. It cannot become a false complete-context claim.
    with store._write() as conn:
        for index in range(MAX_CANDIDATES + 1):
            conn.execute('INSERT INTO tool_receipts(id,request_id,attempt,tool_call_id,tool_name,arguments,status,created,completed) '
                         "VALUES (?, ?, 1, ?, 'read_file', '{}', 'unknown', ?, ?)",
                         (f'unknown-{index}', claim['id'], f'unknown-{index}', 9999999999 + index, 9999999999 + index))
    result = _collect(store)
    assert result['public']['truncated'] and not result['public']['items']
    assert len(result['private']['receipts']) == MAX_CANDIDATES


@pytest.mark.linux_only
def test_removed_revoked_and_unknown_projects_are_not_inferred(source):
    store, _ = source
    _, claim, _ = _inspect(store)
    _review(store, claim)
    for identifiers in ([], ['unknown'], ['alpha', 'alpha']):
        assert not _collect(store, project_ids=identifiers)['public']['items']
    store.settings = replace(store.settings, revoked_root_aliases=('root0',))
    result = _collect(store)
    assert result['public']['status'] == 'stale' and not result['public']['items']


@pytest.mark.linux_only
def test_retained_context_is_bound_to_intake_and_never_promotes_historical_failure(source):
    store, root = source
    _, claim, receipts = _inspect(store)
    _review(store, claim)
    intake, _ = store.begin_intake('Investigate startup', idempotency_key='new-context')
    prompt = store.intake_prompt(intake['id'])
    retained = next(item for item in prompt['evidence'] if item['id'].startswith('retained:'))
    assert retained['verifiedCurrentBlocker'] is False
    assert str(root) not in json.dumps(prompt)
    store.reserve_intake_call(intake['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    proposal = {'snapshotId': intake['snapshotId'], 'routeId': 'manager', 'projectIds': ['alpha'],
        'deliveryMode': 'managed_artifact', 'priority': 'P1', 'rationale': 'Old failure requires investigation.',
        'evidenceIds': [retained['id']], 'acceptanceCriteria': ['Explain the current state'],
        'assumptions': [], 'duplicateCandidateIds': [], 'dependencyCandidateIds': [], 'question': None}
    with pytest.raises(ValueError):
        store.finish_intake(intake['id'], json.dumps(proposal))
    with store._write() as conn:
        conn.execute("UPDATE tool_receipts SET result='{}' WHERE id=?", (receipts[0]['id'],))
    proposal['priority'] = 'P2'
    with pytest.raises(ValueError, match='context changed'):
        store.finish_intake(intake['id'], json.dumps(proposal))

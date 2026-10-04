"""Real ledger contracts for grants, staffing and evidence-bound tool work."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


def _settings(tmp_path, **extra):
    return OrganizationSettings.from_config({'organization': {
        'capabilities': ['work.draft', 'work.analyze', 'work.inspect'],
        'tool_grants': ['read_file'], 'read_roots': [str(tmp_path / 'project')],
        'roster': [{'id': 'reader', 'name': 'Repository analyst', 'team': 'engineering',
                    'capabilities': ['work.inspect'], 'tool_grants': ['read_file']}],
        **extra}})


def _plan(store, *, key='goal', team='engineering', kind='work.inspect', priority='high'):
    obj = store.create_objective('Inspect supplied project', idempotency_key=key, priority=priority)
    manager = store.claim_next()
    store.finish(manager, {'workers': 1, 'tasks': [{'title': 'Inspect README',
        'description': 'Read the configured README and cite its actual contents.',
        'type': kind, 'team': team, 'dependsOn': []}]})
    return obj


def _claim_inspection(store):
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    work = store.claim_next()
    assert work['type'] == 'work.inspect'
    assert work['agent_id'] == 'reader'
    return work


def _read_result(text='Project source material'):
    return json.dumps({'success': True, 'content': text, 'path': 'root0/README.md', 'truncated': False})


def _record(store, claim, call_id='read-1'):
    started = store.record_tool_start(claim, call_id, 'read_file', {'path': 'root0/README.md', 'offset': 1, 'limit': 100})
    assert started['created']
    return store.record_tool_finish(claim, started['id'], _read_result(), 'completed')


def test_configured_staff_hire_read_review_preserve_routes_and_exact_receipts(tmp_path):
    settings = _settings(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    assert next(a for a in store.snapshot()['agents'] if a['id'] == 'reader')['lifecycle'] == 'available'
    assert not any(a['id'].startswith('worker-') for a in store.snapshot()['agents'])
    obj = _plan(store)
    hire = next(r for r in store.snapshot()['requests'] if r['type'] == 'request.hire')
    assert hire['priority'] == 5
    assert hire['requestedRoutes'] == [{'team': 'engineering', 'type': 'work.inspect'}]
    claim = _claim_inspection(store)
    assert store.context(claim)['toolPolicy']['tools'] == ['read_file']
    with pytest.raises(ValueError, match='completed tool evidence'):
        store.finish(claim, {'summary': 'Unsupported claim', 'deliverable': 'I read it.'})
    assert store.snapshot()['knowledge'] == []
    receipt = _record(store, claim)
    store.finish(claim, {'summary': 'Read actual project source', 'deliverable': 'The source says Project source material.'})
    review = store.claim_next()
    assert review['team'] == 'engineering' and review['priority'] == 5
    assert review['agent_id'] != claim['agent_id']
    context = store.context(review)
    assert context['toolPolicy']['tools'] == []
    assert context['toolReceipts'] == context['evidence'][0]['toolReceipts']
    assert context['toolReceipts'][0]['id'] == receipt['id']
    ev_id = context['evidence'][0]['id']
    assert store.evidence(ev_id)['toolReceipts'][0]['result'] == _read_result()
    store.finish(review, {'approved': True, 'summary': 'Artifact is supported by the retained file result.', 'evidenceIds': [ev_id]})
    reopened = OrganizationStore(store.path, settings)
    assert reopened.snapshot()['objectives'][0]['id'] == obj['id']
    assert reopened.snapshot()['objectives'][0]['status'] == 'completed'
    assert reopened.tool_receipts(claim['id'])[0]['resultSha256'] == hashlib.sha256(_read_result().encode()).hexdigest()


def test_receipt_idempotency_is_atomic_and_limits_dispatch(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(_settings(tmp_path), max_tool_calls=1))
    _plan(store)
    claim = _claim_inspection(store)
    def start(_):
        return store.record_tool_start(claim, 'call', 'read_file', {'path': 'root0/README.md'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(start, range(8)))
    assert len({r['id'] for r in receipts}) == 1
    assert sum(r['created'] for r in receipts) == 1
    with pytest.raises(ValueError, match='different arguments'):
        store.record_tool_start(claim, 'call', 'read_file', {'path': 'root0/other.md'})
    with pytest.raises(ValueError, match='limit'):
        store.record_tool_start(claim, 'another-call', 'read_file', {'path': 'root0/README.md'})
    ident = receipts[0]['id']
    with pytest.raises(ValueError, match='successful read'):
        store.record_tool_finish(claim, ident, '{"success":false,"error":"denied"}', 'completed')
    first = store.record_tool_finish(claim, ident, _read_result(), 'completed')
    assert store.record_tool_finish(claim, ident, _read_result(), 'completed') == first
    with pytest.raises(ValueError, match='finalized'):
        store.record_tool_finish(claim, ident, _read_result('Changed'), 'completed')


def test_uncertain_tool_is_not_replayed_or_accepted_after_restart_or_cancel(tmp_path):
    settings = _settings(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    obj = _plan(store)
    claim = _claim_inspection(store)
    started = store.record_tool_start(claim, 'call', 'read_file', {'path': 'root0/README.md'})
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
    reopened = OrganizationStore(store.path, settings)
    assert reopened.recover_expired() == 1
    assert reopened.tool_receipts(claim['id'])[0]['status'] == 'unknown'
    assert reopened.claim_next() is None
    with pytest.raises(ValueError, match='no longer owned'):
        store.record_tool_finish(claim, started['id'], _read_result(), 'completed')
    assert reopened.retry(claim['id'], idempotency_key='retry-once')
    retry = reopened.claim_next()
    assert reopened.request_tool_receipts(retry) == []
    second = reopened.record_tool_start(retry, 'call', 'read_file', {'path': 'root0/README.md'})
    assert second['id'] != started['id']
    reopened.cancel(obj['id'])
    assert all(r['status'] == 'unknown' for r in reopened.tool_receipts(claim['id']))
    with pytest.raises(ValueError, match='no longer owned'):
        reopened.record_tool_finish(retry, second['id'], _read_result(), 'completed')
    assert not reopened.finish(retry, {'summary': 'Late', 'deliverable': 'Rejected'})


def test_review_rejects_changed_tool_evidence_and_revision_links_only_new_attempt(tmp_path):
    settings = _settings(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    _plan(store)
    claim = _claim_inspection(store)
    receipt = _record(store, claim)
    store.finish(claim, {'summary': 'Inspected', 'deliverable': 'Initial analysis'})
    review = store.claim_next()
    evidence_id = store.context(review)['evidence'][0]['id']
    with store._write() as conn:
        conn.execute('UPDATE tool_receipts SET result=? WHERE id=?', (_read_result('Tampered'), receipt['id']))
    with pytest.raises(ValueError, match='has changed'):
        store.finish(review, {'approved': True, 'summary': 'Approve', 'evidenceIds': [evidence_id]})
    with store._write() as conn:
        conn.execute('UPDATE tool_receipts SET result=? WHERE id=?', (_read_result(), receipt['id']))
        conn.execute('DELETE FROM evidence_tools WHERE evidence_id=?', (evidence_id,))
    with pytest.raises(ValueError, match='missing its successful'):
        store.finish(review, {'approved': True, 'summary': 'Approve', 'evidenceIds': [evidence_id]})
    with store._write() as conn:
        conn.execute('INSERT INTO evidence_tools VALUES (?,?)', (evidence_id, receipt['id']))
    store.finish(review, {'approved': False, 'summary': 'Explain implications.', 'evidenceIds': [evidence_id]})
    revision = store.claim_next()
    assert store.context(revision)['toolReceipts'][0]['id'] == receipt['id']
    fresh = _record(store, revision)
    store.finish(revision, {'summary': 'Revised inspection', 'deliverable': 'Revised supported analysis'})
    second_review = store.claim_next()
    context = store.context(second_review)
    assert [r['id'] for r in context['toolReceipts']] == [fresh['id']]


def test_missing_grants_and_unmatched_team_never_hire_a_substitute(tmp_path):
    settings = _settings(tmp_path, tool_grants=[], read_roots=[], roster=[
        {'id': 'reader', 'name': 'Reader', 'team': 'engineering', 'capabilities': ['work.inspect']}])
    store = OrganizationStore(tmp_path / 'state.db', settings)
    _plan(store)
    hire = store.claim_next()
    with pytest.raises(ValueError, match='explicit read_file grant') as error:
        store.finish(hire, {})
    store.fail(hire, str(error.value))
    assert store.claim_next() is None
    request = next(r for r in store.snapshot()['requests'] if r['type'] == 'work.inspect')
    assert request['status'] == 'pending_intervention' and 'read_file' in request['reason']
    assert next(a for a in store.snapshot()['agents'] if a['id'] == 'reader')['lifecycle'] == 'disabled'
    assert not any(a['id'].startswith('worker-') for a in store.snapshot()['agents'])


def test_policy_removal_fences_live_staff_and_restores_without_activation(tmp_path):
    settings = _settings(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    _plan(store)
    claim = _claim_inspection(store)
    started = store.record_tool_start(claim, 'call', 'read_file', {'path': 'root0/README.md'})
    changed = OrganizationStore(store.path, replace(settings, roster=()))
    staff = next(a for a in changed.snapshot()['agents'] if a['id'] == 'reader')
    assert staff['lifecycle'] == 'retired'
    assert changed.tool_receipts(claim['id'])[0]['status'] == 'unknown'
    assert not store.heartbeat(claim)
    with pytest.raises(ValueError, match='no longer owned'):
        store.record_tool_finish(claim, started['id'], _read_result(), 'completed')
    restored = OrganizationStore(store.path, settings)
    assert next(a for a in restored.snapshot()['agents'] if a['id'] == 'reader')['lifecycle'] == 'available'
    assert restored.retry(claim['id'], idempotency_key='restore-staff')
    assert restored.claim_next() is None  # Queues an exact-route reactivation first.
    hire = restored.claim_next()
    assert hire['type'] == 'request.hire'
    restored.finish(hire, {})
    resumed = restored.claim_next()
    assert resumed['id'] == claim['id'] and resumed['agent_id'] == 'reader'


@pytest.mark.parametrize('args', [{'path': '/private/file'}, {'path': 'root0/../secret'},
                                 {'path': 'root0/file', 'token': 'secret'}, {'offset': True}])
def test_audit_drops_unbounded_or_ungranted_arguments(tmp_path, args):
    store = OrganizationStore(tmp_path / 'state.db', _settings(tmp_path))
    _plan(store)
    claim = _claim_inspection(store)
    with pytest.raises(ValueError):
        store.record_tool_start(claim, 'call', 'read_file', args)
    assert store.tool_receipts(claim['id']) == []


def test_snapshot_preview_limits_do_not_hide_full_request_audit(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', _settings(tmp_path))
    _plan(store)
    claim = _claim_inspection(store)
    receipt = _record(store, claim)
    # The small projection window is independent of the authoritative request.
    with store._write() as conn:
        for index in range(201):
            conn.execute('INSERT INTO tool_receipts(id,request_id,attempt,tool_call_id,tool_name,arguments,status,created) '
                         "VALUES (?, ?, 99, ?, 'read_file', '{}', 'unknown', ?)",
                         (f'other-{index}', claim['id'], f'other-{index}', time.time()))
    request = next(r for r in store.snapshot()['requests'] if r['id'] == claim['id'])
    assert request['toolReceiptsTruncated'] and request['toolReceiptCount'] == 202
    assert receipt['id'] not in {r['id'] for r in request['toolReceipts']}
    assert store.tool_receipts(claim['id'])[0]['id'] == receipt['id']
    with pytest.raises(ValueError, match='not found'):
        store.tool_receipts('another-profile-request')

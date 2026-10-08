"""Combined state transitions must retain exact execution evidence."""
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('prior_status', ['completed', 'running', 'blocked'])
def test_read_pause_owner_answer_reopen_does_not_replay_prior_execution_receipt(tmp_path, legacy, prior_status):
    source = tmp_path / 'source'
    source.mkdir()
    store = OrganizationStore(tmp_path / 'state.db', OrganizationSettings.from_config({'organization': {
        'max_attempts': 1, 'capabilities': ['work.inspect'],
        'tool_grants': ['read_file'], 'read_roots': [str(source)],
        'roster': [{'id': 'reader', 'name': 'Reader', 'capabilities': ['work.inspect'],
                    'tool_grants': ['read_file']}],
    }}))
    store.create_objective('Inspect the owner-confirmed source', idempotency_key='read-question')
    store.finish(store.claim_next(), {'workers': 1, 'tasks': [{'title': 'Inspect source',
        'description': 'Read, clarify and verify current source.', 'type': 'work.inspect'}]})
    store.finish(store.claim_next(), {})
    first = store.claim_next()
    prior = store.record_tool_start(first, 'provider-call-1', 'read_file', {'path': 'root0/facts.txt'})
    if prior_status != 'running':
        result = ({'success': True, 'content': 'Old source'} if prior_status == 'completed'
                  else {'success': False, 'blocked': True})
        store.record_tool_finish(first, prior['id'], json.dumps(result), prior_status)
    if legacy:
        with store._write() as conn:
            conn.execute('ALTER TABLE requests DROP COLUMN execution_count')
            if prior_status != 'completed':
                # This is the pre-upgrade pause layout: failure attempts were
                # refunded even when the prior read was unresolved or blocked.
                conn.execute("UPDATE requests SET status='waiting_response',token=NULL,lease=NULL,"
                             "attempts=attempts-1 WHERE id=?", (first['id'],))
        store = OrganizationStore(store.path)
        if prior_status != 'completed':
            assert not store.heartbeat(first)
            assert store.claim_next() is None
            assert next(item for item in store.snapshot()['requests'] if item['id'] == first['id'])['status'] == 'pending_intervention'
            assert store.tool_receipts(first['id'])[0]['status'] == ('unknown' if prior_status == 'running' else prior_status)
            return
    if prior_status != 'completed':
        with pytest.raises(ValueError, match='outcome review'):
            store.finish(first, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Try again?'}]})
        assert store.heartbeat(first)
        assert not any(item['type'] == 'request.question' for item in store.snapshot()['requests'])
        assert store.tool_receipts(first['id'])[0]['status'] == prior_status
        store.fail(first, 'Outcome needs explicit review')
        assert store.claim_next() is None
        return
    store.finish(first, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Use the current source?'}]})
    assert store.claim_next() is None
    question = next(item for item in store.snapshot()['requests'] if item['type'] == 'request.question')
    store.respond(question['id'], 'Yes, verify the current contents.', idempotency_key='owner-answer')
    reopened = OrganizationStore(store.path)
    resumed = reopened.claim_next()
    assert resumed['id'] == first['id'] and resumed['token'] != first['token']
    assert resumed['attempts'] == first['attempts']  # A clarification is not a failed attempt.
    assert reopened.request_tool_receipts(resumed) == []
    fresh = reopened.record_tool_start(resumed, 'provider-call-1', 'read_file', {'path': 'root0/facts.txt'})
    assert fresh['created'] and fresh['id'] != prior['id']
    reopened.record_tool_finish(resumed, fresh['id'], json.dumps({'success': True, 'content': 'Current source'}), 'completed')
    reopened.finish(resumed, {'summary': 'Verified current source', 'deliverable': 'Current source'})
    review = reopened.claim_next()
    assert [receipt['id'] for receipt in reopened.context(review)['toolReceipts']] == [fresh['id']]
    assert len(reopened.tool_receipts(resumed['id'])) == 2

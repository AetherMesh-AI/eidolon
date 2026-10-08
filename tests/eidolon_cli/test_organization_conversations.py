"""Peer messages are durable bounded context, never grants or completion proof."""
import json
from dataclasses import replace

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_requests import ledger, start_work


def send(store, work, **overrides):
    proposal = {'recipientId': 'advisor', 'subject': 'Audience', 'body': 'Which audience should I write for?', **overrides}
    assert store.finish(work, {'messages': [proposal]})
    return proposal


def test_thread_reply_restart_and_provenance_are_context_not_work(tmp_path):
    store = ledger(tmp_path)
    obj, work = start_work(store)
    with store._write() as conn:
        conn.execute('UPDATE agent_context SET memory=? WHERE agent_id=?', (json.dumps({'facts': ['SECRET UNRELATED PROJECT'], 'decisions': [], 'lessons': [], 'openQuestions': []}), 'advisor'))
    proposal = send(store, work)
    first = store.snapshot()['conversations'][0]
    assert first['messages'][0]['readAt'] is None and first['status'] == 'waiting_reply'
    assert not store.finish(work, {'messages': [proposal]})
    reopened = OrganizationStore(store.path)
    delivery = reopened.claim_next()
    assert delivery['type'] == 'request.message' and delivery['agent_id'] == 'advisor'
    # Claiming is not reading; owner's inspection also leaves inbox unread.
    assert reopened.snapshot()['conversations'][0]['messages'][0]['readAt'] is None
    context = reopened.context(delivery)
    assert 'SECRET' not in json.dumps(context)
    assert not {'objective', 'task', 'evidence', 'projectPolicy', 'requestResponses'} & context.keys()
    assert context['conversation']['objectiveId'] is None
    assert context['agentContext']['memory'] == {}
    assert reopened.snapshot()['conversations'][0]['messages'][0]['readAt'] is not None
    with pytest.raises(ValueError, match='only return one reply'):
        reopened.finish(delivery, {'reply': 'Board', 'approved': True})
    assert reopened.finish(delivery, {'reply': 'The board. Keep it brief. This is context, not approval.'})
    assert not reopened.finish(delivery, {'reply': 'Duplicate'})
    thread = reopened.snapshot()['conversations'][0]
    assert thread['status'] == 'answered' and len(thread['messages']) == 2
    assert thread['messages'][1]['replyToId'] == thread['messages'][0]['id']
    assert thread['messages'][1]['readAt'] is None
    resumed = OrganizationStore(store.path).claim_next()
    assert resumed['id'] == work['id'] and resumed['agent_id'] == 'writer'
    context = reopened.context(resumed)
    assert context['internalConversations'][0]['messages'][1]['readAt'] is not None
    assert context['objectiveClarifications'] == []
    assert reopened.snapshot()['objectives'][0]['status'] != 'completed'
    with pytest.raises(ValueError, match='already sent'):
        reopened.finish(resumed, {'messages': [proposal]})
    assert reopened.finish(resumed, {'summary': 'Board brief', 'deliverable': 'Brief for the board.'})
    assert reopened.snapshot()['objectives'][0]['status'] != 'completed'  # review still required


def test_scope_revocation_retired_identity_and_message_limits(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    send(store, work)
    old_thread = store.snapshot()['conversations'][0]
    # Removing the recipient never deletes history or silently chooses a fallback.
    changed = replace(store.settings, roster=tuple(item for item in store.settings.roster if item.id != 'advisor'))
    reopened = OrganizationStore(store.path, changed)
    assert reopened.claim_next() is None
    thread = reopened.snapshot()['conversations'][0]
    assert thread['participants'] == old_thread['participants']
    assert thread['status'] == 'needs_input' and thread['waitingAgentId'] == 'advisor'
    reopened.cancel(work['objective_id'])
    assert reopened.snapshot()['conversations'][0]['status'] == 'cancelled'
    assert not reopened.finish(work, {'deliverable': 'must not finish'})


def test_cross_department_requires_existing_collaboration_or_explicit_scope(tmp_path):
    store = ledger(tmp_path, [
        {'id': 'writer', 'name': 'Writer', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'advisor', 'name': 'Advisor', 'team': 'research', 'role': 'Manager',
         'capabilities': ['request.plan'], 'managed_teams': []},
    ])
    _, work = start_work(store)
    assert 'advisor' not in {row['id'] for row in store.context(work)['messagingDirectory']}
    with pytest.raises(ValueError, match='Cross-department'):
        send(store, work)
    # Explicit managed team scope enables communication but not new tool grants.
    advisor = replace(store.settings.roster[1], managed_teams=('general',))
    store = OrganizationStore(store.path, replace(store.settings, roster=(store.settings.roster[0], advisor)))
    work = store.claim_next()  # policy update fences running work
    if work is None:
        pending = next(r for r in store.snapshot()['requests'] if r['type'] == 'work.draft')
        store.retry(pending['id'], idempotency_key='resume')
        work = store.claim_next()
    assert work is not None
    send(store, work)
    delivery = store.claim_next()
    context = store.context(delivery)
    assert context['toolPolicy']['tools'] == [] and context['conversation']['objectiveId'] is None
    assert store.finish(delivery, {'reply': 'No shared project source was supplied. Ask a formal question if needed.'})


def test_message_payload_cannot_spoof_identity_or_recursive_wakes(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    with pytest.raises(ValueError, match='backend-owned'):
        send(store, work, senderId='executive')
    send(store, work)
    delivery = store.claim_next()
    with pytest.raises(ValueError, match='only return one reply'):
        store.finish(delivery, {'messages': [{'recipientId': 'writer', 'subject': 'Loop', 'body': 'Reply forever'}]})
    with pytest.raises(ValueError, match='one reply'):
        store.finish(delivery, {'tasks': []})
    assert store.finish(delivery, {'reply': 'Answer once.'})
    resumed = store.claim_next()
    assert resumed['id'] == work['id']
    assert store.claim_next() is None


@pytest.mark.parametrize('scope', ['disabled', 'same_team'])
def test_communication_policy_round_trips_and_denies_unapproved_scope(tmp_path, scope):
    settings = OrganizationSettings.from_config({'organization': {'communication_scope': scope}})
    store = OrganizationStore(tmp_path / 'scope.db', settings)
    assert OrganizationStore(store.path).settings.communication_scope == scope
    with pytest.raises(ValueError, match='communication_scope'):
        OrganizationSettings.from_config({'organization': {'communication_scope': 'everybody'}})

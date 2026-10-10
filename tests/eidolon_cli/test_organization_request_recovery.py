"""Unanswerable linked requests retain bounded owner recovery without forged answers."""
from dataclasses import replace
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import decompose


def pending_question(tmp_path, kind='request.question', **limits):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), **limits))
    objective = store.create_objective('Prepare a brief', idempotency_key='objective')
    decompose(store)
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': 'Analyze', 'description': 'Use the supplied facts', 'type': 'work.analyze'}]})
    work = store.claim_next()
    proposal = {'managementProposal': {'members': [{'id': 'new-writer', 'name': 'New writer', 'capabilities': ['work.draft']}]}} if kind == 'request.hire' else {}
    store.finish(work, {'requests': [{'type': kind, 'requestedOutcome': 'An unavailable fact is needed.', **proposal}]})
    handler = store.claim_next()
    if handler:
        store.fail(handler, 'The owner must resolve the missing fact')
    request = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == work['id'])
    assert request['status'] == 'pending_intervention'
    return store, objective, work, request


@pytest.mark.parametrize('kind', ['request.question', 'request.decision', 'request.permission', 'request.hire'])
@pytest.mark.parametrize('action', ['amend_scope', 'request_replan'])
def test_linked_request_recovery_replans_once_and_fences_old_answers(tmp_path, kind, action):
    store, objective, work, request = pending_question(tmp_path, kind)
    actions = {item['action'] for item in request['allowedResolutions']}
    expected_response = {'approve_request', 'deny_request'} if kind in {'request.permission', 'request.hire'} else {'answer_request'}
    assert actions == expected_response | {'amend_scope', 'request_replan'}
    before = store.settings
    text = 'Produce a brief using only supplied facts; omit the unavailable fact.'
    assert store.resolve(request['id'], action, text, idempotency_key='recover')
    reopened = OrganizationStore(store.path)
    assert not reopened.resolve(request['id'], action, text, idempotency_key='recover')
    snapshot = reopened.snapshot()
    assert snapshot['objectives'][0]['id'] == objective['id']
    assert snapshot['objectives'][0]['acceptance']['round'] == 1
    assert len(snapshot['objectives'][0]['ownerResolutions']) == 1
    original = {row['id']: row for row in snapshot['requests']}
    assert original[work['id']]['status'] == original[request['id']]['status'] == 'cancelled'
    assert original[request['id']]['response'] is None
    assert original[request['id']]['requestedOutcome'] == request['requestedOutcome']
    assert reopened.settings == before
    with pytest.raises(ValueError, match='not awaiting'):
        reopened.respond(request['id'], 'Late answer', 'denied' if kind in {'request.permission', 'request.hire'} else 'answered', idempotency_key='late')
    assert not reopened.finish(work, {'summary': 'Late work', 'deliverable': 'Must not publish'})
    fresh = reopened.claim_next()
    assert fresh['type'] == 'request.decompose'
    context = reopened.context(fresh)
    assert context['objective']['round'] == 1
    assert context['feedback'] == text
    assert context['objective']['description'] == (text if action == 'amend_scope' else 'Prepare a brief')
    assert context['objective']['acceptanceCriteria'] == [text if action == 'amend_scope' else 'Prepare a brief']


@pytest.mark.parametrize('limit', ['replans', 'stages', 'deadline', 'cancelled'])
def test_linked_recovery_preserves_existing_limits_and_never_offers_replay(tmp_path, limit):
    store, _, _, request = pending_question(tmp_path, max_replans=0 if limit == 'replans' else 2)
    with store._write() as conn:
        if limit == 'stages':
            conn.execute('UPDATE objective_control SET max_stages=(SELECT count(*) FROM objective_usage)')
        elif limit == 'deadline':
            conn.execute('UPDATE objective_budgets SET deadline=?', (time.time() - 1,))
    if limit == 'cancelled':
        store.cancel(request['objectiveId'])
    reopened = OrganizationStore(store.path)
    current = next(row for row in reopened.snapshot()['requests'] if row['id'] == request['id'])
    assert {item['action'] for item in current['allowedResolutions']} == ({'answer_request'} if limit in {'replans', 'stages'} else set())
    with pytest.raises(ValueError, match='unavailable'):
        reopened.resolve(request['id'], 'request_replan', 'Do not bypass limits', idempotency_key='bypass')
    assert reopened.snapshot()['objectives'][0]['acceptance']['round'] == 0
    assert reopened.snapshot()['objectives'][0]['ownerResolutions'] == []

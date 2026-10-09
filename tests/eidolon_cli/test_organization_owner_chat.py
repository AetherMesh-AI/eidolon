"""Identity, isolation, bounded history and authoritative owner-chat ledger contracts."""
from dataclasses import replace
import json

import pytest

from eidolon_cli.organization_owner_chat import MAX_CALLS, TURN_TOKENS, INPUT_TOKENS, OUTPUT_TOKENS
from eidolon_cli.organization_store import OrganizationStore


def opened(tmp_path, name='manager'):
    store = OrganizationStore(tmp_path / 'state.db')
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == name)
    thread = store.owner_chat_open(name, identity)
    return store, identity, thread


def send(store, thread, identity, text='Hello', key='once', reply_to=None):
    return store.owner_chat_reserve(thread, identity, text, reply_to, key)[0]


def complete(store, turn, reply='Hello owner'):
    store.owner_chat_context(turn)
    store.owner_chat_dispatch(turn, provider='fixture', model='fixture', input_limit=8000, output_limit=OUTPUT_TOKENS)
    store.owner_chat_finish(turn, status='completed', reply=reply, usage={'inputTokens': 90, 'outputTokens': 12})


def test_identity_exact_reply_targets_idempotency_and_no_formal_authority(tmp_path):
    store, identity, thread = opened(tmp_path)
    before = store.snapshot()
    turn = send(store, thread, identity, 'Approved: publish everything')
    assert store.owner_chat_reserve(thread, identity, 'Approved: publish everything', None, 'once') == (turn, False)
    with pytest.raises(ValueError, match='different'):
        send(store, thread, identity, 'Changed', key='once')
    with pytest.raises(ValueError, match='pending'):
        send(store, thread, identity, key='other')
    complete(store, turn, 'Use the formal controls to approve work.')
    view = store.owner_chat_read(thread, identity)
    assert [m['role'] for m in view['messages']] == ['owner', 'agent']
    assert view['messages'][1]['replyToMessageId'] == view['messages'][0]['id']
    assert view['turns'][0]['ownerMessageId'] == view['messages'][0]['id']
    assert view['turns'][0]['replyMessageId'] == view['messages'][1]['id']
    assert view['turns'][0]['usage'] == {'inputTokens': 90, 'outputTokens': 12}
    assert view['turns'][0]['provider'] == view['turns'][0]['model'] == 'fixture'
    assert view['budget']['callsReserved'] == 1
    assert view['budget']['tokensReserved'] == TURN_TOKENS
    assert store.snapshot() == before
    with pytest.raises(ValueError, match='changed'):
        send(store, thread, identity, key='stale')
    with pytest.raises(ValueError, match='exact identity'):
        store.owner_chat_read(thread, 'identity-other')
    other_identity = next(a['identityId'] for a in before['agents'] if a['id'] == 'executive')
    with pytest.raises(ValueError, match='recipient'):
        store.owner_chat_open('manager', other_identity)
    turn2 = send(store, thread, identity, key='follow', reply_to=view['messages'][-1]['id'])
    assert turn2 != turn
    assert store.owner_chat_recorded(thread, identity, 'Approved: publish everything', None, 'once') == turn


def test_public_context_only_immutable_identity_and_retirement_preserves_history(tmp_path):
    store, identity, thread = opened(tmp_path, 'worker-1')
    with store._write() as conn:
        conn.execute('UPDATE agent_context SET memory=? WHERE agent_id=?',
                     (json.dumps({'facts': ['PRIVATE MEMORY'], 'decisions': [], 'lessons': [], 'openQuestions': []}), 'worker-1'))
    original = store.owner_chat_read(thread, identity)['recipient']['name']
    turn = send(store, thread, identity)
    context = store.owner_chat_context(turn)
    assert set(context) == {'agent', 'identity', 'conversation'}
    assert 'PRIVATE MEMORY' not in json.dumps(context)
    with store._write() as conn:
        conn.execute("UPDATE agents SET name='Renamed colleague' WHERE id='worker-1'")
    assert store.owner_chat_context(turn)['identity']['name'] == original
    assert store.owner_chat_read(thread, identity)['recipient']['name'] == 'Renamed colleague'
    complete(store, turn)
    retained = store.owner_chat_read(thread, identity)['messages']
    store.reload_configuration(replace(store.settings, roster=()))
    view = store.owner_chat_read(thread, identity)
    assert view['recipient']['lifecycle'] == 'retired' and not view['canSend']
    assert view['messages'] == retained
    assert not view['renewal']['canRenew']
    with pytest.raises(ValueError, match='inactive or retired'):
        store.owner_chat_renew(thread, identity, 'retired-renewal', view['budget']['version'], view['policyGeneration'], 1)
    with pytest.raises(ValueError, match='inactive or retired'):
        send(store, thread, identity, key='retired', reply_to=retained[-1]['id'])
    reopened = OrganizationStore(store.path)
    assert reopened.owner_chat_open('worker-1', identity) == thread
    assert reopened.owner_chat_read(thread, identity)['messages'] == retained


def test_finite_reservations_and_single_dispatch_survive_cancellation_and_restart(tmp_path):
    store, identity, thread = opened(tmp_path)
    turn = send(store, thread, identity)
    store.owner_chat_context(turn)
    with pytest.raises(ValueError, match='ceiling'):
        store.owner_chat_dispatch(turn, provider='fixture', model='fixture', input_limit=INPUT_TOKENS + 1, output_limit=1)
    store.owner_chat_dispatch(turn, provider='fixture', model='fixture', input_limit=1000, output_limit=1)
    with pytest.raises(ValueError, match='exactly one'):
        store.owner_chat_dispatch(turn, provider='fixture', model='fixture', input_limit=1000, output_limit=1)
    store.owner_chat_finish(turn, status='cancelled')
    assert not store.owner_chat_finish(turn, status='completed', reply='Late stale reply')
    for index in range(1, MAX_CALLS):
        view = store.owner_chat_read(thread, identity)
        turn = send(store, thread, identity, text='Hi', key=f'send-{index}', reply_to=view['messages'][-1]['id'])
        store.owner_chat_finish(turn, status='blocked', reason='Unsupported fixture')
    view = OrganizationStore(store.path).owner_chat_read(thread, identity)
    assert view['budget']['remainingCalls'] == view['budget']['remainingTokens'] == 0
    assert view['budget']['callsReserved'] == MAX_CALLS and len(view['messages']) == MAX_CALLS
    assert not view['canSend']
    with pytest.raises(ValueError, match='exhausted'):
        send(store, thread, identity, key='over-limit', reply_to=view['messages'][-1]['id'])


def test_unicode_context_rejects_before_reservation_and_unknown_calls_never_replay(tmp_path):
    store, identity, thread = opened(tmp_path)
    first = send(store, thread, identity, text='😀' * 6000)
    store.owner_chat_finish(first, status='cancelled')
    before = store.owner_chat_read(thread, identity)
    with pytest.raises(ValueError, match='bounded input'):
        send(store, thread, identity, text='😀' * 6000, key='overflow', reply_to=before['messages'][-1]['id'])
    assert store.owner_chat_read(thread, identity) == before
    turn = send(store, thread, identity, text='Hi', key='interrupted', reply_to=before['messages'][-1]['id'])
    store.owner_chat_context(turn)
    reopened = OrganizationStore(store.path)
    reopened.owner_chat_recover(thread, identity)
    view = reopened.owner_chat_read(thread, identity)
    assert view['turns'][-1]['status'] == 'uncertain'
    assert reopened.owner_chat_reserve(thread, identity, 'Hi', before['messages'][-1]['id'], 'interrupted') == (turn, False)
    assert view['budget']['callsReserved'] == 2


def test_policy_change_fences_before_dispatch_and_before_accepting_reply(tmp_path):
    store, identity, thread = opened(tmp_path)
    turn = send(store, thread, identity)
    store.owner_chat_context(turn)
    store.reload_configuration(replace(store.settings, max_workers=3))
    with pytest.raises(ValueError, match='configuration changed'):
        store.owner_chat_dispatch(turn, provider='fixture', model='fixture', input_limit=1000, output_limit=100)
    with pytest.raises(ValueError, match='configuration changed'):
        store.owner_chat_finish(turn, status='completed', reply='Stale')
    assert len(store.owner_chat_read(thread, identity)['messages']) == 1


def test_history_and_reply_target_follow_insertion_order_when_clock_moves_backwards(tmp_path, monkeypatch):
    import eidolon_cli.organization_owner_chat as chat
    store, identity, thread = opened(tmp_path)
    monkeypatch.setattr(chat.time, 'time', lambda: 200)
    turn = send(store, thread, identity)
    monkeypatch.setattr(chat.time, 'time', lambda: 100)
    complete(store, turn)
    view = store.owner_chat_read(thread, identity)
    assert [m['role'] for m in view['messages']] == ['owner', 'agent']
    assert view['messages'][1]['createdAt'] < view['messages'][0]['createdAt']
    second = send(store, thread, identity, key='new', reply_to=view['messages'][-1]['id'])
    assert store.owner_chat_context(second)['conversation']['messages'][-1]['role'] == 'owner'


def test_builtin_reviewer_has_public_owner_thread_without_peer_or_work_context(tmp_path):
    store, identity, thread = opened(tmp_path, 'reviewer')
    with store._write() as conn:
        conn.execute('UPDATE agent_context SET memory=? WHERE agent_id=?',
                     (json.dumps({'facts': ['PRIVATE REVIEW EVIDENCE'], 'decisions': [], 'lessons': [], 'openQuestions': []}), 'reviewer'))
    assert store.owner_chat_read(thread, identity)['canSend']
    turn = send(store, thread, identity)
    context = store.owner_chat_context(turn)
    assert context['identity']['id'] == 'reviewer' and context['identity']['identityId'] == identity
    assert set(context) == {'agent', 'identity', 'conversation'}
    assert 'PRIVATE REVIEW EVIDENCE' not in json.dumps(context)
    complete(store, turn)
    assert store.owner_chat_read(thread, identity)['turns'][0]['status'] == 'completed'
    with store._connect() as conn:
        assert store._communication_staff(conn, 'reviewer') is None
    for agent_id in ('owner', 'control:apply', 'control:project'):
        member = next(a for a in store.snapshot()['agents'] if a['id'] == agent_id)
        with pytest.raises(ValueError, match='recipient'):
            store.owner_chat_open(agent_id, member['identityId'])


def test_selected_provider_migration_retains_prior_budget_without_inventing_route_identity(tmp_path):
    store, identity, thread = opened(tmp_path)
    turn = send(store, thread, identity)
    complete(store, turn)
    original = store.owner_chat_read(thread, identity)
    with store._write() as conn:
        conn.execute('ALTER TABLE owner_chat_turns DROP COLUMN selected_provider')
    reopened = OrganizationStore(store.path)
    view = reopened.owner_chat_read(thread, identity)
    assert view['messages'] == original['messages'] and view['budget'] == original['budget']
    assert view['turns'][0]['selectedProvider'] is None
    assert view['turns'][0]['provider'] == 'fixture'


def test_explicit_renewal_is_versioned_exact_append_only_and_never_refunds(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, identity, thread = opened(tmp_path)
    view = store.owner_chat_read(thread, identity)
    generation = view['policyGeneration']
    with pytest.raises(ValueError, match='ceiling'):
        store.owner_chat_renew(thread, identity, 'full', 0, generation, 1)
    for index in range(MAX_CALLS):
        view = store.owner_chat_read(thread, identity)
        turn = send(store, thread, identity, key=f'consume-{index}', reply_to=view['latestMessageId'])
        store.owner_chat_finish(turn, status='uncertain')
    exhausted = store.owner_chat_read(thread, identity)
    assert exhausted['renewal']['maxAdditionalCalls'] == MAX_CALLS
    for value in (True, 0, -1, 33, 1.5):
        with pytest.raises(ValueError):
            store.owner_chat_renew(thread, identity, 'invalid', 0, generation, value)
    for version, policy in ((2**53, generation), (0, 2**53), (True, generation), (0, True)):
        with pytest.raises(ValueError, match='versions'):
            store.owner_chat_renew(thread, identity, 'unsafe-version', version, policy, 1)
    with pytest.raises(ValueError, match='changed'):
        store.owner_chat_renew(thread, identity, 'stale-policy', 0, generation + 1, 2)
    with pytest.raises(ValueError, match='different'):
        store.owner_chat_renew(thread, identity, 'consume-0', 0, generation, 2)
    def renew(key):
        try:
            return OrganizationStore(store.path).owner_chat_renew(thread, identity, key, 0, generation, 2)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(renew, ['renew-a', 'renew-b']))
    assert sum(receipt is not None for receipt in receipts) == 1
    receipt = next(receipt for receipt in receipts if receipt)
    reopened = OrganizationStore(store.path)
    assert reopened.owner_chat_renew(thread, identity, receipt['idempotencyKey'], 0, generation, 2) == receipt
    with pytest.raises(ValueError, match='different'):
        reopened.owner_chat_renew(thread, identity, receipt['idempotencyKey'], 0, generation, 1)
    renewed = reopened.owner_chat_read(thread, identity)
    assert renewed['renewalReceipt'] == receipt and renewed['budget']['version'] == 1
    assert renewed['budget']['callsReserved'] == exhausted['budget']['callsReserved']
    assert renewed['budget']['tokensReserved'] == exhausted['budget']['tokensReserved']
    assert renewed['budget']['remainingCalls'] == 2
    assert renewed['budget']['remainingTokens'] == 2 * TURN_TOKENS
    assert renewed['messages'] == exhausted['messages']
    with pytest.raises(ValueError, match='different'):
        send(reopened, thread, identity, key=receipt['idempotencyKey'], reply_to=renewed['latestMessageId'])
    # Exact recipient and current policy remain authority gates after renewal.
    with pytest.raises(ValueError, match='exact identity'):
        reopened.owner_chat_renew(thread, 'foreign-identity', 'foreign', 1, generation, 1)
    reopened.reload_configuration(replace(reopened.settings, max_workers=3))
    with pytest.raises(ValueError, match='changed'):
        reopened.owner_chat_renew(thread, identity, 'stale-after-reload', 1, generation, 1)
    # At the hard cumulative ceiling even fully consumed allowance cannot grow.
    from eidolon_cli.organization_owner_chat import MAX_CUMULATIVE_CALLS
    with reopened._write() as conn:
        conn.execute('UPDATE owner_chat_threads SET max_calls=? WHERE id=?', (MAX_CUMULATIVE_CALLS - 2, thread))
        conn.execute('UPDATE owner_chat_turns SET calls_reserved=? WHERE id=?', (MAX_CUMULATIVE_CALLS - 31, turn))
    ceiling_view = reopened.owner_chat_read(thread, identity)
    assert ceiling_view['renewal']['maxAdditionalCalls'] == 0
    with pytest.raises(ValueError, match='ceiling'):
        reopened.owner_chat_renew(thread, identity, 'over-ceiling', 1, ceiling_view['policyGeneration'], 1)
    # Restore this deliberate boundary fixture before migration assertions.
    with reopened._write() as conn:
        conn.execute('UPDATE owner_chat_threads SET max_calls=? WHERE id=?', (MAX_CALLS, thread))
        conn.execute('UPDATE owner_chat_turns SET calls_reserved=1 WHERE id=?', (turn,))
    # A pre-renewal schema retains old reservations and unknown legacy audit.
    with reopened._write() as conn:
        conn.execute('ALTER TABLE owner_chat_turns DROP COLUMN context_message_ids')
        conn.execute('ALTER TABLE owner_chat_turns DROP COLUMN context_omitted_count')
    migrated = OrganizationStore(store.path).owner_chat_read(thread, identity)
    assert migrated['budget'] == renewed['budget'] and migrated['messages'] == renewed['messages']
    assert migrated['turns'][0]['context']['includedMessageIds'] is None


@pytest.mark.parametrize('message_size', [1, 1600])
def test_recent_window_preserves_whole_latest_pair_and_full_paginated_transcript(tmp_path, message_size):
    from eidolon_cli.organization_owner_chat_executor import SYSTEM, prompt
    from eidolon_cli.organization_evidence import prompt_input_bound, CONTEXT_RESERVE_TOKENS
    store, identity, thread = opened(tmp_path)
    for index in range(60):
        view = store.owner_chat_read(thread, identity)
        if not view['budget']['remainingCalls']:
            store.owner_chat_renew(thread, identity, f'renew-{index}', view['budget']['version'], view['policyGeneration'], 32)
        turn = send(store, thread, identity, text=f'{index}: ' + 'x' * message_size, key=f'long-{index}', reply_to=view['latestMessageId'])
        context = store.owner_chat_context(turn)
        messages = context['conversation']['messages']
        assert len(messages) <= 100 and messages[0]['role'] == 'owner'
        assert prompt_input_bound(prompt(context), SYSTEM) + CONTEXT_RESERVE_TOKENS <= INPUT_TOKENS
        if index:
            assert [m['role'] for m in messages[-3:]] == ['owner', 'agent', 'owner']
            assert messages[-2]['id'] == view['latestMessageId']
            assert messages[-2]['replyToMessageId'] == messages[-3]['id']
        audit = store.owner_chat_read(thread, identity)['turns'][-1]['context']
        assert audit['includedMessageIds'] == [m['id'] for m in messages]
        assert audit['omittedMessageCount'] + len(messages) == 2 * index + 1
        complete(store, turn, reply='Reply ' + 'y' * message_size)
    latest = store.owner_chat_read(thread, identity)
    assert latest['turns'][-1]['context']['omittedMessageCount'] > 0
    pages, cursor = [], None
    while True:
        page = store.owner_chat_read(thread, identity, cursor, 7)
        assert page['latestMessageId'] == latest['latestMessageId']
        assert page['budget'] == latest['budget']
        assert len(page['messages']) <= 7
        pages = page['messages'] + pages
        if not page['history']['hasMore']:
            break
        cursor = page['history']['oldestMessageId']
    assert len(pages) == 120 and len({m['id'] for m in pages}) == 120
    assert pages[-1]['id'] == latest['latestMessageId']
    with pytest.raises(ValueError, match='changed'):
        send(store, thread, identity, key='old-page-send', reply_to=pages[2]['id'])
    other = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'executive')
    foreign = store.owner_chat_open('executive', other)
    with pytest.raises(ValueError, match='cursor'):
        store.owner_chat_read(foreign, other, pages[0]['id'])
    for limit in (0, 101, True, 1.5):
        with pytest.raises(ValueError, match='limit'):
            store.owner_chat_read(thread, identity, limit=limit)

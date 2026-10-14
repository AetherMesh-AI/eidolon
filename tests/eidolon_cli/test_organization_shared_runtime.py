"""Real profile stores enforce the installation allowance before any paid send."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_budget import OrganizationModelCost, budget_view
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_executor import _prompt
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_pacing import OrganizationPacingDeferred
from eidolon_cli.organization_shared_allowance import (
    SharedAllowanceError, SharedAllowanceLedger, read_member_projection,
)
from eidolon_cli.organization_store import OrganizationStore


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def dimensions(calls, tokens, cost=None):
    return {'modelCalls': calls, 'tokens': tokens, 'configuredCostUsd': cost}


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])

    def scope(home):
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('{}\n')
        path = home / 'organization' / 'state.db'
        metadata = save_metadata(path, display_name='Organization', owner_display_name='Owner',
            expected_organization_id=None, expected_revision=None, idempotency_key='metadata')['metadata']
        settings = OrganizationSettings(max_context_tokens=4096, max_output_tokens=512,
            model_costs=(OrganizationModelCost('fixture', 'bounded', '500', '500'),))
        return SimpleNamespace(home=home, store=OrganizationStore(path, settings),
            organization_id=metadata['organizationId'], owner_id=metadata['owner']['ownerId'])

    root = scope(tmp_path / 'installation')
    monkeypatch.setenv('HERMES_HOME', str(root.home))
    root.alpha = scope(root.home / 'profiles' / 'alpha')
    root.ledger = SharedAllowanceLedger(root.home, home=root.home)
    root.now = now
    return root


def configure(root, *, calls=20, tokens=20000, cost=None, protected_calls=0, protected_tokens=0):
    value = {'periodId': 'runtime-period', 'startsAt': iso(root.now[0] - 60),
        'endsAt': iso(root.now[0] + 3600),
        'limits': dimensions(calls * 2, tokens * 2, str(Decimal(cost) * 2) if cost else None),
        'protected': dimensions(protected_calls * 2, protected_tokens * 2),
        'allocations': [{'profile': name, 'organizationId': scope.organization_id, 'ownerId': scope.owner_id,
            'limits': dimensions(calls, tokens, cost),
            'protected': dimensions(protected_calls, protected_tokens)}
            for name, scope in [('default', root), ('alpha', root.alpha)]]}
    root.ledger.configure(value, expected_revision=0, idempotency_key='configure',
        root_organization_id=root.organization_id, root_owner_id=root.owner_id)


def observe(root, percent=5):
    root.ledger.observe({'remainingPercent': percent, 'asOf': iso(root.now[0]),
        'expiresAt': iso(root.now[0] + 60), 'resetAt': None}, expected_revision=1,
        idempotency_key='observe', root_organization_id=root.organization_id, root_owner_id=root.owner_id)


def objective(scope, key='objective', *, priority='normal'):
    return scope.store.create_objective('Bounded ' + key, priority=priority, idempotency_key=key)


def queue(store, item, kind):
    # A trusted backend stage producer, never a model-selected classification.
    with store._write() as conn:
        return store._request(conn, item['id'], kind, store.settings.team, 3)


def reserve(store, claim, *, input_limit=100, output_limit=100):
    return store.reserve_model_call(claim, provider='fixture', model='bounded',
        input_limit=input_limit, output_limit=output_limit)


def rows(store, table):
    with store._connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]


def usage(store, item):
    with store._connect() as conn:
        return budget_view(conn, item['id'], store.settings)


def owner_turn(scope, key='owner-send'):
    store = scope.store
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'manager')
    thread = store.owner_chat_open('manager', identity)
    turn, created = store.owner_chat_reserve(thread, identity, 'Summarize our work.', None, key)
    assert created
    store.owner_chat_context(turn)
    return turn


def dispatch(store, turn):
    store.owner_chat_dispatch(turn, provider='custom', selected_provider='fixture', model='bounded',
        input_limit=100, output_limit=100)


def arm_late_managed_lock(root, monkeypatch, mode, final_key):
    """Publish real administrative policy after the final row is inserted."""
    from eidolon_cli.organization_shared_allowance import managed_shared_allowance
    managed = root.home.parent / 'managed-policy'
    managed.mkdir()
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    assert not managed_shared_allowance(root.home)
    original = root.ledger._connection
    inserted = []

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, statement, parameters=()):
            result = self.conn.execute(statement, parameters)
            if statement.startswith('INSERT INTO allowance_configurations ') and parameters[1] == final_key:
                inserted.append(self.conn.execute('SELECT count(*) FROM allowance_configurations').fetchone()[0])
                if mode == 'managed-install':
                    (root.home / '.managed').write_text('true')
                else:
                    (managed / 'config.yaml').write_text(json.dumps({'organization': {'shared_allowance': {'locked': True}}}))
            return result

    @contextmanager
    def connection(*, write=False, initial_identity=None):
        with original(write=write, initial_identity=initial_identity) as (conn, identity):
            yield (Connection(conn) if write else conn), identity

    monkeypatch.setattr(root.ledger, '_connection', connection)
    return inserted


def shared_dump(root):
    with closing(sqlite3.connect(root.ledger.path)) as conn:
        return tuple(conn.iterdump())


@pytest.mark.parametrize('dimension', ['modelCalls', 'tokens', 'configuredCostUsd'])
def test_real_stores_conserve_allocation_and_pool_across_objectives_and_profiles(installation, dimension):
    root = installation
    configure(root, **{'modelCalls': {'calls': 3}, 'tokens': {'tokens': 600},
        'configuredCostUsd': {'cost': '0.3'}}[dimension])

    def consume(scope):
        items = []
        for index, count in enumerate((2, 1)):
            item = objective(scope, 'objective-' + str(index))
            initial = scope.store.claim_next()
            assert initial['objective_id'] == item['id']
            assert scope.store.fail(initial, 'Fixture queues a trusted completion stage')
            identifier = queue(scope.store, item, 'request.review')
            claim = scope.store.claim_next()
            assert claim['id'] == identifier
            for _ in range(count):
                reserve(scope.store, claim)
            if index:
                with pytest.raises(SharedAllowanceError, match='exhausted_' + dimension):
                    reserve(scope.store, claim)
            assert scope.store.fail(claim, 'Retained fixture stage ended')
            items.append(item)
        return scope, items

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, (root, root.alpha)))
    shared = root.ledger.read()
    local_calls = []
    for scope, items in results:
        reopened = OrganizationStore(scope.store.path)
        calls = rows(reopened, 'organization_model_calls')
        local_calls.extend(calls)
        receipts = rows(reopened, 'organization_shared_call_receipts')
        assert len(calls) == len(receipts) == 3
        assert [usage(reopened, item)['modelCalls'] for item in items] == [2, 1]
        assert all(usage(reopened, item)['modelCalls'] < usage(reopened, item)['modelCallLimit'] for item in items)
        member = read_member_projection(scope.home, scope.organization_id)['member']
        assert member['reserved']['modelCalls'] == len(calls)
        assert Decimal(member['remaining'][dimension]) == 0
        assert {row['model_call_id'] for row in receipts} == {row['id'] for row in calls}
        for row in receipts:
            receipt = json.loads(row['receipt_json'])
            assert receipt['organizationId'] == scope.organization_id
            assert receipt['callKey'] == row['call_key']
    assert shared['pool']['reserved']['modelCalls'] == len(local_calls)
    assert shared['pool']['reserved']['tokens'] == sum(row['input_limit'] + row['output_limit'] for row in local_calls)
    assert Decimal(shared['pool']['reserved']['configuredCostUsd']) == sum(Decimal(row['reserved_cost_usd']) for row in local_calls)
    assert Decimal(shared['pool']['remaining'][dimension]) == 0


@pytest.mark.parametrize('profile', ['default', 'alpha'])
def test_manager_prompt_gets_redacted_shared_budget_with_local_adaptive_pacing_off(installation, profile):
    root = installation
    configure(root, cost='2')
    observe(root)
    scope = root if profile == 'default' else root.alpha
    other = root.alpha if profile == 'default' else root
    item = objective(scope)
    claim = scope.store.claim_next()
    context = scope.store.context(claim)
    assert not scope.store.settings.adaptive_pacing_enabled
    assert context['costBudgetEnabled'] is True
    assert 'adaptivePacing' not in context['budget']
    submitted = json.loads(_prompt(claim, context, claim['type']).split('Submitted context:\n', 1)[1])
    view = submitted['context']['budget']['sharedAllowance']
    assert view == usage(scope.store, item)['sharedAllowance']
    if profile == 'default':
        assert view['pool']['limits']['modelCalls'] == view['member']['limits']['modelCalls'] * 2
    else:
        assert 'pool' not in view
    full = read_member_projection(scope.home, scope.organization_id)
    for field in ('ordinaryRemaining', 'remaining'):
        for dimension, amount in view['effectiveCapacity'][field].items():
            assert Decimal(amount) == min(Decimal(full[name][field][dimension]) for name in ('pool', 'member'))
    for dimension, amount in view['effectiveCapacity']['protectedRemaining'].items():
        assert Decimal(amount) == max(Decimal(0), Decimal(view['effectiveCapacity']['remaining'][dimension])
            - Decimal(view['effectiveCapacity']['ordinaryRemaining'][dimension]))
    assert view['effectiveCapacity']['configuredCostBounded'] is True
    assert view['pacing']['enabled'] is True and view['pacing']['mode'] == 'critical'
    serialized = json.dumps(view)
    assert not {'configuration', 'allocations', 'root', 'home', 'ledgerId'} & view.keys()
    assert other.organization_id not in serialized and other.owner_id not in serialized
    assert str(root.home) not in serialized
    peer = json.loads(_prompt({'type': 'request.message'}, {**context, 'conversation': {}},
        'request.message').split('Submitted context:\n', 1)[1])
    assert 'budget' not in peer['context']


def test_member_effective_protection_includes_tighter_pool_ordinary_envelope(installation):
    root = installation
    configure(root, calls=8)
    configuration = root.ledger.read()['configuration']
    configuration['limits']['modelCalls'] = 10
    configuration['protected']['modelCalls'] = 4
    next(row for row in configuration['allocations'] if row['profile'] == 'default')['limits']['modelCalls'] = 2
    root.ledger.configure(configuration, expected_revision=1, idempotency_key='asymmetric',
        root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    item = objective(root.alpha)
    view = usage(root.alpha.store, item)['sharedAllowance']
    assert 'pool' not in view
    assert view['member']['protectedRemaining']['modelCalls'] == 0
    assert view['effectiveCapacity']['remaining']['modelCalls'] == 8
    assert view['effectiveCapacity']['ordinaryRemaining']['modelCalls'] == 6
    assert view['effectiveCapacity']['protectedRemaining']['modelCalls'] == 2


def test_member_only_cost_ceiling_enables_runtime_cost_enforcement(installation):
    root = installation
    configure(root, cost='2')
    configuration = root.ledger.read()['configuration']
    configuration['limits']['configuredCostUsd'] = None
    root.ledger.configure(configuration, expected_revision=1, idempotency_key='member-cost-only',
        root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    objective(root.alpha)
    claim = root.alpha.store.claim_next()
    context = root.alpha.store.context(claim)
    assert context['costBudgetEnabled'] is True
    assert context['budget']['sharedAllowance']['effectiveCapacity']['configuredCostBounded'] is True
    assert context['budget']['configuredCostLimitUsd'] is None


def test_shared_wait_precedes_attempts_and_cross_profile_completion_is_not_spacing_starved(installation):
    root = installation
    configure(root)
    observe(root)
    objective(root)
    first = root.store.claim_next()
    reserve(root.store, first)
    started = root.ledger.read()['pacing']['deferUntil']
    target = objective(root.alpha)
    assert root.alpha.store.claim_next() is None
    waiting = rows(root.alpha.store, 'requests')[0]
    assert waiting['status'] == 'queued'
    assert waiting['attempts'] == waiting['execution_count'] == 0
    assert waiting['token'] is None and waiting['lease'] is None
    assert 'Shared organization allowance pacing' in waiting['reason']
    assert rows(root.alpha.store, 'objective_usage') == []
    low_priority = objective(root.alpha, 'low-priority-completion', priority='low')
    completion = queue(root.alpha.store, low_priority, 'request.review')
    claim = root.alpha.store.claim_next()
    assert claim['id'] == completion
    assert claim['priority'] < waiting['priority']
    reserve(root.alpha.store, claim)
    assert root.ledger.read()['pacing']['deferUntil'] == started
    root.now[0] += 301
    admitted = root.alpha.store.claim_next()
    assert admitted['id'] == waiting['id']
    assert admitted['attempts'] == admitted['execution_count'] == 1
    reserve(root.alpha.store, admitted)
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 3


def test_preclaimed_stage_rechecks_shared_spacing_without_debit_or_local_reservation(installation):
    root = installation
    configure(root)
    observe(root)
    objective(root)
    objective(root.alpha)
    first, second = root.store.claim_next(), root.alpha.store.claim_next()
    reserve(root.store, first)
    with pytest.raises(OrganizationPacingDeferred) as deferred:
        reserve(root.alpha.store, second)
    assert deferred.value.defer_until > root.now[0]
    assert rows(root.alpha.store, 'organization_model_calls') == []
    assert rows(root.alpha.store, 'organization_shared_call_receipts') == []
    assert root.alpha.store.defer(second, str(deferred.value), defer_until=deferred.value.defer_until)
    request = rows(root.alpha.store, 'requests')[0]
    assert request['attempts'] == 0 and request['execution_count'] == 1
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 1


@pytest.mark.parametrize('limit,expected', [
    ({'max_model_calls': 1}, 'model-call budget exhausted'),
    ({'max_total_tokens': 4096}, 'reserved-token budget'),
    ({'max_cost_usd': '0.1'}, 'configured-cost budget exhausted'),
])
def test_local_caps_reject_before_real_global_debit(installation, limit, expected):
    root = installation
    root.store = OrganizationStore(root.store.path, replace(root.store.settings, **limit))
    configure(root)
    objective(root)
    claim = root.store.claim_next()
    options = {'input_limit': 2000} if 'max_total_tokens' in limit else {}
    reserve(root.store, claim, **options)
    before = root.ledger.read()['pool']['reserved']
    with pytest.raises(ValueError, match=expected):
        reserve(root.store, claim, **options)
    assert root.ledger.read()['pool']['reserved'] == before
    assert len(rows(root.store, 'organization_model_calls')) == 1
    assert len(rows(root.store, 'organization_shared_call_receipts')) == 1


def test_owner_chat_and_objective_share_real_debits_and_uncertain_turn_never_refunds(installation):
    root = installation
    configure(root, cost='2')
    turn = owner_turn(root)
    dispatch(root.store, turn)
    with pytest.raises(ValueError, match='exactly one'):
        dispatch(root.store, turn)
    assert root.store.owner_chat_finish(turn, status='uncertain', reason='Fixture interrupted after dispatch')
    objective(root)
    reserve(root.store, root.store.claim_next())
    shared = root.ledger.read()
    assert shared['pool']['reserved']['modelCalls'] == shared['member']['reserved']['modelCalls'] == 2
    assert Decimal(shared['member']['reserved']['configuredCostUsd']) == Decimal('0.2')
    receipts = rows(root.store, 'organization_shared_call_receipts')
    assert {json.loads(row['receipt_json'])['payload']['kind'] for row in receipts} == {'owner_chat', 'objective'}
    assert next(row for row in receipts if row['owner_chat_turn_id'])['owner_chat_turn_id'] == turn


def test_owner_chat_cannot_spend_protected_completion_but_review_can(installation):
    root = installation
    configure(root, calls=3, protected_calls=2)
    item = objective(root)
    first = root.store.claim_next()
    reserve(root.store, first)
    turn = owner_turn(root)
    with pytest.raises(SharedAllowanceError, match='protected_or_exhausted_modelCalls'):
        dispatch(root.store, turn)
    assert root.store.owner_chat_turn_state(turn)['dispatched'] == 0
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 1
    completion = queue(root.store, item, 'request.review')
    claim = root.store.claim_next()
    assert claim['id'] == completion
    reserve(root.store, claim)
    assert root.ledger.read()['member']['protectedRemaining']['modelCalls'] == 1


def test_real_global_commit_survives_local_failure_and_retry_is_not_a_second_send(installation):
    root = installation
    configure(root)
    objective(root)
    claim = root.store.claim_next()
    with root.store._write() as conn:
        conn.execute("CREATE TRIGGER reject_fixture_call BEFORE INSERT ON organization_model_calls BEGIN SELECT RAISE(ABORT,'fixture_local_failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture_local_failure'):
        reserve(root.store, claim)
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 1
    assert rows(root.store, 'organization_model_calls') == []
    assert rows(root.store, 'organization_shared_call_receipts') == []
    with root.store._write() as conn:
        conn.execute('DROP TRIGGER reject_fixture_call')
    with pytest.raises(SharedAllowanceError, match='already_debited_no_replay'):
        reserve(root.store, claim)
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 1
    assert rows(root.store, 'organization_model_calls') == []


@pytest.mark.parametrize('destination', ['standalone', 'different-profile'])
def test_copied_enrolled_store_cannot_escape_its_local_scope_pin(installation, tmp_path, destination):
    root = installation
    configure(root)
    objective(root.alpha)
    claim = root.alpha.store.claim_next()
    reserve(root.alpha.store, claim)
    target = tmp_path / 'moved' if destination == 'standalone' else root.home / 'profiles' / 'renamed'
    shutil.copytree(root.alpha.home, target)
    copied = OrganizationStore(target / 'organization' / 'state.db')
    with pytest.raises(ValueError, match='scope provenance changed'):
        reserve(copied, claim)
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 0
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 1
    assert len(rows(copied, 'organization_model_calls')) == 1
    assert len(rows(copied, 'organization_shared_call_receipts')) == 1


def test_existing_local_pin_cannot_silently_opt_out_when_shared_files_disappear(installation):
    root = installation
    configure(root)
    item = objective(root)
    claim = root.store.claim_next()
    reserve(root.store, claim)
    root.ledger.path.unlink()
    root.ledger.marker.unlink()
    reopened = OrganizationStore(root.store.path)
    with pytest.raises(SharedAllowanceError):
        reserve(reopened, claim)
    assert usage(reopened, item)['sharedAllowance']['state'] == 'blocked'
    assert len(rows(reopened, 'organization_model_calls')) == 1
    assert not root.ledger.path.exists() and not root.ledger.marker.exists()


@pytest.mark.parametrize('mutation', ['null-pin', 'missing-pin'])
def test_corrupt_local_provenance_cannot_downgrade_previously_shared_calls(installation, mutation):
    root = installation
    configure(root)
    item = objective(root)
    claim = root.store.claim_next()
    reserve(root.store, claim)
    with root.store._write() as conn:
        operation = 'update' if mutation == 'null-pin' else 'delete'
        name = 'shared_scope_pin_immutable_' + operation
        trigger = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        conn.execute('DROP TRIGGER ' + name)
        if mutation == 'null-pin':
            conn.execute("UPDATE organization_shared_scope_pin SET binding_json='null'")
        else:
            conn.execute('DELETE FROM organization_shared_scope_pin')
        conn.execute(trigger)
    root.ledger.path.unlink()
    root.ledger.marker.unlink()
    with pytest.raises(ValueError):
        reserve(root.store, claim)
    assert usage(root.store, item)['sharedAllowance']['state'] == 'blocked'
    assert len(rows(root.store, 'organization_model_calls')) == 1
    assert len(rows(root.store, 'organization_shared_call_receipts')) == 1
    assert not root.ledger.path.exists() and not root.ledger.marker.exists()


@pytest.mark.parametrize('reason', ['exhausted', 'period-inactive'])
def test_shared_hard_stop_blocks_scheduler_before_claim_or_attempt(installation, reason):
    root = installation
    configure(root, calls=1)
    objective(root.alpha)
    first = root.alpha.store.claim_next()
    reserve(root.alpha.store, first)
    assert root.alpha.store.fail(first, 'Fixture stage ended')
    item = objective(root.alpha, 'next-objective')
    if reason == 'period-inactive':
        root.now[0] += 3601
    assert root.alpha.store.claim_next() is None
    request = next(row for row in rows(root.alpha.store, 'requests') if row['objective_id'] == item['id'])
    assert request['status'] == 'pending_intervention'
    assert request['attempts'] == request['execution_count'] == 0
    assert request['token'] is None and request['lease'] is None
    assert ('exhausted' if reason == 'exhausted' else 'inactive') in request['reason']
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 1


@pytest.mark.parametrize('action', ['configure', 'observe'])
@pytest.mark.parametrize('mode', ['managed-install', 'managed-config-key'])
def test_late_managed_lock_rolls_back_complete_shared_owner_transaction(installation, monkeypatch, action, mode):
    from eidolon_cli.organization_shared_allowance import _hash, managed_shared_allowance
    root = installation
    configure(root)
    objective(root)
    claim = root.store.claim_next()
    reserve(root.store, claim)
    assert root.store.fail(claim, 'Retain one debit while changing owner policy')
    before = root.ledger.read()
    durable = shared_dump(root)
    key = 'late-' + action
    final_key = key if action == 'configure' else 'observation:' + _hash(key)
    inserted = arm_late_managed_lock(root, monkeypatch, mode, final_key)
    arguments = {'expected_revision': before['revision'], 'idempotency_key': key,
        'root_organization_id': root.organization_id, 'root_owner_id': root.owner_id}
    with pytest.raises(SharedAllowanceError, match='managed_configuration'):
        if action == 'configure':
            configuration = json.loads(json.dumps(before['configuration']))
            configuration['limits']['modelCalls'] += 2
            root.ledger.configure(configuration, **arguments)
        else:
            root.ledger.observe({'remainingPercent': 5, 'asOf': iso(root.now[0]),
                'expiresAt': iso(root.now[0] + 60), 'resetAt': None}, **arguments)
    assert inserted == [2]
    assert managed_shared_allowance(root.home)
    assert shared_dump(root) == durable
    assert root.ledger.read() == before
    assert root.ledger.read()['revision'] == 1
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 1
    # The original immutable receipt is still replayable under the new lock.
    receipt = root.ledger.configure(before['configuration'], expected_revision=0, idempotency_key='configure',
        root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    assert receipt['revision'] == 1
    assert shared_dump(root) == durable


@pytest.mark.parametrize('mode', ['managed-install', 'managed-config-key'])
def test_late_managed_lock_during_initial_activation_retains_fail_closed_marker(installation, monkeypatch, mode):
    from eidolon_cli.organization_shared_allowance import resolve_binding
    root = installation
    inserted = arm_late_managed_lock(root, monkeypatch, mode, 'configure')
    with pytest.raises(SharedAllowanceError, match='managed_configuration'):
        configure(root)
    assert inserted == [1]
    assert root.ledger.marker.is_file()
    with closing(sqlite3.connect(root.ledger.path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    with pytest.raises(SharedAllowanceError):
        root.ledger.read()
    with pytest.raises(SharedAllowanceError):
        resolve_binding(root.alpha.home, root.alpha.organization_id)
    assert rows(root.store, 'organization_shared_call_receipts') == []
    assert rows(root.alpha.store, 'organization_shared_call_receipts') == []

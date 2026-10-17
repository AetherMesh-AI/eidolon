"""Real owner/SQLite/source path; synthetic account evidence, never native or HTTP."""
from dataclasses import asdict
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_metadata as metadata
from eidolon_cli import organization_setup_subscription_policy as policy
from eidolon_cli import organization_team_budget as budget
from eidolon_cli import organization_owner_policy as owner
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import _fingerprint, persisted_settings
from eidolon_cli.organization_store import ORGANIZATION_SCHEMA
from eidolon_cli.organization_setup_subscription_store import TrustedBinding
from tui_gateway import organization_credential_transport as transport_module
from tui_gateway import methods_organization_setup_subscription as gateway


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    (tmp_path / 'config.yaml').write_text('{}')
    (tmp_path / 'organization').mkdir()
    conn = sqlite3.connect(tmp_path / 'organization' / 'state.db', isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    # Match production initialization: one durable DDL transaction, not a
    # separate Windows filesystem flush for every schema statement.
    conn.executescript('BEGIN IMMEDIATE;\n' + ORGANIZATION_SCHEMA + '\nCOMMIT;')
    identity = metadata._save_in_transaction(conn,
        {'displayName': 'Subscription fixture', 'ownerDisplayName': 'Owner',
         'expectedOrganizationId': None, 'expectedRevision': None}, 'metadata', 'initial')['metadata']
    settings = OrganizationSettings()
    conn.execute('INSERT INTO organization_policy VALUES (1,1,?,?)',
                 (_fingerprint(asdict(settings)), json.dumps(asdict(settings), sort_keys=True)))

    class Transport:
        closed = False
    transport, generation = Transport(), object()
    monkeypatch.setitem(transport_module._owners, transport, (tmp_path.resolve(), generation))
    binding = TrustedBinding('synthetic-host', str(tmp_path), identity['organizationId'],
                             identity['owner']['ownerId'], 'synthetic-registration',
                             'synthetic-client', 'synthetic-account')
    account_digest = policy.preview_digest(asdict(binding))
    epoch = '00000000-0000-4000-8000-000000000003'

    class Store:
        calls = 0
        deny = False
        def attest_subscription_binding_nonblocking(self, actual, **kwargs):
            assert actual is binding
            assert kwargs['lease_generation_guard']() is True
            self.calls += 1
            if self.deny:
                raise policy.SubscriptionPolicyError('connection_binding_changed')
            return {'account_binding_digest': account_digest, 'authorization_epoch': epoch, 'store_revision': 1}
    store = Store()
    from eidolon_cli.organization_team_setup import _current
    revision = _current(conn, tmp_path, identity['organizationId'])[3]
    catalog = {'version': 1, 'state': 'ready', 'catalogId': 'synthetic-catalog',
               'connectionRevision': 1, 'connectionId': account_digest, 'authorizationEpoch': epoch,
               'sourceRevision': revision, 'fetchedAt': time.time(), 'expiresAt': time.time() + 3600,
               'models': [{'slug': 'synthetic-model', 'displayName': 'Synthetic'}]}
    evidence = policy.SubscriptionPreflight(binding, store, 1, epoch, account_digest, 'synthetic-model',
        catalog, generation, lambda: transport_module.owner_generation(transport) is generation,
        None, lambda: None)
    monkeypatch.setattr(gateway, 'subscription_preflight', lambda *a, **k: evidence)
    monkeypatch.setattr(gateway, 'turnover_subscription_context', lambda *a: {'catalogState': 'not_requested'})
    candidate = {'version': 3, 'mode': 'subscription_calls', 'improveCallLimit': 4,
        'generationCallLimit': 3, 'totalCallLimit': 7, 'requestTimeoutSeconds': 120,
        'connectionId': account_digest, 'authorizationEpoch': epoch, 'modelSlug': 'synthetic-model'}
    value = SimpleNamespace(conn=conn, home=tmp_path, identity=identity, settings=settings,
        transport=transport, generation=generation, candidate=candidate, evidence=evidence, store=store)
    yield value
    conn.close()


def request(ledger):
    from eidolon_cli.organization_team_setup import _current
    _, current, _, revision = _current(ledger.conn, ledger.home, ledger.identity['organizationId'])
    ledger.evidence.catalog_evidence['sourceRevision'] = revision
    return {'expectedOrganizationId': ledger.identity['organizationId'], 'expectedGeneration': current['generation'],
            'sourceRevision': revision, 'configuration': dict(ledger.candidate)}


def reviewed(ledger, key='activation'):
    params = request(ledger)
    preview = policy.preview_subscription_policy(ledger.home, params, ledger.transport)
    return {**params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': key,
            'confirmed': True, 'acknowledgement': preview['acknowledgement']}


def apply(ledger, key='activation'):
    params = reviewed(ledger, key)
    return policy.apply_subscription_policy(ledger.home, params, ledger.transport), params


def dump(ledger):
    return list(ledger.conn.iterdump())


@pytest.mark.macos_only
def test_legacy_empty_activation_is_explicit_atomic_and_preserves_execution(ledger):
    ledger.conn.execute('INSERT INTO organization_team_generations '
        '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
        'VALUES (?,?,?,?,?,?,?,0)', ('legacy', ledger.identity['organizationId'], 'hash', 'source', 'failed', 1, 200))
    before = dump(ledger)
    params = request(ledger)
    preview = policy.preview_subscription_policy(ledger.home, params, ledger.transport)
    assert dump(ledger) == before
    assert preview['effectiveCallLimits'] == {'improveCallLimit': 4, 'generationCallLimit': 3, 'totalCallLimit': 7}
    assert preview['remainingCalls']['total'] == 6
    assert all(item['historicalOrigin'] == 'unknown' for item in preview['retiredGuarantees'])
    settings_before = tuple(ledger.conn.execute('SELECT settings,fingerprint FROM organization_policy').fetchone())
    result, params = apply(ledger)
    assert result['receipt']['appliedGeneration'] == 2
    assert tuple(ledger.conn.execute('SELECT settings,fingerprint FROM organization_policy').fetchone()) == settings_before
    assert persisted_settings(ledger.conn) == ledger.settings
    assert budget.operation_counts(ledger.conn)['legacy'] == 1
    assert ledger.conn.execute('SELECT reserved_tokens FROM organization_team_generations').fetchone()[0] == 200
    assert ledger.conn.execute('SELECT count(*) FROM agent_history').fetchone()[0] == 0
    after = dump(ledger)
    replay = policy.apply_subscription_policy(ledger.home, params, ledger.transport)
    assert replay['historical'] is True and replay['receipt'] == result['receipt']
    assert dump(ledger) == after
    with pytest.raises(policy.SubscriptionPolicyError, match='idempotency_conflict'):
        policy.apply_subscription_policy(ledger.home, {**params, 'configuration': {**ledger.candidate, 'totalCallLimit': 8}}, ledger.transport)
    view = budget.budget_view(ledger.conn, ledger.settings)
    assert view['policyVersion'] == 3 and view['remaining']['totalCalls'] == 6
    assert view['reservedTokens'] is None and view['reservedCostUsd'] is None
    assert view['setupUsage']['costUsd'] is None and view['setupUsage']['totalTokens'] is None


@pytest.mark.parametrize('namespace', sorted(policy.ORDINARY_NAMESPACES))
def test_known_ordinary_namespace_is_classified_and_digested(ledger, namespace):
    (ledger.home / 'config.yaml').write_text(json.dumps({namespace: {}}))
    first = policy._closed_sources(ledger.conn, ledger.home)
    (ledger.home / 'config.yaml').write_text(json.dumps({namespace: {'synthetic': True}}))
    second = policy._closed_sources(ledger.conn, ledger.home)
    assert first[0] == second[0] == ledger.settings
    assert first[2] != second[2]


@pytest.mark.parametrize('raw', [{'new_authority': {}}, {'organization': {'unknown': 1}},
    {'organization': {'ledger_id': 'spoof'}}, {'organization': {'shared_allowance': {}}}])
@pytest.mark.macos_only
def test_unclassified_source_refuses_without_mutation(ledger, raw):
    (ledger.home / 'config.yaml').write_text(json.dumps(raw))
    before = dump(ledger)
    with pytest.raises(policy.SubscriptionPolicyError):
        policy.preview_subscription_policy(ledger.home, request(ledger), ledger.transport)
    assert dump(ledger) == before


@pytest.mark.macos_only
def test_unknown_table_and_managed_scope_block(ledger, monkeypatch):
    ledger.conn.execute('CREATE TABLE unknown_authority(cap TEXT)')
    with pytest.raises(policy.SubscriptionPolicyError, match='unsupported_scope'):
        policy.preview_subscription_policy(ledger.home, request(ledger), ledger.transport)
    ledger.conn.execute('DROP TABLE unknown_authority')
    from eidolon_cli import managed_scope
    monkeypatch.setattr(managed_scope, 'load_managed_config', lambda: {'organization': {'unknown': 7}})
    with pytest.raises(policy.SubscriptionPolicyError, match='managed'):
        policy._closed_sources(ledger.conn, ledger.home)


@pytest.mark.parametrize('status', ['running', 'uncertain'])
@pytest.mark.macos_only
def test_pending_attempt_blocks_policy_without_repair(ledger, status):
    ledger.conn.execute('INSERT INTO organization_team_generations '
        '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,0)',
        ('pending', ledger.identity['organizationId'], 'hash', 'source', status))
    before = dump(ledger)
    with pytest.raises(policy.SubscriptionPolicyError, match='attempt_running_or_uncertain'):
        reviewed(ledger)
    assert dump(ledger) == before


@pytest.mark.macos_only
def test_fence_failure_rolls_back_all_schema_and_override(ledger):
    params = reviewed(ledger)
    original = ledger.store.attest_subscription_binding_nonblocking
    count = 0
    def fail_final(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise policy.SubscriptionPolicyError('retired')
        return original(*args, **kwargs)
    ledger.store.attest_subscription_binding_nonblocking = fail_final
    before = dump(ledger)
    with pytest.raises(policy.SubscriptionPolicyError, match='retired'):
        policy.apply_subscription_policy(ledger.home, params, ledger.transport)
    assert dump(ledger) == before


@pytest.mark.macos_only
def test_cas_ack_and_source_mismatch_do_not_activate(ledger):
    params = reviewed(ledger)
    for changed in ({'expectedGeneration': 2}, {'previewVersion': '0' * 64},
                    {'acknowledgement': {'version': 1}}, {'sourceRevision': 'f' * 64}):
        before = dump(ledger)
        with pytest.raises((policy.SubscriptionPolicyError, owner.OwnerPolicyError)):
            policy.apply_subscription_policy(ledger.home, {**params, **changed}, ledger.transport)
        assert dump(ledger) == before


@pytest.mark.parametrize('mutation', ['partial', 'trigger', 'receipt', 'missing_attempt'])
@pytest.mark.macos_only
def test_schema_and_cross_table_corruption_refused(ledger, mutation):
    apply(ledger)
    if mutation == 'partial':
        ledger.conn.execute('DROP TABLE organization_setup_subscription_outcomes')
    elif mutation == 'trigger':
        ledger.conn.execute('DROP TRIGGER organization_setup_subscription_attempts_immutable_delete')
    elif mutation == 'receipt':
        with pytest.raises(sqlite3.IntegrityError):
            ledger.conn.execute("UPDATE organization_setup_subscription_policy_receipts SET input_hash='corrupt'")
        return
    else:
        ledger.conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,0)',
            ('missing', ledger.identity['organizationId'], 'h', 's', 'running'))
        budget.record_operation(ledger.conn, 'missing', ledger.identity['organizationId'], 'generateTeam', 2)
    with pytest.raises((policy.SubscriptionPolicyError, owner.OwnerPolicyError)):
        budget.read_policy(ledger.conn)


@pytest.mark.macos_only
def test_generic_v2_editor_cannot_downgrade_v3_even_before_dispatch(ledger):
    apply(ledger)
    state = budget.budget_policy(ledger.home, ledger.identity['organizationId'])
    with pytest.raises(owner.OwnerPolicyError, match='subscription_mode_transition_unsupported'):
        budget.preview_budget(ledger.home, {'expectedOrganizationId': state['organizationId'],
            'expectedGeneration': state['policyGeneration'], 'sourceRevision': state['sourceRevision'],
            'configuration': dict(budget.SUGGESTED_POLICY)})


@pytest.mark.macos_only
def test_passive_readiness_never_uses_account_evidence(ledger, monkeypatch):
    monkeypatch.setattr(gateway, 'subscription_preflight', lambda *a, **k: pytest.fail('passive preflight'))
    state = policy.readiness_subscription_policy(ledger.home, ledger.identity['organizationId'], ledger.transport)
    assert state['state'] == 'available' and state['connectionReadiness'] == 'unchecked'
    assert ledger.store.calls == 0


@pytest.mark.linux_only
def test_linux_refuses_before_grant_store_or_inference(tmp_path):
    with pytest.raises(policy.SubscriptionPolicyError, match='subscription_platform_unsupported'):
        policy.readiness_subscription_policy(tmp_path, '00000000-0000-4000-8000-000000000001', object())


@pytest.mark.windows_only
def test_windows_refuses_before_grant_store_or_inference(tmp_path):
    with pytest.raises(policy.SubscriptionPolicyError, match='subscription_platform_unsupported'):
        policy.readiness_subscription_policy(tmp_path, '00000000-0000-4000-8000-000000000001', object())


@pytest.mark.macos_only
def test_historical_replay_never_requires_live_account(ledger, monkeypatch):
    result, params = apply(ledger)
    monkeypatch.setattr(gateway, 'subscription_preflight', lambda *a, **k: pytest.fail('historical grant reuse'))
    replay = policy.apply_subscription_policy(ledger.home, params, ledger.transport)
    assert replay['receipt'] == result['receipt'] and replay['historical'] is True
    ledger.transport.closed = True
    with pytest.raises(policy.SubscriptionPolicyError, match='local_owner'):
        policy.apply_subscription_policy(ledger.home, params, ledger.transport)


@pytest.mark.macos_only
def test_immutable_audit_cannot_be_downgraded_by_override_removal(ledger):
    apply(ledger)
    ledger.conn.execute('UPDATE organization_owner_budget_policy SET configuration=?',
                       (json.dumps({'setup_budget': budget.SUGGESTED_POLICY}),))
    with pytest.raises(owner.OwnerPolicyError, match='subscription_mode_transition_unsupported'):
        budget.read_policy(ledger.conn)


@pytest.mark.macos_only
def test_policy_preview_does_not_reduce_explicit_raise_to_legacy_three(ledger):
    preview = policy.preview_subscription_policy(ledger.home, request(ledger), ledger.transport)
    assert preview['priorPolicy'] is None
    assert preview['effectiveCallLimits']['totalCallLimit'] == 7
    assert preview['retainedInheritanceCallCeiling'] == ledger.settings.max_model_calls
    assert preview['remainingCalls'] == {'total': 7, 'generateTeam': 3, 'improveTeamInput': 4}


@pytest.mark.macos_only
def test_unexpected_trigger_cannot_change_debt_during_activation(ledger):
    ledger.conn.execute('CREATE TRIGGER unexpected_debt AFTER UPDATE ON organization_owner_budget_policy '
                        'BEGIN UPDATE organization_team_generations SET dispatched=0; END')
    with pytest.raises(policy.SubscriptionPolicyError, match='unsupported_scope'):
        reviewed(ledger)


@pytest.mark.parametrize('expiry', [True, float('nan'), float('inf'), 0])
def test_fence_rejects_invalid_or_expired_catalog_before_store(ledger, expiry):
    ledger.evidence.catalog_evidence['expiresAt'] = expiry
    with pytest.raises(policy.SubscriptionPolicyError, match='catalog_evidence_expired'):
        policy.fence_preflight(ledger.evidence)
    assert ledger.store.calls == 0


def test_original_final_fence_may_outlive_catalog_cache_but_not_binding(ledger):
    ledger.evidence.catalog_evidence['expiresAt'] = 0
    policy.fence_preflight(ledger.evidence, admitted_catalog=True)
    ledger.store.deny = True
    with pytest.raises(policy.SubscriptionPolicyError, match='connection_binding_changed'):
        policy.fence_preflight(ledger.evidence, admitted_catalog=True)


# Executed from a separate immutable checkout supplied by hosted CI. No source
# inspection or replacement of old validators: this is a real old import graph.
_OLD_RUNTIME_PROBE = r'''
import json, os, sqlite3, sys
from pathlib import Path
from eidolon_cli.organization_team_setup import _current
from eidolon_cli.organization_team_admission import _infer
from eidolon_cli.organization_team_budget import read_policy
from eidolon_cli.organization_owner_policy import owner_policy
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_config import OrganizationSettings
home = Path(os.environ['HERMES_HOME'])
params = json.loads(os.environ['EIDOLON_OLD_PARAMS'])
calls = []
def forbidden_send(*args, **kwargs):
    calls.append('provider')
    raise AssertionError('Old runtime reached inference')
def read_current():
    with sqlite3.connect(home / 'organization' / 'state.db') as conn:
        conn.row_factory = sqlite3.Row
        return _current(conn, home, params['expectedOrganizationId'])
def read_budget():
    with sqlite3.connect(home / 'organization' / 'state.db') as conn:
        conn.row_factory = sqlite3.Row
        return read_policy(conn)
def infer():
    return _infer(home, params, 'old-runtime-attempt', 'generateTeam', forbidden_send,
                  lambda conn, response: response)
actions = {'setup': read_current, 'budget': read_budget,
           'owner': lambda: owner_policy(home, params['expectedOrganizationId']),
           'infer': infer,
           'initialize': lambda: OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings())}
if os.environ.get('EIDOLON_OLD_LIVE') == '1':
    before = read_current()
    print(json.dumps({'ready': True, 'generation': before[1]['generation']}), flush=True)
    sys.stdin.readline()
results = {}
for name, action in actions.items():
    try:
        action()
    except Exception as exc:
        results[name] = {'type': type(exc).__name__, 'reason': getattr(exc, 'reason', None), 'message': str(exc)}
    else:
        results[name] = {'accepted': True}
print(json.dumps({'results': results, 'providerCalls': len(calls)}), flush=True)
'''


def _old_environment(ledger, *, live=False):
    import os
    from pathlib import Path
    old = os.environ.get('EIDOLON_OLD_RUNTIME_PATH')
    if not old:
        pytest.skip('Immutable old runtime checkout is supplied by hosted compatibility CI')
    old = Path(old).resolve()
    assert (old / 'eidolon_cli' / 'organization_team_budget.py').is_file()
    params = request(ledger)
    env = {**os.environ, 'PYTHONPATH': str(old), 'HERMES_HOME': str(ledger.home),
           'EIDOLON_OLD_PARAMS': json.dumps(params), 'EIDOLON_OLD_LIVE': '1' if live else '0'}
    return old, env


def _assert_old_refusal(result):
    assert result['providerCalls'] == 0
    assert set(result['results']) == {'setup', 'budget', 'owner', 'infer', 'initialize'}
    for name, outcome in result['results'].items():
        assert 'accepted' not in outcome, (name, outcome)
        assert (outcome.get('reason') == 'setup_budget_corrupt'
                or 'Setup budget requires version 2' in outcome.get('message', '')), (name, outcome)


@pytest.mark.macos_only
def test_immutable_old_runtime_cold_reopen_rejects_v3_without_send(ledger):
    import subprocess
    import sys
    old, env = _old_environment(ledger)
    apply(ledger)
    result = subprocess.run([sys.executable, '-c', _OLD_RUNTIME_PROBE], cwd=old, env=env,
                            capture_output=True, text=True, timeout=45, check=True)
    _assert_old_refusal(json.loads(result.stdout.strip().splitlines()[-1]))


@pytest.mark.macos_only
def test_already_live_old_runtime_rejects_next_admission_after_v3(ledger):
    import subprocess
    import sys
    old, env = _old_environment(ledger, live=True)
    child = subprocess.Popen([sys.executable, '-c', _OLD_RUNTIME_PROBE], cwd=old, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as reader:
            ready = reader.submit(child.stdout.readline)
            try:
                line = ready.result(timeout=45)
            except BaseException:
                child.kill()
                raise
        assert json.loads(line) == {'ready': True, 'generation': 1}
        apply(ledger)
        output, _ = child.communicate('continue\n', timeout=45)
        assert child.returncode == 0
        _assert_old_refusal(json.loads(output.strip().splitlines()[-1]))
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


@pytest.mark.macos_only
def test_retained_global_pause_blocks_policy_and_valid_v3_snapshot_without_clearing(ledger):
    apply(ledger)
    params = request(ledger)
    ledger.conn.execute('INSERT INTO organization_policy_pause VALUES (1)')
    before = dump(ledger)
    with pytest.raises(policy.SubscriptionPolicyError, match='organization_policy_paused'):
        policy.build_authoritative_snapshot(ledger.conn, ledger.home, ledger.transport,
                                            ledger.generation, ledger.evidence)
    with pytest.raises(policy.SubscriptionPolicyError, match='organization_policy_paused'):
        policy.preview_subscription_policy(ledger.home, params, ledger.transport)
    state = policy.readiness_subscription_policy(ledger.home, ledger.identity['organizationId'], ledger.transport)
    assert state['state'] == 'blocked' and state['reason'] == 'organization_policy_paused'
    assert dump(ledger) == before
    assert ledger.conn.execute('SELECT count(*) FROM organization_team_generations').fetchone()[0] == 0


def test_current_config_roots_require_explicit_setup_scope_classification():
    from eidolon_cli.config import _KNOWN_ROOT_KEYS
    from eidolon_cli.config_defaults import DEFAULT_CONFIG
    supported = policy.ORDINARY_NAMESPACES | {'organization'}
    assert not (set(DEFAULT_CONFIG) | set(_KNOWN_ROOT_KEYS)) - supported, (
        'New recognized configuration roots need an explicit dedicated-setup authority classification')


@pytest.mark.macos_only
def test_policy_preview_cannot_rebind_foreign_expected_organization(ledger):
    params = request(ledger)
    params['expectedOrganizationId'] = '00000000-0000-4000-8000-000000000099'
    with pytest.raises(policy.SubscriptionPolicyError, match='identity_conflict'):
        policy.preview_subscription_policy(ledger.home, params, ledger.transport)

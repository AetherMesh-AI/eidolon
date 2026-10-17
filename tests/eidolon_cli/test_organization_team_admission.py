"""Physical setup sends are durably bounded and never replay uncertain outcomes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import threading

import pytest

from eidolon_cli import organization_team_admission as admission
from eidolon_cli import organization_team_generation as generation
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from tests.eidolon_cli.test_organization_team_setup import profile, params, dump, confirmed
from tests.eidolon_cli.test_organization_shared_allowance import installation, configure


def request(profile, key='generate'):
    draft = params(profile)
    return {key: value for key, value in draft.items() if key != 'team'} | {
        'jobs': 'Research\nand write', 'goals': 'Useful reports', 'namingStyle': '', 'idempotencyKey': key}


def fake_provider(monkeypatch, *, outcome=None):
    sends = []
    def generate(home, jobs, goals, naming, *, reserve_model_call, cancel=None):
        reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        sends.append((str(home), jobs, goals, naming))
        if outcome:
            return outcome()
        return deepcopy(team.presets()[0]['team'])
    monkeypatch.setattr(generation, 'generate', generate)
    return sends


def rows(profile):
    with sqlite3.connect(profile.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM organization_team_generations')]


def test_generation_returns_review_only_and_exact_replay_without_second_send(profile, monkeypatch):
    sends = fake_provider(monkeypatch)
    value = request(profile, ' generate ')
    before = params(profile)
    result = admission.generate_team(profile.home, value)
    after = dump(profile)
    assert result['team'] == before['team']
    assert result['sourceRevision'] == before['sourceRevision']
    assert admission.generate_team(profile.home, value) == result
    assert len(sends) == 1
    assert sends[0][1:] == ('Research\nand write', 'Useful reports', '')
    assert dump(profile) == after
    assert team.team_setup(profile.home, profile.identity['organizationId'])['state'] == 'available'
    assert rows(profile)[0]['reserved_tokens'] == 200
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        admission.generate_team(profile.home, {**value, 'goals': 'Different'})
    with sqlite3.connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 0


def test_three_completed_proposals_exhaust_allowance_without_fourth_send(profile, monkeypatch):
    sends = fake_provider(monkeypatch)
    for index in range(3):
        admission.generate_team(profile.home, request(profile, str(index)))
    with pytest.raises(ValueError, match='allowance'):
        admission.generate_team(profile.home, request(profile, 'fourth'))
    assert len(sends) == 3
    assert sum(row['reserved_tokens'] for row in rows(profile)) == 600


@pytest.mark.parametrize('outcome', ['cancel', 'invalid'])
def test_failed_or_cancelled_physical_send_never_refunds_or_replays(profile, monkeypatch, outcome):
    def fail():
        if outcome == 'cancel':
            raise InterruptedError('Cancelled after send')
        return {'executive': {}, 'members': []}
    sends = fake_provider(monkeypatch, outcome=fail)
    value = request(profile)
    with pytest.raises((InterruptedError, ValueError)):
        admission.generate_team(profile.home, value)
    assert rows(profile)[0]['status'] == ('uncertain' if outcome == 'cancel' else 'failed')
    assert rows(profile)[0]['reserved_tokens'] == 200
    with pytest.raises(OwnerPolicyError, match='uncertain|failed'):
        admission.generate_team(profile.home, value)
    if outcome == 'cancel':
        with pytest.raises(OwnerPolicyError, match='uncertain'):
            admission.generate_team(profile.home, {**value, 'idempotencyKey': 'new-key'})
        assert len(sends) == 1
    else:
        with pytest.raises(ValueError):
            admission.generate_team(profile.home, {**value, 'idempotencyKey': 'new-key'})
        assert len(sends) == 2
        assert sum(row['reserved_tokens'] for row in rows(profile)) == 400
    assert team.create_team(profile.home, confirmed(profile))['state'] == 'created'


def test_crash_left_running_admission_prevents_restart_replay(profile, monkeypatch):
    value = request(profile)
    with sqlite3.connect(profile.path) as conn:
        conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key, organization_id, input_hash, source_revision, status, created) VALUES (?,?,?,?,?,0)',
            (value['idempotencyKey'], value['expectedOrganizationId'], admission._hash(value), value['sourceRevision'], 'running'))
    sends = fake_provider(monkeypatch)
    for key in ('generate', 'new-key'):
        with pytest.raises(OwnerPolicyError, match='running|uncertain'):
            admission.generate_team(profile.home, {**value, 'idempotencyKey': key})
    assert not sends


def test_concurrent_admission_single_send_and_no_reservation_reentry(profile, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def wait():
        entered.set()
        assert release.wait(10)
        return deepcopy(team.presets()[0]['team'])
    sends = fake_provider(monkeypatch, outcome=wait)
    value = request(profile)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(admission.generate_team, profile.home, value)
        try:
            assert entered.wait(10)
            with pytest.raises(OwnerPolicyError, match='generation_running'):
                admission.generate_team(profile.home, value)
            with pytest.raises(ValueError, match='exactly one'):
                admission._reserve(profile.home, value, provider='fixture', model='fixture', input_limit=100, output_limit=100)
        finally:
            release.set()
        assert first.result(timeout=10)['previewVersion']
    assert len(sends) == 1


def test_policy_edit_during_model_call_cannot_publish_stale_preview(profile, monkeypatch):
    def edit():
        (profile.home / 'config.yaml').write_text('{"model":{"default":"changed"}}')
        return deepcopy(team.presets()[0]['team'])
    sends = fake_provider(monkeypatch, outcome=edit)
    with pytest.raises(OwnerPolicyError, match='source_conflict'):
        admission.generate_team(profile.home, request(profile))
    assert len(sends) == 1
    assert rows(profile)[0]['status'] == 'failed'


def test_success_requires_physical_reservation(profile, monkeypatch):
    monkeypatch.setattr(generation, 'generate', lambda *args, **kwargs: team.presets()[0]['team'])
    with pytest.raises(ValueError, match='reserve'):
        admission.generate_team(profile.home, request(profile))
    assert rows(profile)[0]['status'] == 'failed'


def test_real_shared_ledger_limits_setup_calls_and_retains_receipts(installation, monkeypatch):
    root = installation
    configure(root, calls=1, tokens=1000)
    root.path, root.identity = root.store.path, root.metadata
    sends = fake_provider(monkeypatch)
    admission.generate_team(root.home, request(root))
    assert json.loads(rows(root)[0]['shared_receipt'])['payload']['kind'] == 'team_setup'
    with pytest.raises(ValueError):
        admission.generate_team(root.home, request(root, 'second'))
    assert len(sends) == 1


def test_shared_debit_survives_failed_local_commit_without_send(installation, monkeypatch):
    root = installation
    configure(root, calls=1, tokens=1000)
    root.path, root.identity = root.store.path, root.metadata
    sends = fake_provider(monkeypatch)
    with sqlite3.connect(root.path) as conn:
        conn.execute("CREATE TRIGGER reject_setup_reservation BEFORE UPDATE OF dispatched ON organization_team_generations BEGIN SELECT RAISE(ABORT,'local_failure'); END")
    with pytest.raises(OwnerPolicyError):
        admission.generate_team(root.home, request(root))
    assert not sends
    assert rows(root)[0]['dispatched'] == 0
    with sqlite3.connect(root.path) as conn:
        conn.execute('DROP TRIGGER reject_setup_reservation')
    with pytest.raises(ValueError):
        admission.generate_team(root.home, request(root, 'second'))
    assert not sends


@pytest.mark.parametrize('status', ['running', 'uncertain'])
@pytest.mark.parametrize('configured', [False, True])
def test_active_or_uncertain_generation_blocks_shared_budget_changes(installation, status, configured):
    root = installation
    if configured:
        configure(root)
    with sqlite3.connect(root.store.path) as conn:
        conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,0)',
            ('pending', root.organization_id, 'hash', 'revision', status))
    with pytest.raises(ValueError, match='scope_execution_active'):
        configure(root, revision=1 if configured else 0, key='change')


@pytest.mark.parametrize('limits', [
    {'max_model_calls': 1},
    {'max_total_tokens': 4096},
    {'max_cost_usd': '0.0003', 'model_costs': [
        {'provider': 'fixture', 'model': 'fixture', 'input_usd_per_million': '1', 'output_usd_per_million': '1'}]},
])
def test_configured_local_call_token_and_cost_limits_are_not_overridden(profile, monkeypatch, limits):
    cfg = json.loads((profile.home / 'config.yaml').read_text())
    cfg['organization'] = limits
    (profile.home / 'config.yaml').write_text(json.dumps(cfg))
    sends = []
    def generate(home, jobs, goals, naming, *, reserve_model_call, cancel=None):
        input_limit = 2100 if 'max_total_tokens' in limits else 100
        reserve_model_call(provider='fixture', model='fixture', input_limit=input_limit, output_limit=100)
        sends.append(True)
        return deepcopy(team.presets()[0]['team'])
    monkeypatch.setattr(generation, 'generate', generate)
    admission.generate_team(profile.home, request(profile))
    with pytest.raises(ValueError, match='allowance|cost limit'):
        admission.generate_team(profile.home, request(profile, 'second'))
    assert len(sends) == 1


def test_cost_limit_requires_exact_selected_provider_price(profile, monkeypatch):
    cfg = json.loads((profile.home / 'config.yaml').read_text())
    cfg['organization'] = {'max_cost_usd': '1', 'model_costs': [
        {'provider': 'fixture', 'model': 'fixture', 'input_usd_per_million': '1', 'output_usd_per_million': '1'}]}
    (profile.home / 'config.yaml').write_text(json.dumps(cfg))
    def generate(home, jobs, goals, naming, *, reserve_model_call, cancel=None):
        reserve_model_call(provider='fixture', selected_provider='custom-selected', model='fixture', input_limit=100, output_limit=100)
        raise AssertionError('Missing exact selected-route price must block before send')
    monkeypatch.setattr(generation, 'generate', generate)
    with pytest.raises(ValueError, match='exact-route prices'):
        admission.generate_team(profile.home, request(profile))
    assert rows(profile)[0]['dispatched'] == 0


def test_complete_rejected_json_is_charged_and_allows_bounded_new_attempt(profile, monkeypatch):
    def invalid():
        raise generation.TeamProposalRejected('Invalid JSON received')
    sends = fake_provider(monkeypatch, outcome=invalid)
    for key in ('first', 'second'):
        with pytest.raises(generation.TeamProposalRejected):
            admission.generate_team(profile.home, request(profile, key))
    assert len(sends) == 2
    assert all(row['status'] == 'failed' and row['reserved_tokens'] == 200 for row in rows(profile))


@pytest.mark.parametrize('status', ['running', 'uncertain'])
@pytest.mark.parametrize('action', ['budgetPolicy', 'modelRouting'])
def test_outstanding_generation_fences_local_owner_policy_mutation(profile, status, action):
    from eidolon_cli import organization_owner_policy as policy
    from tests.eidolon_cli.test_organization_owner_policy import policy_params, routing
    current = policy.owner_policy(profile.home, profile.identity['organizationId'])
    value = policy_params(current, {'max_model_calls': 2} if action == 'budgetPolicy' else routing())
    preview = policy.preview_policy(profile.home, action, value)
    value.update(previewVersion=preview['previewVersion'], idempotencyKey='change', confirmed=True)
    with sqlite3.connect(profile.path) as conn:
        conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,0)',
            ('pending', profile.identity['organizationId'], 'hash', 'revision', status))
    before = dump(profile)
    with pytest.raises(OwnerPolicyError, match='team_generation_in_progress_or_uncertain'):
        policy.apply_policy(profile.home, action, value)
    assert dump(profile) == before


def test_shared_enrollment_racing_real_generation_cannot_change_scope(installation, monkeypatch):
    root = installation
    root.path, root.identity = root.store.path, root.metadata
    configure(root, calls=3, tokens=1000)
    entered, release = threading.Event(), threading.Event()
    def wait():
        entered.set()
        assert release.wait(10)
        return deepcopy(team.presets()[0]['team'])
    sends = fake_provider(monkeypatch, outcome=wait)
    value = request(root)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(admission.generate_team, root.home, value)
        try:
            assert entered.wait(10)
            from tests.eidolon_cli.test_organization_shared_allowance import configuration
            changed = configuration(root, calls=5, tokens=2000)
            changed['allocations'] = [item for item in changed['allocations'] if item['profile'] != 'default']
            with pytest.raises(ValueError, match='scope_execution_active'):
                configure(root, config=changed, revision=1, key='remove-enrollment')
        finally:
            release.set()
        assert pending.result(timeout=10)['previewVersion']
    assert len(sends) == 1
    assert rows(root)[0]['shared_receipt'] is not None


def improvement_request(profile, key='improve', field='jobs'):
    return {**request(profile, key), 'field': field,
            'goals' if field == 'jobs' else 'jobs': ''}


def fake_improver(monkeypatch, *, outcome=None, reserve=True):
    sends = []
    def improve(home, field, jobs, goals, naming, *, reserve_model_call, cancel=None):
        if reserve:
            reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        sends.append((field, jobs, goals, naming))
        return outcome() if outcome else {'suggestion': 'Clarify the desired deliverables.', 'assumptions': []}
    monkeypatch.setattr(generation, 'improve', improve)
    return sends


def test_improvement_is_review_only_and_shares_generation_allowance_and_idempotency(profile, monkeypatch):
    generated = fake_provider(monkeypatch)
    improved = fake_improver(monkeypatch)
    before = params(profile)
    config_before = (profile.home / 'config.yaml').read_bytes()
    value = improvement_request(profile)
    result = admission.improve_team_input(profile.home, value)
    assert result == {'capabilityVersion': 1, 'organizationId': value['expectedOrganizationId'],
        'sourceRevision': value['sourceRevision'], 'field': 'jobs',
        'suggestion': 'Clarify the desired deliverables.', 'assumptions': [],
        'generationAllowance': {'status': 'available', 'remainingCalls': 2}}
    after = dump(profile)
    assert admission.improve_team_input(profile.home, value) == result
    assert dump(profile) == after
    assert (profile.home / 'config.yaml').read_bytes() == config_before
    assert params(profile) == before
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        admission.generate_team(profile.home, request(profile, 'improve'))
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        admission.improve_team_input(profile.home, {**value, 'field': 'goals', 'goals': 'Changed'})
    admission.generate_team(profile.home, request(profile, 'generate'))
    admission.improve_team_input(profile.home, improvement_request(profile, 'other', 'goals'))
    with pytest.raises(ValueError, match='allowance'):
        admission.generate_team(profile.home, request(profile, 'fourth'))
    assert len(generated) == 1 and len(improved) == 2
    assert sum(row['reserved_tokens'] for row in rows(profile)) == 600
    replay = admission.improve_team_input(profile.home, value)
    assert replay['generationAllowance'] == {'status': 'exhausted', 'remainingCalls': 0}
    assert {key: item for key, item in replay.items() if key != 'generationAllowance'} == {
        key: item for key, item in result.items() if key != 'generationAllowance'}
    assert json.loads(rows(profile)[0]['result_json']) == result
    with sqlite3.connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0


def test_legacy_completed_generation_receipt_remains_replayable(profile, monkeypatch):
    sends = fake_provider(monkeypatch)
    value = request(profile)
    result = admission.generate_team(profile.home, value)
    legacy = {key: item for key, item in result.items() if key != 'generationAllowance'}
    with sqlite3.connect(profile.path) as conn:
        conn.execute('UPDATE organization_team_generations SET input_hash=?,result_json=?',
                     (admission._hash(value), json.dumps(legacy)))
    assert admission.generate_team(profile.home, value) == result
    assert len(sends) == 1


@pytest.mark.parametrize('outcome', ['uncertain', 'malformed', 'stale', 'unreserved'])
def test_improvement_failures_retain_debit_and_never_replay(profile, monkeypatch, outcome):
    def fail():
        if outcome == 'uncertain':
            raise InterruptedError('Disconnected after send')
        if outcome == 'malformed':
            raise generation.TeamInputRejected('Malformed complete response')
        if outcome == 'stale':
            (profile.home / 'config.yaml').write_text('{"model":{"default":"changed"}}')
        return {'suggestion': 'Suggested wording', 'assumptions': []}
    sends = fake_improver(monkeypatch, outcome=fail, reserve=outcome != 'unreserved')
    value = improvement_request(profile)
    with pytest.raises((InterruptedError, generation.TeamInputRejected, OwnerPolicyError, ValueError)):
        admission.improve_team_input(profile.home, value)
    row = rows(profile)[0]
    assert row['status'] == ('uncertain' if outcome == 'uncertain' else 'failed')
    assert row['reserved_tokens'] == (0 if outcome == 'unreserved' else 200)
    with pytest.raises(OwnerPolicyError, match='uncertain|failed'):
        admission.improve_team_input(profile.home, value)
    if outcome == 'uncertain':
        with pytest.raises(OwnerPolicyError, match='uncertain'):
            admission.generate_team(profile.home, request(profile, 'other'))
    assert len(sends) == 1


def test_completed_improvement_cannot_replay_after_team_creation(profile, monkeypatch):
    sends = fake_improver(monkeypatch)
    value = improvement_request(profile)
    admission.improve_team_input(profile.home, value)
    team.create_team(profile.home, confirmed(profile))
    with pytest.raises(OwnerPolicyError, match='source_conflict'):
        admission.improve_team_input(profile.home, value)
    assert len(sends) == 1


def test_improvement_and_generation_share_real_budget_ledger(installation, monkeypatch):
    root = installation
    configure(root, calls=1, tokens=1000)
    root.path, root.identity = root.store.path, root.metadata
    improved = fake_improver(monkeypatch)
    generated = fake_provider(monkeypatch)
    admission.improve_team_input(root.home, improvement_request(root))
    assert json.loads(rows(root)[0]['shared_receipt'])['payload']['kind'] == 'team_setup'
    with pytest.raises(ValueError):
        admission.generate_team(root.home, request(root, 'second'))
    assert len(improved) == 1 and not generated


def activate_split_budget(profile, *, improve=4, generate=3, total=7):
    from eidolon_cli import organization_team_budget as budget
    state = budget.budget_policy(profile.home, profile.identity['organizationId'])
    value = {'expectedOrganizationId': state['organizationId'],
             'expectedGeneration': state['policyGeneration'], 'sourceRevision': state['sourceRevision'],
             'configuration': {'version': 2, 'mode': 'bounded_api', 'improveCallLimit': improve,
                               'generationCallLimit': generate, 'totalCallLimit': total}}
    preview = budget.preview_budget(profile.home, value)
    return budget.apply_budget(profile.home, {**value, 'previewVersion': preview['previewVersion'],
        'idempotencyKey': 'split-budget', 'confirmed': True})


def test_split_setup_buckets_share_aggregate_and_replay_never_sends(profile, monkeypatch):
    improved, generated = fake_improver(monkeypatch), fake_provider(monkeypatch)
    activate_split_budget(profile)
    for index in range(4):
        value = improvement_request(profile, f'improve-{index}')
        admission.improve_team_input(profile.home, value)
    with pytest.raises(OwnerPolicyError, match='setup_budget_exhausted'):
        admission.improve_team_input(profile.home, improvement_request(profile, 'improve-excess'))
    for index in range(3):
        value = request(profile, f'generate-{index}')
        result = admission.generate_team(profile.home, value)
        assert admission.generate_team(profile.home, value) == result
    assert len(improved) == 4 and len(generated) == 3
    allowance = team.team_setup(profile.home, profile.identity['organizationId'])['generationAllowance']
    assert allowance == {'policyVersion': 2, 'status': 'exhausted', 'remainingCalls': 0,
                         'remainingByOperation': {'improveTeamInput': 0, 'generateTeam': 0}}
    with pytest.raises(OwnerPolicyError, match='setup_budget_exhausted'):
        admission.generate_team(profile.home, request(profile, 'generate-excess'))
    assert len(generated) == 3


def test_split_activation_retains_legacy_debt_once_and_stales_old_inputs(profile, monkeypatch):
    generated, improved = fake_provider(monkeypatch), fake_improver(monkeypatch)
    for index in range(3):
        admission.generate_team(profile.home, request(profile, f'legacy-{index}'))
    stale = improvement_request(profile, 'stale')
    activate_split_budget(profile)
    with pytest.raises(OwnerPolicyError, match='source_conflict'):
        admission.improve_team_input(profile.home, stale)
    for index in range(4):
        admission.improve_team_input(profile.home, improvement_request(profile, f'new-{index}'))
    from eidolon_cli.organization_team_budget import operation_counts
    with sqlite3.connect(profile.path) as conn:
        conn.row_factory = sqlite3.Row
        assert operation_counts(conn) == {'legacy': 3, 'improveTeamInput': 4, 'generateTeam': 0, 'total': 7}
    with pytest.raises(OwnerPolicyError, match='setup_budget_exhausted'):
        admission.generate_team(profile.home, request(profile, 'new-excess'))
    assert len(generated) == 3 and len(improved) == 4


def test_active_split_budget_cannot_bypass_real_shared_allowance(installation, monkeypatch):
    root = installation
    configure(root, calls=1, tokens=1000)
    root.path, root.identity = root.store.path, root.metadata
    activate_split_budget(root)
    sends = fake_provider(monkeypatch)
    admission.generate_team(root.home, request(root, 'first-shared'))
    assert json.loads(rows(root)[0]['shared_receipt'])['payload']['kind'] == 'team_setup'
    with pytest.raises(ValueError):
        admission.generate_team(root.home, request(root, 'second-shared'))
    assert len(sends) == 1
    assert sum(row['dispatched'] for row in rows(root)) == 1
    assert team.team_setup(root.home, root.identity['organizationId'])['generationAllowance']['remainingCalls'] == 6


def test_active_split_unknown_shared_send_keeps_charge_and_blocks_policy_change(installation, monkeypatch):
    root = installation
    configure(root, calls=3, tokens=1000)
    root.path, root.identity = root.store.path, root.metadata
    activate_split_budget(root)
    def unknown():
        raise TimeoutError('synthetic response lost after dispatch')
    sends = fake_provider(monkeypatch, outcome=unknown)
    with pytest.raises(TimeoutError):
        admission.generate_team(root.home, request(root, 'unknown-shared'))
    row = rows(root)[0]
    assert row['status'] == 'uncertain' and row['dispatched'] == 1 and row['reserved_tokens'] == 200
    assert json.loads(row['shared_receipt'])['payload']['kind'] == 'team_setup'
    from eidolon_cli import organization_team_budget as budget
    current = budget.budget_policy(root.home, root.identity['organizationId'])
    assert current['setupBudget']['status'] == 'uncertain'
    assert current['setupBudget']['remaining']['totalCalls'] == 6
    with pytest.raises(OwnerPolicyError, match='in_progress_or_uncertain'):
        budget.preview_budget(root.home, {'expectedOrganizationId': current['organizationId'],
            'sourceRevision': current['sourceRevision'], 'expectedGeneration': current['policyGeneration'],
            'configuration': {**current['configuration'], 'totalCallLimit': 8}})
    with pytest.raises(OwnerPolicyError, match='in_progress_or_uncertain'):
        admission.generate_team(root.home, request(root, 'no-new-send'))
    assert len(sends) == 1

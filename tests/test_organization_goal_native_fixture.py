from scripts.ci.organization_goal_native_fixture import seed, inspect


def test_goal_native_seed_uses_paused_ordinary_objective(tmp_path):
    (tmp_path / 'config.yaml').write_text('{}')
    result = seed(tmp_path)
    state = inspect(tmp_path)
    assert state['objective_dispatch'] == [{'objective_id': result['objectiveId'], 'paused': 1, 'revision': 1}]
    assert state['objectiveCount'] == 1
    assert state['attempts'] == state['modelCalls'] == 0
    assert state['organization_continuation_launches'] == []
    assert state['organization_goal_revisions'] == []


def test_goal_native_seed_allows_real_reviewed_controls(tmp_path):
    import time
    from eidolon_cli.organization_store import OrganizationStore
    from tests.eidolon_cli.test_organization_goal_continuation import template
    from tests.eidolon_cli.test_organization_goal_allowances import limits
    (tmp_path / 'config.yaml').write_text('{}')
    result = seed(tmp_path)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    store.reload_profile_configuration(tmp_path)
    store.configure_goal_allowance('native-goal', objective_ids=[result['objectiveId']], limits=limits(),
        paused=False, expected_revision=0, idempotency_key='native-allowance', confirmed=True)
    templates = [template(), template(id='two', templateDependencyIds=['one'])]
    deadline = int(time.time()) + 1800
    preview = store.preview_goal_continuation('native-goal', templates=templates, deadline_timestamp=deadline)
    store.approve_goal_continuation('native-goal', templates=templates, deadline_timestamp=deadline,
        preview_sha256=preview['sha256'], idempotency_key='native-approve', confirmed=True)
    store.set_goal_continuation_enabled('native-goal', enabled=True, expected_revision=1,
        idempotency_key='native-enable', confirmed=True)
    store.set_goal_continuation_enabled('native-goal', enabled=False, expected_revision=2,
        idempotency_key='native-disable', confirmed=True)
    state = inspect(tmp_path)
    assert [row['enabled'] for row in state['organization_continuation_controls']] == [0, 1, 0]
    assert state['attempts'] == state['modelCalls'] == 0
    assert state['organization_continuation_launches'] == []

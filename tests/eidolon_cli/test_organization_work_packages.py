"""Durable Executive delegation, scoped Manager plans and shared objective ceilings."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore, _SCHEMA


CRITERIA = ['Support the first finding', 'Support the second finding', 'Combine both findings']


def roster():
    return [
        {'id': 'chief', 'name': 'Chief', 'role': 'Executive', 'team': 'general',
         'capabilities': ['request.decompose', 'request.accept']},
        {'id': 'coordinator', 'name': 'Coordinator', 'role': 'Manager', 'team': 'general',
         'manager_id': 'chief', 'capabilities': ['request.plan', 'request.integrate']},
        *[{'id': f'lead-{name}', 'name': f'Lead {name}', 'role': 'Manager', 'team': 'general',
           'manager_id': 'chief', 'capabilities': ['request.plan', 'request.integrate']} for name in 'abc'],
        *[{'id': f'writer-{name}', 'name': f'Writer {name}', 'team': 'general',
           'manager_id': f'lead-{name}', 'capabilities': ['work.draft']} for name in 'abc'],
    ]


def package_output():
    return {'workPackages': [
        {'title': f'Finding {name}', 'description': f'Prepare finding {name} against its criterion',
         'managerId': f'lead-{name}', 'criterionIndexes': [index], 'projectIds': [],
         'dependsOn': [0, 1] if name == 'c' else [], 'maxTasks': 1}
        for index, name in enumerate('abc')
    ]}


def plan_output(claim):
    name = claim['agent_id'].rsplit('-', 1)[-1]
    return {'tasks': [{'title': f'Draft {name}', 'description': f'Write the exact finding {name}',
                       'type': 'work.draft', 'team': 'general', 'agentId': f'writer-{name}',
                       'managerId': claim['agent_id'], 'dependsOn': []}]}


def make_store(tmp_path, **limits):
    settings = OrganizationSettings.from_config({'organization': {
        'roster': roster(), 'max_inflight': 4, 'max_workers': 3, 'max_tasks': 3, **limits}})
    store = OrganizationStore(tmp_path / 'organization.db', settings)
    objective = store.create_objective('Prepare a supported combined finding', idempotency_key='goal',
        acceptance_criteria=CRITERIA, manager_id='coordinator', executive_id='chief')
    return store, objective


def decompose(store):
    claim = store.claim_next()
    assert claim['type'] == 'request.decompose' and claim['agent_id'] == 'chief'
    assert store.finish(claim, package_output())
    return claim


def plan_all(store):
    with ThreadPoolExecutor(max_workers=3) as pool:
        plans = list(pool.map(lambda _: OrganizationStore(store.path).claim_next(), range(3)))
    assert {row['agent_id'] for row in plans} == {'lead-a', 'lead-b', 'lead-c'}
    for claim in plans:
        context = store.context(claim)
        assert context['workPackage']['managerId'] == claim['agent_id']
        assert context['workPackage']['planRequestId'] == claim['id']
        assert len(context['workPackages']) == 3
        assert context['objective']['acceptanceCriteria'] == CRITERIA
        assert store.finish(claim, plan_output(claim))
    return plans


def next_nonstaffing(store):
    for _ in range(10):
        claim = store.claim_next()
        if claim is None or claim['type'] != 'request.hire':
            return claim
        assert store.finish(claim, {})
    pytest.fail('Staffing did not settle')


def finish_review(store, claim, *, approved=True):
    context = store.context(claim)
    ids = [item['id'] for item in context['evidence']]
    result = {'approved': approved, 'summary': 'Compared the exact persisted findings', 'evidenceIds': ids}
    if claim['type'] == 'request.accept':
        result.update(conflicts=[] if approved else ['Findings need reconciliation'], criteriaResults=[
            {'criterion': criterion, 'satisfied': approved, 'evidenceIds': ids,
             'reason': 'The exact findings support the result' if approved else 'The combined result is inconsistent'}
            for criterion in context['objective']['acceptanceCriteria']])
    assert store.finish(claim, result)


def ledger_dump(store):
    with store._connect() as conn:
        return '\n'.join(conn.iterdump())


def test_independent_plans_overlap_and_dependencies_wait_for_every_exact_review(tmp_path):
    store, objective = make_store(tmp_path)
    first = decompose(store)
    assert store.finish(first, package_output()) is False
    plans = plan_all(store)
    assert all(store.finish(claim, plan_output(claim)) is False for claim in plans)
    work = [next_nonstaffing(store), next_nonstaffing(store)]
    assert {claim['agent_id'] for claim in work} == {'writer-a', 'writer-b'}
    assert next_nonstaffing(store) is None
    contents = {}
    for claim in work:
        content = f"Exact source from {claim['agent_id']}: café, 中文, and a final independent finding."
        contents[claim['task_id']] = content
        assert store.finish(claim, {'summary': 'Source produced', 'deliverable': content})
    first_review = store.claim_next()
    assert first_review['type'] == 'request.review'
    assert store.claim_next() is None
    finish_review(store, first_review)
    second_review = store.claim_next()
    assert second_review['type'] == 'request.review'
    assert store.claim_next() is None
    assert not any(row['type'] == 'request.integrate' for row in store.snapshot()['requests'])
    finish_review(store, second_review)
    store = OrganizationStore(store.path)
    dependent = next_nonstaffing(store)
    assert dependent['agent_id'] == 'writer-c'
    context = store.context(dependent)
    assert context['workPackage']['id'] == context['task']['workPackageId']
    assert {row['workPackageId'] for row in context['dependencies']} == set(context['workPackage']['dependencyIds'])
    assert {row['taskId']: row['deliverable'] for row in context['dependencies']} == contents
    assert all(hashlib.sha256(row['deliverable'].encode()).hexdigest() == row['sha256'] for row in context['dependencies'])
    assert store.finish(dependent, {'summary': 'Combined', 'deliverable': 'Both independently reviewed findings are reconciled.'})
    assert not any(row['type'] == 'request.integrate' for row in store.snapshot()['requests'])
    finish_review(store, store.claim_next())
    integrate = store.claim_next()
    assert integrate['type'] == 'request.integrate' and integrate['agent_id'] == 'coordinator'
    assert all(package['status'] == 'completed' for package in store.context(integrate)['workPackages'])
    assert store.finish(integrate, {'summary': 'Integrated', 'deliverable': 'One coherent, supported combined finding.'})
    accept = store.claim_next()
    assert accept['type'] == 'request.accept' and accept['agent_id'] == 'chief'
    finish_review(store, accept)
    reopened = OrganizationStore(store.path)
    result = reopened.snapshot()['objectives'][0]
    assert result['id'] == objective['id'] and result['status'] == 'completed'
    assert result['planningMode'] == 'executive_packages'
    assert len(result['workPackages']) == 3 and all(row['status'] == 'completed' for row in result['workPackages'])
    assert reopened.claim_next() is None


@pytest.mark.parametrize('invalid', ['unknown-manager', 'wrong-executive', 'missing-criterion', 'unknown-project',
                                    'forward-dependency', 'duplicate-dependency', 'too-many-packages', 'allocation-overflow'])
def test_invalid_decomposition_rolls_back_the_whole_package_set_and_can_retry(tmp_path, invalid):
    store, _ = make_store(tmp_path)
    claim = store.claim_next()
    output = package_output()
    packages = output['workPackages']
    if invalid == 'unknown-manager':
        packages[1]['managerId'] = 'absent'
    elif invalid == 'wrong-executive':
        packages[1]['managerId'] = 'manager'
    elif invalid == 'missing-criterion':
        packages[2]['criterionIndexes'] = [0]
    elif invalid == 'unknown-project':
        packages[1]['projectIds'] = ['not-owner-selected']
    elif invalid == 'forward-dependency':
        packages[1]['dependsOn'] = [2]
    elif invalid == 'duplicate-dependency':
        packages[2]['dependsOn'] = [0, 0]
    elif invalid == 'too-many-packages':
        output['workPackages'] = [dict(packages[0], title=f'Package {index}') for index in range(9)]
    else:
        packages[2]['maxTasks'] = 2
    before = ledger_dump(store)
    with pytest.raises(ValueError):
        store.finish(claim, output)
    assert ledger_dump(store) == before
    assert store.heartbeat(claim)
    assert store.snapshot()['objectives'][0]['workPackages'] == []
    assert store.finish(claim, package_output())
    assert len(store.snapshot()['objectives'][0]['workPackages']) == 3


@pytest.mark.parametrize('invalid', ['allocation', 'package-id', 'criteria', 'round', 'project', 'late-bad-task'])
def test_partial_manager_plan_validation_is_atomic_and_cannot_duplicate_completed_plan(tmp_path, invalid):
    store, _ = make_store(tmp_path, max_tasks=4)
    executive = store.claim_next()
    packages = package_output()
    if invalid == 'late-bad-task':
        packages['workPackages'][1]['maxTasks'] = 2
    assert store.finish(executive, packages)
    first, second = store.claim_next(), store.claim_next()
    assert store.finish(first, plan_output(first))
    output = plan_output(second)
    if invalid == 'allocation':
        output['tasks'].append({**output['tasks'][0], 'title': 'Unauthorized extra task'})
    elif invalid == 'package-id':
        output['workPackageId'] = store.context(second)['workPackages'][0]['id']
    elif invalid == 'criteria':
        output['criterionIndexes'] = [2]
    elif invalid == 'round':
        output['round'] = 1
    elif invalid == 'project':
        output['tasks'][0]['projectId'] = 'not-in-package'
    else:
        output['tasks'].append({**output['tasks'][0], 'title': 'Invalid second task', 'agentId': 'missing-worker'})
    before = ledger_dump(store)
    with pytest.raises(ValueError):
        store.finish(second, output)
    assert ledger_dump(store) == before
    assert store.heartbeat(second)
    assert store.finish(second, plan_output(second))
    after = ledger_dump(store)
    assert not store.finish(first, plan_output(first))
    assert not store.finish(second, plan_output(second))
    assert ledger_dump(store) == after
    assert len(store.snapshot()['tasks']) == 2
    assert not any(row['type'] == 'request.integrate' for row in store.snapshot()['requests'])


@pytest.mark.parametrize('stage', ['request.decompose', 'request.plan'])
def test_restart_expiry_retry_and_cancel_fence_package_claims(tmp_path, stage):
    store, objective = make_store(tmp_path)
    if stage == 'request.plan':
        decompose(store)
    stale = store.claim_next()
    assert stale['type'] == stage
    package_id = store.context(stale)['workPackage']['id'] if stage == 'request.plan' else None
    output = plan_output(stale) if package_id else package_output()
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, stale['id']))
    reopened = OrganizationStore(store.path)
    assert reopened.recover_expired() == 1
    assert not reopened.finish(stale, output)
    assert reopened.retry(stale['id'], idempotency_key='retry')
    assert not reopened.retry(stale['id'], idempotency_key='retry')
    retried = reopened.claim_next()
    assert retried['id'] == stale['id'] and retried['token'] != stale['token']
    if package_id:
        assert reopened.context(retried)['workPackage']['id'] == package_id
    assert reopened.cancel(objective['id'])
    assert not reopened.finish(retried, output)
    assert not reopened.heartbeat(retried)
    assert OrganizationStore(store.path).claim_next() is None
    assert store.snapshot()['tasks'] == []


@pytest.mark.parametrize('limit', ['model-calls', 'tokens', 'deadline', 'stages'])
def test_parallel_packages_share_one_persisted_admission_budget(tmp_path, limit):
    limits = {'max_model_calls': 2} if limit == 'model-calls' else {}
    if limit == 'tokens':
        limits.update(max_total_tokens=4096)
    if limit == 'stages':
        limits.update(max_stages=4)
    store, objective = make_store(tmp_path, **limits)
    decompose(store)
    plans = [store.claim_next(), store.claim_next(), store.claim_next()]
    if limit == 'stages':
        for claim in plans:
            assert store.finish(claim, plan_output(claim))
        assert next_nonstaffing(store) is None
        usage = OrganizationStore(store.path).snapshot()['objectives'][0]['usage']
        assert usage['stages'] == usage['stageLimit'] == 4
        return
    if limit == 'deadline':
        with store._write() as conn:
            conn.execute('UPDATE objective_budgets SET deadline=? WHERE objective_id=?', (time.time() - 1, objective['id']))
        for claim in plans:
            assert not store.finish(claim, plan_output(claim))
        assert store.snapshot()['tasks'] == []
        assert OrganizationStore(store.path).claim_next() is None
        return
    def reserve(claim):
        return OrganizationStore(store.path).reserve_model_call(claim, provider='local', model='package-fixture',
                                                                input_limit=1536, output_limit=512)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, plans[:2]))
    assert len(set(reservations)) == 2
    reopened = OrganizationStore(store.path)
    with pytest.raises(ValueError, match='budget'):
        reserve(plans[2])
    usage = reopened.snapshot()['objectives'][0]['usage']
    assert usage['modelCalls'] == 2 and usage['reservedTokens'] == 4096
    assert sum(len(reopened.execution_audit(claim['id'])['modelCalls']) for claim in plans) == 2


def test_preupgrade_objectives_keep_legacy_planning_while_new_objectives_decompose(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO objectives VALUES ('old','old-key','digest','Old brief','Old scope',3,?,0)", (time.time(),))
        conn.execute("INSERT INTO requests(id,objective_id,type,team,priority,status,created) "
                     "VALUES ('old-plan','old','request.plan','general',3,'queued',?)", (time.time(),))
    store = OrganizationStore(path)
    old = store.claim_next()
    assert old['id'] == 'old-plan' and old['type'] == 'request.plan'
    assert store.context(old)['planningMode'] == 'legacy'
    store.fail(old, 'Clarification needed')
    assert store.resolve(old['id'], 'request_replan', 'Retain the old workflow', idempotency_key='legacy-replan')
    old_again = OrganizationStore(path).claim_next()
    assert old_again['type'] == 'request.plan' and old_again['objective_id'] == 'old'
    assert store.cancel('old')
    new = store.create_objective('New brief', idempotency_key='new')
    fresh = store.claim_next()
    assert fresh['objective_id'] == new['id'] and fresh['type'] == 'request.decompose'
    assert store.context(fresh)['planningMode'] == 'executive_packages'


def test_selected_projects_are_covered_and_each_manager_is_confined_to_its_package(tmp_path):
    roots = [tmp_path / name for name in ('alpha', 'beta')]
    for root in roots:
        root.mkdir()
        (root / 'facts.txt').write_text('Scoped source facts', encoding='utf-8')
    settings = OrganizationSettings.from_config({'organization': {
        'roster': roster(), 'max_inflight': 4, 'max_tasks': 3, 'read_roots': [str(root) for root in roots],
        'project_grants': [{'id': f'check-{index}', 'files': [f'root{index}/facts.txt'],
                            'execution': {'root': f'root{index}'}} for index in range(2)],
        'projects': [{'id': name, 'root': f'root{index}', 'recipe': f'check-{index}', 'team': 'general'}
                     for index, name in enumerate(('alpha', 'beta'))]}})
    store = OrganizationStore(tmp_path / 'projects.db', settings)
    store.create_objective('Cover both selected projects', idempotency_key='projects',
        acceptance_criteria=CRITERIA, project_ids=['alpha', 'beta'], manager_id='coordinator', executive_id='chief')
    claim = store.claim_next()
    output = package_output()
    before = ledger_dump(store)
    with pytest.raises(ValueError, match='every.*project'):
        store.finish(claim, output)
    assert ledger_dump(store) == before
    output['workPackages'][0]['projectIds'] = ['alpha']
    output['workPackages'][1]['projectIds'] = ['beta']
    assert store.finish(claim, output)
    plan = store.claim_next()
    assert plan['agent_id'] == 'lead-a'
    assert store.context(plan)['workPackage']['projectIds'] == ['alpha']
    proposed = plan_output(plan)
    proposed['tasks'][0]['projectId'] = 'beta'
    before = ledger_dump(store)
    with pytest.raises(ValueError, match='inside its work package'):
        store.finish(plan, proposed)
    assert ledger_dump(store) == before
    proposed['tasks'][0]['projectId'] = 'alpha'
    assert store.finish(plan, proposed)
    task = store.snapshot()['tasks'][0]
    assert task['projectId'] == 'alpha'
    assert task['workPackageId'] == store.snapshot()['objectives'][0]['workPackages'][0]['id']


def test_replan_retains_package_rounds_and_exact_old_evidence_without_reusing_ownership(tmp_path):
    store, objective = make_store(tmp_path, max_replans=1)
    decompose(store)
    plan_all(store)
    old_evidence = {}
    while True:
        claim = next_nonstaffing(store)
        assert claim is not None
        if claim['type'] == 'work.draft':
            assert store.finish(claim, {'summary': 'Original finding', 'deliverable': 'Exact original ' + claim['agent_id']})
        elif claim['type'] == 'request.review':
            evidence, = store.context(claim)['evidence']
            old_evidence[evidence['id']] = evidence['content']
            finish_review(store, claim)
        elif claim['type'] == 'request.integrate':
            assert store.finish(claim, {'summary': 'First combination', 'deliverable': 'Original combined candidate'})
        else:
            assert claim['type'] == 'request.accept'
            old_packages = store.context(claim)['workPackages']
            finish_review(store, claim, approved=False)
            break
    store = OrganizationStore(store.path)
    replan = store.claim_next()
    assert replan['type'] == 'request.decompose' and replan['agent_id'] == 'chief'
    context = store.context(replan)
    assert context['planningMode'] == 'executive_packages'
    assert context['objective']['round'] == 1 and context['maxTasks'] == 3
    assert all(not row['currentRound'] for row in context['workPackages'])
    assert {key: store.evidence(key)['content'] for key in old_evidence} == old_evidence
    assert store.finish(replan, package_output())
    current = [row for row in store.snapshot()['objectives'][0]['workPackages'] if row['currentRound']]
    assert len(current) == 3 and all(row['round'] == 1 for row in current)
    assert not {row['id'] for row in old_packages} & {row['id'] for row in current}
    assert all(row['historical'] for row in store.snapshot()['tasks'])
    new_plan = store.claim_next()
    assert store.context(new_plan)['workPackage']['round'] == 1
    assert store.context(new_plan)['dependencies'] == []
    assert store.cancel(objective['id'])
    assert not store.finish(new_plan, plan_output(new_plan))
    assert {key: store.evidence(key)['content'] for key in old_evidence} == old_evidence


def test_original_and_lowered_task_caps_constrain_all_manager_plans(tmp_path):
    store, _ = make_store(tmp_path)
    store.reload_configuration(replace(store.settings, max_tasks=12))
    claim = store.claim_next()
    output = package_output()
    output['workPackages'][0]['maxTasks'] = 2
    before = ledger_dump(store)
    with pytest.raises(ValueError, match='task'):
        store.finish(claim, output)
    assert ledger_dump(store) == before
    assert store.finish(claim, package_output())
    for _ in range(2):
        plan = store.claim_next()
        assert plan['type'] == 'request.plan'
        assert store.finish(plan, plan_output(plan))
    store.reload_configuration(replace(store.settings, max_tasks=2))
    plan = store.claim_next()
    assert plan['type'] == 'request.plan'
    before = ledger_dump(store)
    with pytest.raises(ValueError, match='Aggregate objective task capacity'):
        store.finish(plan, plan_output(plan))
    assert ledger_dump(store) == before
    assert len(OrganizationStore(store.path).snapshot()['tasks']) == 2


def test_reviewed_partial_work_cannot_integrate_while_other_manager_plans_are_unfinished(tmp_path):
    store, _ = make_store(tmp_path)
    decompose(store)
    plans = [store.claim_next(), store.claim_next(), store.claim_next()]
    assert store.finish(plans[0], plan_output(plans[0]))
    work = next_nonstaffing(store)
    assert work['type'] == 'work.draft'
    assert store.finish(work, {'summary': 'First part', 'deliverable': 'One independently reviewed part only'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    finish_review(store, review)
    snapshot = OrganizationStore(store.path).snapshot()
    assert all(row['status'] == 'completed' for row in snapshot['tasks'])
    assert sum(row['status'] == 'completed' for row in snapshot['objectives'][0]['workPackages']) == 1
    assert not any(row['type'] in {'request.integrate', 'request.accept'} for row in snapshot['requests'])
    assert store.heartbeat(plans[1]) and store.heartbeat(plans[2])


def test_package_manager_can_route_worker_to_another_authorized_task_manager(tmp_path):
    store, _ = make_store(tmp_path)
    executive = store.claim_next()
    package = package_output()['workPackages'][0]
    package['criterionIndexes'] = [0, 1, 2]
    assert store.finish(executive, {'workPackages': [package]})
    plan = store.claim_next()
    assert plan['agent_id'] == 'lead-a'
    package_id = store.context(plan)['workPackage']['id']
    output = plan_output(plan)
    output['tasks'][0].update(agentId='writer-b', managerId='lead-b')
    assert store.finish(plan, output)
    work = next_nonstaffing(store)
    context = store.context(work)
    assert work['agent_id'] == 'writer-b'
    assert context['task']['managingAgentId'] == 'lead-b'
    assert context['task']['workPackageId'] == package_id
    assert context['workPackage']['managerId'] == 'lead-a'


def test_peer_lowered_task_budget_fences_inflight_package_plan(tmp_path):
    store, _ = make_store(tmp_path)
    decompose(store)
    for _ in range(2):
        plan = store.claim_next()
        assert store.finish(plan, plan_output(plan))
    inflight = store.claim_next()
    assert inflight['type'] == 'request.plan'
    peer = OrganizationStore(store.path)
    peer.reload_configuration(replace(peer.settings, max_tasks=2))
    assert not store.finish(inflight, plan_output(inflight))
    assert len(OrganizationStore(store.path).snapshot()['tasks']) == 2


@pytest.mark.parametrize('decrease', [False, True])
def test_prepackage_policy_hash_migrates_without_losing_legacy_routes_or_real_budget_fences(tmp_path, decrease):
    import json
    from eidolon_cli.organization_policy import _AUTHORITY_FIELDS

    legacy_roster = roster()
    legacy_roster[0]['capabilities'] = ['request.accept']
    settings = OrganizationSettings.from_config({'organization': {'roster': legacy_roster, 'max_tasks': 3}})
    store = OrganizationStore(tmp_path / 'legacy-policy.db', settings)
    store.create_objective('Existing legacy brief', idempotency_key='legacy-policy',
                           manager_id='coordinator', executive_id='chief')
    claim = store.claim_next()
    assert claim['type'] == 'request.plan'
    generation = store._policy_generation
    with store._write() as conn:
        values = json.loads(conn.execute('SELECT settings FROM organization_policy').fetchone()[0])
        previous = {key: values[key] for key in _AUTHORITY_FIELDS if key != 'max_tasks'}
        old_hash = hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        conn.execute('UPDATE organization_policy SET fingerprint=?', (old_hash,))
        # Simulate an objective admitted before planning-mode persistence existed.
        conn.execute('DELETE FROM objective_planning')
    reopened = OrganizationStore(store.path, replace(settings, max_tasks=2) if decrease else None)
    assert reopened.snapshot()['objectives'][0]['planningMode'] == 'legacy'
    assert reopened._policy_generation == generation + int(decrease)
    with reopened._connect() as conn:
        assert conn.execute('SELECT fingerprint FROM organization_policy').fetchone()[0] != old_hash
    if decrease:
        assert not reopened.finish(claim, {'tasks': [{'title': 'Late legacy task', 'description': 'Stale ceiling', 'type': 'work.draft'}]})
        assert reopened.snapshot()['tasks'] == []
    else:
        assert reopened.heartbeat(claim)
        assert reopened.context(claim)['planningMode'] == 'legacy'
        assert reopened.context(claim)['maxTasks'] == 3
    second = OrganizationStore(store.path)
    assert second._policy_generation == reopened._policy_generation
    assert second.settings.max_tasks == (2 if decrease else 3)


def test_scope_amendment_keeps_old_package_criteria_and_existing_authority_budgets(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'facts.txt').write_text('Existing owner-selected source', encoding='utf-8')
    settings = OrganizationSettings.from_config({'organization': {
        'roster': roster(), 'max_tasks': 3, 'tool_grants': ['read_file'], 'read_roots': [str(root)],
        'project_grants': [{'id': 'source-check', 'files': ['root0/facts.txt'], 'execution': {'root': 'root0'}}],
        'projects': [{'id': 'source', 'root': 'root0', 'recipe': 'source-check', 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'amendment.db', settings)
    objective = store.create_objective('Support the original findings', idempotency_key='amended-packages',
        acceptance_criteria=CRITERIA, project_ids=['source'], manager_id='coordinator', executive_id='chief')
    executive = store.claim_next()
    store.reserve_model_call(executive, provider='local', model='fixture', input_limit=64, output_limit=16)
    output = package_output()
    output['workPackages'][0]['projectIds'] = ['source']
    assert store.finish(executive, output)
    before = store.snapshot()['objectives'][0]
    old_packages = before['workPackages']
    assert [row['criteria'] for row in old_packages] == [[criterion] for criterion in CRITERIA]
    with store._connect() as conn:
        budget = tuple(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (objective['id'],)).fetchone())
        planning = tuple(conn.execute('SELECT * FROM objective_planning WHERE objective_id=?', (objective['id'],)).fetchone())
    manager = store.claim_next()
    assert manager['type'] == 'request.plan'
    store.fail(manager, 'Owner scope decision required')
    revised = ['One revised, explicitly approved finding']
    assert store.resolve(manager['id'], 'amend_scope', 'Use only the revised finding.',
                         acceptance_criteria=revised, idempotency_key='approve-revised-root')
    store = OrganizationStore(store.path)
    new_decomposition = store.claim_next()
    context = store.context(new_decomposition)
    assert new_decomposition['type'] == 'request.decompose'
    assert context['objective']['acceptanceCriteria'] == revised
    assert context['objective']['round'] == 1
    historical = context['workPackages']
    assert [row['criteria'] for row in historical] == [[criterion] for criterion in CRITERIA]
    assert [row['criterionIndexes'] for row in historical] == [[0], [1], [2]]
    assert all(not row['currentRound'] and row['round'] == 0 for row in historical)
    replacement = package_output()['workPackages'][0]
    replacement.update(title='Revised finding', criterionIndexes=[0], projectIds=['source'], maxTasks=3)
    assert store.finish(new_decomposition, {'workPackages': [replacement]})
    final = OrganizationStore(store.path).snapshot()['objectives'][0]
    current, = [row for row in final['workPackages'] if row['currentRound']]
    assert current['criteria'] == revised and current['criterionIndexes'] == [0] and current['round'] == 1
    assert {row['id']: row['criteria'] for row in final['workPackages'] if not row['currentRound']} == {
        row['id']: row['criteria'] for row in old_packages}
    assert final['projects'] == before['projects'] and store.settings == settings
    assert final['usage']['modelCalls'] == before['usage']['modelCalls'] == 1
    assert final['usage']['reservedTokens'] == before['usage']['reservedTokens'] == 80
    with store._connect() as conn:
        assert tuple(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (objective['id'],)).fetchone()) == budget
        assert tuple(conn.execute('SELECT * FROM objective_planning WHERE objective_id=?', (objective['id'],)).fetchone()) == planning


@pytest.mark.parametrize('field,value', [('version', 2), ('mode', 'unknown')])
def test_unknown_persisted_planning_contract_fails_closed(tmp_path, field, value):
    store, objective = make_store(tmp_path)
    with store._write() as conn:
        conn.execute(f'UPDATE objective_planning SET {field}=? WHERE objective_id=?', (value, objective['id']))
    with pytest.raises(ValueError, match='Unsupported persisted objective planning'):
        OrganizationStore(store.path)


@pytest.mark.parametrize('field,value', [('workPackageId', 'foreign-package'), ('round', True), ('round', 9)])
def test_corrupt_plan_request_references_cannot_commit_tasks(tmp_path, field, value):
    store, _ = make_store(tmp_path)
    executive = store.claim_next()
    store.finish(executive, package_output())
    planner = store.claim_next()
    payload = json.loads(planner['payload'])
    payload[field] = value
    with store._write() as conn:
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), planner['id']))
    with pytest.raises(ValueError, match='exact work package and round'):
        store.finish(planner, plan_output(planner))
    assert store.snapshot()['tasks'] == []

"""Selective repair uses captured runtime inputs, never model reuse assertions."""
from dataclasses import replace
import json
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_selective_repair import dependency_closure, plan_repair
from tests.eidolon_cli.test_organization_acceptance import _integrate


def fixture(tmp_path, *, missing_dependency=False, review_b_first=False, packages=False):
    store = OrganizationStore(tmp_path/'state.db', replace(OrganizationSettings(), max_attempts=3))
    # Exercise both legacy and default executive planning modes.
    with store._write() as conn:
        if not packages:
            conn.execute("UPDATE agents SET accepts='[\"request.accept\"]' WHERE role='Executive'")
    objective = store.create_objective('Three independent outputs', idempotency_key='repair', delivery_mode='managed_artifact',
        acceptance_criteria=[{'id': c, 'text': c, 'kind': 'deliverable'} for c in ['a', 'b', 'c']])
    if packages:
        from tests.organization_package_helpers import decompose
        decompose(store)
    plan = store.claim_next()
    assert plan['type'] == 'request.plan'
    specs = [{'title': c.upper(), 'description': 'Produce '+c, 'type': 'work.draft', 'criterionIds': [c],
              'dependsOn': [1] if c == 'c' else []} for c in ['a', 'b', 'c']]
    if missing_dependency:
        specs[0].pop('dependsOn')
    store.finish(plan, {'tasks': specs})
    tasks = {}
    reviewed = 0
    while reviewed < 3:
        claim = store.claim_next()
        context = store.context(claim)
        if claim['type'] == 'work.draft':
            tasks[context['task']['title'].lower()] = claim['task_id']
            store.finish(claim, {'summary': context['task']['title'], 'deliverable': 'Output '+context['task']['title']})
            if review_b_first and context['task']['title'] == 'B':
                with store._write() as conn:
                    conn.execute("UPDATE requests SET priority=5 WHERE task_id=? AND type='request.review'", (claim['task_id'],))
        else:
            assert claim['type'] == 'request.review'
            ids = [e['id'] for e in context['evidence']]
            store.finish(claim, {'approved': True, 'summary': 'Independently reviewed.', 'evidenceIds': ids})
            reviewed += 1
    accept = _integrate(store)
    context = store.context(accept)
    ids = [e['id'] for e in context['evidence']]
    result = {'approved': False, 'summary': 'B needs correction.', 'evidenceIds': ids, 'conflicts': [],
              'criteriaResults': [{'criterion': c['text'], 'criterionId': c['id'], 'criterionRevision': c['revision'],
                                  'satisfied': c['id'] != 'b', 'evidenceIds': ids, 'reason': 'Checked exact outcome.'}
                                 for c in context['objective']['outcomeContract']['criteria']]}
    return store, objective['id'], tasks, accept, result


def test_independent_a_reused_b_and_dependent_c_repaired_without_budget_reset(tmp_path):
    store, objective, tasks, accept, result = fixture(tmp_path)
    with store._connect() as conn:
        plan = plan_repair(store, conn, objective, result)
        assert plan['mode'] == 'selective', plan
        assert [p['taskId'] for p in plan['reused']] == [tasks['a']]
        before = {name: [tuple(r) for r in conn.execute('SELECT * FROM '+name)] for name in ('objective_budgets', 'objective_usage', 'evidence', 'reviews')}
        old_requests = [dict(r) for r in conn.execute('SELECT * FROM requests WHERE task_id=?', (tasks['b'],))]
    assert store.finish(accept, result)
    with store._connect(write=True) as conn:
        retained = conn.execute('SELECT * FROM objective_reused_evidence').fetchall()
        assert len(retained) == 1 and retained[0]['task_id'] == tasks['a']
        for name in ('objective_budgets', 'evidence', 'reviews'):
            assert before[name] == [tuple(r) for r in conn.execute('SELECT * FROM '+name)]
        assert len(before['objective_usage']) == conn.execute('SELECT count(*) FROM objective_usage').fetchone()[0]
        assert conn.execute('SELECT round FROM objective_control').fetchone()[0] == 1
        replacements = conn.execute('SELECT * FROM task_repair_lineage').fetchall()
        assert {r['source_task_id'] for r in replacements} == {tasks['b'], tasks['c']}
        b = next(r['task_id'] for r in replacements if r['source_task_id'] == tasks['b'])
        work = conn.execute("SELECT * FROM requests WHERE task_id=? AND type='work.draft'", (b,)).fetchone()
        assert work['attempts'] == next(r['attempts'] for r in old_requests if r['type'] == 'work.draft')
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            conn.execute("UPDATE objective_reused_evidence SET sha256='changed'")
    next_work = store.claim_next()
    assert next_work['task_id'] == b and next_work['attempts'] == 2


@pytest.mark.parametrize('change', ['missing_dependency', 'scope', 'artifact', 'review', 'conflict', 'check', 'work_payload', 'task', 'policy'])
def test_ambiguous_or_changed_inputs_fall_back(tmp_path, change):
    store, objective, tasks, accept, result = fixture(tmp_path, missing_dependency=change == 'missing_dependency')
    with store._write() as conn:
        if change == 'scope':
            conn.execute("UPDATE objective_control SET amended_scope='changed'")
        elif change == 'artifact':
            conn.execute("UPDATE evidence SET content='changed' WHERE task_id=?", (tasks['a'],))
        elif change == 'review':
            conn.execute("UPDATE requests SET payload='{}' WHERE type='request.review' AND task_id=?", (tasks['a'],))
        elif change == 'work_payload':
            conn.execute("UPDATE requests SET payload='{\"changed\":true}' WHERE type='work.draft' AND task_id=?", (tasks['a'],))
        elif change == 'task':
            conn.execute("UPDATE tasks SET description='changed' WHERE id=?", (tasks['a'],))
        elif change == 'policy':
            store.settings = replace(store.settings, max_tool_calls=store.settings.max_tool_calls + 1)
        elif change == 'conflict':
            result['conflicts'] = ['Unlocalized inconsistency']
        elif change == 'check':
            conn.execute("UPDATE objective_control SET required_checks='[\"project_tests\"]'")
        plan = plan_repair(store, conn, objective, result)
        assert plan['mode'] == 'whole_replan'
        assert not plan['reused']
        if change == 'check':
            assert plan['invalidatedIntegrationChecks'] == ['project_tests']


def test_closure_includes_output_collisions_and_missing_edges():
    nodes = {k: {'dependenciesDeclared': True, 'dependencies': [], 'outputs': []} for k in 'abcd'}
    nodes['c']['dependencies'] = ['b']
    nodes['b']['outputs'] = [('root', 'shared/output')]
    nodes['d']['outputs'] = [('root', 'shared/output/child')]
    assert dependency_closure(nodes, {'b'}) == ({'b', 'c', 'd'}, None)
    nodes['a']['dependenciesDeclared'] = False
    assert dependency_closure(nodes, {'b'}) == (set(nodes), 'missing_dependency_declaration')


def test_repaired_round_reintegrates_and_accepts_exact_reused_artifact(tmp_path):
    store, objective, tasks, accept, result = fixture(tmp_path)
    store.finish(accept, result)
    snapshot = store.snapshot()
    retained = next(t for t in snapshot['tasks'] if t['id'] == tasks['a'])
    assert retained['reuse']['disposition'] == 'reused'
    assert retained['currentRound'] and not retained['historical'] and retained['originalRound'] == 0
    count = 0
    while count < 2:
        claim = store.claim_next()
        context = store.context(claim)
        if claim['type'] == 'work.draft':
            store.finish(claim, {'summary': 'Corrected', 'deliverable': 'Corrected '+context['task']['title']})
        else:
            assert claim['type'] == 'request.review'
            assert claim['attempts'] == 2
            store.finish(claim, {'approved': True, 'summary': 'Reviewed corrected bytes',
                'evidenceIds': [e['id'] for e in context['evidence']]})
            count += 1
    acceptance = _integrate(store)
    context = store.context(acceptance)
    assert retained['reuse']['evidenceId'] in [e['id'] for e in context['evidence']]
    result.update(approved=True, evidenceIds=[e['id'] for e in context['evidence']])
    for criterion in result['criteriaResults']:
        criterion.update(satisfied=True, evidenceIds=result['evidenceIds'])
    assert store.finish(acceptance, result)
    assert store.snapshot()['objectives'][0]['status'] == 'completed'


def test_reuse_is_rechecked_after_repair_and_cannot_outlive_artifact_mutation(tmp_path):
    store, objective, tasks, accept, result = fixture(tmp_path)
    store.finish(accept, result)
    from eidolon_cli.organization_selective_repair import verify_reused
    with store._write() as conn:
        conn.execute("UPDATE evidence SET content='mutated' WHERE task_id=?", (tasks['a'],))
        with pytest.raises(ValueError):
            verify_reused(store, conn, objective, 1)


def test_legacy_passive_reads_do_not_create_repair_tables(tmp_path):
    from eidolon_cli.organization_selective_repair import REPAIR_TABLES, reuse_view
    from eidolon_cli.organization_observation import _compatible_schema
    store = OrganizationStore(tmp_path/'legacy.db')
    with store._connect(write=True) as conn:
        for table in REPAIR_TABLES:
            conn.execute('DROP TABLE '+table)
        before = '\n'.join(conn.iterdump())
        assert reuse_view(conn, 'missing', 0) == {}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert _compatible_schema(conn, tables)
        assert '\n'.join(conn.iterdump()) == before
        conn.execute('CREATE TABLE task_repair_inputs(x TEXT)')
        tables.add('task_repair_inputs')
        assert not _compatible_schema(conn, tables)


def test_reviewer_consumed_failed_b_invalidates_otherwise_independent_a(tmp_path):
    store, objective, tasks, _, result = fixture(tmp_path, review_b_first=True)
    with store._connect() as conn:
        plan = plan_repair(store, conn, objective, result)
        assert plan['mode'] == 'whole_replan'
        assert plan['reason'] == 'no_independent_reusable_evidence'
        assert not plan['reused']


@pytest.mark.parametrize('history_task', [None, 'self'])
def test_unbound_and_self_history_are_ineligible_runtime_inputs(tmp_path, history_task):
    from eidolon_cli.organization_selective_repair import capture_inputs
    store = OrganizationStore(tmp_path/'state.db')
    store.create_objective('Draft', idempotency_key='one')
    from tests.eidolon_cli.test_organization_acceptance import _plan
    _plan(store)
    claim = store.claim_next()
    context = store.context(claim, mark_read=False)
    context['agentContext']['recentHistory'] = [{'taskId': claim['task_id'] if history_task == 'self' else None, 'summary': 'Unknown retained input'}]
    with store._write() as conn:
        capture_inputs(store, conn, claim, context)
        assert conn.execute('SELECT eligible FROM task_repair_inputs WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0


def test_default_executive_package_plan_is_carried_without_fabricated_planning(tmp_path):
    store, objective, tasks, accept, result = fixture(tmp_path, packages=True)
    with store._connect() as conn:
        before = [dict(r) for r in conn.execute('SELECT * FROM manager_work_packages')]
        plans = conn.execute("SELECT count(*) FROM requests WHERE type IN ('request.plan','request.decompose')").fetchone()[0]
        plan = plan_repair(store, conn, objective, result)
        assert plan['mode'] == 'selective', plan
    assert store.finish(accept, result)
    snapshot = store.snapshot()
    packages = snapshot['objectives'][0]['workPackages']
    current = [p for p in packages if p['currentRound']]
    assert len(current) == 1 and current[0]['planDisposition'] == 'reused'
    assert current[0]['sourcePackageId'] == before[0]['id']
    assert tasks['a'] in current[0]['taskIds']
    assert next(t for t in snapshot['tasks'] if t['id'] == tasks['a'])['workPackageId'] == current[0]['id']
    with store._connect() as conn:
        assert before == [dict(r) for r in conn.execute('SELECT * FROM manager_work_packages')]
        assert plans == conn.execute("SELECT count(*) FROM requests WHERE type IN ('request.plan','request.decompose')").fetchone()[0]
    reviewed = 0
    while reviewed < 2:
        claim = store.claim_next()
        context = store.context(claim)
        assert context['workPackage']['id'] == current[0]['id']
        if claim['type'] == 'work.draft':
            store.finish(claim, {'summary': 'Repaired', 'deliverable': 'Repaired output'})
        else:
            assert claim['type'] == 'request.review'
            store.finish(claim, {'approved': True, 'summary': 'Reviewed', 'evidenceIds': [e['id'] for e in context['evidence']]})
            reviewed += 1
    acceptance = _integrate(store)
    context = store.context(acceptance)
    result.update(approved=True, evidenceIds=[e['id'] for e in context['evidence']])
    for criterion in result['criteriaResults']:
        criterion.update(satisfied=True, evidenceIds=result['evidenceIds'])
    store.finish(acceptance, result)
    assert store.snapshot()['objectives'][0]['status'] == 'completed'
    with store._connect() as conn:
        assert before == [dict(r) for r in conn.execute('SELECT * FROM manager_work_packages')]


def test_three_manager_packages_preserve_membership_and_dependency_order(tmp_path):
    from tests.eidolon_cli.test_organization_work_packages import roster, package_output, plan_output
    settings = OrganizationSettings.from_config({'organization': {'roster': roster(), 'max_inflight': 4,
        'max_workers': 3, 'max_tasks': 3, 'max_attempts': 3}})
    store = OrganizationStore(tmp_path/'packages.db', settings)
    objective = store.create_objective('Three findings', idempotency_key='goal', delivery_mode='managed_artifact',
        manager_id='coordinator', executive_id='chief',
        acceptance_criteria=[{'id': c, 'text': c, 'kind': 'deliverable'} for c in 'abc'])
    decomposition = store.claim_next()
    store.finish(decomposition, package_output())
    for _ in range(3):
        claim = store.claim_next()
        output = plan_output(claim)
        output['tasks'][0]['criterionIds'] = [claim['agent_id'].rsplit('-', 1)[-1]]
        store.finish(claim, output)
    tasks = {}
    reviewed = 0
    while reviewed < 3:
        claim = store.claim_next()
        context = store.context(claim)
        if claim['type'] == 'request.hire':
            store.finish(claim, {})
        elif claim['type'] == 'work.draft':
            name = claim['agent_id'].rsplit('-', 1)[-1]
            tasks[name] = claim['task_id']
            store.finish(claim, {'summary': name, 'deliverable': 'Finding '+name})
        else:
            assert claim['type'] == 'request.review'
            store.finish(claim, {'approved': True, 'summary': 'Reviewed', 'evidenceIds': [e['id'] for e in context['evidence']]})
            reviewed += 1
    accept = _integrate(store)
    context = store.context(accept)
    ids = [e['id'] for e in context['evidence']]
    result = {'approved': False, 'summary': 'Repair b', 'evidenceIds': ids, 'conflicts': [],
        'criteriaResults': [{'criterion': c['text'], 'criterionId': c['id'], 'criterionRevision': c['revision'],
            'satisfied': c['id'] != 'b', 'evidenceIds': ids, 'reason': 'Checked'} for c in context['objective']['outcomeContract']['criteria']]}
    with store._connect() as conn:
        plan = plan_repair(store, conn, objective['id'], result)
        assert plan['mode'] == 'selective', plan
        assert {p['taskId'] for p in plan['reused']} == {tasks['a']}
    store.finish(accept, result)
    current = [p for p in store.snapshot()['objectives'][0]['workPackages'] if p['currentRound']]
    assert len(current) == 3 and all(p['planDisposition'] == 'reused' for p in current)
    dependent = next(p for p in current if p['managerId'] == 'lead-c')
    assert set(dependent['dependencyIds']) == {p['id'] for p in current if p['managerId'] != 'lead-c'}
    work = store.claim_next()
    assert work['agent_id'] == 'writer-b'
    context = store.context(work)
    assert context['workPackage']['managerId'] == 'lead-b'
    store.finish(work, {'summary': 'Corrected b', 'deliverable': 'Corrected b'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    store.finish(review, {'approved': True, 'summary': 'Reviewed', 'evidenceIds': [e['id'] for e in store.context(review)['evidence']]})
    downstream = store.claim_next()
    assert downstream['agent_id'] == 'writer-c'
    context = store.context(downstream)
    assert {e['taskId'] for e in context['dependencies']} == {tasks['a'], work['task_id']}


@pytest.mark.parametrize('mutation', ['plan', 'membership'])
def test_carried_plan_and_membership_tampering_fails_closed(tmp_path, mutation):
    from eidolon_cli.organization_package_repair import package_row
    store, _, tasks, accept, result = fixture(tmp_path, packages=True)
    store.finish(accept, result)
    with store._write() as conn:
        carried = conn.execute('SELECT * FROM objective_package_reuse').fetchone()
        if mutation == 'plan':
            conn.execute("UPDATE manager_work_packages SET title='Changed plan' WHERE id=?", (carried['source_package_id'],))
        else:
            conn.execute('INSERT INTO repair_package_members VALUES (?,?)', (carried['id'], tasks['b']))
        with pytest.raises(ValueError, match='changed'):
            package_row(conn, carried['id'])


def test_reused_package_retains_manager_obligation_and_immutable_history(tmp_path):
    from eidolon_cli.organization_management import _validate_open_packages
    store, _, _, accept, result = fixture(tmp_path, packages=True)
    store.finish(accept, result)
    with store._write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='Immutable repaired package'):
            conn.execute("UPDATE objective_package_reuse SET sha256='changed'")
        with pytest.raises(sqlite3.IntegrityError, match='Immutable repaired package'):
            conn.execute('DELETE FROM repair_package_members')
        conn.execute("UPDATE agents SET accepts='[]' WHERE role='Manager'")
        with pytest.raises(ValueError, match='Open work package'):
            _validate_open_packages(conn, {})


def test_absent_package_repair_schema_is_passive_but_partial_schema_is_rejected(tmp_path):
    from eidolon_cli.organization_package_repair import present
    conn = sqlite3.connect(tmp_path/'legacy.db')
    before = list(conn.iterdump())
    assert not present(conn)
    assert list(conn.iterdump()) == before
    conn.execute('CREATE TABLE objective_package_reuse(id TEXT)')
    with pytest.raises(ValueError, match='incomplete'):
        present(conn)


def test_repeated_package_repair_falls_back_when_history_is_not_independent(tmp_path):
    from eidolon_cli.organization_package_repair import package_row
    store, objective, tasks, accept, result = fixture(tmp_path, packages=True)
    with store._connect() as conn:
        budgets = [tuple(r) for r in conn.execute('SELECT * FROM objective_budgets')]
        initial_plans = conn.execute("SELECT count(*) FROM requests WHERE type IN ('request.plan','request.decompose')").fetchone()[0]
    for round_number in (1,):
        with store._connect() as conn:
            plan = plan_repair(store, conn, objective, result)
            assert plan['mode'] == 'selective', plan
        store.finish(accept, result)
        snapshot = store.snapshot()
        current = next(p for p in snapshot['objectives'][0]['workPackages'] if p['currentRound'])
        assert tasks['a'] in current['taskIds']
        with store._connect() as conn:
            row = package_row(conn, current['id'])
            assert row['round'] == round_number
            assert package_row(conn, row['sourcePackageId'])['round'] == round_number - 1
            assert budgets == [tuple(r) for r in conn.execute('SELECT * FROM objective_budgets')]
            assert initial_plans == conn.execute("SELECT count(*) FROM requests WHERE type IN ('request.plan','request.decompose')").fetchone()[0]
        reviewed = 0
        while reviewed < 2:
            claim = store.claim_next()
            context = store.context(claim)
            assert claim['attempts'] == round_number + 1
            if claim['type'] == 'work.draft':
                store.finish(claim, {'summary': 'Repaired', 'deliverable': f'Repair round {round_number}'})
            else:
                assert claim['type'] == 'request.review'
                store.finish(claim, {'approved': True, 'summary': 'Reviewed', 'evidenceIds': [e['id'] for e in context['evidence']]})
                reviewed += 1
        accept = _integrate(store)
        context = store.context(accept)
        result['evidenceIds'] = [e['id'] for e in context['evidence']]
        for criterion in result['criteriaResults']:
            criterion['evidenceIds'] = result['evidenceIds']
    with store._connect() as conn:
        plan = plan_repair(store, conn, objective, result)
        assert plan['mode'] == 'whole_replan'
        assert not plan['reused']
        assert budgets == [tuple(r) for r in conn.execute('SELECT * FROM objective_budgets')]
    store.finish(accept, result)
    with store._connect() as conn:
        assert conn.execute('SELECT round FROM objective_control').fetchone()[0] == 2
        assert budgets == [tuple(r) for r in conn.execute('SELECT * FROM objective_budgets')]
    with pytest.raises(ValueError, match='replan limit'):
        with store._write() as conn:
            store._replan(conn, objective, 'No allowance reset')


def test_package_projection_verifies_two_immutable_ancestry_generations(tmp_path):
    from eidolon_cli.organization_package_repair import carry_packages, package_row
    store, objective, _, accept, result = fixture(tmp_path, packages=True)
    store.finish(accept, result)
    current = next(p for p in store.snapshot()['objectives'][0]['workPackages'] if p['currentRound'])
    with store._write() as conn:
        carry_packages(conn, objective, 1, 2, {ident: ident for ident in current['taskIds']})
        newest = conn.execute('SELECT id FROM objective_package_reuse WHERE round=2').fetchone()[0]
        second = package_row(conn, newest)
        first = package_row(conn, second['sourcePackageId'])
        original = package_row(conn, first['sourcePackageId'])
        assert [second['round'], first['round'], original['round']] == [2, 1, 0]
        assert second['plan_request_id'] == first['plan_request_id'] == original['plan_request_id']
        assert second['plan'] == original['plan']

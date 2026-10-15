"""Typed outcome proof is exact, durable and independent of model optimism."""
from copy import deepcopy
import json
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_outcome_contracts import definitions, record_human_judgment
from tests.eidolon_cli.test_organization_acceptance import _plan, _work_and_review, _integrate, _decision


def create(store, kind='deliverable', **kw):
    return store.create_objective('Brief', 'Write a supported recommendation', idempotency_key='create',
        delivery_mode='managed_artifact', acceptance_criteria=[dict(id='recommendation', text='One supported recommendation', kind=kind, **kw)])


def prepare(store, kind='deliverable', **kw):
    objective = create(store, kind, **kw)
    _plan(store)
    _work_and_review(store)
    return objective


def decision(store, claim, **kw):
    result = _decision(store, claim, **kw)
    contract = store.context(claim)['objective']['outcomeContract']
    for check, criterion in zip(result['criteriaResults'], contract['criteria']):
        check.update(criterionId=criterion['id'], criterionRevision=criterion['revision'])
    return result


def test_typed_deliverable_is_durable_bound_and_requires_final_artifact(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    objective = prepare(store)
    claim = _integrate(store)
    value = decision(store, claim)
    original = deepcopy(value)
    value['criteriaResults'][0]['evidenceIds'] = value['evidenceIds'][:-1]
    with pytest.raises(ValueError, match='required kind'):
        store.finish(claim, value)
    for key in ['criterionId', 'criterionRevision']:
        value = deepcopy(original)
        value['criteriaResults'][0].pop(key)
        with pytest.raises(ValueError, match='id and revision'):
            store.finish(claim, value)
    assert store.finish(claim, original)
    reopened = OrganizationStore(store.path)
    assert reopened.snapshot()['objectives'][0]['status'] == 'completed'
    with reopened._connect() as conn:
        proof = conn.execute('SELECT * FROM objective_contract_acceptances').fetchone()
        assert proof['contract_sha256'] == objective['acceptance']['contract']['sha256']
        assert json.loads(proof['evidence_bindings'])[0]['evidenceHashes']
    with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
        with reopened._write() as conn:
            conn.execute("UPDATE objective_contract_revisions SET sha256='changed'")


@pytest.mark.parametrize('value', [[], [{'text':'x','kind':'deliverable'}], [{'id':'x','text':'x','kind':'unknown'}],
    [{'id':'x','text':'x','kind':'deliverable','checkKind':'project_tests'}],
    [{'id':'x','text':'x','kind':'authorized_check','checkKind':'shell'}],
    [{'id':'x','text':'x','kind':'deliverable','grant':True}],
    [{'id':'same','text':'x','kind':'deliverable'}, {'id':'same','text':'y','kind':'deliverable'}]])
def test_invalid_definitions_fail_closed(value):
    with pytest.raises(ValueError):
        definitions(value, 'description')


def test_legacy_strings_keep_identity_and_history(tmp_path):
    store=OrganizationStore(tmp_path/'state.db')
    first=store.create_objective('Brief', idempotency_key='legacy')
    assert store.create_objective('Brief', idempotency_key='legacy')['id']==first['id']
    assert first['acceptance']['criteria']==['Brief']
    assert first['acceptance']['contract']['criteria'][0]['legacy'] is True
    _plan(store); _work_and_review(store)
    claim=_integrate(store)
    assert store.finish(claim, _decision(store,claim))


def test_kind_changes_change_idempotency_identity(tmp_path):
    store=OrganizationStore(tmp_path/'state.db')
    create(store)
    with pytest.raises(ValueError, match='different objective'):
        create(store, 'human_judgment')


def test_amendment_retains_ids_revisions_history_and_never_reuses_binding(tmp_path):
    store=OrganizationStore(tmp_path/'state.db')
    first=create(store)
    claim=store.claim_next(); store.fail(claim,'Needs scope input')
    store.resolve(claim['id'],'amend_scope','Use current supplied facts',idempotency_key='amend',
        acceptance_criteria=[{'id':'recommendation','text':'A changed recommendation','kind':'deliverable'}])
    current=store.snapshot()['objectives'][0]['acceptance']['contract']
    assert current['revision']==2 and current['criteria'][0]['revision']==2
    assert current['criteria'][0]['id']=='recommendation'
    assert current['sha256']!=first['acceptance']['contract']['sha256']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objective_contract_revisions').fetchone()[0]==2


def test_scope_only_amendment_preserves_explicit_human_requirement(tmp_path):
    store=OrganizationStore(tmp_path/'state.db'); create(store,'human_judgment')
    claim=store.claim_next();store.fail(claim,'Question')
    store.resolve(claim['id'],'amend_scope','Narrow the facts',idempotency_key='amend')
    criterion=store.snapshot()['objectives'][0]['acceptance']['contract']['criteria'][0]
    assert criterion['kind']=='human_judgment' and criterion['id']=='recommendation' and criterion['revision']==1


def judgment_args(store):
    objective=store.snapshot()['objectives'][0]
    return dict(criterion_id='recommendation', contract_sha256=objective['acceptance']['contract']['sha256'],
        deliverable_sha256=store.evidence(objective['acceptance']['deliverableId'])['sha256'],
        approved=True, reason='I reviewed these exact bytes', idempotency_key='owner', confirmed=True)


def test_human_judgment_waits_without_model_dispatch_then_resumes_exactly(tmp_path):
    store=OrganizationStore(tmp_path/'state.db'); objective=prepare(store,'human_judgment')
    integrate=store.claim_next()
    store.finish(integrate,{'summary':'Combined','deliverable':'Choose A based on supplied facts.'})
    assert store.claim_next() is None
    args=judgment_args(store)
    for invalid in ({'confirmed':False},{'contract_sha256':'0'*64},{'deliverable_sha256':'0'*64},{'approved':'yes'}):
        with pytest.raises(ValueError):
            record_human_judgment(store,objective['id'],**{**args,**invalid})
    assert record_human_judgment(store,objective['id'],**args)['recorded']
    assert not record_human_judgment(store,objective['id'],**args)['recorded']
    with pytest.raises(ValueError,match='different input'):
        record_human_judgment(store,objective['id'],**{**args,'approved':False})
    claim=store.claim_next(); assert claim['type']=='request.accept'
    assert store.context(claim)['objective']['outcomeHumanJudgments'][0]['approved']
    assert store.finish(claim,decision(store,claim))


def test_human_rejection_cannot_be_overridden_by_executive(tmp_path):
    store=OrganizationStore(tmp_path/'state.db'); objective=prepare(store,'human_judgment')
    integrate=store.claim_next();store.finish(integrate,{'summary':'Combined','deliverable':'Choose A.'})
    args=judgment_args(store);args['approved']=False
    record_human_judgment(store,objective['id'],**args)
    claim=store.claim_next()
    with pytest.raises(ValueError,match='Explicit owner judgment'):
        store.finish(claim,decision(store,claim))
    assert store.finish(claim,decision(store,claim,approved=False))


@pytest.mark.parametrize('check',['project_build','project_typecheck'])
def test_compiler_checks_cannot_be_claimed_by_model_or_test_receipts(tmp_path,check):
    store=OrganizationStore(tmp_path/'state.db'); objective=create(store,'authorized_check',checkKind=check)
    with store._connect() as conn:
        with pytest.raises(ValueError,match='unsupported|no authorized execution receipt'):
            store._verify_required_checks(conn,objective['id'],[check])


def test_unknown_required_check_is_not_silently_accepted(tmp_path):
    store=OrganizationStore(tmp_path/'state.db');objective=create(store)
    with store._connect() as conn:
        with pytest.raises(ValueError,match='requiredChecks'):
            store._verify_required_checks(conn,objective['id'],['forged'])


def test_bound_contract_hash_cannot_be_replaced_with_model_assertion(tmp_path):
    store=OrganizationStore(tmp_path/'state.db');prepare(store)
    claim=_integrate(store);value=decision(store,claim)
    with store._write() as conn:
        payload=json.loads(conn.execute('SELECT payload FROM requests WHERE id=?',(claim['id'],)).fetchone()[0])
        payload['outcomeContractSha256']='0'*64
        conn.execute('UPDATE requests SET payload=? WHERE id=?',(json.dumps(payload),claim['id']))
    with pytest.raises(ValueError,match='contract revision is stale'):
        store.finish(claim,value)


def test_human_receipt_cannot_be_replayed_against_a_changed_final(tmp_path):
    store=OrganizationStore(tmp_path/'state.db');objective=prepare(store,'human_judgment')
    integrate=store.claim_next();store.finish(integrate,{'summary':'Combined','deliverable':'Choose A.'})
    args=judgment_args(store)
    record_human_judgment(store,objective['id'],**args)
    claim=store.claim_next();value=decision(store,claim)
    with store._write() as conn:
        conn.execute("UPDATE objective_deliverables SET content='Changed' WHERE objective_id=?",(objective['id'],))
    with pytest.raises(ValueError,match='changed'):
        store.finish(claim,value)


def test_owner_receipt_is_immutable_and_rejection_never_becomes_approval(tmp_path):
    store=OrganizationStore(tmp_path/'state.db');objective=prepare(store,'human_judgment')
    integrate=store.claim_next();store.finish(integrate,{'summary':'Combined','deliverable':'Choose A.'})
    args=judgment_args(store);args['approved']=False
    record_human_judgment(store,objective['id'],**args)
    for query in ('DELETE FROM objective_human_judgments', 'UPDATE objective_human_judgments SET approved=1'):
        with pytest.raises(sqlite3.IntegrityError,match='Immutable'):
            with store._write() as conn:
                conn.execute(query)


def test_unselected_project_cannot_be_named_by_a_criterion(tmp_path):
    store=OrganizationStore(tmp_path/'state.db')
    with pytest.raises(ValueError,match='explicitly selected'):
        create(store,'authorized_check',checkKind='project_tests',projectId='outside')
    assert store.snapshot()['objectives']==[]


@pytest.mark.parametrize('explicit',[False,True])
def test_replacement_preserves_typed_requirement_and_identity(tmp_path,monkeypatch,explicit):
    import time
    store=OrganizationStore(tmp_path/'state.db');objective=create(store,'human_judgment')
    now=time.time();monkeypatch.setattr(time,'time',lambda:now+store.settings.objective_timeout_seconds+1)
    preview=store.preview_replacement(objective['id'])
    typed=[{'id':'recommendation','text':'One supported recommendation','kind':'human_judgment'}]
    criteria=typed if explicit else preview['acceptanceCriteria']
    args=(objective['id'],preview['sourceVersion'],preview['title'],preview['description'],criteria)
    replacement=store.replace_objective(*args,confirmed=True)
    contract=replacement['acceptance']['contract']
    assert contract['criteria'][0]['kind']=='human_judgment'
    assert contract['criteria'][0]['id']=='recommendation'
    assert store.replace_objective(*args,confirmed=True)['id']==replacement['id']
    with pytest.raises(ValueError,match='different input'):
        store.replace_objective(*args[:-1],[{**typed[0],'kind':'deliverable'}],confirmed=True)


def bind_test_contract(store, objective_id, kind, check=None):
    """Fixture-only substitution before final requests are created."""
    from eidolon_cli.organization_outcome_contracts import write_contract
    with store._write() as conn:
        text=json.loads(conn.execute('SELECT criteria FROM objective_control WHERE objective_id=?',(objective_id,)).fetchone()[0])[0]
        spec={'id':'proof','text':text,'kind':kind}
        if check:spec['checkKind']=check
        write_contract(conn,objective_id,definitions([spec],text))


def typed_decision(store,claim):
    value=_decision(store,claim)
    for result,criterion in zip(value['criteriaResults'],store.context(claim)['objective']['outcomeContract']['criteria']):
        result.update(criterionId=criterion['id'],criterionRevision=criterion['revision'])
    return value


def test_managed_validation_requires_actual_retained_receipt(tmp_path):
    from tests.eidolon_cli.test_organization_edits import _setup,_propose,_review
    store,source,objective=_setup(tmp_path)
    bind_test_contract(store,objective['id'],'authorized_check','managed_validation')
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact' WHERE objective_id=?",(objective['id'],))
    _propose(store);_review(store)
    assert store.finish(store.claim_next(),{})
    assert store.finish(store.claim_next(),{})
    claim=_integrate(store);value=typed_decision(store,claim)
    wrong=deepcopy(value);wrong['criteriaResults'][0]['evidenceIds']=[value['evidenceIds'][-1]]
    with pytest.raises(ValueError,match='required kind'):
        store.finish(claim,wrong)
    assert store.finish(claim,value)


def test_typed_source_handoff_rechecks_real_bytes_and_retains_source_digest(tmp_path):
    from tests.eidolon_cli.test_organization_edits import _setup,_propose,_review
    store,source,objective=_setup(tmp_path)
    bind_test_contract(store,objective['id'],'source_integration')
    _,artifact,_,_=_propose(store);_review(store)
    store.finish(store.claim_next(),{});store.finish(store.claim_next(),{})
    pending=next(row for row in store.snapshot()['requests'] if row['type']=='request.merge')
    output=artifact['editProposal']['newContent']
    source.write_text(output)
    store.resolve(pending['id'],'record_handoff','Applied exact reviewed output',[artifact['id']],idempotency_key='handoff')
    claim=_integrate(store);value=typed_decision(store,claim)
    source.write_text('unrelated new contents')
    with pytest.raises(ValueError,match='changed'):
        store.finish(claim,value)
    source.write_text(output)
    assert store.finish(claim,value)
    with store._connect() as conn:
        binding=json.loads(conn.execute('SELECT evidence_bindings FROM objective_contract_acceptances').fetchone()[0])[0]
        assert len(binding['sourceHandoffSha256'])==64
        assert len(binding['sourceManifestSha256'])==64


@pytest.mark.linux_only
def test_typed_project_tests_require_current_independently_reviewed_run(tmp_path,monkeypatch):
    import threading
    from eidolon_cli import organization_project_runner as runner
    from tests.eidolon_cli.test_organization_project_security import _reviewed_project,_simulated_terminal_result
    store,_,objective,_=_reviewed_project(tmp_path)
    bind_test_contract(store,objective['id'],'authorized_check','project_tests')
    # Inject only the terminal runtime result; actual SQLite proof/review gates run.
    monkeypatch.setattr(runner,'run_project_tests',lambda files,grant,cancel:_simulated_terminal_result(files))
    claim=store.claim_next();store.run_project_stage(claim,threading.Event());store.finish(claim,{})
    review=store.claim_next()
    store.finish(review,{'approved':True,'summary':'Independent exact receipt review','evidenceIds':json.loads(review['payload'])['evidenceIds']})
    accept=_integrate(store);value=typed_decision(store,accept)
    wrong=deepcopy(value);wrong['criteriaResults'][0]['evidenceIds']=[value['evidenceIds'][-1]]
    with pytest.raises(ValueError,match='required kind'):
        store.finish(accept,wrong)
    assert store.finish(accept,value)

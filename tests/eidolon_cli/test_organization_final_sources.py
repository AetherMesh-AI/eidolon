"""Final review sees all exact managed output, including retained prior rounds."""
import pytest
from tests.eidolon_cli.test_organization_edits import _setup, _review
from tests.eidolon_cli.test_organization_project_workflow import project_proposal, apply_validate


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_retained_prior_round_source_reaches_final_review(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact' WHERE objective_id=?", (objective['id'],))
    original = project_proposal(store, source)
    _review(store)
    store.finish(store.claim_next(), {})
    validation = store.claim_next()
    store.fail(validation, 'Validation outcome not committed')
    store.resolve(validation['id'], 'request_replan', 'Repair first file, preserve second', idempotency_key='repair-artifact')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Repair', 'description': 'Repair first file, preserve second', 'type': 'work.edit', 'team': 'engineering'}]})
    project_proposal(store, source, second=False, old='new value', new='final value')
    apply_validate(store)
    integrate = store.claim_next()
    assert integrate['type'] == 'request.integrate'
    context = store.context(integrate)
    retained = store.evidence(original)['editProposal']['files'][1]
    assert retained['sourcePath'] == 'root0/config.json'
    from eidolon_cli.organization_evidence import project_evidence
    for stage in ("request.integrate", "request.accept"):
        context = store.context(integrate)
        projected = project_evidence(context)
        aggregate = next(item for item in projected["evidence"] if item.get("projectValidation"))
        files = aggregate["projectValidation"]["sourceFiles"]
        exact = next(item for item in files if item["path"] == retained["sourcePath"])
        assert exact["evidenceId"] == original
        assert exact["sha256"] == retained["newSha256"]
        assert projected["evidenceBodies"][exact["content"]["bodySha256"]] == retained["newContent"]
        if stage == "request.integrate":
            store.finish(integrate, {"summary": "Integrated current project", "deliverable": "Both managed files retained."})
            integrate = store.claim_next()
            assert integrate["type"] == "request.accept"

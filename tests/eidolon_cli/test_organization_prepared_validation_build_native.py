"""Real three-recipe durable lifecycle with synthetic independent judgments.

No provider is called. Actual compiler, selected UI test and authored renderer
build run in the pinned sandbox; this does not claim full UI-suite coverage.
"""
from copy import deepcopy
import json
import stat
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
from eidolon_cli.organization_prepared_validation_events import retained_runtime_events
from eidolon_cli.organization_prepared_validation_ledger import _charged_units, reconstruct_prepared_snapshot
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_workspace_receipt import validate_prepared_workspace_receipt
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_root_grants import folder_profile  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA, record_complete_context
from tests.eidolon_cli.test_organization_prepared_validation_native import (
    keep_native_claims_alive, measure_native_observations, measure_native_phase,
    measure_native_writes, owner_source_policy,
)
from tests.eidolon_cli.test_organization_project_workspace_build_native import (
    UI_TEST, assert_renderer_manifest, prepared_build,  # noqa: F401
)

BUILD_CRITERIA = [*deepcopy(CRITERIA),
    {'id': 'renderer-build', 'kind': 'authorized_check', 'checkKind': 'project_build', 'projectId': 'notes',
     'text': 'The exact selected renderer produces a bounded build output metadata claim.'}]


@pytest.fixture
def native_build_owner(folder_profile, prepared_build):
    from eidolon_cli.organization_folder_setup import register_folder
    source, dependencies, manifest, profile = prepared_build
    owner = folder_profile
    register_folder(owner.home, owner.apply)
    for item in source.files:
        target = owner.root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
        target.chmod(item.mode | stat.S_IWUSR)
    selection = {'projectId': 'notes', 'enabled': True,
        'source': owner_source_policy(source), 'profile': deepcopy(profile),
        'dependencies': {'directory': str(dependencies), 'manifest': manifest}}
    return SimpleNamespace(**vars(owner), dependency=dependencies, selection=selection)


def assert_build_receipt_boundaries(receipt, context):
    """Revalidate retained bytes and forged derivatives without rerunning Vite."""
    checked = validate_prepared_workspace_receipt(receipt, **context)
    assert checked['hostReceiptConsistent'] is True
    assert checked['workloadEvidenceOrigin'] == 'workload-claim'
    assert checked['requiresIndependentReview'] is True
    assert checked['acceptanceAuthorized'] is checked['sourceDeliveryAuthorized'] is checked['replayAllowed'] is False
    bad_path = deepcopy(receipt['outcome']['outputManifest'])
    bad_path[0]['path'] = '../escaped-output'
    mutations = [
        ('outcome', {'outputBodiesRetained': True}),
        ('outcome', {'outputManifest': [], 'outputManifestSha256': digest([])}),
        ('outcome', {'outputManifest': bad_path, 'outputManifestSha256': digest(bad_path)}),
        ('outcome', {'evidenceOrigin': 'trusted-collector-proof'}),
        (None, {'stagingCleanupConfirmed': False}),
        (None, {'snapshotSha256': '0' * 64}),
    ]
    for target, updates in mutations:
        candidate = deepcopy(receipt)
        (candidate[target] if target else candidate).update(updates)
        # Original host retention authenticates neither a model's modified
        # body nor its newly calculated digest.
        with pytest.raises(ValueError):
            validate_prepared_workspace_receipt(candidate, **context)
        # A malformed record accidentally committed to a trusted sink still
        # must satisfy the complete semantic, scope and cleanup checks.
        with pytest.raises(ValueError):
            validate_prepared_workspace_receipt(candidate,
                **{**context, 'trusted_receipt_sha256': digest(candidate)})
    stale = deepcopy(context['profile'])
    stale['limits']['wallSeconds'] -= 1
    with pytest.raises(ValueError):
        validate_prepared_workspace_receipt(receipt, **{**context, 'profile': stale})


def test_build_receipt_rejection_probe_without_native_opt_in():
    # Only helper correctness is synthetic; native lifecycle uses real events.
    from tests.eidolon_cli.test_organization_project_workspace_build import build_receipt_fixture
    receipt, context = build_receipt_fixture()
    assert_build_receipt_boundaries(receipt, context)


def build_acceptance_claim(store):
    integrate = store.claim_next(recoverable=True)
    assert integrate is not None and integrate['type'] == 'request.integrate'
    record_complete_context(store, integrate)
    assert store.finish(integrate, {'summary': 'Exact three-recipe evidence retained with independent review.',
        'deliverable': 'The selected source passed the compiler, the explicitly selected idle-mount UI test, and '
        'the actual renderer build. Build output is bounded workload-claimed hashes and sizes only; no output '
        'bodies or publication authority are retained. This does not establish full UI-suite coverage.'})
    acceptance = store.claim_next(recoverable=True)
    assert acceptance is not None and acceptance['type'] == 'request.accept'
    assert acceptance['agent_id'] != integrate['agent_id']
    context = record_complete_context(store, acceptance)
    evidence_ids = json.loads(acceptance['payload'])['evidenceIds']
    result = {'approved': True, 'summary': 'All exact typed criteria have current independently reviewed evidence.',
        'evidenceIds': evidence_ids, 'conflicts': [], 'criteriaResults': [
            {'criterion': criterion['text'], 'criterionId': criterion['id'], 'criterionRevision': criterion['revision'],
             'satisfied': True, 'evidenceIds': evidence_ids,
             'reason': 'Current complete three-recipe evidence and independent review support this exact criterion.'}
            for criterion in context['objective']['outcomeContract']['criteria']]}
    assert any(item['criterionId'] == 'renderer-build' for item in result['criteriaResults'])
    return acceptance, result


def test_actual_three_recipe_set_finishes_reviews_accepts_and_reopens(native_build_owner, monkeypatch, record_property):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running
    owner = native_build_owner
    state = setup_running(owner, read_path=UI_TEST, clear_execution_only=False,
                          acceptance_criteria=BUILD_CRITERIA, full_planning=True)
    store = state.store
    assert state.saved['receipt']['candidate']['contract']['version'] == 2
    with measure_native_observations(monkeypatch, record_property), measure_native_writes(store, monkeypatch, record_property), keep_native_claims_alive(store, state.objective, record_property) as writer_events:
        with measure_native_phase('execute_three_recipes', record_property):
            assert store.run_project_stage(state.claim, state.cancel) == {}
        with store._connect() as conn:
            assert _charged_units(conn, state.objective) == 3
            steps = list(conn.execute('SELECT run_id,recipe,units FROM prepared_validation_steps ORDER BY step_id'))
            assert len(steps) == 3 and len({step['run_id'] for step in steps}) == 3
            assert sum(step['units'] for step in steps) == 3
            streams = {step['recipe']: retained_runtime_events(conn, step['run_id']) for step in steps}
            receipts = {recipe: next(event['data']['receipt'] for event in stream if event['kind'] == 'receipt')
                        for recipe, stream in streams.items()}
            set_id, envelope_record = conn.execute('SELECT id,envelope FROM prepared_validation_sets').fetchone()
            envelope = json.loads(envelope_record)
            retained_profile = json.loads(conn.execute(
                "SELECT json_extract(grant_record,'$.profile') FROM prepared_validation_bindings "
                'WHERE objective_id=? AND project_id=?', (state.objective, 'notes')).fetchone()[0])
            assert envelope['version'] == retained_profile['version'] == 2
            assert envelope['contract'] == 'prepared-validation-set-v2'
            assert envelope['identity']['profileSha256'] == digest(retained_profile)
            source = reconstruct_prepared_snapshot(conn, set_id)
        assert set(receipts) == {'workspace_typescript', 'workspace_vitest_ui', 'workspace_vite_build'}
        for key in ('snapshotSha256', 'profileSha256', 'bundleSha256'):
            assert len({receipt[key] for receipt in receipts.values()}) == 1
        assert len({receipt['resourceIdentity']['runId'] for receipt in receipts.values()}) == 3
        assert len({receipt['stepDefinitionSha256'] for receipt in receipts.values()}) == 3
        assert all(receipt['status'] == 'passed' and receipt['cleanupConfirmed'] is True
                   and receipt['stagingCleanupConfirmed'] is True
                   and receipt['sourceWritesPerformed'] is False for receipt in receipts.values())
        assert receipts['workspace_typescript']['outcome']['sourceCount'] > 0
        tested = receipts['workspace_vitest_ui']['outcome']
        assert tested['testCount'] > 0
        assert tested['reportedFiles'] == ['/source/root0/' + UI_TEST]
        built = receipts['workspace_vite_build']
        assert_renderer_manifest(built, source)
        record_property('three_recipe_run_ids', {recipe: receipt['resourceIdentity']['runId']
                                                for recipe, receipt in receipts.items()})
        record_property('selected_ui_test', UI_TEST)
        record_property('build_manifest_sha256', built['outcome']['outputManifestSha256'])
        build_event = next(event['data'] for event in streams['workspace_vite_build'] if event['kind'] == 'receipt')
        context = {'source': source, 'profile': owner.selection['profile'], 'step_id': built['stepId'],
            'resource_identity': built['resourceIdentity'], 'expected_dependency_identity': tuple(
                built['dependencyRootIdentity'][key] for key in ('device', 'inode')),
            'expected_runtime': built['runtime'], 'staging_identity': built['stagingIdentity'],
            'container_id': built['containerId'], 'container_config_sha256': built['containerConfigSha256'],
            'trusted_receipt_sha256': build_event['receiptSha256']}
        assert_build_receipt_boundaries(built, context)
        with measure_native_phase('finish_execution', record_property, writer_events):
            assert store.finish(state.claim, {}) is True
        with measure_native_phase('claim_review', record_property):
            review = store.claim_next(recoverable=True)
        assert review['type'] == 'request.test_review'
        assert review['agent_id'] not in {state.claim['agent_id'], 'inspector'}
        with measure_native_phase('record_review_context', record_property):
            context = record_complete_context(store, review)
        payload = json.loads(review['payload'])
        # Dispatch keeps its compatibility discriminator; retained set/profile
        # identities above carry the independently asserted protocol version.
        assert payload['executionContract'] == 'prepared-validation-v1'
        assert set(payload['evidenceIds']) == {item['id'] for item in context['evidence']}
        artifacts = [item for item in context['evidence'] if item.get('kind') == 'project_execution']
        assert {item['projectExecution']['checkKind'] for item in artifacts} == {
            'project_typecheck', 'project_tests', 'project_build'}
        assert len(payload['evidenceIds']) == len(artifacts) + 2
        build_id = built['resourceIdentity']['runId']
        with measure_native_phase('reject_missing_build_review', record_property), pytest.raises(ValueError):
            store.finish(review, {'approved': True, 'summary': 'Invalid review omits the required build.',
                'evidenceIds': [identifier for identifier in payload['evidenceIds'] if identifier != build_id]})
        with measure_native_phase('finish_review', record_property, writer_events):
            assert store.finish(review, {'approved': True,
                'summary': 'Synthetic independent review of actual typecheck, selected idle-mount UI test and bounded renderer build metadata.',
                'evidenceIds': payload['evidenceIds']}) is True
        with measure_native_phase('prepare_acceptance', record_property, writer_events):
            acceptance, accepted = build_acceptance_claim(store)
        invalid = deepcopy(accepted)
        next(item for item in invalid['criteriaResults'] if item['criterionId'] == 'renderer-build')['evidenceIds'] = [
            identifier for identifier in accepted['evidenceIds'] if identifier != build_id]
        with measure_native_phase('reject_untyped_build_acceptance', record_property), pytest.raises(ValueError):
            store.finish(acceptance, invalid)
        with measure_native_phase('finish_acceptance', record_property, writer_events):
            assert store.finish(acceptance, accepted) is True
        reopened = OrganizationStore(store.path, store.settings)
        with measure_native_phase('reopen_verification', record_property), reopened._connect() as conn:
            verified = verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
            assert verified['runtimeReceiptsVerified'] is verified['independentReviewVerified'] is True
            assert verified['sourceDeliveryAuthorized'] is verified['replayAllowed'] is False
            assert {artifact['projectExecution']['checkKind'] for _, artifact in verified['checks']} == {
                'project_typecheck', 'project_tests', 'project_build'}
            assert _charged_units(conn, state.objective) == 3
            assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 3
            assert conn.execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM objective_contract_acceptances').fetchone()[0] == 1
            assert conn.execute('SELECT status FROM objective_control WHERE objective_id=?', (state.objective,)).fetchone()[0] == 'accepted'
        # Reopening must perform fresh source and Vite-dependency checks. These
        # negative observations reuse retained execution and never rerun tools.
        for path in (owner.root / 'apps/desktop/index.html', owner.dependency / 'node_modules/vite/dist/node/index.js'):
            original, mode = path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
            try:
                path.chmod(mode | stat.S_IWUSR)
                path.write_bytes(original + b'\n<!-- stale native build input -->\n')
                path.chmod(mode)
                with reopened._connect() as conn, pytest.raises(ValueError):
                    verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
            finally:
                path.chmod(mode | stat.S_IWUSR)
                path.write_bytes(original)
                path.chmod(mode)
        from tests.eidolon_cli.test_organization_prepared_validation_setup import revoke_params
        setup.revoke_prepared_validation(owner.home, revoke_params(owner, state.saved))
        reopened = OrganizationStore(store.path, store.settings)
        with reopened._connect() as conn:
            with pytest.raises(ValueError):
                verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
            assert _charged_units(conn, state.objective) == 3
            assert conn.execute('SELECT count(*) FROM prepared_validation_runtime_events WHERE kind=?', ('receipt',)).fetchone()[0] == 3
            assert conn.execute('SELECT count(*) FROM objective_contract_acceptances').fetchone()[0] == 1

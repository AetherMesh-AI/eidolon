"""Versioned owner/admission integration; synthetic dependencies, no execution."""
from copy import deepcopy
import json

import pytest

from eidolon_cli import organization_prepared_validation_setup as setup
from tests.eidolon_cli.test_organization_prepared_validation_contract import raw
from tests.eidolon_cli.test_organization_prepared_validation_setup import (
    confirmed, folder_profile, owner, prepared,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_v2 import prepared_v2  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running


@pytest.fixture
def build_owner(owner, prepared_v2):
    grant = prepared_v2['contract']
    (owner.root / 'package-lock.json').write_bytes(prepared_v2['inputs']['lockfile_bytes'])
    (owner.root / 'apps/desktop/index.html').write_text('<html><script type="module" src="/src/main.test.tsx"></script></html>')
    (owner.root / 'apps/desktop/vite.config.ts').write_text('export default {}')
    version = grant['dependencies']['toolchain']['tools']['vite']
    for path, data in {
        'node_modules/vite/package.json': raw({'name': 'vite', 'version': version}),
        'node_modules/vite/dist/node/index.js': b'// inert renderer fixture',
    }.items():
        target = owner.dependency / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
    owner.selection = {'projectId': 'notes', 'enabled': True, 'source': deepcopy(grant['source']),
        'profile': deepcopy(grant['profile']),
        'dependencies': {'directory': str(owner.dependency), 'manifest': deepcopy(grant['dependencies']['manifest'])}}
    owner.preview_params = {**owner.preview_params, 'selection': owner.selection}
    return owner


def test_v2_owner_preview_save_and_explicit_objective_require_all_three_checks(build_owner):
    state = setup_running(build_owner)
    assert state.saved['receipt']['candidate']['contract']['version'] == 2
    view = setup.prepared_validation_setup(build_owner.home, build_owner.identity['organizationId'])
    assert view['projects'][0]['preparedProfileVersion'] == 2
    with state.store._connect() as conn:
        checks = json.loads(conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?',
                                         (state.objective,)).fetchone()[0])
        assert set(checks) == {'project_typecheck', 'project_tests', 'project_build'}
        assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 0
        from eidolon_cli.organization_prepared_validation_authority import require_prepared_authority
        authority = require_prepared_authority(state.store, conn, state.objective, 'notes', execution=True)
        assert authority['contract']['profile']['version'] == 2


def test_v2_current_scope_cannot_drop_build_check(build_owner):
    state = setup_running(build_owner)
    from eidolon_cli.organization_prepared_validation_objectives import validate_selected_prepared_checks
    with state.store._connect() as conn:
        projects = state.store._execution_projects(conn, state.objective)
        with pytest.raises(ValueError, match='mandatory'):
            validate_selected_prepared_checks(conn, state.objective, projects, ['project_typecheck', 'project_tests'])
        validate_selected_prepared_checks(conn, state.objective, projects,
                                          ['project_typecheck', 'project_tests', 'project_build'])


def build_host(monkeypatch, state, intervene=None):
    from tests.eidolon_cli import test_organization_prepared_validation_stage_runtime as fixture
    from tests.eidolon_cli.test_organization_project_workspace_build import build_receipt_fixture
    from eidolon_cli import organization_project_workspace_node as runtime
    original = fixture.fixture
    monkeypatch.setattr(fixture, 'fixture', lambda recipe: build_receipt_fixture()
                        if recipe == 'workspace_vite_build' else original(recipe))
    calls = []

    def run(source, directory, manifest, toolchain, profile, step_id, **options):
        assert fixture.scalar(state, 'SELECT count(*) FROM prepared_validation_steps') == 3
        assert options['check_authorized']() is True
        receipt = fixture.synthetic_receipt(source, profile, step_id, options)
        calls.append(receipt)
        if intervene is not None:
            intervene(receipt, options, len(calls))
        for event in fixture.events_for(receipt):
            assert options['record_event'](event) is True
        return receipt

    monkeypatch.setattr(runtime, 'run_prepared_workspace_step', run)
    monkeypatch.setattr(runtime, '_docker', lambda *a, **k: pytest.fail('Synthetic lifecycle cannot call Docker'))
    return calls


def test_synthetic_three_recipe_set_finishes_reviews_accepts_and_reopens(build_owner, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA, review_result, finish_acceptance
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
    criteria = [*CRITERIA, {'id': 'renderer', 'kind': 'authorized_check', 'checkKind': 'project_build',
        'projectId': 'notes', 'text': 'Validate the renderer build and retain metadata only.'}]
    state = setup_running(build_owner, acceptance_criteria=criteria, full_planning=True)
    calls = build_host(monkeypatch, state)
    assert state.store.run_project_stage(state.claim, state.cancel) == {}
    assert [item['recipe'] for item in calls] == ['workspace_typescript', 'workspace_vitest_ui', 'workspace_vite_build']
    assert state.store.finish(state.claim, {})
    review = state.store.claim_next(recoverable=True)
    assert review['type'] == 'request.test_review'
    assert state.store.finish(review, review_result(state, review))
    finish_acceptance(state.store)
    reopened = OrganizationStore(state.store.path, state.store.settings)
    with reopened._connect() as conn:
        verified = verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
        assert len(verified['checks']) == 3
        build = next(artifact for _, artifact in verified['checks'] if artifact['projectExecution']['checkKind'] == 'project_build')
        assert build['projectExecution']['buildOutputs']['outputBodiesRetained'] is False
        assert 'outputManifest' not in build['content']
        assert conn.execute('SELECT sum(units) FROM prepared_validation_steps').fetchone()[0] == 3
        from eidolon_cli.organization_prepared_validation_status import prepared_validation_status
        from tests.eidolon_cli.test_organization_prepared_validation_status import forbid_payload_reads
        with forbid_payload_reads(conn) as denied:
            status = prepared_validation_status(conn, state.objective)
        assert denied == []
        assert status['contractVersion'] == 2 and status['setCounts'] == {'v1': 0, 'v2': 1}
        assert status['preparedUnits'] == 3 and status['current']['notes']['reservedUnits'] == 3


def test_failed_third_recipe_retains_three_charges_without_success_review(build_owner, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import scalar
    state = setup_running(build_owner)

    def fail_build(receipt, _options, number):
        if number == 3:
            receipt['status'] = 'failed'

    calls = build_host(monkeypatch, state, fail_build)
    result = state.store.run_project_stage(state.claim, state.cancel)
    assert state.store.finish(state.claim, result)
    assert len(calls) == 3
    assert scalar(state, 'SELECT sum(units) FROM prepared_validation_steps') == 3
    assert scalar(state, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(state, "SELECT count(*) FROM requests WHERE type='request.project_failed' AND status='pending_intervention'") == 1
    assert scalar(state, 'SELECT count(*) FROM objective_contract_acceptances') == 0


@pytest.mark.parametrize('change', ['source', 'bundle', 'author', 'registration'])
def test_three_recipe_review_revalidates_current_authority(build_owner, monkeypatch, change):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import review_result, change_current_authority
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import scalar
    state = setup_running(build_owner, full_planning=True)
    build_host(monkeypatch, state)
    assert state.store.run_project_stage(state.claim, state.cancel) == {}
    assert state.store.finish(state.claim, {})
    review = state.store.claim_next(recoverable=True)
    result = review_result(state, review)
    change_current_authority(state, change)
    with pytest.raises(ValueError):
        state.store.finish(review, result)
    assert scalar(state, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 0
    assert scalar(state, 'SELECT sum(units) FROM prepared_validation_steps') == 3
    assert scalar(state, 'SELECT count(*) FROM objective_contract_acceptances') == 0


def test_source_change_after_ui_prevents_build_execution(build_owner, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import scalar
    state = setup_running(build_owner)

    def mutate_source(_receipt, _options, number):
        if number == 2:
            (state.owner.root / 'apps/desktop/index.html').write_text('<html>changed before build</html>')

    calls = build_host(monkeypatch, state, mutate_source)
    with pytest.raises(ValueError):
        state.store.run_project_stage(state.claim, state.cancel)
    assert len(calls) == 2
    assert scalar(state, 'SELECT sum(units) FROM prepared_validation_steps') == 3
    assert scalar(state, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0

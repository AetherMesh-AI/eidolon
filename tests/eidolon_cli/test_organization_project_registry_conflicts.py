"""Project registry source conflicts fence every host until authoritative repair."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml

from eidolon_cli import organization_service as services
from eidolon_cli.config_primitives import InvalidUserConfigError
from eidolon_cli.organization_budget import budget_view
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import resolve_settings
from eidolon_cli.organization_project_setup import project_setup
from eidolon_cli.organization_store import OrganizationStore
from gateway.organization_runtime import GatewayOrganizationRuntime
from tests.organization_project_registry_helpers import (
    PROJECT,
    SECOND,
    edit_source,
    ledger,
    saved,
)
from tests.organization_project_registry_helpers import (
    profile as profile,
)


@pytest.mark.parametrize('conflict', ['id', 'alias', 'root_retarget', 'recipe_change', 'recipe_revoke', 'team_revoke'])
def test_source_conflict_fences_old_new_hosts_and_exact_repair_never_replays(profile, conflict):
    saved(profile)
    original = (profile / 'config.yaml').read_bytes()
    old = services.get_service()
    objective = old.store.create_objective('Inspect registered alpha', project_ids=['alpha'], idempotency_key='inspect')
    claim = old.store.claim_next()
    assert claim is not None
    call_id = old.store.reserve_model_call(claim, provider='synthetic', model='fixture',
                                           input_limit=3072, output_limit=512)
    audit = old.store.execution_audit(claim['id'])
    assert [call['id'] for call in audit['modelCalls']] == [call_id]
    with old.store._connect() as conn:
        budget = budget_view(conn, objective['id'], old.store.settings)
    assert budget['modelCalls'] == 1 and budget['reservedTokens'] == 3072 + 512

    def assert_reservations_retained(current):
        assert current.execution_audit(claim['id']) == audit
        with current._connect() as conn:
            assert budget_view(conn, objective['id'], current.settings) == budget
        retained = next(item for item in current.snapshot()['objectives'] if item['id'] == objective['id'])
        assert retained['usage']['usageComplete'] is False

    observer = OrganizationStore(ledger(profile))
    generation = observer._policy_generation
    assert_reservations_retained(observer)

    def change(raw):
        if conflict == 'id':
            raw['projects'] = [{**SECOND, 'id': PROJECT['id']}]
        elif conflict == 'alias':
            raw['projects'] = [{**PROJECT, 'id': 'yaml-project'}]
        elif conflict == 'root_retarget':
            replacement = profile / 'private-replacement-root'
            replacement.mkdir()
            raw['read_roots'][0] = str(replacement)
        elif conflict == 'recipe_change':
            raw['project_grants'][0]['files'] = ['root0/different.txt']
        elif conflict == 'recipe_revoke':
            raw['project_grants'] = raw['project_grants'][1:]
        else:
            raw['roster'][0]['team'] = 'other-builders'

    edit_source(profile, change)
    host = GatewayOrganizationRuntime(SimpleNamespace(), can_dispatch=lambda: False)
    try:
        host.discover()
        gateway = host._services[profile.resolve()]
        assert gateway.store.settings.project_registry_conflicts
        paused_generation = gateway.store._policy_generation
        assert paused_generation > generation
        setup = project_setup()
        assert setup['blocked'] and 'registry_conflict' in setup['blockers']
        assert setup['ledgerProjects'] == [PROJECT]
        assert 'private-' not in json.dumps(setup)
        assert not old.store.heartbeat(claim)
        assert not old.store.finish(claim, {'summary': 'Stale result', 'deliverable': 'Must not commit'})
        for candidate in [old.store, observer, gateway.store, OrganizationStore(ledger(profile))]:
            assert candidate.claim_next() is None
            with pytest.raises(ValueError):
                candidate.create_objective('Must stay fenced', idempotency_key='blocked-' + str(id(candidate)))
            with pytest.raises(ValueError):
                candidate.resolve(claim['id'], 'retry_configuration', idempotency_key='retry-' + str(id(candidate)))
            with pytest.raises(ValueError, match='no longer owned'):
                candidate.reserve_model_call(claim, provider='synthetic', model='fixture',
                                             input_limit=3072, output_limit=512)
            assert_reservations_retained(candidate)
        # A normal DB reopen or repeated gateway reload cannot silently heal a
        # source conflict by treating persisted overlay entries as YAML entries.
        for _ in range(3):
            host.discover()
            reopened = OrganizationStore(ledger(profile))
            assert reopened.settings.project_registry_conflicts
            assert reopened._policy_generation == paused_generation
            assert reopened.claim_next() is None
            assert_reservations_retained(reopened)
        (profile / 'config.yaml').write_bytes(original)
        host.discover()
        repaired = OrganizationStore(ledger(profile))
        assert not repaired.settings.project_registry_conflicts
        assert repaired._policy_generation > paused_generation
        repaired_generation = repaired._policy_generation
        for _ in range(3):
            host.discover()
            reopened = OrganizationStore(ledger(profile))
            assert reopened._policy_generation == repaired_generation
            assert_reservations_retained(reopened)
        assert [asdict(item) for item in repaired.settings.projects] == [PROJECT]
        retained = next(item for item in repaired.snapshot()['objectives'] if item['id'] == objective['id'])
        assert retained['projects'] == objective['projects']
        request = next(item for item in repaired.snapshot()['requests'] if item['id'] == claim['id'])
        assert request['status'] == 'pending_intervention' and request['attempts'] == claim['attempts']
        assert repaired.claim_next() is None
        assert repaired.snapshot()['knowledge'] == []
        assert_reservations_retained(repaired)
    finally:
        assert host.stop()


def test_repeated_resolution_preserves_provenance_but_actual_yaml_collision_stays_visible(profile):
    saved(profile)
    service = services.get_service()
    with service.store._connect() as conn:
        effective = service.settings
        for _ in range(3):
            effective = resolve_settings(conn, effective)
            assert [asdict(item) for item in effective.projects] == [PROJECT]
            assert not effective.project_registry_conflicts
        raw = yaml.safe_load((profile / 'config.yaml').read_text())
        raw['organization']['projects'] = [PROJECT]
        # Provenance is internal: user-supplied lookalike fields never authorize
        # erasing a real YAML collision before it is checked against the ledger.
        raw['organization']['ledger_project_ids'] = ['alpha']
        raw['organization']['project_registry_conflicts'] = []
        actual_yaml = OrganizationSettings.from_config(raw)
        assert not actual_yaml.ledger_project_ids
        for _ in range(3):
            actual_yaml = resolve_settings(conn, actual_yaml)
            assert actual_yaml.project_registry_conflicts


def test_registry_repair_cannot_clear_an_independent_invalid_yaml_pause(profile):
    saved(profile)
    original = (profile / 'config.yaml').read_bytes()
    service = services.get_service()
    edit_source(profile, lambda raw: raw.update(projects=[PROJECT]))
    service.reload_profile_configuration()
    assert service.store.settings.project_registry_conflicts
    (profile / 'config.yaml').write_text('organization: [broken\n')
    with pytest.raises(InvalidUserConfigError):
        service.reload_profile_configuration()
    paused = OrganizationStore(ledger(profile))
    with paused._connect() as conn:
        assert conn.execute('SELECT 1 FROM organization_policy_pause').fetchone()
    # Reading or reopening the last persisted seed cannot prove malformed YAML
    # was repaired. Only a successful authoritative source reload can do that.
    assert paused.claim_next() is None
    with pytest.raises(ValueError):
        paused.create_objective('Cannot bypass unreadable authority', idempotency_key='paused')
    (profile / 'config.yaml').write_bytes(original)
    assert OrganizationStore(ledger(profile)).claim_next() is None
    service.reload_profile_configuration()
    repaired = OrganizationStore(ledger(profile))
    with repaired._connect() as conn:
        assert repaired._policy_current(conn)
        assert conn.execute('SELECT 1 FROM organization_policy_pause').fetchone() is None
    assert not repaired.settings.project_registry_conflicts

"""Private and atomic ledger-only project registration requires current owner review."""
import json
import sqlite3
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_project_setup import (
    project_draft,
    project_save,
    project_setup,
)
from eidolon_cli.organization_store import OrganizationStore
from gateway.organization_runtime import GatewayOrganizationRuntime
from tests.organization_project_registry_helpers import (
    PROJECT,
    SECOND,
    dump,
    ledger,
    saved,
)
from tests.organization_project_registry_helpers import (
    profile as profile,
)


def test_first_save_accepts_reviewed_cold_revision_and_never_writes_yaml(profile):
    original = (profile / 'config.yaml').read_bytes()
    setup = project_setup()
    assert not ledger(profile).exists()
    draft = project_draft(PROJECT, setup['revision'])
    result = project_save(draft['project'], draft['revision'], 'cold-save', confirm_save=True)
    assert result['saved'] is True and result['project'] == PROJECT
    assert (profile / 'config.yaml').read_bytes() == original
    assert project_setup()['projects'] == [PROJECT]


def test_registration_is_private_atomic_and_retry_has_no_mutation(profile):
    source = (profile / 'config.yaml').read_bytes()
    revision, result = saved(profile)
    service = services.get_service()
    before = dump(profile)
    assert project_save(PROJECT, revision, 'register-alpha', confirm_save=True) == result
    assert dump(profile) == before
    with pytest.raises(ValueError, match='different draft'):
        project_save(SECOND, revision, 'register-alpha', confirm_save=True)
    assert dump(profile) == before
    with sqlite3.connect(ledger(profile)) as conn:
        registrations = conn.execute('SELECT project,binding_hash FROM organization_project_registry').fetchall()
        receipts = conn.execute('SELECT result FROM organization_project_registration_receipts').fetchall()
    assert [json.loads(row[0]) for row in registrations] == [PROJECT]
    assert [json.loads(row[0]) for row in receipts] == [result]
    assert len(registrations[0][1]) == 64
    public = json.dumps([registrations, receipts, project_setup()])
    assert 'private-' not in public and str(profile) not in public
    assert (profile / 'config.yaml').read_bytes() == source
    assert not service.running
    assert [asdict(item) for item in service.settings.projects] == [PROJECT]
    assert service.settings == service.store.settings


@pytest.mark.parametrize('confirmation', [False, None, 'true', 1])
def test_save_requires_deliberate_boolean_confirmation(profile, confirmation):
    revision = project_setup()['revision']
    source = (profile / 'config.yaml').read_bytes()
    with pytest.raises(ValueError, match='confirm'):
        project_save(PROJECT, revision, 'unconfirmed', confirm_save=confirmation)
    assert not ledger(profile).exists()
    assert (profile / 'config.yaml').read_bytes() == source


@pytest.mark.parametrize('change', ['yaml', 'policy', 'open_objective', 'open_without_request'])
def test_stale_drafts_and_open_work_never_register(profile, change):
    service = services.get_service()
    revision = project_setup()['revision']
    if change == 'yaml':
        with (profile / 'config.yaml').open('a') as stream:
            stream.write('# Owner edited this file after reviewing the draft.\n')
    elif change == 'policy':
        configuration = service.store.configuration_snapshot()
        configuration['max_inflight'] += 1
        service.store.configure_organization(configuration, expected_generation=service.store._policy_generation,
                                             idempotency_key='concurrent-policy')
    else:
        objective = service.store.create_objective('Unfinished synthetic work', idempotency_key='unfinished')
        if change == 'open_without_request':
            with service.store._write() as conn:
                conn.execute("UPDATE requests SET status='completed' WHERE objective_id=?", (objective['id'],))
    before = dump(profile)
    source = (profile / 'config.yaml').read_bytes()
    with pytest.raises(ValueError, match='changed|open objectives'):
        project_save(PROJECT, revision, 'stale-save', confirm_save=True)
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source


def test_reopen_get_service_and_gateway_reload_keep_the_same_ledger_identity(profile):
    _, result = saved(profile)
    source = (profile / 'config.yaml').read_bytes()
    initial = services.get_service()
    generation = initial.store._policy_generation
    assert [asdict(item) for item in OrganizationStore(ledger(profile)).settings.projects] == [PROJECT]
    assert services.stop_services()
    services._services.clear()
    reopened = services.get_service()
    assert reopened is not initial
    host = GatewayOrganizationRuntime(SimpleNamespace(), can_dispatch=lambda: False)
    try:
        for _ in range(3):
            host.discover()
            gateway = host._services[profile.resolve()]
            assert gateway.running
            assert [asdict(item) for item in gateway.settings.projects] == [PROJECT]
            assert gateway.store._policy_generation == generation
            assert project_setup()['revision'] == result['revision']
        assert [asdict(item) for item in reopened.settings.projects] == [PROJECT]
        assert (profile / 'config.yaml').read_bytes() == source
    finally:
        assert host.stop()


def test_owner_capacity_edit_preserves_registration_and_team_removal_is_atomic(profile):
    saved(profile)
    service = services.get_service()
    config = service.store.configuration_snapshot()
    config['max_inflight'] += 1
    generation = service.store._policy_generation
    service.store.configure_organization(config, expected_generation=generation, idempotency_key='capacity')
    fresh = OrganizationStore(ledger(profile))
    assert fresh.settings.max_inflight == config['max_inflight']
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]
    assert not fresh.settings.project_registry_conflicts
    assert fresh._policy_generation > generation
    fresh.reload_profile_configuration(profile)
    assert not fresh.settings.project_registry_conflicts
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]
    config = fresh.configuration_snapshot()
    config['roster'][0]['team'] = 'other-team'
    before = dump(profile)
    with pytest.raises(ValueError, match='project|Project'):
        fresh.configure_organization(config, expected_generation=fresh._policy_generation,
                                     idempotency_key='revoke-project-team')
    assert dump(profile) == before
    assert [asdict(item) for item in fresh.settings.projects] == [PROJECT]

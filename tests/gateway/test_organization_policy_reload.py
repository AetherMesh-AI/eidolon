"""Live config changes share durable fences with every profile-local runtime."""
from tests.organization_package_helpers import claim_after_decomposition
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.config import InvalidUserConfigError
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from gateway.organization_runtime import GatewayOrganizationRuntime


def wait_for(check):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError('The live policy transition did not complete')


def configure(home, raw):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text(json.dumps({'organization': raw}))


@pytest.mark.parametrize('revocation', ['tool', 'project'])
def test_live_revocation_fences_peers_without_replaying_or_releasing_live_capacity(tmp_path, monkeypatch, revocation):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'README.md').write_text('Synthetic project source\n')
    raw = {'gateway_enabled': True, 'max_inflight': 1, 'capabilities': ['work.inspect'],
           'tool_grants': ['read_file'], 'read_roots': [str(project)],
           'project_grants': [{'id': 'checks', 'files': ['root0/README.md'], 'execution': {'root': 'root0'}}],
           'projects': [{'id': 'source', 'root': 'root0', 'recipe': 'checks', 'team': 'engineering'}],
           'roster': [{'id': name, 'name': name, 'team': 'engineering', 'capabilities': ['work.inspect'],
                       'tool_grants': ['read_file']} for name in ['reader-a', 'reader-b']]}
    configure(tmp_path, raw)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', OrganizationSettings.from_config({'organization': raw}))
    retained_roster = [{**member, 'name': 'Owner-named ' + member['id']} for member in raw['roster']]
    store.configure_organization({'roster': retained_roster, 'max_inflight': 2, 'max_members': 20},
                                expected_generation=store._policy_generation, idempotency_key='owner-roster')
    store.create_objective('Inspect selected source', project_ids=['source'], idempotency_key='source')
    assert store.finish(claim_after_decomposition(store), {'workers': 2, 'tasks': [
        {'title': 'Inspect ' + name, 'description': 'Read README for ' + name, 'type': 'work.inspect',
         'team': 'engineering', 'projectId': 'source', 'agentId': name} for name in ['reader-a', 'reader-b']]})
    release = threading.Event()
    calls = []

    def execute(request, context, cancel):
        receipt = context['recordToolStart']('first-read', 'read_file', {'path': 'root0/README.md'})
        calls.append((request, context, cancel, receipt))
        assert release.wait(120)
        return {'summary': 'Late answer', 'deliverable': 'Must never be accepted'}

    monkeypatch.setattr(services, '_execute', execute)
    desktop = services.OrganizationService(OrganizationStore(store.path), home=tmp_path,
                 settings=replace(store.settings, max_inflight=1), executor=execute, poll_seconds=0.01)
    host = GatewayOrganizationRuntime(SimpleNamespace())
    try:
        desktop.start()
        wait_for(lambda: len(calls) == 1)
        host.discover()
        wait_for(lambda: len(calls) == 2)
        gateway = host._services[tmp_path.resolve()]
        generation = gateway.store._policy_generation
        revoked = dict(raw)
        if revocation == 'tool':
            revoked.update(tool_grants=[], roster=[{**member, 'tool_grants': []} for member in raw['roster']])
        else:
            revoked.update(projects=[], project_grants=[])
        configure(tmp_path, revoked)
        host.discover()
        desktop.refresh_configuration()
        assert all(cancel.is_set() for _, _, cancel, _ in calls)
        changed = OrganizationStore(store.path)
        assert changed._policy_generation > generation
        assert changed.settings.max_inflight == 2 and changed.settings.max_members == 20
        assert {member.name for member in changed.settings.roster} == {member['name'] for member in retained_roster}
        if revocation == 'tool':
            assert changed.settings.tool_grants == ()
        else:
            assert changed.settings.projects == changed.settings.project_grants == ()
        host.discover()
        assert OrganizationStore(store.path)._policy_generation == changed._policy_generation
        assert host._services[tmp_path.resolve()] is gateway
        assert host.active_execution_count == desktop.active_execution_count == 1
        peer = services.OrganizationService(changed, home=tmp_path)
        assert peer._execution_slot() is None
        for claim, context, _, receipt in calls:
            with services._ExecutionLock(tmp_path / 'organization' / 'execution-locks', claim['id']) as acquired:
                assert not acquired
            with services._ExecutionLock(tmp_path / 'organization' / 'agent-locks', claim['agent_id']) as acquired:
                assert not acquired
            with pytest.raises(ValueError):
                context['recordToolStart']('late-read', 'read_file', {'path': 'root0/README.md'})
            with pytest.raises(ValueError):
                context['recordToolFinish'](receipt['id'], json.dumps({'success': True, 'content': 'late'}), 'completed')
            with pytest.raises(ValueError):
                context['reserveModelCall'](provider='synthetic', model='synthetic', input_limit=4096, output_limit=256)
            assert not changed.finish(claim, {'summary': 'Late', 'deliverable': 'Must not commit'})
        with changed._connect() as conn:
            assert {row[0] for row in conn.execute('SELECT status FROM tool_receipts')} == {'unknown'}
        configure(tmp_path, raw)
        host.discover()
        desktop.refresh_configuration()
        restored = OrganizationStore(store.path)
        assert restored._policy_generation > changed._policy_generation
        for claim, _, _, _ in calls:
            with pytest.raises(ValueError, match='still active'):
                peer.resolve(claim['id'], action='retry_configuration', idempotency_key='too-early-' + claim['id'])
        release.set()
        wait_for(lambda: host.active_execution_count == desktop.active_execution_count == 0)
        desktop.refresh_configuration()
        gateway.refresh_configuration()
        assert len(calls) == 2
        assert restored.snapshot()['knowledge'] == []
        rows = {row['id']: row for row in restored.snapshot()['requests']}
        for claim, _, _, _ in calls:
            assert rows[claim['id']]['status'] == 'pending_intervention'
            assert rows[claim['id']]['attempts'] == claim['attempts']
        assert restored.claim_next() is None
    finally:
        release.set()
        assert host.stop()
        assert desktop.stop()


def test_invalid_profile_config_pauses_desktop_peer_but_other_profile_recovers(tmp_path, monkeypatch):
    from eidolon_cli import profiles
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: tmp_path)
    good = tmp_path / 'profiles' / 'good'
    configure(tmp_path, {'gateway_enabled': True, 'max_inflight': 1})
    configure(good, {'gateway_enabled': True})
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', replace(OrganizationSettings(), max_inflight=1))
    store.create_objective('Unconfirmed first plan', idempotency_key='first')
    store.create_objective('Queued second plan', idempotency_key='second')
    sibling = OrganizationStore(good / 'organization' / 'state.db')
    release, entered = threading.Event(), threading.Event()
    calls, cancellations = [], []

    def execute(request, context, cancel):
        calls.append(request['id'])
        if context['objective']['title'] == 'Unconfirmed first plan':
            cancellations.append(cancel)
            entered.set()
            assert release.wait(120)
            return {'tasks': [{'title': 'Late plan', 'description': 'Must not commit', 'type': 'work.analyze'}]}
        return {'intervention': 'Synthetic stop'}

    monkeypatch.setattr(services, '_execute', execute)
    desktop = services.OrganizationService(store, home=tmp_path, executor=execute, poll_seconds=0.01)
    host = GatewayOrganizationRuntime(SimpleNamespace(multiplex_profiles=True, multiplex_profile_allowlist=['good']),
                                       discovery_seconds=0.05)
    try:
        desktop.start()
        assert entered.wait(60)
        # A newly started gateway cannot infer opt-in from malformed YAML, but
        # the existing desktop ledger must still lose its stale authority.
        (tmp_path / 'config.yaml').write_text('organization: [')
        host.discover()
        desktop.refresh_configuration()
        assert tmp_path.resolve() not in host._services
        assert cancellations[0].is_set()
        cold_paused = OrganizationStore(store.path)
        assert cold_paused.claim_next() is None
        configure(tmp_path, {'gateway_enabled': False, 'max_inflight': 1})
        host._failures.clear()
        host.discover()
        first_repair = OrganizationStore(store.path)
        with first_repair._connect() as conn:
            assert first_repair._policy_current(conn)
        assert not host._services.get(tmp_path.resolve())
        # Semantically invalid but parseable configuration cannot look like a
        # harmless opt-out, even in another gateway with no cached service.
        configure(tmp_path, [])
        cold_host = GatewayOrganizationRuntime(SimpleNamespace())
        try:
            cold_host.discover()
            assert not cold_host._services
            semantic_pause = OrganizationStore(store.path)
            assert semantic_pause._policy_generation > first_repair._policy_generation
            assert semantic_pause.claim_next() is None
            configure(tmp_path, {'gateway_enabled': False, 'max_inflight': 1})
            cold_host._failures.clear()
            cold_host.discover()
            repaired = OrganizationStore(store.path)
            with repaired._connect() as conn:
                assert repaired._policy_current(conn)
            assert not cold_host._services
            cold_host.discover()
            assert OrganizationStore(store.path)._policy_generation == repaired._policy_generation
        finally:
            assert cold_host.stop()
        configure(tmp_path, {'gateway_enabled': True, 'max_inflight': 1})
        host._failures.clear()
        host.discover()
        gateway = host._services[tmp_path.resolve()]
        configure(tmp_path, {'gateway_enabled': True, 'read_roots': ['relative/unsafe']})
        host.discover()
        desktop.refresh_configuration()
        assert gateway._stop.is_set()
        assert cancellations[0].is_set()
        assert desktop.active_execution_count == 1
        assert store.claim_next() is None
        reopened = OrganizationStore(store.path)
        paused_generation = reopened._policy_generation
        assert reopened.claim_next() is None
        with pytest.raises(ValueError):
            reopened.create_objective('Blocked while invalid', idempotency_key='blocked')
        sibling.create_objective('Other profile remains live', idempotency_key='sibling')
        wait_for(lambda: sibling.snapshot()['requests'][0]['status'] == 'pending_intervention')
        assert not host._services[good.resolve()]._stop.is_set()
        release.set()
        wait_for(lambda: desktop.active_execution_count == 0)
        assert store.snapshot()['tasks'] == []
        assert any(row['status'] == 'queued' and row['attempts'] == 0 for row in store.snapshot()['requests'])
        # A parse failure must remain fail-closed across a read-only reopen too.
        (tmp_path / 'config.yaml').write_text('organization: [')
        host._failures.clear()
        host.discover()
        still_paused = OrganizationStore(store.path)
        assert still_paused._policy_generation == paused_generation
        assert still_paused.claim_next() is None
        with pytest.raises(InvalidUserConfigError):
            services.get_service()
        configure(tmp_path, {'gateway_enabled': True, 'max_inflight': 1})
        host._failures.clear()
        host.discover()
        wait_for(lambda: all(row['status'] == 'pending_intervention' for row in store.snapshot()['requests']))
        assert len(calls) == len(set(calls)) == 3
        assert store.snapshot()['tasks'] == []
    finally:
        release.set()
        assert host.stop()
        assert desktop.stop()

"""Real durable organization admission hosted independently of a desktop client."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from gateway.organization_runtime import GatewayOrganizationRuntime, stop_gateway_organization


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError('Organization lifecycle did not reach the expected state')


def configure(home, enabled=True, **organization):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text(json.dumps({'organization': {
        'gateway_enabled': enabled, **organization}}))


def ledger(home, title='Queued while desktop is closed'):
    store = OrganizationStore(home / 'organization' / 'state.db')
    store.create_objective(title, idempotency_key=title)
    return store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    configure(tmp_path)
    return tmp_path


def runtime(config=None):
    return GatewayOrganizationRuntime(config or SimpleNamespace(), discovery_seconds=0.05)


def test_gateway_only_completes_reviewed_objective(home, monkeypatch):
    store = ledger(home)
    stages = []

    def execute(request, context, cancel):
        kind = request['type']
        stages.append(kind)
        evidence = [item['id'] for item in context.get('evidence', [])]
        if kind == 'request.plan':
            return {'tasks': [{'title': 'Analyze', 'description': 'Analyze supplied facts', 'type': 'work.analyze'}]}
        if kind == 'work.analyze':
            return {'summary': 'Analysis', 'deliverable': 'Retained analysis of supplied facts.'}
        if kind == 'request.review':
            return {'approved': True, 'summary': 'Reviewed exact output', 'evidenceIds': evidence}
        if kind == 'request.integrate':
            return {'summary': 'Integrated result', 'deliverable': 'Final supported result.'}
        if kind == 'request.accept':
            return {'approved': True, 'summary': 'Accepted', 'evidenceIds': evidence, 'conflicts': [],
                    'criteriaResults': [{'criterion': item, 'satisfied': True, 'evidenceIds': evidence,
                                         'reason': 'Supported by retained output'}
                                        for item in context['objective']['acceptanceCriteria']]}
        raise AssertionError(kind)

    monkeypatch.setattr(services, '_execute', execute)
    host = runtime()
    try:
        host.start()
        wait_for(lambda: store.snapshot()['objectives'][0]['status'] == 'completed')
        assert stages == ['request.plan', 'work.analyze', 'request.review', 'request.integrate', 'request.accept']
        reopened = OrganizationStore(store.path)
        assert reopened.snapshot()['objectives'][0]['status'] == 'completed'
    finally:
        assert host.stop()


@pytest.mark.parametrize('enabled', [False, None, 'true', 1])
def test_background_execution_requires_explicit_boolean_opt_in(home, monkeypatch, enabled):
    configure(home, enabled)
    store = ledger(home)
    calls = []
    monkeypatch.setattr(services, '_execute', lambda *args: calls.append(args))
    host = runtime()
    try:
        host.discover()
        assert not host._services
        assert calls == []
        assert store.snapshot()['requests'][0]['status'] == 'queued'
    finally:
        assert host.stop()


def test_discovers_first_ledger_created_after_gateway_start(home, monkeypatch):
    calls = []
    monkeypatch.setattr(services, '_execute', lambda request, *args:
                        calls.append(request['id']) or {'intervention': 'Deterministic stop'})
    host = runtime()
    try:
        host.discover()
        assert not (home / 'organization').exists()
        store = ledger(home)
        host.start()
        wait_for(lambda: bool(calls))
        wait_for(lambda: store.snapshot()['requests'][0]['status'] == 'pending_intervention')
        assert len(calls) == 1
    finally:
        assert host.stop()


def test_new_allowlisted_profile_uses_own_secrets_and_removed_profile_stops(home, monkeypatch):
    from agent.secret_scope import get_secret
    from eidolon_constants import get_eidolon_home
    from eidolon_cli import profiles
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: home)
    seen = []
    monkeypatch.setattr(services, '_execute', lambda *args:
                        seen.append((get_eidolon_home().resolve(), get_secret('OPENAI_API_KEY')))
                        or {'intervention': 'Deterministic stop'})
    host = runtime(SimpleNamespace(multiplex_profiles=True, multiplex_profile_allowlist=['alpha']))
    try:
        host.discover()
        alpha, beta = (home / 'profiles' / name for name in ('alpha', 'beta'))
        for directory in (alpha, beta):
            configure(directory)
            (directory / '.env').write_text('OPENAI_API_KEY=' + directory.name + '-test-only\n')
            ledger(directory, directory.name)
        host.discover()
        wait_for(lambda: bool(seen))
        assert seen == [(alpha.resolve(), 'alpha-test-only')]
        assert OrganizationStore(beta / 'organization' / 'state.db').snapshot()['requests'][0]['status'] == 'queued'
        host.allowlist = []
        host.discover()
        assert host._services[alpha.resolve()]._stop.is_set()
    finally:
        assert host.stop()


def test_disabling_background_execution_fences_live_result_and_preserves_queue(home, monkeypatch):
    store = ledger(home, 'First')
    store.create_objective('Second', idempotency_key='second')
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(request, context, cancel):
        calls.append(request['id'])
        entered.set()
        assert cancel.wait(5)
        assert release.wait(5)
        return {'tasks': [{'title': 'Late', 'description': 'Must not commit', 'type': 'work.analyze'}]}

    monkeypatch.setattr(services, '_execute', execute)
    host = runtime()
    try:
        host.discover()
        assert entered.wait(5)
        configure(home, False)
        host.discover()
        wait_for(lambda: any(row['status'] == 'pending_intervention' for row in store.snapshot()['requests']))
        release.set()
        wait_for(lambda: not host._services[home.resolve()].running)
        host.discover()
        assert len(calls) == 1
        assert store.snapshot()['tasks'] == []
        assert any(row['status'] == 'queued' for row in store.snapshot()['requests'])
    finally:
        release.set()
        assert host.stop()


def test_concurrent_desktop_and_gateway_share_capacity_and_shutdown_is_local(home, monkeypatch):
    configure(home, max_inflight=1)
    settings = replace(OrganizationSettings(), max_inflight=1)
    store = OrganizationStore(home / 'organization' / 'state.db', settings)
    store.create_objective('Desktop owned', idempotency_key='one')
    store.create_objective('Gateway queued', idempotency_key='two')
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(request, context, cancel):
        calls.append(request['id'])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {'intervention': 'Deterministic stop'}

    desktop = services.OrganizationService(store, home=home, executor=execute, poll_seconds=0.01)
    monkeypatch.setattr(services, '_execute', execute)
    host = runtime()
    try:
        desktop.start()
        assert entered.wait(5)
        host.discover()
        gateway = host._services[home.resolve()]
        gateway._fill_slots()
        assert not gateway._running
        assert len(calls) == 1
        # Gateway stopping cannot cancel the desktop's independent service.
        assert host.stop()
        assert desktop.running and not desktop._stop.is_set()
        # Desktop close fences its call, never replays it; a new gateway may
        # continue the remaining queued objective only after the live call exits.
        assert not desktop.stop(timeout=0)
        second = runtime()
        try:
            second.discover()
            assert len(calls) == 1
            release.set()
            wait_for(lambda: len(calls) == 2)
            assert len(set(calls)) == 2
            assert store.snapshot()['tasks'] == []
        finally:
            assert second.stop()
    finally:
        release.set()
        assert desktop.stop()
        assert host.stop()


def test_provider_offline_does_not_replay_or_reset_attempts_on_discovery(home, monkeypatch):
    store = ledger(home)
    calls = []

    def offline(request, context, cancel):
        calls.append(request['id'])
        raise ConnectionError('private provider URL must not escape')

    monkeypatch.setattr(services, '_execute', offline)
    host = runtime()
    try:
        host.discover()
        wait_for(lambda: store.snapshot()['requests'][0]['status'] == 'pending_intervention')
        for _ in range(5):
            host.discover()
        row = store.snapshot()['requests'][0]
        assert row['attempts'] == 1 and len(calls) == 1
        assert 'private provider' not in row['reason']
        assert host.stop()
        second = runtime()
        try:
            second.discover()
            assert len(calls) == 1
            assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
        finally:
            assert second.stop()
    finally:
        assert host.stop()


def test_recovery_failure_backs_off_before_retrying_discovery(home, monkeypatch):
    host = runtime()
    calls = []
    real = host._discover_home
    clock = [100.0]
    monkeypatch.setattr('gateway.organization_runtime.time.monotonic', lambda: clock[0])

    def fail_once(profile):
        calls.append(profile)
        if len(calls) < 3:
            raise OSError('private path')
        return real(profile)

    monkeypatch.setattr(host, '_discover_home', fail_once)
    try:
        host.discover()
        host.discover()
        assert len(calls) == 1
        first_deadline = host._failures[home.resolve()][1]
        clock[0] = first_deadline
        host.discover()
        second_deadline = host._failures[home.resolve()][1]
        assert second_deadline - first_deadline == pytest.approx(2 * host.discovery_seconds)
        clock[0] = second_deadline
        host.discover()
        assert len(calls) == 3 and not host._failures
    finally:
        assert host.stop()


@pytest.mark.asyncio
async def test_async_shutdown_remains_responsive(home, monkeypatch):
    host = runtime()
    entered, release = threading.Event(), threading.Event()
    original = host.stop

    def slow_stop(timeout):
        entered.set()
        release.wait(5)
        return original(timeout)

    monkeypatch.setattr(host, 'stop', slow_stop)
    task = asyncio.create_task(stop_gateway_organization(SimpleNamespace(_organization_runtime=host)))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert host._stop.is_set()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        assert await task


def test_invalid_profile_config_pauses_only_its_owned_service(home, monkeypatch):
    from eidolon_cli import profiles
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: home)
    alpha = home / 'profiles' / 'alpha'
    configure(alpha)
    ledger(alpha, 'Alpha')
    ledger(home, 'Root')
    seen = []
    monkeypatch.setattr(services, '_execute', lambda request, context, cancel:
                        seen.append(context['objective']['title']) or {'intervention': 'Deterministic stop'})
    host = runtime(SimpleNamespace(multiplex_profiles=True, multiplex_profile_allowlist=['alpha']))
    try:
        host.discover()
        wait_for(lambda: len(seen) == 2)
        (alpha / 'config.yaml').write_text('organization: [malformed')
        host.discover()
        assert host._services[alpha.resolve()]._stop.is_set()
        assert not host._services[home.resolve()]._stop.is_set()
        assert alpha.resolve() in host._failures
    finally:
        assert host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('startup_fails', [False, True])
async def test_gateway_entry_owns_lifecycle_and_cleans_up_partial_start(home, monkeypatch, startup_fails):
    """Real start_gateway wiring; only messaging/OS/cron boundaries are doubles."""
    from unittest.mock import AsyncMock
    import gateway.run as gateway_run
    from gateway.config import GatewayConfig
    store = ledger(home)
    calls, runners = [], []
    monkeypatch.setattr(services, '_execute', lambda request, *args:
                        calls.append(request['id']) or {'intervention': 'Deterministic stop'})

    class Runner:
        def __init__(self, config):
            self.config = config
            self._running = True
            self._draining = self._external_drain_active = False
            self.should_exit_cleanly = False
            self.adapters = {}
            runners.append(self)

        async def start(self):
            return True

        def _start_systemd_watchdog(self):
            pass

        async def wait_for_shutdown(self):
            deadline = time.monotonic() + 5
            while not calls and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert calls
            assert self._organization_runtime._thread.is_alive()

    monkeypatch.setattr(gateway_run, 'GatewayRunner', Runner)
    monkeypatch.setattr('eidolon_cli.process_identity.register_self', lambda *args: None)
    monkeypatch.setattr('eidolon_cli.process_identity.attach_self_to_kill_on_close_job', lambda: None)
    monkeypatch.setattr('gateway.status.get_running_pid', lambda: None)
    monkeypatch.setattr(gateway_run, '_start_gateway_configure_logging', lambda *args: None)
    monkeypatch.setattr(gateway_run, '_enable_multiplex_log_routing', lambda *args: None)
    monkeypatch.setattr(gateway_run, '_run_planned_stop_watcher', lambda *args: None)
    monkeypatch.setattr(gateway_run, '_start_gateway_claim_pid_file', lambda: True)
    monkeypatch.setattr(gateway_run, '_start_gateway_start_control_socket', AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_run, '_discover_gateway_mcp_tools', AsyncMock())
    monkeypatch.setattr('gateway.lifecycle_ledger.record_startup', lambda: None)
    monkeypatch.setattr('eidolon_cli.nous_auth_keepalive.start_nous_auth_keepalive', lambda: None)
    monkeypatch.setattr('gateway.shutdown_flush.recover_pending_to_db', lambda: 0)
    monkeypatch.setattr(gateway_run, '_ensure_windows_gateway_venv_imports', lambda: None)
    monkeypatch.setattr(gateway_run, '_start_gateway_shutdown_tail', AsyncMock(return_value=True))

    def start_cron(runner):
        if startup_fails:
            raise RuntimeError('Deterministic startup failure')
        return threading.Event(), None, None, None

    monkeypatch.setattr(gateway_run, '_start_gateway_start_cron_and_housekeeping', start_cron)
    # Do not replace process-level signal handling in the pytest host.
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, 'add_signal_handler', lambda *args: None)
    if startup_fails:
        with pytest.raises(RuntimeError, match='Deterministic startup'):
            await gateway_run.start_gateway(GatewayConfig(), verbosity=None)
    else:
        assert await gateway_run.start_gateway(GatewayConfig(), verbosity=None)
        assert len(calls) == 1
    host = runners[0]._organization_runtime
    assert host._stop.is_set()
    assert not host._thread.is_alive()
    assert not any(service.running for service in host._services.values())
    assert store.snapshot()['tasks'] == []


def test_close_admission_alone_rejects_finished_worker_before_shutdown_join(home):
    store = ledger(home)
    entered, release = threading.Event(), threading.Event()

    def execute(request, context, cancel):
        entered.set()
        assert release.wait(5)
        return {'tasks': [{'title': 'Late plan', 'description': 'Must not commit', 'type': 'work.analyze'}]}

    service = services.OrganizationService(store, home=home, executor=execute, poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(5)
        with service._lock:
            service.close_admission()
            release.set()
            for record in service._running.values():
                record.thread.join(5)
                assert not record.thread.is_alive()
            service._maintain()
        assert store.snapshot()['tasks'] == []
        assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
    finally:
        release.set()
        assert service.stop()


def test_close_admission_during_context_read_prevents_executor_dispatch(home, monkeypatch):
    store = ledger(home)
    entered, release = threading.Event(), threading.Event()
    calls = []
    context = store.context

    def gated_context(claim):
        result = context(claim)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(store, 'context', gated_context)
    service = services.OrganizationService(store, home=home,
                executor=lambda *args: calls.append(args), poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(5)
        with service._lock:
            service.close_admission()
            assert all(record.cancel.is_set() for record in service._running.values())
            release.set()
            for record in service._running.values():
                record.thread.join(5)
                assert not record.thread.is_alive()
            assert calls == []
            service._maintain()
        assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
    finally:
        release.set()
        assert service.stop()


@pytest.mark.parametrize('root_from_source', [False, True])
def test_cold_opted_in_profile_hydrates_existing_secret_source_before_dispatch(home, monkeypatch, root_from_source):
    from eidolon_cli import env_loader, profiles
    from agent.secret_scope import get_secret
    from eidolon_constants import get_eidolon_home
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: home)
    hydrated, seen = [], []
    values = {}

    def hydrate(profile):
        hydrated.append(profile)
        values[str(profile.resolve())] = {'OPENAI_API_KEY': profile.name + '-source-test-only',
                                         'ORG_ROOT': str(profile.resolve()), 'ORG_TEAM': profile.name + '-source-team'}
        return values[str(profile.resolve())]

    monkeypatch.setattr(env_loader, 'hydrate_profile_secret_sources', hydrate)
    monkeypatch.setattr(env_loader, 'get_secret_source_values', lambda profile: values.get(str(profile.resolve()), {}))
    monkeypatch.setattr(services, '_execute', lambda *args:
                        seen.append((get_eidolon_home().resolve(), get_secret('OPENAI_API_KEY')))
                        or {'intervention': 'Deterministic stop'})
    alpha, beta = (home / 'profiles' / name for name in ('alpha', 'beta'))
    configure(alpha, team='${ORG_TEAM}', max_inflight=1,
              read_roots=['${ORG_ROOT}' if root_from_source else str(alpha.resolve())])
    configure(beta, False)
    desktop = OrganizationStore(alpha / 'organization' / 'state.db', replace(OrganizationSettings(),
        team='alpha-source-team', max_inflight=1, read_roots=(str(alpha.resolve()),)))
    desktop.create_objective('Already owned by desktop', idempotency_key='desktop')
    desktop_claim = desktop.claim_next()
    ledger(alpha, 'Enabled')
    ledger(beta, 'Disabled')
    host = runtime(SimpleNamespace(multiplex_profiles=True, multiplex_profile_allowlist=['alpha', 'beta']))
    try:
        host.discover()
        assert desktop.heartbeat(desktop_claim), 'Cold discovery must not adopt unresolved placeholder authority'
        assert host._services[alpha.resolve()].store.settings.read_roots == (str(alpha.resolve()),)
        assert desktop.finish(desktop_claim, {'intervention': 'End synthetic desktop execution'})
        wait_for(lambda: bool(seen))
        assert hydrated == [alpha.resolve()]
        assert seen == [(alpha.resolve(), 'alpha-source-test-only')]
        assert not (home / 'organization').exists()
    finally:
        assert host.stop()


def test_gateway_drain_pauses_admission_and_resumes_without_spending_attempts(home, monkeypatch):
    store = ledger(home)
    paused = threading.Event()
    paused.set()
    calls = []
    monkeypatch.setattr(services, '_execute', lambda request, *args:
                        calls.append(request['id']) or {'intervention': 'Deterministic stop'})
    host = GatewayOrganizationRuntime(SimpleNamespace(), discovery_seconds=0.05,
                                       can_dispatch=lambda: not paused.is_set())
    try:
        host.discover()
        service = host._services[home.resolve()]
        service._fill_slots()
        assert calls == []
        row = store.snapshot()['requests'][0]
        assert row['status'] == 'queued' and row['attempts'] == 0
        paused.clear()
        service._wake.set()
        wait_for(lambda: bool(calls))
        wait_for(lambda: store.snapshot()['requests'][0]['status'] == 'pending_intervention')
        assert len(calls) == 1
    finally:
        assert host.stop()


@pytest.mark.asyncio
async def test_live_organization_counts_for_restart_drain_and_prevents_idle_suspend(home, monkeypatch):
    from gateway.run_shutdown import GatewayShutdownMixin
    ledger(home)
    entered, release = threading.Event(), threading.Event()

    def execute(request, context, cancel):
        entered.set()
        assert release.wait(5)
        return {'intervention': 'Deterministic stop'}

    class Runner(GatewayShutdownMixin):
        _restart_after_turn_timeout = 5
        _last_inbound_at = 0
        _running_agents = {}
        adapters = {}

        def _running_agent_count(self): return 0
        def _running_cron_job_count(self): return 0
        def _scale_to_zero_idle_timeout_seconds(self): return 1
        def _scale_to_zero_has_live_background_work(self): return False
        def _scale_to_zero_status(self, *args): pass

    monkeypatch.setattr(services, '_execute', execute)
    monkeypatch.setattr('gateway.scale_to_zero.dashboard_client_last_seen', lambda: None)
    host = runtime()
    runner = Runner()
    runner._organization_runtime = host
    task = None
    try:
        host.discover()
        assert entered.wait(5)
        assert runner._active_work_count() == 1
        assert runner._awaitable_work_count() == 1
        assert not runner._scale_to_zero_is_idle()
        task = asyncio.create_task(runner._await_active_work_before_restart())
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        assert await task
        assert runner._active_work_count() == 0
        assert runner._scale_to_zero_is_idle()
    finally:
        release.set()
        if task is not None:
            await task
        assert host.stop()

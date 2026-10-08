"""Opt-in organization execution hosted by the existing messaging gateway.

This is a lifecycle owner, not a dispatcher: OrganizationService owns admission,
leases, durable budgets and process-independent execution fences. No OS service,
profile ledger, provider credential or notification destination is provisioned.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from pathlib import Path

from agent.secret_scope import (build_profile_secret_scope, reset_secret_scope,
                                reset_secret_scope_required, set_secret_scope,
                                set_secret_scope_required)
from eidolon_constants import get_eidolon_home, reset_eidolon_home_override, set_eidolon_home_override

logger = logging.getLogger(__name__)
_DISCOVERY_SECONDS = 5.0
_MAX_FAILURE_BACKOFF_SECONDS = 60.0


class GatewayOrganizationRuntime:
    """Discover opted-in ledgers off-loop; own only this gateway's schedulers."""

    def __init__(self, config, *, discovery_seconds=_DISCOVERY_SECONDS, can_dispatch=None):
        self.home = Path(get_eidolon_home()).resolve()
        self.can_dispatch = can_dispatch or (lambda: True)
        self.multiplex = getattr(config, 'multiplex_profiles', False) is True
        allowlist = getattr(config, 'multiplex_profile_allowlist', None)
        self.allowlist = list(allowlist) if allowlist is not None else None
        self.discovery_seconds = max(0.05, discovery_seconds)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._services = {}
        self._failures = {}
        self._thread = None
        # Do not inherit any UI/request transport or another profile's secrets.
        self._context = contextvars.Context()
        self._context.run(set_eidolon_home_override, self.home)

    @property
    def active_execution_count(self):
        """No ledger I/O: safe for gateway drain and fail-awake idle predicates."""
        with self._lock:
            services = tuple(self._services.values())
        return sum(service.active_execution_count for service in services)

    def start(self):
        with self._lock:
            if self._stop.is_set():
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._context.run, args=(self._run,),
                                                name='gateway-organization-discovery', daemon=True)
                self._thread.start()

    def close_admission(self):
        """Nonblocking fence usable at the beginning of gateway teardown."""
        self._stop.set()
        with self._lock:
            for service in self._services.values():
                service.close_admission()

    def _homes(self):
        homes = [self.home]
        if self.multiplex:
            from eidolon_cli.profiles import profiles_to_serve
            homes.extend(Path(home).resolve() for _, home in profiles_to_serve(True, self.allowlist))
        return set(homes)

    def _stop_home(self, home):
        with self._lock:
            service = self._services.get(home)
        if service is not None:
            service.close_admission()
            try:
                service.stop(timeout=0)
            except Exception:
                # The stop flag is already set; the service coordinator retries
                # persistence. A busy/corrupt ledger cannot kill discovery.
                logger.warning('Organization stop persistence is pending')

    def _discover_home(self, home):
        from eidolon_cli.config import load_config, require_parseable_user_config
        from eidolon_cli.organization_config import from_config
        from eidolon_cli.organization_service import OrganizationService
        from eidolon_cli.organization_store import OrganizationStore

        # Install a fail-closed secret scope even for the launch profile. Config
        # expansion must never borrow the discovery caller's environment grants.
        home_token = set_eidolon_home_override(home)
        required_token = set_secret_scope_required(True)
        secret_token = None
        try:
            secret_token = set_secret_scope(build_profile_secret_scope(home))
            require_parseable_user_config()
            config = load_config()
            raw = config.get('organization', {})
            enabled = isinstance(raw, dict) and raw.get('gateway_enabled') is True
            path = home / 'organization' / 'state.db'
            if not enabled or not path.is_file():
                self._stop_home(home)
                return
            with self._lock:
                current = self._services.get(home)
            if current is not None:
                if current.running:
                    return  # Also wait for an old disabled call to unwind.
                if not current._stop.is_set():
                    current.start()
                    return
            # Match the gateway's normal cold-profile credential path. Only
            # opted-in existing ledgers may hydrate configured external sources;
            # no process-global environment or new credential is introduced.
            from eidolon_cli.env_loader import hydrate_profile_secret_sources
            hydrate_profile_secret_sources(home)
            set_secret_scope(build_profile_secret_scope(home))
            require_parseable_user_config()
            config = load_config()
            if (not isinstance(config.get('organization'), dict)
                    or config['organization'].get('gateway_enabled') is not True):
                return  # The owner may have disabled execution during hydration.
            # Existing ledgers are the only admission source. Configuration still
            # validates all grants and persisted membership through the real store.
            store = OrganizationStore(path, settings=from_config(config))
            service = OrganizationService(store, settings=store.settings, home=home, can_dispatch=self.can_dispatch)
            with self._lock:
                if self._stop.is_set():
                    return
                self._services[home] = service
                service.start()
        finally:
            if secret_token is not None:
                reset_secret_scope(secret_token)
            reset_secret_scope_required(required_token)
            reset_eidolon_home_override(home_token)

    def discover(self):
        """One bounded discovery pass; exposed for deterministic lifecycle tests."""
        if self._stop.is_set():
            return
        try:
            homes = self._homes()
        except Exception:
            # A failed enumeration cannot establish that any secondary profile
            # remains authorized. Fence rather than silently keep serving it.
            with self._lock:
                homes_to_stop = list(self._services)
            for home in homes_to_stop:
                self._stop_home(home)
            logger.warning('Organization profile discovery failed; background work is paused')
            return
        with self._lock:
            removed = set(self._services) - homes
        for home in removed:
            self._stop_home(home)
        for home in sorted(homes):
            if self._stop.is_set():
                return
            failures, available = self._failures.get(home, (0, 0))
            if time.monotonic() < available:
                continue
            try:
                self._discover_home(home)
                self._failures.pop(home, None)
            except Exception:
                self._stop_home(home)
                delay = min(_MAX_FAILURE_BACKOFF_SECONDS, self.discovery_seconds * 2 ** min(failures, 10))
                self._failures[home] = (failures + 1, time.monotonic() + delay)
                # No exception strings: provider/config errors may include secrets.
                logger.warning('Organization background recovery failed; retrying discovery in %.0fs', delay)

    def _run(self):
        try:
            while not self._stop.is_set():
                self.discover()
                self._stop.wait(self.discovery_seconds)
        finally:
            with self._lock:
                homes = list(self._services)
            for home in homes:
                self._stop_home(home)

    def stop(self, timeout=5.0):
        self.close_admission()
        deadline = time.monotonic() + max(0.0, timeout)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        complete = thread is None or not thread.is_alive()
        with self._lock:
            services = list(self._services.values())
        for service in services:
            complete = service.stop(max(0.0, deadline - time.monotonic())) and complete
        return complete


async def stop_gateway_organization(runner, timeout=5.0):
    runtime = getattr(runner, '_organization_runtime', None)
    if runtime is None:
        return True
    runtime.close_admission()
    try:
        # SQLite persistence and thread joins must not block adapter heartbeats.
        return await asyncio.wait_for(asyncio.to_thread(runtime.stop, timeout), timeout + 0.25)
    except asyncio.TimeoutError:
        logger.warning('Organization execution is still stopping; unresolved outcomes require review')
        return False

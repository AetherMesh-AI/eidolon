"""Updater isolation contracts against genuine imported production modules."""
import contextlib
import importlib
from dataclasses import dataclass, field
import logging
from pathlib import Path
import signal
import sys
import types
import unittest
from unittest.mock import patch

@contextlib.contextmanager
def patch_dependencies(fixtures):
    """Patch attributes on genuine imported modules, restoring every dependency."""
    modules = [(importlib.import_module(name), vars(fixture))
               for name, fixture in fixtures.items()]
    with contextlib.ExitStack() as stack:
        for module, attrs in modules:
            for name, value in attrs.items():
                stack.enter_context(patch.object(module, name, value))
        yield


def fake_module(name, **attrs):
    # A dependency specification, never installed into sys.modules.
    return types.SimpleNamespace(**attrs)


class ProductionTestCase(unittest.TestCase):
    def production(self, module_name, names, **dependencies):
        """Import full production modules and patch only their external seams."""
        module = importlib.import_module(module_name)
        for name, value in dependencies.items():
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return {name: getattr(module, name) for name in names}


class GatewayInstallIsolation(ProductionTestCase):
    def identity(self, *, install='own', home='/sandbox/.eidolon', recorded=10.0,
                 live=10.0, purpose='gateway', profiles=(), denied=False):
        entry = dict(pid=501, install=install, home=home, create_time=recorded, purpose=purpose)
        def process(pid):
            if denied:
                raise PermissionError('fixture')
            return types.SimpleNamespace(create_time=lambda: live, environ=lambda: {'HERMES_HOME': home})
        modules = {
            'psutil': fake_module('psutil', Process=process),
            'eidolon_constants': fake_module('eidolon_constants', get_default_hermes_root=lambda: '/sandbox/.eidolon'),
            'eidolon_cli.profiles': fake_module('eidolon_cli.profiles', list_profiles=lambda: [types.SimpleNamespace(path=p) for p in profiles]),
        }
        ns = self.production('eidolon_cli.process_identity', {'owned_gateway_start_time'},
                       Path=Path, _read_ledger=lambda _: [entry], _ledger_path=lambda: 'fake', install_id=lambda: 'own')
        with patch_dependencies(modules):
            return ns['owned_gateway_start_time'](501)

    def test_default_and_enumerated_profile_owned(self):
        self.assertEqual(self.identity(), 10.0)
        self.assertEqual(self.identity(home='/sandbox/.eidolon/profiles/work',
                                       profiles=['/sandbox/.eidolon/profiles/work']), 10.0)

    def test_foreign_install_home_and_unenumerated_profile_rejected(self):
        for kwargs in ({'install': 'hermes'}, {'home': '/sandbox/.hermes'},
                       {'home': '/sandbox/.eidolon/profiles/unknown'},
                       {'home': '/sandbox/.hermes', 'profiles': ['/sandbox/.hermes']}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.identity(**kwargs))

    def test_unknown_reused_and_invalid_incarnations_fail_closed(self):
        for kwargs in ({'live': 11.0}, {'recorded': None}, {'recorded': float('nan')},
                       {'recorded': True}, {'purpose': 'serve'}, {'denied': True}, {'home': 'relative'}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.identity(**kwargs))

    def test_complete_all_profiles_discovery_filters_service_and_scan_pids(self):
        ns = self.production('eidolon_cli.gateway', {'find_gateway_pids', '_find_gateway_candidate_pids', 'find_gateway_process_identities', '_append_unique_pid'},
                       os=types.SimpleNamespace(getpid=lambda: 1), logging=logging,
                       _get_service_pids=lambda **_: [501, 502], supports_systemd_services=lambda: False,
                       _scan_gateway_pids=lambda *a, **k: [503, 504])
        identity = fake_module('eidolon_cli.process_identity', owned_gateway_identity=lambda p: (10.0, 1000) if p in {501, 503} else None)
        with patch_dependencies({'eidolon_cli.process_identity': identity}):
            self.assertEqual(ns['find_gateway_pids'](all_profiles=True), [501, 503])

    def manual(self, *, identities=None, mapped=False, reuse_on_drain=False):
        calls, reads = [], {}
        identities = {501: (10.0, 1000), 502: None} if identities is None else identities
        def identity(pid):
            reads[pid] = reads.get(pid, 0) + 1
            if reuse_on_drain and reads[pid] >= 2:
                return (99.0, 9900)
            return identities.get(pid)
        profile = types.SimpleNamespace(pid=501, profile='work')
        gateway = fake_module('eidolon_cli.gateway', find_gateway_process_identities=lambda **_: {p: i for p, i in identities.items() if i is not None},
                              find_profile_gateway_processes=lambda **_: [profile] if mapped else [],
                              _prepare_profile_gateway_update_restart=lambda *a, **k: 'detached',
                              _get_service_pids=lambda **_: set(), _wait_for_gateway_exit=lambda **_: None)
        def drain(pid, budget, label, *, identity_check, expected_start_time):
            if identity_check():
                calls.append((pid, 'drain'))
                return True
            return False
        ns = self.production('eidolon_cli.update_cmd_fleet', {'_restart_manual_gateways', '_GatewayRestartOutcome'},
                       dataclass=dataclass, field=field, suppress=contextlib.suppress,
                       os=types.SimpleNamespace(kill=lambda p, s: calls.append((p, s))),
                       _drain_or_signal_gateway_for_update=drain)
        out = ns['_GatewayRestartOutcome'](False, [], None, [], [], [], [], set())
        with patch_dependencies({'eidolon_cli.gateway': gateway,
                       'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=identity),
                       'gateway.status': fake_module('gateway.status', terminate_pid=lambda p, **k: calls.append((p, signal.SIGTERM)))}):
            ns['_restart_manual_gateways'](out, 45)
        return calls, out

    def test_unmapped_foreign_untouched_owned_stopped(self):
        calls, out = self.manual()
        self.assertEqual(calls, [(501, signal.SIGTERM)])
        self.assertEqual(out.gateway_start_times, {501: (10.0, 1000)})
        self.assertEqual(out.killed_pids, {501})

    def test_owned_legacy_gateway_keeps_profile_restart(self):
        calls, out = self.manual(mapped=True)
        self.assertEqual(calls, [(501, 'drain')])
        self.assertEqual(out.relaunched_profiles, ['work'])

    def test_reuse_between_prepare_and_drain_blocks_both_signals(self):
        calls, out = self.manual(mapped=True, reuse_on_drain=True)
        self.assertEqual(calls, [])
        self.assertTrue(out.incomplete)
        self.assertEqual(out.killed_pids, set())

    def test_mapped_foreign_profile_record_cannot_authorize_signal(self):
        calls, out = self.manual(mapped=True, identities={501: None, 502: None})
        self.assertEqual(calls, [])
        self.assertEqual(out.killed_pids, set())

    def test_sigusr1_helpers_recheck_before_signal(self):
        calls = []
        ns = self.production('eidolon_cli.gateway', {'_request_gateway_self_restart', '_graceful_restart_via_sigusr1'},
                       signal=signal, os=types.SimpleNamespace(kill=lambda *a: calls.append(a)),
                       _is_pid_ancestor_of_current_process=lambda _: True, _wait_for_pid_exit=lambda *a: True)
        with patch_dependencies({'gateway.status': fake_module('gateway.status', get_process_start_time=lambda _: 1000)}):
            self.assertFalse(ns['_request_gateway_self_restart'](501, identity_check=lambda: False, expected_start_time=1000))
            self.assertFalse(ns['_graceful_restart_via_sigusr1'](501, 45, identity_check=lambda: False, expected_start_time=1000))
        self.assertEqual(calls, [])

    def test_wedged_recheck_blocks_escalation_after_reuse(self):
        calls, answers = [], iter([True, False])
        ns = self.production('eidolon_cli.gateway', {'_escalate_wedged_gateway'},
                       terminate_pid=lambda *a, **k: calls.append((a, k)), _wait_for_pid_exit=lambda *a: False)
        status = fake_module('gateway.status', get_process_start_time=lambda _: 1000)
        with patch_dependencies({'gateway.status': status}):
            self.assertFalse(ns['_escalate_wedged_gateway'](501, identity_check=lambda: next(answers), expected_start_time=1000))
        self.assertEqual(calls, [((501,), {'force': False, 'expected_start_time': 1000})])

    def test_force_sweep_requires_original_incarnation_and_marker(self):
        for live, marker, expected, count in [((10.0, 1000), 1000, {501: (10.0, 1000)}, 1),
                                             ((11.0, 1100), 1100, {501: (10.0, 1000)}, 0),
                                             (None, 1000, {501: (10.0, 1000)}, 0),
                                             (None, None, {501: (10.0, 1000)}, 0),
                                             ((10.0, 1000), 1000, {}, 0)]:
            calls = []
            ns = self.production('eidolon_cli.update_cmd_fleet', {'_force_kill_stuck_gateways'},
                           suppress=contextlib.suppress, _time=types.SimpleNamespace(sleep=lambda _: None),
                           _best_effort=lambda _: contextlib.nullcontext())
            modules = {
                'eidolon_cli.gateway': fake_module('eidolon_cli.gateway', find_gateway_pids=lambda **_: [501], _get_service_pids=lambda **_: set()),
                'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=lambda _: live),
                'gateway.status': fake_module('gateway.status', get_process_start_time=lambda _: marker, terminate_pid=lambda *a, **k: calls.append((a, k))),
            }
            with self.subTest(live=live, marker=marker, expected=expected), patch_dependencies(modules):
                ns['_force_kill_stuck_gateways']({501}, expected_start_times=expected)
                self.assertEqual(len(calls), count)

    def test_pending_fleet_catchup_rechecks_before_termination(self):
        calls, reads = [], {}
        def identity(pid):
            reads[pid] = reads.get(pid, 0) + 1
            return (11.0, 1100)
        ns = self.production('eidolon_cli.gateway', {'kill_gateway_processes'},
                       logging=logging, find_gateway_process_identities=lambda **_: {501: (10.0, 1000)},
                       terminate_pid=lambda *a, **k: calls.append((a, k)))
        with patch_dependencies({'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=identity)}):
            self.assertEqual(ns['kill_gateway_processes'](all_profiles=True), 0)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Captured-identity propagation and low-level signal contract regressions."""
from pathlib import Path
import signal
import subprocess
import sys
import types
import unittest
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_update_gateway_install_isolation import ProductionTestCase, fake_module, patch_dependencies


class GatewaySignalIdentity(ProductionTestCase):
    def test_restart_watcher_refuses_reuse_and_unknown_old_fleet_entry(self):
        for mode in ('capture-reuse', 'fallback-reuse', 'old-foreign', 'own'):
            current = [(10., 1000) if mode != 'old-foreign' else None]
            calls = []
            def capture(pid):
                if mode == 'capture-reuse':
                    current[0] = (11., 1001)
                return ['python', '-m', 'eidolon_cli.main', 'gateway', 'run']
            def profile(*args, **kwargs):
                self.assertEqual(kwargs["expected_identity"], (10., 1000))
                calls.append('profile')
                if mode == 'fallback-reuse':
                    current[0] = (11., 1001)
                return False
            ns = self.production('eidolon_cli.gateway', {'_prepare_profile_gateway_update_restart'},
                _capture_gateway_argv=capture, launch_detached_profile_gateway_restart=profile,
                launch_detached_gateway_restart_by_cmdline=lambda *a, **kw: calls.append('fallback') or True)
            with patch_dependencies({
                    'eidolon_cli.process_identity': fake_module('identity', owned_gateway_identity=lambda _: current[0], owned_gateway_home=lambda *a, **kw: '/own'),
                    'psutil': fake_module('psutil', Process=lambda _: types.SimpleNamespace(environ=lambda: {'HERMES_HOME': '/own'})),
                    'eidolon_cli.profiles': fake_module('profiles', list_profiles=lambda: [types.SimpleNamespace(name='default', path='/own')])}):
                kwargs = {} if mode == 'old-foreign' else {'expected_identity': (10., 1000)}
                result = ns['_prepare_profile_gateway_update_restart']('default', 501, **kwargs)
            self.assertEqual(calls, ['profile', 'fallback'] if mode == 'own' else
                             ['profile'] if mode == 'fallback-reuse' else [])
            self.assertEqual(result, 'detached-cmdline' if mode == 'own' else None)

    def test_external_supervisor_requires_exact_profile_home(self):
        gateway = self.production('eidolon_cli.gateway', {'_prepare_profile_gateway_update_restart'},
            _capture_gateway_argv=lambda _: ['python', '-m', 'eidolon_cli.main', 'gateway', 'run', '--external-supervisor'],
            launch_detached_profile_gateway_restart=lambda *a, **kw: self.fail('supervised process cannot get a watcher'))
        for home, expected in [('/own/work', 'external-supervisor'), ('/own/other', None), ('', None)]:
            with self.subTest(home=home), patch_dependencies({
                'eidolon_cli.process_identity': fake_module('identity', owned_gateway_identity=lambda _: (10., 1000), owned_gateway_home=lambda *a, **kw: home),
                'eidolon_cli.profiles': fake_module('profiles', list_profiles=lambda: [types.SimpleNamespace(name='work', path='/own/work')]),
                'psutil': fake_module('psutil', Process=lambda _: types.SimpleNamespace(environ=lambda: {'HERMES_HOME': home})),
            }):
                self.assertEqual(gateway['_prepare_profile_gateway_update_restart']('work', 501, expected_identity=(10., 1000)), expected)

    def test_default_profile_ledger_home_authorizes_missing_env_but_not_mismatch(self):
        home = '/sandbox/.eidolon'
        ledger = [dict(pid=501, install='own', home=home, create_time=10., purpose='gateway')]
        self.production('eidolon_cli.process_identity', set(),
            _read_ledger=lambda _: ledger, _ledger_path=lambda: 'fixture', install_id=lambda: 'own')
        calls = []
        def restart(profile, pid, **kwargs):
            calls.append((profile, pid, kwargs.get('expected_identity')))
            return True
        gateway = self.production('eidolon_cli.gateway', {'_prepare_profile_gateway_update_restart'},
            _capture_gateway_argv=lambda _: ['python', '-m', 'eidolon_cli.main', 'gateway', 'run'],
            launch_detached_profile_gateway_restart=restart)
        for env, expected in [({}, 'detached'), ({'HERMES_HOME': home}, 'detached'),
                ({'HERMES_HOME': '/sandbox/.hermes'}, None),
                ({'HERMES_HOME': '/sandbox/.eidolon/profiles/other'}, None)]:
            calls.clear()
            with self.subTest(env=env), patch_dependencies({
                'eidolon_constants': fake_module('constants', get_default_hermes_root=lambda: home),
                'eidolon_cli.profiles': fake_module('profiles', list_profiles=lambda: []),
                'psutil': fake_module('psutil', Process=lambda _: types.SimpleNamespace(create_time=lambda: 10., environ=lambda: env)),
                'gateway.status': fake_module('status', get_process_start_time=lambda _: 1000),
            }):
                self.assertEqual(gateway['_prepare_profile_gateway_update_restart']('default', 501), expected)
            self.assertEqual(calls, [('default', 501, (10., 1000))] if expected else [])

    def test_native_marker_capture_sandwich_rejects_reused_pid(self):
        for identities, marker, expected in [([10., 10.], 1000, (10., 1000)),
                                             ([10., 11.], 1000, None),
                                             ([10.], None, None), ([10.], True, None),
                                             ([None], 1000, None)]:
            sequence = iter(identities)
            ns = self.production('eidolon_cli.process_identity', {'owned_gateway_identity'},
                           owned_gateway_start_time=lambda _, **kwargs: next(sequence))
            with self.subTest(identities=identities, marker=marker), patch_dependencies({
                    'gateway.status': fake_module('gateway.status', get_process_start_time=lambda _: marker)}):
                self.assertEqual(ns['owned_gateway_identity'](501), expected)

    def test_native_marker_read_error_fails_closed(self):
        ns = self.production('eidolon_cli.process_identity', {'owned_gateway_identity'}, owned_gateway_start_time=lambda _, **kwargs: 10.)
        def denied(_):
            raise PermissionError('fixture')
        with patch_dependencies({'gateway.status': fake_module('gateway.status', get_process_start_time=denied)}):
            self.assertIsNone(ns['owned_gateway_identity'](501))

    @pytest.mark.linux_only
    def test_term_and_force_reject_malformed_unavailable_and_changed_original_linux(self):
        self._assert_term_and_force_reject_malformed_unavailable_and_changed_original()

    @pytest.mark.macos_only
    def test_term_and_force_reject_malformed_unavailable_and_changed_original_macos(self):
        self._assert_term_and_force_reject_malformed_unavailable_and_changed_original()

    def _assert_term_and_force_reject_malformed_unavailable_and_changed_original(self):
        for force in (False, True):
            for current, expected in [(1001, 1000), (None, 1000), (1000, 'bad'), (1000, float('nan')), (1000, 0)]:
                calls = []
                ns = self.production('gateway.status', {'terminate_pid', '_start_times_agree'},
                               _get_process_start_time=lambda _: current,
                               os=types.SimpleNamespace(kill=lambda *a: calls.append(a)), signal=signal)
                with self.subTest(force=force, current=current, expected=expected):
                    with self.assertRaises(OSError):
                        ns['terminate_pid'](501, force=force, expected_start_time=expected)
                    self.assertEqual(calls, [])

    @pytest.mark.linux_only
    def test_exact_term_and_existing_unguarded_term_semantics_linux(self):
        self._assert_exact_term_and_existing_unguarded_term_semantics()

    @pytest.mark.macos_only
    def test_exact_term_and_existing_unguarded_term_semantics_macos(self):
        self._assert_exact_term_and_existing_unguarded_term_semantics()

    def _assert_exact_term_and_existing_unguarded_term_semantics(self):
        for expected in (1000, None):
            calls = []
            ns = self.production('gateway.status', {'terminate_pid', '_start_times_agree'},
                           _get_process_start_time=lambda _: 1000,
                           os=types.SimpleNamespace(kill=lambda *a: calls.append(a)), signal=signal)
            ns['terminate_pid'](501, expected_start_time=expected)
            self.assertEqual(calls, [(501, signal.SIGTERM)])

    def test_usr1_uses_original_marker_not_newly_observed_marker(self):
        calls = []
        ns = self.production('eidolon_cli.gateway', {'_request_gateway_self_restart', '_graceful_restart_via_sigusr1'},
                       signal=signal, os=types.SimpleNamespace(kill=lambda *a: calls.append(a)),
                       _is_pid_ancestor_of_current_process=lambda _: True, _wait_for_pid_exit=lambda *a: True)
        for marker in (None, 1001):
            with patch_dependencies({'gateway.status': fake_module('gateway.status', get_process_start_time=lambda _: marker)}):
                self.assertFalse(ns['_request_gateway_self_restart'](501, identity_check=lambda: True, expected_start_time=1000))
                self.assertFalse(ns['_graceful_restart_via_sigusr1'](501, 45, identity_check=lambda: True, expected_start_time=1000))
        self.assertEqual(calls, [])

    def test_discovery_identity_is_not_recaptured_by_destructive_consumers(self):
        # A changed same-install incarnation is denied at the first consumption.
        calls = []
        ns = self.production('eidolon_cli.gateway', {'kill_gateway_processes'},
                       logging=__import__('logging'), find_gateway_process_identities=lambda **_: {501: (10., 1000)},
                       terminate_pid=lambda *a, **k: calls.append((a, k)))
        with patch_dependencies({'eidolon_cli.process_identity': fake_module(
                'eidolon_cli.process_identity', owned_gateway_identity=lambda _: (11., 1100))}):
            self.assertEqual(ns['kill_gateway_processes'](all_profiles=True), 0)
        self.assertEqual(calls, [])

    def test_service_direct_pid_calls_propagate_guard_and_original_marker(self):
        current = [(10., 1000)]
        calls = []
        def drain(pid, *args, **guard):
            self.assertEqual(pid, 501)
            self.assertEqual(guard['expected_start_time'], 1000)
            self.assertTrue(guard['identity_check']())
            current[0] = (11., 1100)
            self.assertFalse(guard['identity_check']())
            calls.append(pid)
            return True
        fleet = self.production('eidolon_cli.update_cmd_fleet',
            {'_restart_one_systemd_gateway_unit', '_restart_macos_launchd_gateways'},
            _systemctl=lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout='active' if 'is-active' in cmd else '501'),
            _update_systemd_restart_is_owned=lambda *a: True,
            _resolve_manage_cmd=lambda *a: None,
            _service_unit_supports_graceful_sigusr1_restart=lambda _: True,
            _drain_or_signal_gateway_for_update=drain,
            _service_restart_sec=lambda *a, **k: 0.,
            _wait_for_service_active=lambda *a, **k: True,
            _restart_launchd_gateway_after_update=lambda **k: ([], []))
        with patch_dependencies({
            'eidolon_cli.process_identity': fake_module('identity', owned_gateway_identity=lambda _: current[0]),
            'eidolon_cli.gateway_update_ownership': fake_module('ownership', _update_systemd_service_identity=lambda *a: (501, 10., ())),
            'eidolon_cli.gateway': fake_module('gateway', get_launchd_label=lambda: 'current',
                launchd_gateway_labels_for_install=lambda: ['sibling'],
                _locate_launchd_gateway_service=lambda _: ('gui/501', 501),
                _graceful_restart_via_sigusr1=drain,
                _launchd_kickstart=lambda *a, **k: self.fail('unexpected manager restart'),
                _wait_for_launchd_service_pid=lambda *a, **k: True),
        }):
            restarted, failed = [], []
            fleet['_restart_one_systemd_gateway_unit']('eidolon-gateway', scope='user',
                scope_cmd=['systemctl', '--user'], drain_budget=1., _manage_cmd_cache={},
                restarted_services=restarted, failed_or_stale_units=failed)
            self.assertEqual(restarted, ['eidolon-gateway'])
            self.assertEqual(failed, [])
            current[0] = (10., 1000)
            fleet['_restart_macos_launchd_gateways'](restarted, failed, 1.)
            self.assertEqual(restarted, ['eidolon-gateway', 'sibling'])
            self.assertEqual(failed, [])
        self.assertEqual(calls, [501, 501])

    def test_current_launchd_restart_enables_update_ownership_guard(self):
        calls = []
        fleet = self.production('eidolon_cli.update_cmd_fleet', {'_restart_launchd_gateway_after_update'})
        with patch_dependencies({'eidolon_cli.gateway': fake_module('gateway',
            get_launchd_label=lambda: 'ai.eidolon.gateway',
            get_launchd_plist_path=lambda: types.SimpleNamespace(exists=lambda: True),
            launchd_restart=lambda **kwargs: calls.append(kwargs))}):
            self.assertEqual(fleet['_restart_launchd_gateway_after_update'](supervision_verify=False),
                             (['ai.eidolon.gateway'], []))
        self.assertEqual(calls, [{'for_update': True}])

    def test_current_launchd_unknown_pid_never_uses_manager_fallback(self):
        calls = []
        ns = self.production('eidolon_cli.gateway', {'launchd_restart'},
                       get_launchd_label=lambda: 'ai.eidolon.gateway', _launchd_domain=lambda: 'gui/501',
                       update_launchd_restart_is_owned=lambda _: True,
                       _request_gateway_self_restart=lambda *a, **k: self.fail('foreign self restart'),
                       probe_gateway_loop_liveness=lambda *a: self.fail('foreign probe'),
                       subprocess=types.SimpleNamespace(run=lambda argv, **k: calls.append(argv),
                                                        CalledProcessError=subprocess.CalledProcessError),
                       _launchd_ok=lambda _: None)
        with patch_dependencies({
                'gateway.status': fake_module('gateway.status', get_running_pid=lambda: 501),
                'eidolon_cli.gateway_update_ownership': fake_module('ownership', _update_launchd_service_identity=lambda _: (('gui/501', 501, 10., (), '/own'),)),
                'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=lambda _: None)}):
            with self.assertRaises(RuntimeError):
                ns['launchd_restart'](for_update=True)
        self.assertEqual(calls, [])

    def test_current_launchd_owned_pid_carries_snapshot_through_self_restart(self):
        calls = []
        def restart(pid, **guard):
            self.assertEqual(guard['expected_start_time'], 1000)
            self.assertTrue(guard['identity_check']())
            calls.append(pid)
            return True
        ns = self.production('eidolon_cli.gateway', {'launchd_restart'},
                       get_launchd_label=lambda: 'ai.eidolon.gateway', _launchd_domain=lambda: 'gui/501',
                       update_launchd_restart_is_owned=lambda _: True,
                       _request_gateway_self_restart=restart, _launchd_ok=lambda _: None,
                       subprocess=types.SimpleNamespace(CalledProcessError=subprocess.CalledProcessError))
        with patch_dependencies({
                'gateway.status': fake_module('gateway.status', get_running_pid=lambda: 501),
                'eidolon_cli.gateway_update_ownership': fake_module('ownership', _update_launchd_service_identity=lambda _: (('gui/501', 501, 10., (), '/own'),)),
                'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=lambda _: (10., 1000))}):
            ns['launchd_restart'](for_update=True)
        self.assertEqual(calls, [501])


    def test_restart_wait_requires_proved_exit_not_timeout_reuse_or_denial(self):
        import psutil
        cases = [('exit', True), ('timeout', False), ('reuse', False), ('marker', False), ('denied', False)]
        ns = self.production('eidolon_cli.gateway', {'_wait_for_gateway_restart_incarnation_exit'})
        for mode, expected in cases:
            def process(pid):
                if mode == 'exit':
                    raise psutil.NoSuchProcess(pid)
                if mode == 'denied':
                    raise psutil.AccessDenied(pid)
                return types.SimpleNamespace(create_time=lambda: 11. if mode == 'reuse' else 10.)
            with self.subTest(mode=mode), patch_dependencies({
                'psutil': fake_module('psutil', Process=process),
                'gateway.status': fake_module('status', get_process_start_time=lambda _: 1100 if mode == 'marker' else 1000),
            }):
                self.assertEqual(ns['_wait_for_gateway_restart_incarnation_exit'](501, (10., 1000), timeout=0), expected)

    def test_legacy_pid_selector_excludes_foreign_install(self):
        selector = self.production('eidolon_cli.gateway', {'find_gateway_pids'},
            _get_service_pids=lambda **_: [], supports_systemd_services=lambda: False,
            _scan_gateway_pids=lambda *a, **k: [501, 502])
        with patch_dependencies({'eidolon_cli.process_identity': fake_module('identity',
            owned_gateway_identity=lambda pid: (10., 1000) if pid == 501 else None)}):
            self.assertEqual(selector['find_gateway_pids'](all_profiles=True), [501])


if __name__ == '__main__':
    unittest.main(verbosity=2)

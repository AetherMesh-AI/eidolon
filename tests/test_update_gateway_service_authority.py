"""Named manager actions cannot bypass foreign-process isolation; all managers are spies."""
import contextlib
import os
import uuid
import pytest
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_update_gateway_install_isolation import ProductionTestCase, fake_module, patch_dependencies


class ServiceAuthority(ProductionTestCase):
    def process_snapshot(self, details, *, owned=True):
        return types.SimpleNamespace(create_time=lambda: 10.,
            environ=lambda: {'HERMES_HOME': details['home'] if owned else '/foreign', 'VIRTUAL_ENV': details['venv']},
            cmdline=lambda: details['args'], exe=lambda: details['args'][0], cwd=lambda: details['cwd'])

    def test_foreign_live_systemd_service_never_gets_reset_or_restart(self):
        calls = []
        ns = self.production('eidolon_cli.update_cmd_fleet', {'_systemctl_reset_and_restart'},
                       _update_systemd_restart_is_owned=lambda *a: False,
                       _systemctl=lambda *a, **k: calls.append((a, k)))
        with patch_dependencies({'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                _update_systemd_service_identity=lambda *a: None)}):
            result = ns['_systemctl_reset_and_restart'](['systemctl', '--user'], 'eidolon-gateway')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_verified_systemd_service_keeps_manager_restart(self):
        calls = []
        ns = self.production('eidolon_cli.update_cmd_fleet', {'_systemctl_reset_and_restart'},
                       _update_systemd_restart_is_owned=lambda *a: True, _systemd_restart_timeout=lambda *a: 90,
                       _systemctl=lambda *a, **k: calls.append((a, k)) or types.SimpleNamespace(returncode=0))
        with patch_dependencies({'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                _update_systemd_service_identity=lambda *a: (501, 10., ()))}):
            self.assertEqual(ns['_systemctl_reset_and_restart'](['systemctl', '--user'], 'eidolon-gateway').returncode, 0)
        self.assertEqual([item[0][0][-2] for item in calls], ['reset-failed', 'restart'])

    def test_foreign_live_launchd_service_never_gets_kickstart(self):
        calls = []
        ns = self.production('eidolon_cli.gateway', {'_launchd_kickstart'},
            subprocess=types.SimpleNamespace(run=lambda *a, **k: calls.append((a, k))))
        with patch_dependencies({'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                _update_launchd_service_identity=lambda _: None)}):
            with self.assertRaises(RuntimeError):
                ns['_launchd_kickstart']('ai.eidolon.gateway', 'gui/501', for_update=True)
        self.assertEqual(calls, [])

    def launchd_loaded_fixture(self):
        details = dict(args=['/own/venv/bin/python', '-m', 'eidolon_cli.main', 'gateway', 'run'],
                       home='/own', venv='/own/venv', cwd='/own', definition_path='/own/gateway.plist')
        output = """gui/501/ai.eidolon.gateway = {
    path = /own/gateway.plist
    program = /own/venv/bin/python
    working directory = /own
    arguments = {
        /own/venv/bin/python
        -m
        eidolon_cli.main
        gateway
        run
    }
    environment = {
        HERMES_HOME => /own
        VIRTUAL_ENV => /own/venv
    }
    pid = 501
}
"""
        return details, output

    def test_loaded_launchd_fields_must_match_not_only_live_ledger(self):
        details, output = self.launchd_loaded_fixture()
        ns = self.production('eidolon_cli.gateway_update_ownership', {'_update_launchd_service_identity'})
        for changed, process_owned, expected in [('', True, True), ('program', True, False),
                ('home', True, False), ('injected', True, False), ('', False, False)]:
            raw = output
            if changed == 'program':
                raw = raw.replace('program = /own/venv/bin/python', 'program = /foreign/python')
            elif changed == 'home':
                raw = raw.replace('HERMES_HOME => /own', 'HERMES_HOME => /foreign')
            elif changed == 'injected':
                raw = raw.replace('VIRTUAL_ENV =>', 'PYTHONPATH => /foreign\n        VIRTUAL_ENV =>')
            with self.subTest(changed=changed, process_owned=process_owned), patch_dependencies({
                'eidolon_cli.gateway': fake_module('gateway',
                    _update_service_definition_details=lambda *a, **kw: details),
                'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                    subprocess=types.SimpleNamespace(run=lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=raw, stderr=''))),
                'psutil': fake_module('psutil', Process=lambda _: self.process_snapshot(details, owned=process_owned)),
            }):
                self.assertEqual(ns['_update_launchd_service_identity']('ai.eidolon.gateway') is not None, expected)

    def test_loaded_launchd_accepts_generated_wrapper_and_rejects_ambiguous_blocks(self):
        details, _ = self.launchd_loaded_fixture()
        gateway = self.production('eidolon_cli.gateway', {'_timestamped_stderr_gateway_command'},
            get_python_path=lambda: '/own/venv/bin/python', _profile_arg=lambda: '')
        details['args'] = gateway['_timestamped_stderr_gateway_command'](
            Path('/own/logs/{gateway}.error.log'), external_supervisor=True)
        arguments = '\n'.join('        ' + arg for arg in details['args'])
        output = ('service = {\n    path = /own/gateway.plist\n'
                  '    program = /own/venv/bin/python\n    working directory = /own\n'
                  '    arguments = {\n' + arguments + '\n    }\n'
                  '    environment = {\n        HERMES_HOME => /own\n'
                  '        VIRTUAL_ENV => /own/venv\n    }\n    pid = 501\n}\n')
        ownership = self.production('eidolon_cli.gateway_update_ownership', {'_loaded_launchd_definition_matches'})
        matches = ownership['_loaded_launchd_definition_matches']
        self.assertTrue(matches(output, details))
        with patch_dependencies({
            'eidolon_cli.gateway': fake_module('gateway', _update_service_definition_details=lambda *a, **kw: details),
            'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                subprocess=types.SimpleNamespace(run=lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=output, stderr=''))),
            'psutil': fake_module('psutil', Process=lambda _: self.process_snapshot(details)),
        }):
            from eidolon_cli.gateway_update_ownership import _update_launchd_service_identity
            self.assertIsNotNone(_update_launchd_service_identity('ai.eidolon.gateway'))
        self.assertFalse(matches(output + '    arguments = {\n        /foreign/python\n    }\n', details))
        self.assertFalse(matches(output.replace('    }\n    environment', '    environment', 1), details))
        self.assertFalse(matches(output.replace('        VIRTUAL_ENV =>', '        HERMES_HOME => /foreign\n        VIRTUAL_ENV =>'), details))

    @pytest.mark.macos_only
    def test_native_launchd_print_accepts_exact_fixture_with_literal_braces(self):
        from eidolon_cli.gateway_update_ownership import _loaded_launchd_definition_matches
        label = 'ai.eidolon.test-ownership-' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='eidolon-launchd-contract-') as temporary:
            home = Path(temporary).resolve()
            plist = home / (label + '.plist')
            python = str(Path(sys.executable).resolve())
            args = [python, '-c', 'import time; time.sleep(120)', "{'fixture': True}"]
            venv = str(Path(sys.prefix).resolve())
            plist.write_bytes(plistlib.dumps({'Label': label, 'ProgramArguments': args,
                'WorkingDirectory': str(home), 'EnvironmentVariables': {
                    'HERMES_HOME': str(home), 'VIRTUAL_ENV': venv}, 'RunAtLoad': True}))
            details = dict(args=args, home=str(home), venv=venv, cwd=str(home), definition_path=str(plist))
            attempted, installed, errors = [], None, []
            try:
                for domain in (f'gui/{os.getuid()}', f'user/{os.getuid()}'):
                    target = f'{domain}/{label}'
                    attempted.append(target)
                    result = subprocess.run(['launchctl', 'bootstrap', domain, str(plist)],
                                            capture_output=True, text=True, timeout=15)
                    if result.returncode == 0:
                        installed = target
                        break
                    errors.append(f'{domain}: {result.stderr.strip()}')
                self.assertIsNotNone(installed, 'Native launchd fixture could not bootstrap: ' + '; '.join(errors))
                result = subprocess.run(['launchctl', 'print', installed], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(_loaded_launchd_definition_matches(result.stdout, details), result.stdout)
                self.assertFalse(_loaded_launchd_definition_matches(result.stdout,
                    {**details, 'args': [*args[:-1], 'foreign-argument']}))
            finally:
                # These UUID-qualified targets were created only for this test.
                for target in attempted:
                    cleanup = subprocess.run(['launchctl', 'bootout', target], capture_output=True, text=True, timeout=15)
                    if target == installed:
                        self.assertEqual(cleanup.returncode, 0, 'Fixture cleanup failed: ' + cleanup.stderr)

    def test_unloaded_unknown_and_pidless_launchd_cannot_use_disk_authority(self):
        details, _ = self.launchd_loaded_fixture()
        ns = self.production('eidolon_cli.gateway_update_ownership', {'_update_launchd_service_identity'})
        for code, output, error in [(113, '', 'Could not find service ai.eidolon.gateway'),
                (5, '', 'input/output error'), (0, 'state = waiting', '')]:
            calls = []
            def probe(*args, **kwargs):
                calls.append(args)
                return types.SimpleNamespace(returncode=code, stdout=output, stderr=error)
            with self.subTest(code=code), patch_dependencies({
                'eidolon_cli.gateway': fake_module('gateway', _update_service_definition_details=lambda *a, **kw: details,
                    update_service_process_is_owned=lambda *a, **kw: self.fail('no live PID')),
                'eidolon_cli.gateway_update_ownership': fake_module('ownership', subprocess=types.SimpleNamespace(run=probe)),
            }):
                self.assertIsNone(ns['_update_launchd_service_identity']('ai.eidolon.gateway'))
            self.assertGreaterEqual(len(calls), 1)

    def loaded_definition_fixture(self, **changes):
        details = dict(args=['/own/venv/bin/python', '-m', 'eidolon_cli.main', 'gateway', 'run'],
                       home='/own', venv='/own/venv', cwd='/own', definition_path='/own/gateway.service')
        fields = dict(MainPID='501', ExecStart='{ path=/own/venv/bin/python ; argv[]=/own/venv/bin/python -m eidolon_cli.main gateway run ; ignore_errors=no ; pid=501 ; }',
                      Environment='HERMES_HOME=/own VIRTUAL_ENV=/own/venv', WorkingDirectory='/own',
                      FragmentPath='/own/gateway.service', EnvironmentFiles='', PassEnvironment='',
                      UnsetEnvironment='', ExecStartPre='', ExecStartPost='', ExecStop='', ExecStopPost='',
                      RootDirectory='', RootImage='')
        fields.update(changes)
        return details, types.SimpleNamespace(returncode=0, stdout='\n'.join(f'{k}={v}' for k, v in fields.items()))

    def test_loaded_systemd_definition_and_live_process_must_both_match(self):
        cases = [{}, {'MainPID': '0'}, {'MainPID': ''}, {'MainPID': '-1'}, {'MainPID': 'broken'},
                 {'ExecStart': '{ path=/foreign/python ; argv[]=/foreign/python gateway run ; ignore_errors=no ; pid=501 ; }'},
                 {'EnvironmentFiles': '/foreign/env'}, {'PassEnvironment': 'PYTHONPATH'},
                 {'ExecStartPost': '/foreign/hook'}, {'Environment': 'HERMES_HOME=/foreign VIRTUAL_ENV=/own/venv'},
                 {'WorkingDirectory': '/foreign'}, {'FragmentPath': '/foreign/gateway.service'}]
        ns = self.production('eidolon_cli.gateway_update_ownership', {'_update_systemd_service_identity'})
        for changes in cases:
            details, result = self.loaded_definition_fixture(**changes)
            with self.subTest(changes=changes), patch_dependencies({
                'eidolon_cli.gateway': fake_module('gateway',
                    _update_service_definition_details=lambda *a, **kw: details),
                'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                    subprocess=types.SimpleNamespace(run=lambda *a, **kw: result)),
                'psutil': fake_module('psutil', Process=lambda _: self.process_snapshot(details)),
            }):
                identity = ns['_update_systemd_service_identity'](['systemctl', '--user'], 'eidolon-gateway')
                self.assertEqual(identity is not None, not bool(changes))

    def test_changed_loaded_service_between_reset_and_restart_is_not_restarted(self):
        calls = []
        identities = iter([(501, 10., ('own',)), (501, 10., ('own',)), (501, 10., ('foreign',))])
        ns = self.production('eidolon_cli.update_cmd_fleet', {'_systemctl_reset_and_restart'},
            _systemd_restart_timeout=lambda *a: 90,
            _systemctl=lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0))
        with patch_dependencies({'eidolon_cli.gateway_update_ownership': fake_module('ownership',
                _update_systemd_service_identity=lambda *a: next(identities))}):
            result = ns['_systemctl_reset_and_restart'](['systemctl', '--user'], 'eidolon-gateway')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([cmd[-2] for cmd in calls], ['reset-failed'])

    def test_owned_drain_zero_pid_uses_only_passive_owned_recovery(self):
        for recovered in (True, False):
            calls, restarted, failed = [], [], []
            authority = iter([(501, 10., ()), None, (501, 11., ()) if recovered else None, None])
            def systemctl(cmd, **kwargs):
                calls.append(cmd)
                return types.SimpleNamespace(returncode=0, stdout='active' if 'is-active' in cmd else '501')
            ns = self.production('eidolon_cli.update_cmd_fleet', {'_restart_one_systemd_gateway_unit', '_systemctl_reset_and_restart'},
                _systemctl=systemctl,
                _resolve_manage_cmd=lambda *a: ['systemctl', '--user'],
                _service_unit_supports_graceful_sigusr1_restart=lambda _: True,
                _drain_or_signal_gateway_for_update=lambda *a, **k: True,
                _service_restart_sec=lambda *a, **k: 0.,
                _wait_for_service_active=lambda *a, **k: True,
                subprocess=subprocess)
            with patch_dependencies({
                    'eidolon_cli.process_identity': fake_module('identity', owned_gateway_identity=lambda _: (10., 1000)),
                    'eidolon_cli.gateway_update_ownership': fake_module('ownership', _update_systemd_service_identity=lambda *a: next(authority))}):
                ns['_restart_one_systemd_gateway_unit']('eidolon-gateway', scope='user',
                    scope_cmd=['systemctl', '--user'], drain_budget=1., _manage_cmd_cache={},
                    restarted_services=restarted, failed_or_stale_units=failed)
            self.assertEqual(restarted, ['eidolon-gateway'] if recovered else [])
            self.assertEqual(failed, [] if recovered else ['eidolon-gateway'])
            self.assertTrue(all('show' in cmd or 'is-active' in cmd for cmd in calls))

    def definition_fixture(self, root, *, mutate=None, duplicate=False, manager='launchd'):
        home, project = root / '.eidolon', root / '.eidolon' / 'eidolon-agent'
        home.mkdir(exist_ok=True)
        python = str(project / 'venv' / 'bin' / 'python')
        venv = str(project / 'venv')
        args = [python, '-m', 'eidolon_cli.stderr_timestamp', '--error-log', str(home / 'logs' / 'gateway.error.log'),
                '--', python, '-m', 'eidolon_cli.main', 'gateway', 'run', '--external-supervisor']
        data = {'Label': 'ai.eidolon.gateway', 'ProgramArguments': args,
                'WorkingDirectory': str(home), 'EnvironmentVariables': {'HERMES_HOME': str(home), 'VIRTUAL_ENV': venv}}
        if mutate:
            mutate(data)
        plist = root / 'owned.plist'
        raw = plistlib.dumps(data)
        if duplicate:
            raw = raw.replace(b'<dict>', b'<dict><key>Label</key><string>foreign</string>', 1)
        plist.write_bytes(raw)
        unit = root / 'owned.service'
        unit.write_text(f'ExecStart={python} -m eidolon_cli.main gateway run\nWorkingDirectory={home}\n'
                        f'Environment="HERMES_HOME={home}"\nEnvironment="VIRTUAL_ENV={venv}"\n')
        resets = []
        modules = {
            'eidolon_constants': fake_module('eidolon_constants', get_default_hermes_root=lambda: home,
                set_eidolon_home_override=lambda _: 'token', reset_eidolon_home_override=lambda token: resets.append(token)),
            'eidolon_cli.profiles': fake_module('eidolon_cli.profiles', list_profiles=lambda: [types.SimpleNamespace(path=home)]),
        }
        ns = self.production('eidolon_cli.gateway', {'_update_service_definition_details'},
                       Path=Path, shlex=shlex, PROJECT_ROOT=project,
                       get_launchd_label=lambda: 'ai.eidolon.gateway', get_launchd_plist_path=lambda: plist,
                       _timestamped_stderr_gateway_command=lambda *a, **k: args,
                       get_service_name=lambda: 'eidolon-gateway', get_systemd_unit_path=lambda **_: unit,
                       get_python_path=lambda: python, _profile_arg=lambda: '',
                       generate_launchd_plist=lambda: self.fail('generator is not read-only'),
                       generate_systemd_unit=lambda **_: self.fail('generator is not read-only'))
        return ns, modules, resets, unit

    def test_definition_parser_is_read_only_and_checks_unique_owned_launch_fields(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-definition-proof-') as temp:
            root = Path(temp)
            ns, modules, resets, _ = self.definition_fixture(root)
            with patch_dependencies(modules):
                self.assertIsNotNone(ns['_update_service_definition_details']('ai.eidolon.gateway', manager='launchd'))
                self.assertIsNotNone(ns['_update_service_definition_details']('eidolon-gateway', manager='systemd'))
            self.assertEqual(resets, ['token', 'token'])
            self.assertFalse((root / '.eidolon' / 'logs').exists())

    def test_deceptive_foreign_and_duplicate_definitions_fail_closed(self):
        mutations = [lambda d: d['EnvironmentVariables'].update(HERMES_HOME='/foreign/.hermes'),
                     lambda d: d['EnvironmentVariables'].update(PYTHONPATH='/foreign/hermes-agent'),
                     lambda d: d['EnvironmentVariables'].pop('VIRTUAL_ENV'),
                     lambda d: d.update(WorkingDirectory=''),
                     lambda d: d.update(ProgramArguments=['/foreign/python', '-m', 'hermes_cli.main', 'gateway', 'run'])]
        for mutate in mutations:
            with tempfile.TemporaryDirectory(prefix='eidolon-definition-proof-') as temp:
                ns, modules, _, _ = self.definition_fixture(Path(temp), mutate=mutate)
                with patch_dependencies(modules):
                    self.assertIsNone(ns['_update_service_definition_details']('ai.eidolon.gateway', manager='launchd'))
        with tempfile.TemporaryDirectory(prefix='eidolon-definition-proof-') as temp:
            ns, modules, _, unit = self.definition_fixture(Path(temp), duplicate=True)
            with patch_dependencies(modules):
                self.assertIsNone(ns['_update_service_definition_details']('ai.eidolon.gateway', manager='launchd'))
                with unit.open('a') as handle:
                    handle.write('ExecStart=/foreign/python -m hermes_cli.main gateway run\n')
                self.assertIsNone(ns['_update_service_definition_details']('eidolon-gateway', manager='systemd'))

    def test_wrapper_requires_exact_own_runtime_and_rechecks_incarnation(self):
        details = {'args': ['/own/venv/bin/python', '-m', 'eidolon_cli.stderr_timestamp'],
                   'home': '/own', 'venv': '/own/venv', 'cwd': '/own'}
        for changed, wrong_env, expected in [(False, False, True), (True, False, False), (False, True, False)]:
            count = [0]
            def process(_):
                count[0] += 1
                created = 11. if changed and count[0] > 1 else 10.
                return types.SimpleNamespace(create_time=lambda: created,
                    environ=lambda: {'HERMES_HOME': '/foreign' if wrong_env else '/own', 'VIRTUAL_ENV': '/own/venv'},
                    cmdline=lambda: details['args'], exe=lambda: details['args'][0], cwd=lambda: '/own')
            ns = self.production('eidolon_cli.gateway', {'update_service_process_is_owned'},
                           Path=Path, _update_service_definition_details=lambda *a, **k: details)
            with patch_dependencies({
                    'psutil': fake_module('psutil', Process=process),
                    'eidolon_cli.process_identity': fake_module('eidolon_cli.process_identity', owned_gateway_identity=lambda *a, **k: None)}):
                self.assertEqual(ns['update_service_process_is_owned'](501, 'ai.eidolon.gateway', manager='launchd'), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)

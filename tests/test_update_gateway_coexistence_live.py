"""Two owned synthetic children, no applications/providers or user process inspection.

Runs production ownership filtering and the manual updater sweep against separate
loopback echo fixtures. Every signal is fenced to an exact child incarnation.
"""
import contextlib
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_update_gateway_install_isolation import ProductionTestCase, fake_module, patch_dependencies


SERVER = '''import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); s.listen()
print(s.getsockname()[1],flush=True)
while True:
 c,_=s.accept()
 with c:
  data=c.recv(32)
  c.sendall(b"fixture:"+data)
'''


class LiveGatewayCoexistence(ProductionTestCase):
    @pytest.mark.linux_only
    def test_foreign_gateway_remains_responsive_linux(self):
        self._assert_foreign_gateway_remains_responsive()

    @pytest.mark.macos_only
    def test_foreign_gateway_remains_responsive_macos(self):
        self._assert_foreign_gateway_remains_responsive()

    def _assert_foreign_gateway_remains_responsive(self):
        children = []
        with tempfile.TemporaryDirectory(prefix='eidolon-owned-coexistence-') as temp:
            root = Path(temp)
            own_home, foreign_home = root / '.eidolon', root / '.hermes'
            own_home.mkdir(); foreign_home.mkdir()
            try:
                def launch(home, brand):
                    child = subprocess.Popen([sys.executable, '-B', '-u', '-c', SERVER, brand, 'gateway', 'run'],
                                             env={**os.environ, 'HERMES_HOME': str(home)},
                                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True, start_new_session=True)
                    children.append(child)
                    with selectors.DefaultSelector() as ready:
                        ready.register(child.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(timeout=5), 'owned fixture did not start within 5s')
                        port = int(child.stdout.readline().strip())
                    return child, port
                own, own_port = launch(own_home, 'eidolon')
                foreign, foreign_port = launch(foreign_home, 'hermes')
                starts = {c.pid: psutil.Process(c.pid).create_time() for c in children}
                def echo(port):
                    with socket.create_connection(('127.0.0.1', port), timeout=2) as conn:
                        conn.sendall(b'alive')
                        return conn.recv(32)
                self.assertEqual(echo(own_port), b'fixture:alive')
                self.assertEqual(echo(foreign_port), b'fixture:alive')
                entries = [dict(pid=own.pid, create_time=starts[own.pid], purpose='gateway', install='own', home=str(own_home)),
                           dict(pid=foreign.pid, create_time=starts[foreign.pid], purpose='gateway', install='hermes', home=str(foreign_home))]
                identity_ns = self.production('eidolon_cli.process_identity', {'owned_gateway_start_time', 'owned_gateway_identity'},
                                        Path=Path, _read_ledger=lambda _: entries, _ledger_path=lambda: 'fixture', install_id=lambda: 'own')
                identity = fake_module('eidolon_cli.process_identity', **{'owned_gateway_identity': identity_ns['owned_gateway_identity']})
                common = {
                    'eidolon_cli.process_identity': identity,
                    'eidolon_constants': fake_module('eidolon_constants', get_default_hermes_root=lambda: own_home),
                    'eidolon_cli.profiles': fake_module('eidolon_cli.profiles', list_profiles=lambda: []),
                }
                gateway_ns = self.production('eidolon_cli.gateway', {'find_gateway_pids', '_find_gateway_candidate_pids', 'find_gateway_process_identities', '_append_unique_pid'},
                                       os=os, logging=logging, _get_service_pids=lambda **_: [],
                                       supports_systemd_services=lambda: False,
                                       _scan_gateway_pids=lambda *a, **k: [own.pid, foreign.pid])
                gateway = fake_module('eidolon_cli.gateway', find_gateway_process_identities=gateway_ns['find_gateway_process_identities'],
                                      find_profile_gateway_processes=lambda **_: [],
                                      _prepare_profile_gateway_update_restart=lambda *a: None,
                                      _get_service_pids=lambda **_: set(), _wait_for_gateway_exit=lambda **_: None)
                calls = []
                real_kill = os.kill
                def owned_signal(pid, sig):
                    # Test safety floor: never signal an unrelated/reused PID even if source regresses.
                    self.assertIn(pid, starts)
                    self.assertEqual(psutil.Process(pid).create_time(), starts[pid])
                    calls.append((pid, sig))
                    real_kill(pid, sig)
                fleet = self.production('eidolon_cli.update_cmd_fleet', {'_restart_manual_gateways', '_GatewayRestartOutcome'},
                                  dataclass=dataclass, field=field, suppress=contextlib.suppress,
                                  os=types.SimpleNamespace(kill=owned_signal))
                out = fleet['_GatewayRestartOutcome'](False, [], None, [], [], [], [], set())
                status = fake_module('gateway.status', os=types.SimpleNamespace(kill=owned_signal))
                with patch_dependencies({**common, 'eidolon_cli.gateway': gateway, 'gateway.status': status}):
                    fleet['_restart_manual_gateways'](out, 45)
                self.assertEqual(own.wait(timeout=5), -signal.SIGTERM)
                self.assertIsNone(foreign.poll())
                self.assertEqual(echo(foreign_port), b'fixture:alive')
                self.assertEqual(calls, [(own.pid, signal.SIGTERM)])
                self.assertEqual(out.killed_pids, {own.pid})
                print('Coexistence proof: own fixture stopped; foreign Hermes fixture remains alive and responsive.')
            finally:
                # Popen-owned direct children only; no process-table sweeps or name matches.
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            child.kill(); child.wait(timeout=3)
                    child.stdout.close(); child.stderr.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

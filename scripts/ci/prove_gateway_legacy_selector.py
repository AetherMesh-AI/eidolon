"""Legacy-frame manual selector proof against an exact pre-isolation fleet module.

CI supplies the immutable baseline and verifies its Git blob before this import.
Signals are spies; this is not a proof of old named-service or PID-reuse safety."""
import contextlib
import importlib.util
from pathlib import Path
import signal
import sys
import types
from unittest.mock import patch

import eidolon_cli.gateway as gateway
import eidolon_cli.process_identity as identity

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('legacy_update_cmd_fleet_proof', path)
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)
calls = []
out = types.SimpleNamespace(killed_pids=set(), stopped_unmapped_pids=set(),
    restarted_services=[], relaunched_profiles=[], externally_supervised_profiles=[])
with contextlib.ExitStack() as stack:
    fixtures = {
        '_get_service_pids': lambda **kw: set(),
        'supports_systemd_services': lambda: False,
        '_scan_gateway_pids': lambda *a, **kw: [501, 502],
        'find_profile_gateway_processes': lambda **kw: [],
        '_prepare_profile_gateway_update_restart': lambda *a, **kw: None,
        '_wait_for_gateway_exit': lambda **kw: None,
    }
    for name, value in fixtures.items():
        stack.enter_context(patch.object(gateway, name, value))
    stack.enter_context(patch.object(identity, 'owned_gateway_identity',
        lambda pid: (10., 1000) if pid == 501 else None))
    stack.enter_context(patch.object(legacy, 'os', types.SimpleNamespace(kill=lambda *args: calls.append(args))))
    legacy._restart_manual_gateways(out, 45)
assert calls == [(501, signal.SIGTERM)], calls
assert out.killed_pids == {501}, out.killed_pids
print('PASS: genuine baseline fleet module uses repaired selector; only owned PID reaches its signal spy.')
print('Scope: foreign-install exclusion in old manual sweep, not new identity propagation in old frames.')

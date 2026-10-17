"""A pending restart is discharged by supervisor evidence, not an empty PID scan."""
import subprocess
from types import SimpleNamespace

import pytest

from eidolon_cli import gateway, main, update_cmd_fleet as fleet


@pytest.mark.linux_only
@pytest.mark.parametrize("failure", ["listing", "timeout", "missing", "restart", "inactive", "running", None])
def test_pending_marker_requires_complete_systemd_recovery(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(main, "_purge_stale_hermes_modules", lambda: None)
    stopped = []
    monkeypatch.setattr(gateway, "find_gateway_pids", lambda **kw: [123] if failure == "running" and not stopped else [])
    monkeypatch.setattr(gateway, "kill_gateway_processes", lambda **kw: stopped.append(True))
    monkeypatch.setattr(gateway, "_wait_for_gateway_exit", lambda **kw: None)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(fleet, "_SYSTEMD_SCOPES", (("user", ["systemctl", "--user"]),))
    monkeypatch.setattr(fleet._time, "sleep", lambda _: None)
    ticks = iter(range(1000))
    monkeypatch.setattr(fleet._time, "monotonic", lambda: next(ticks))
    recovered = []
    from eidolon_cli import gateway_update_ownership as ownership
    identities = {name: (pid, 100.0, (("MainPID", str(pid)),)) for name, pid in
                  (("eidolon-gateway-one", 41), ("eidolon-gateway-two", 42))}
    monkeypatch.setattr(ownership, "_update_systemd_service_identity",
                        lambda scope, name: identities.get(name) if scope == ["systemctl", "--user"] else None)

    def systemctl(cmd, **kw):
        if "list-units" in cmd:
            if stopped:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(cmd, 10)
            if failure == "missing":
                raise FileNotFoundError("systemctl")
            return SimpleNamespace(returncode=int(failure == "listing"), stdout=(
                "eidolon-gateway-one.service loaded active running\n"
                "eidolon-gateway-two.service loaded failed failed\n"), stderr="")
        bad = cmd[-1] == "eidolon-gateway-two"
        if "restart" in cmd:
            recovered.append(cmd[-1])
            return SimpleNamespace(returncode=int(bad and failure == "restart"), stdout="")
        if "is-active" in cmd:
            active = not (bad and failure == "inactive")
            return SimpleNamespace(returncode=0 if active else 3, stdout="active" if active else "inactive")
        return SimpleNamespace(returncode=0, stdout="0s")

    monkeypatch.setattr(fleet, "_systemctl", systemctl)
    marker = fleet._fleet_restart_pending_marker_path()
    marker.write_text("expected_sha=pending\n")
    if failure not in (None, "running"):
        with pytest.raises(SystemExit, match="1"):
            fleet._apply_pending_fleet_restart_catchup()
        assert marker.exists()
    else:
        fleet._apply_pending_fleet_restart_catchup()
        assert not marker.exists()
        assert set(recovered) == {"eidolon-gateway-one", "eidolon-gateway-two"}


@pytest.mark.parametrize("failure", ["listing", "restart", "inactive", "unloaded", None])
def test_pending_launchd_requires_complete_supervision(monkeypatch, tmp_path, failure):
    # Host-independent subprocess-boundary fixture, not native launchd validation.
    current, sibling = "ai.eidolon.gateway", "ai.eidolon.gateway-two"
    (tmp_path / f"{sibling}.plist").touch()
    monkeypatch.setattr(gateway, "get_launchd_label", lambda: current)
    monkeypatch.setattr(gateway, "get_launchd_plist_path", lambda: tmp_path / f"{current}.plist")
    monkeypatch.setattr(gateway, "launchd_gateway_labels_for_install", lambda: [current, sibling])
    monkeypatch.setattr(fleet, "_restart_launchd_gateway_after_update", lambda **kw: ([], []))
    monkeypatch.setattr(gateway, "_locate_launchd_gateway_service", lambda _: (None, None) if failure == "unloaded" else ("gui/501", 41))
    monkeypatch.setattr(gateway, "_wait_for_launchd_service_pid", lambda *a, **kw: None if failure == "inactive" else 42)

    from eidolon_cli import gateway_update_ownership as ownership, process_identity
    monkeypatch.setattr(process_identity, "owned_gateway_identity", lambda pid: (41, 100.0) if pid == 41 else None)
    monkeypatch.setattr(ownership, "_update_launchd_service_identity", lambda label: (("gui/501", 41, 100.0, ("eidolon", "gateway", "run"), str(tmp_path)),) if label == sibling and failure != "unloaded" else None)
    monkeypatch.setattr(gateway, "_graceful_restart_via_sigusr1", lambda *a, **kw: False)

    def kickstart(*args, for_update):
        assert for_update is True
        if failure == "restart":
            raise subprocess.CalledProcessError(1, "launchctl")

    monkeypatch.setattr(gateway, "_launchd_kickstart", kickstart)
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=int(failure == "listing"), stdout="", stderr=""))
    restarted, failed = [], []
    fleet._restart_macos_launchd_gateways(restarted, failed, 0, require_supervision=True)
    assert bool(failed) is bool(failure)
    assert (sibling in restarted) is (failure is None)

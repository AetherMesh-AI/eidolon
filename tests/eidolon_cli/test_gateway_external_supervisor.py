"""Tests for explicit ownership by a wrapped external gateway supervisor."""

from types import SimpleNamespace

import pytest

import eidolon_cli.gateway as gateway


@pytest.fixture
def owned_work_gateway(monkeypatch, tmp_path):
    import eidolon_cli.process_identity as identity
    import eidolon_cli.profiles as profiles

    home = str(tmp_path / "work")
    monkeypatch.setattr(identity, "owned_gateway_identity", lambda pid: (1234, 100.0) if pid == 1234 else None)
    monkeypatch.setattr(identity, "owned_gateway_home", lambda pid, **kw: home if pid == 1234 else None)
    monkeypatch.setattr(profiles, "list_profiles", lambda: [SimpleNamespace(name="work", path=home)])


def _clear_native_supervisor_markers(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("HERMES_S6_SUPERVISED_CHILD", raising=False)
    monkeypatch.setenv("XPC_SERVICE_NAME", "0")


def test_external_marker_identifies_supervisor_process(monkeypatch):
    _clear_native_supervisor_markers(monkeypatch)
    monkeypatch.setenv(gateway.EXTERNAL_GATEWAY_SUPERVISOR_ENV, "1")

    assert gateway._running_under_gateway_supervisor() is True


def test_gateway_run_external_supervisor_flag_marks_process(monkeypatch):
    monkeypatch.delenv(gateway.EXTERNAL_GATEWAY_SUPERVISOR_ENV, raising=False)
    monkeypatch.setattr(
        gateway, "_maybe_redirect_run_to_s6_supervision", lambda _args: False
    )
    observed = []
    monkeypatch.setattr(
        gateway,
        "run_gateway",
        lambda *_args, **_kwargs: observed.append(
            gateway.os.environ.get(gateway.EXTERNAL_GATEWAY_SUPERVISOR_ENV)
        ),
    )

    gateway._gateway_command_inner(
        SimpleNamespace(gateway_command="run", external_supervisor=True)
    )

    assert observed == ["1"]


def test_update_hands_external_supervisor_gateway_back_without_watcher(monkeypatch, owned_work_gateway):
    monkeypatch.setattr(
        gateway,
        "_capture_gateway_argv",
        lambda _pid: [
            "python",
            "-m",
            "eidolon_cli.main",
            "gateway",
            "run",
            "--external-supervisor",
        ],
    )
    monkeypatch.setattr(
        gateway,
        "launch_detached_profile_gateway_restart",
        lambda *_args, **_kw: pytest.fail("detached watcher must not be launched"),
    )

    assert gateway._prepare_profile_gateway_update_restart("work", 1234) == (
        "external-supervisor"
    )


def test_update_hands_generated_launchd_inner_argv_back_without_watcher(monkeypatch, owned_work_gateway):
    """New launchd ProgramArguments put --external-supervisor on the grandchild."""
    monkeypatch.setattr(
        gateway,
        "_capture_gateway_argv",
        lambda _pid: [
            "/usr/bin/python3",
            "-m",
            "eidolon_cli.main",
            "--profile",
            "work",
            "gateway",
            "run",
            "--replace",
            "--external-supervisor",
        ],
    )
    monkeypatch.setattr(
        gateway,
        "launch_detached_profile_gateway_restart",
        lambda *_args, **_kw: pytest.fail("detached watcher must not be launched"),
    )

    assert gateway._prepare_profile_gateway_update_restart("work", 1234) == (
        "external-supervisor"
    )


def test_update_follows_wrapper_upgrade_of_stale_plist_argv(monkeypatch, owned_work_gateway):
    """stderr_timestamp upgrades stale inner argv so update sees the flag."""
    from eidolon_cli.stderr_timestamp import _prepare_child_command

    stale = [
        "/usr/bin/python3",
        "-m",
        "eidolon_cli.main",
        "gateway",
        "run",
        "--replace",
    ]
    upgraded = _prepare_child_command(
        stale, {"XPC_SERVICE_NAME": "ai.eidolon.gateway-work"}
    )
    assert upgraded[-1] == "--external-supervisor"

    monkeypatch.setattr(gateway, "_capture_gateway_argv", lambda _pid: upgraded)
    monkeypatch.setattr(
        gateway,
        "launch_detached_profile_gateway_restart",
        lambda *_args, **_kw: pytest.fail("detached watcher must not be launched"),
    )

    assert gateway._prepare_profile_gateway_update_restart("work", 1234) == (
        "external-supervisor"
    )


def test_update_still_uses_detached_watcher_without_supervisor_flag(monkeypatch, owned_work_gateway):
    monkeypatch.setattr(
        gateway,
        "_capture_gateway_argv",
        lambda _pid: [
            "python",
            "-m",
            "eidolon_cli.main",
            "gateway",
            "run",
            "--replace",
        ],
    )
    launched = []
    monkeypatch.setattr(
        gateway,
        "launch_detached_profile_gateway_restart",
        lambda profile, pid, *, expected_identity: launched.append((profile, pid)) or expected_identity == (1234, 100.0),
    )

    assert gateway._prepare_profile_gateway_update_restart("work", 1234) == "detached"
    assert launched == [("work", 1234)]



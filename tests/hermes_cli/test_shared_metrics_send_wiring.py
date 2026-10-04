"""Metrics stay local on live hooks, including profiles with legacy send=true."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli.observability import relay_shared_metrics as mod
from hermes_cli.observability.shared_metrics import SharedMetricsStore
from hermes_cli.observability.shared_metrics_contract import client_resource
from hermes_cli.observability.shared_metrics_sender import reconcile_send_consent
from hermes_cli.sqlite_util import write_txn


def _store_with_legacy_consent():
    store = SharedMetricsStore()
    store.record_client_active(client_resource("test", os_name="Linux", architecture="x86_64", install_method="pip"))
    with store._connection() as connection, write_txn(connection):
        reconcile_send_consent(connection, True)
    return store


def test_export_keeps_local_packages_closes_old_consent_and_never_starts_sender(monkeypatch):
    store = _store_with_legacy_consent()
    monkeypatch.setattr(mod, "_raw_config", lambda: {"telemetry": {"shared_metrics": {
        "enabled": True, "send": True, "endpoint": "https://telemetry.nousresearch.com/v1/telemetry",
    }}})
    sender = Mock(side_effect=AssertionError("sender must not be created"))
    network = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr("hermes_cli.observability.shared_metrics_sender.SharedMetricsSender", sender)
    monkeypatch.setattr("urllib.request.urlopen", network)
    runtime = mod._Runtime.__new__(mod._Runtime)
    runtime.subscriber = SimpleNamespace(store=store)

    runtime._export()
    assert list(store.outbox_directory.glob("*.json"))
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM send_consent_windows WHERE closed_at IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM package_outbox WHERE sent_at IS NOT NULL").fetchone()[0] == 0
    sender.assert_not_called()
    network.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_hook_closes_legacy_consent_even_before_collection_gate(monkeypatch, enabled):
    store = _store_with_legacy_consent()
    monkeypatch.setattr(mod, "_consent_reconcile_done", False)
    monkeypatch.setattr(mod, "_raw_config", lambda: {"telemetry": {"shared_metrics": {"enabled": enabled, "send": True}}})
    monkeypatch.setattr(mod, "_get_runtime", lambda: None)
    network = Mock(side_effect=AssertionError("network cannot run"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    mod.observe_lifecycle("on_session_start", session_id="local-only")
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM send_consent_windows").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM send_consent_windows WHERE closed_at IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM counter_aggregates").fetchone()[0] > 0
    network.assert_not_called()

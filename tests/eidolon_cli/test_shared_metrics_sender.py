"""The retired sender preserves local packages and cannot call a transport.

Delivery/retry/claim tests were retired with that implementation. Local store,
consent-window and schema-migration tests remain in their dedicated suites.
"""

from unittest.mock import Mock

import pytest

from eidolon_cli.observability.shared_metrics import SharedMetricsStore
from eidolon_cli.observability.shared_metrics_contract import client_resource
from eidolon_cli.observability.shared_metrics_sender import SharedMetricsSender, reconcile_send_consent
from eidolon_cli.sqlite_util import write_txn


@pytest.mark.parametrize("endpoint", ["https://telemetry.nousresearch.com/v1/telemetry", "https://custom.test"])
def test_legacy_sender_never_sends_or_changes_local_history(tmp_path, monkeypatch, endpoint):
    store = SharedMetricsStore(database_path=tmp_path / "metrics.db", outbox_directory=tmp_path / "outbox")
    store.record_client_active(client_resource("test", os_name="Linux", architecture="x86_64", install_method="pip"))
    packages = store.create_and_export_package()
    assert packages
    files_before = {path: path.read_bytes() for path in packages}
    with store._connection() as connection, write_txn(connection):
        reconcile_send_consent(connection, True)
        rows_before = [tuple(row) for row in connection.execute("SELECT * FROM package_outbox")]

    post = Mock(side_effect=AssertionError("legacy transport cannot run"))
    network = Mock(side_effect=AssertionError("network cannot run"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    outcome = SharedMetricsSender(store, endpoint, post=post, consent_check=lambda: True).send_pending()
    assert outcome.disabled and outcome.sent == 0
    post.assert_not_called()
    network.assert_not_called()
    assert {path: path.read_bytes() for path in packages} == files_before
    with store._connection() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM package_outbox")] == rows_before


def test_legacy_sender_does_not_even_open_a_broken_store():
    store = Mock()
    store._connection.side_effect = AssertionError("sender must not inspect private metrics")
    outcome = SharedMetricsSender(store, "https://unused.test").send_pending()
    assert outcome.disabled
    store._connection.assert_not_called()

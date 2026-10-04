"""Legacy telemetry preferences can enable local collection, never transmission."""

import logging

import pytest

from eidolon_cli.config import DEFAULT_CONFIG
from eidolon_cli.observability.shared_metrics_send_config import resolve_send_config, reset_warning_latch_for_tests


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("endpoint", [None, "", "https://telemetry.nousresearch.com/v1/telemetry",
                                      "https://example.test/ingest", "http://localhost:8099", "ftp://localhost"])
def test_legacy_send_and_endpoint_cannot_enable_egress(enabled, endpoint, monkeypatch):
    monkeypatch.setenv("HERMES_TELEMETRY_ENDPOINT", "https://other.test")
    resolved = resolve_send_config({"telemetry": {"shared_metrics": {
        "enabled": enabled, "send": True, "endpoint": endpoint,
    }}})
    assert resolved.enabled is enabled
    assert resolved.send is False
    assert resolved.endpoint == ""
    defaults = resolve_send_config(DEFAULT_CONFIG)
    assert not defaults.enabled and not defaults.send and not defaults.endpoint


def test_retirement_notice_is_once_per_process_and_does_not_expose_endpoint(caplog):
    reset_warning_latch_for_tests()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            resolve_send_config({"telemetry": {"shared_metrics": {
                "enabled": True, "send": True, "endpoint": "https://private.test/token",
            }}})
    messages = [r.getMessage() for r in caplog.records if "retired" in r.getMessage()]
    assert len(messages) == 1
    assert "private.test" not in messages[0]
    for config in (None, {}, {"telemetry": None}, {"telemetry": {"shared_metrics": None}}):
        assert not resolve_send_config(config).send
    reset_warning_latch_for_tests()

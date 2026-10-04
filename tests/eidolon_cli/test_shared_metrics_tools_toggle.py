"""The `eidolon tools` metrics toggle describes and persists local-only behavior."""

from __future__ import annotations

import pytest

from eidolon_cli.tools_config import (
    _configure_shared_metrics_interactive,
    _shared_metrics_menu_label,
    _shared_metrics_state,
)


def _config(**shared):
    return {"telemetry": {"shared_metrics": shared}}


class TestState:
    def test_missing_telemetry_section_is_off(self):
        assert _shared_metrics_state({}) == (False, False)

    def test_malformed_section_does_not_raise(self):
        assert _shared_metrics_state({"telemetry": "nonsense"}) == (False, False)

    def test_reads_both_flags(self):
        assert _shared_metrics_state(_config(enabled=True, send=True)) == (True, True)


class TestMenuLabel:
    def test_off_state(self):
        assert "off" in _shared_metrics_menu_label({})

    def test_local_only_state(self):
        label = _shared_metrics_menu_label(_config(enabled=True))
        assert "collecting locally" in label
        assert "Nous" not in label

    def test_legacy_send_state_still_reports_local_collection(self):
        label = _shared_metrics_menu_label(_config(enabled=True, send=True))
        assert "collecting locally" in label
        assert "Nous" not in label


class TestToggle:
    def test_legacy_send_is_disabled_and_persisted(self, monkeypatch):
        config = _config(enabled=True, send=True)
        saved = {}
        monkeypatch.setattr(
            "eidolon_cli.setup.prompt_yes_no", lambda *_a, **_k: True
        )
        monkeypatch.setattr(
            "eidolon_cli.setup._record_send_consent_change", lambda **_k: None
        )
        monkeypatch.setattr(
            "eidolon_cli.tools_config.save_config",
            lambda cfg: saved.update({"cfg": cfg}),
        )
        _configure_shared_metrics_interactive(config)
        assert config["telemetry"]["shared_metrics"]["send"] is False
        assert saved, "a consent change must be written to disk"

    def test_no_write_when_nothing_changed(self, monkeypatch):
        config = _config(enabled=False, send=False)
        saved = []
        monkeypatch.setattr(
            "eidolon_cli.setup.prompt_yes_no", lambda *_a, **_k: False
        )
        monkeypatch.setattr(
            "eidolon_cli.tools_config.save_config", lambda cfg: saved.append(cfg)
        )
        _configure_shared_metrics_interactive(config)
        assert saved == []

    def test_disabling_collection_also_disables_sending(self, monkeypatch):
        """The toggle must not leave send=true with nothing to send."""
        config = _config(enabled=True, send=True)
        monkeypatch.setattr(
            "eidolon_cli.setup.prompt_yes_no", lambda *_a, **_k: False
        )
        monkeypatch.setattr(
            "eidolon_cli.tools_config.save_config", lambda cfg: None
        )
        _configure_shared_metrics_interactive(config)
        shared = config["telemetry"]["shared_metrics"]
        assert shared["enabled"] is False
        assert shared["send"] is False

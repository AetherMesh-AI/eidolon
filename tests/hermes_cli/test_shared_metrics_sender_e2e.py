"""The legacy staging command is retired without generating or sending packages."""

import runpy
from pathlib import Path
from unittest.mock import Mock


def test_staging_command_refuses_without_collecting_or_transmitting(monkeypatch, capsys):
    from hermes_cli.observability.shared_metrics import SharedMetricsStore

    collect = Mock(side_effect=AssertionError("staging probe cannot collect metrics"))
    network = Mock(side_effect=AssertionError("staging probe cannot transmit metrics"))
    monkeypatch.setattr(SharedMetricsStore, "create_and_export_package", collect)
    monkeypatch.setattr("urllib.request.urlopen", network)
    script = Path(__file__).resolve().parents[2] / "scripts" / "e2e_shared_metrics_staging.py"
    main = runpy.run_path(str(script))["main"]
    assert main() != 0
    assert "retired" in capsys.readouterr().err
    collect.assert_not_called()
    network.assert_not_called()

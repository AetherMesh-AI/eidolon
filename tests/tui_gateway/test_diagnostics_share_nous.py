"""Older Desktop clients cannot reactivate the retired upstream upload RPC."""

from unittest.mock import Mock

import pytest

from tui_gateway import server


@pytest.mark.parametrize("params", [{}, {
    "error_context": "private error context",
    "extra_files": {"desktop.log": "private desktop logs"},
    "log_lines": 2000,
}])
def test_legacy_diagnostics_rpc_rejects_without_collecting_or_uploading(monkeypatch, params):
    collect = Mock(side_effect=AssertionError("must not collect logs"))
    network = Mock(side_effect=AssertionError("must not transmit"))
    monkeypatch.setattr("eidolon_cli.debug.collect_share_bundle", collect)
    monkeypatch.setattr("urllib.request.urlopen", network)

    result = server._methods["diagnostics.share_nous"]("retired-upload", params)
    assert result["result"]["ok"] is False
    assert "retired" in result["result"]["error"]
    assert "private" not in result["result"]["error"]
    collect.assert_not_called()
    network.assert_not_called()

"""Legacy upload invocations fail closed; local reports remain available."""

import argparse
from unittest.mock import Mock

import pytest

from eidolon_cli import debug
from eidolon_cli.subcommands.debug import build_debug_parser


def _args(*flags):
    parser = argparse.ArgumentParser()
    build_debug_parser(parser.add_subparsers(), cmd_debug=debug.run_debug)
    return parser.parse_args(["debug", "share", *flags])


@pytest.mark.parametrize("flags", [("--nous",), ("--nous", "--yes"), ("--nous", "--local", "--no-redact")])
def test_old_nous_upload_never_collects_or_transmits(monkeypatch, capsys, flags):
    collect = Mock(side_effect=AssertionError("must not collect private diagnostics"))
    network = Mock(side_effect=AssertionError("must not send any request"))
    monkeypatch.setattr(debug, "collect_share_bundle", collect)
    monkeypatch.setattr("urllib.request.urlopen", network)

    args = _args(*flags)
    with pytest.raises(SystemExit) as result:
        args.func(args)

    assert result.value.code != 0
    assert "retired" in capsys.readouterr().err
    collect.assert_not_called()
    network.assert_not_called()


def test_local_report_never_runs_network_cleanup(monkeypatch, capsys):
    network = Mock(side_effect=AssertionError("local diagnostics cannot use the network"))
    sweep = Mock(side_effect=AssertionError("local diagnostics cannot delete remote pastes"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    monkeypatch.setattr(debug, "_best_effort_sweep_expired_pastes", sweep)
    monkeypatch.setattr(debug, "collect_share_bundle", lambda **kwargs: {"report": "local report"})

    args = _args("--local")
    args.func(args)
    assert "local report" in capsys.readouterr().out
    network.assert_not_called()
    sweep.assert_not_called()

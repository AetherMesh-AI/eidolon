"""Exercise the existing Codex wire shape against unchanged organization budgets."""
from unittest.mock import Mock

import pytest

from agent.transports.codex import ResponsesApiTransport
from eidolon_cli.organization_executor import _ToolFreeBoundary, OrganizationExecutionError


def test_subscription_wire_without_output_cap_is_refused_before_reservation():
    wire = ResponsesApiTransport().build_kwargs(
        'gpt-5.4', [{'role': 'user', 'content': 'Synthetic bounded task.'}],
        provider='openai-codex', is_codex_backend=True, max_tokens=128,
        base_url='https://chatgpt.com/backend-api/codex',
    )
    assert not {'max_tokens', 'max_completion_tokens', 'max_output_tokens'} & wire.keys()
    boundary = _ToolFreeBoundary()
    boundary.api_mode = 'codex_responses'
    boundary.provider = 'openai-codex'
    boundary.model = 'gpt-5.4'
    boundary._organization_output_limit = 128
    boundary._organization_reserve = Mock()
    with pytest.raises(OrganizationExecutionError, match='no enforceable output-token cap'):
        boundary._organization_reserve_call(wire)
    boundary._organization_reserve.assert_not_called()


def test_non_subscription_responses_retains_explicit_output_cap():
    wire = ResponsesApiTransport().build_kwargs(
        'gpt-5.4', [{'role': 'user', 'content': 'Synthetic bounded task.'}],
        provider='openai', is_codex_backend=False, max_tokens=128,
    )
    assert wire['max_output_tokens'] == 128

"""Physical inference boundary without external providers."""
import threading
import pytest
from eidolon_cli import organization_intake_executor as executor
from eidolon_cli.organization_executor import OrganizationExecutionError
from tests.eidolon_cli.test_organization_team_generation import loopback_provider


def test_real_loopback_single_send_without_tools_or_private_prompt(loopback_provider):
    _, received, mode = loopback_provider
    mode['output'] = '{"proposal":"fixture"}'
    reserved = []
    result = executor.execute({'request': 'Fixture intake'}, reserve_model_call=lambda **kw: reserved.append(kw),
                              cancel=threading.Event())
    assert result['raw'] == mode['output']
    assert result['usage'] == {'inputTokens': 10, 'outputTokens': 10}
    assert len(reserved) == len(received) == 1
    assert not received[0].get('tools') and not received[0].get('functions')
    assert 'PRIVATE SOUL' not in str(received)


def test_real_loopback_error_has_one_retained_send(loopback_provider):
    _, received, mode = loopback_provider
    mode['status'] = 503
    reserved = []
    with pytest.raises(OrganizationExecutionError):
        executor.execute({'request': 'Fixture intake'}, reserve_model_call=lambda **kw: reserved.append(kw),
                         cancel=threading.Event())
    assert len(reserved) == len(received) == 1


def test_cancel_before_dispatch_never_reserves():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OrganizationExecutionError, match='cancelled'):
        executor.execute({}, reserve_model_call=lambda **_: pytest.fail('reserved'), cancel=cancel)

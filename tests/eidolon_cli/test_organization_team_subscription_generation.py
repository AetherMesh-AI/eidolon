"""Dedicated setup generation never constructs an ordinary agent or repairs output."""
import json
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_team_generation as generation
from eidolon_cli import organization_setup_subscription_transport as transport
from eidolon_cli.organization_team_setup import presets


@pytest.mark.parametrize('operation,field,kind', [
    ('generateTeam', None, 'generate'),
    ('improveTeamInput', 'jobs', 'improve_jobs'),
    ('improveTeamInput', 'goals', 'improve_goals'),
])
def test_subscription_request_is_exact_form_only(operation, field, kind):
    request = generation.subscription_request(operation, 'Write reports', 'Clear outcomes',
        'Short names', model_slug='synthetic-model', field=field)
    payload = json.loads(request.payload)
    assert set(payload) == {'model', 'instructions', 'input', 'stream', 'store'}
    assert payload['model'] == 'synthetic-model'
    assert payload['stream'] is True and payload['store'] is False
    assert request.operation == kind
    content = json.loads(payload['input'][0]['content'])
    assert content == {'jobs': 'Write reports', 'goals': 'Clear outcomes',
                       'namingStyle': 'Short names', **({'field': field} if field else {})}
    assert payload['instructions'] == (generation.SYSTEM if field is None else generation.IMPROVE_SYSTEM)


@pytest.mark.parametrize('kind,value', [
    ('generate', presets()[0]['team']),
    ('improve_jobs', {'suggestion': 'Write weekly reports.', 'assumptions': []}),
])
def test_subscription_completion_uses_one_sender_and_strict_parser(monkeypatch, kind, value):
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail('dedicated subscription branch entered general runtime')
    for name in ('_create_agent', '_guard_plugin_integrations', '_runtime_kwargs', 'readiness_reason'):
        monkeypatch.setattr(generation, name, forbidden)
    prepared = SimpleNamespace(request=SimpleNamespace(operation=kind))
    def send(item):
        calls.append(item)
        return SimpleNamespace(text=json.dumps(value), observation=SimpleNamespace(terminal_kind='completed'))
    monkeypatch.setattr(transport, 'send_once', send)
    assert generation.complete_subscription(prepared) == value
    assert calls == [prepared]
    monkeypatch.setattr(transport, 'send_once', lambda item: SimpleNamespace(
        text='{"suggestion":"a","suggestion":"b","assumptions":[]}',
        observation=SimpleNamespace(terminal_kind='completed')))
    with pytest.raises((generation.TeamInputRejected, generation.TeamProposalRejected)):
        generation.complete_subscription(prepared)


@pytest.mark.parametrize('terminal,expected', [
    ('completed', generation.TeamProposalRejected),
    ('failed', generation.TeamProposalRejected),
    ('incomplete', generation.TeamProposalRejected),
    ('unknown', generation.OrganizationExecutionError),
])
def test_missing_text_never_delivers_or_repairs(monkeypatch, terminal, expected):
    monkeypatch.setattr(transport, 'send_once', lambda item: SimpleNamespace(
        text=None, observation=SimpleNamespace(terminal_kind=terminal)))
    with pytest.raises(expected):
        generation.complete_subscription(SimpleNamespace(request=SimpleNamespace(operation='generate')))

"""Replacement writes stay behind the authenticated, profile-bound RPC contract."""
import time

import pytest

from tests.tui_gateway.test_organization_requests import rpc, service  # noqa: F401
import tui_gateway.server as server


def test_replacement_rpc_requires_confirmation_and_rejects_authority_injection(service, monkeypatch):
    original = rpc('organization.create', title='Expired original', idempotencyKey='original')['objective']
    now = time.time() + service.store.settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: now)
    draft = rpc('organization.previewReplacement', id=original['id'])
    params = {key: draft[key] for key in ('sourceId', 'sourceVersion', 'title', 'description', 'acceptanceCriteria')}
    for extra in ({}, {'confirmed': False}, {'confirmed': True, 'projectIds': ['unreviewed']},
                  {'confirmed': True, 'maxModelCalls': 100000}, {'confirmed': True, 'executiveId': 'foreign'}):
        result = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.replaceObjective', 'params': {**params, **extra}})
        assert result['error']['code'] == -32602
    params['confirmed'] = True
    unbound = server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.replaceObjective', 'params': params})
    assert unbound['error']['code'] == 4001
    first = rpc('organization.replaceObjective', **params)
    assert rpc('organization.replaceObjective', **params)['objective']['id'] == first['objective']['id']
    assert len(first['snapshot']['objectives']) == 2
    assert first['objective']['replacesObjectiveId'] == original['id']

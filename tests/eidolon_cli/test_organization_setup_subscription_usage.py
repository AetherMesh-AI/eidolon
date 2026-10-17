"""Synthetic accounting observations never convert missing usage into free work."""
from dataclasses import replace
import json

import pytest

from eidolon_cli.organization_setup_subscription_usage import (
    TransportObservationV1, TransportResult, UsageObservationV1,
)


def terminal(**changes):
    value = dict(version=1, attempt_id='attempt', binding_digest='a' * 64,
                 dispatch_nonce_digest='b' * 64, request_sha256='c' * 64,
                 dispatch_state='possible_send', terminal_kind='completed',
                 response_id_digest='d' * 64,
                 usage=UsageObservationV1(source='response.completed', unknown_reason='absent'),
                 result_content_digest='e' * 64, stable_protocol_error_code=None)
    value.update(changes)
    return TransportObservationV1(**value)


@pytest.mark.parametrize('usage,reason', [
    (None, 'absent'), ({}, 'invalid'),
    ({'input_tokens': True, 'output_tokens': 0, 'total_tokens': 1}, 'invalid'),
    ({'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 4}, 'invalid'),
    ({'input_tokens': -1, 'output_tokens': 2, 'total_tokens': 1}, 'invalid'),
    ({'input_tokens': 1.0, 'output_tokens': 2, 'total_tokens': 3}, 'invalid'),
    ({'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
      'input_tokens_details': {'cached_tokens': 2}}, 'invalid'),
    ({'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
      'output_tokens_details': {'reasoning_tokens': True}}, 'invalid'),
])
def test_absent_invalid_usage_is_unknown_not_zero(usage, reason):
    result = UsageObservationV1.from_response({'usage': usage}, 'response.completed')
    assert result.token_status == 'unknown' and result.unknown_reason == reason
    assert result.input_tokens is result.output_tokens is result.total_tokens is None
    assert result.cached_input_tokens is result.reasoning_tokens is None
    assert result.cost_status == 'unknown' and result.cost_usd is None


def test_observed_zero_and_optional_unknown_subcounts_survive_roundtrip():
    observed = UsageObservationV1.from_response({'usage': {'input_tokens': 0,
        'output_tokens': 0, 'total_tokens': 0}}, 'response.completed')
    assert observed.token_status == 'observed' and observed.total_tokens == 0
    assert observed.cached_input_tokens is observed.reasoning_tokens is None
    value = terminal(usage=observed)
    assert TransportObservationV1.from_dict(json.loads(value.canonical_bytes())) == value


def test_closed_text_free_dto_stable_hash_excludes_local_diagnostics():
    value = terminal()
    replay = replace(value, wire_bytes=123, entity_bytes=100, event_count=5,
                     elapsed_ms=999, http_status=200)
    assert value.outcome_hash == replay.outcome_hash
    assert value.canonical_bytes() != replay.canonical_bytes()
    assert b'text' not in value.canonical_bytes()
    assert replace(value, stable_protocol_error_code='unsupported_output').outcome_hash != value.outcome_hash
    for extra in ('text', 'token', 'response_id', 'raw_error', 'input'):
        with pytest.raises(ValueError):
            TransportObservationV1.from_dict(dict(value.to_dict(), **{extra: 'private'}))
    with pytest.raises(ValueError):
        replace(value, stable_protocol_error_code='secret provider exception')


def test_result_never_delivers_unaccounted_or_failed_text():
    value = terminal()
    with pytest.raises(ValueError):
        TransportResult(value, text='private output')
    result = TransportResult(value, text='private output', accounting_committed=True)
    assert 'private output' not in repr(result)
    with pytest.raises(ValueError):
        replace(result, observation=replace(value, stable_protocol_error_code='unsupported_output'))

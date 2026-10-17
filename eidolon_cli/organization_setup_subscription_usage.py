"""Closed, text-free subscription accounting types. Audit digests grant no authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re

ENDPOINT_IDENTITY = 'https://api.openai.com/v1/responses'
OPERATIONS = frozenset({'generate', 'improve_jobs', 'improve_goals'})
SAFE_ERRORS = frozenset({
    'invalid_request', 'admission_denied', 'stale_binding', 'cancelled',
    'deadline_exceeded', 'connect_failed', 'http_rejected', 'invalid_headers',
    'invalid_sse', 'unsupported_event', 'unsupported_output', 'resource_limit',
    'terminal_failed', 'terminal_incomplete', 'refusal', 'incomplete_stream',
    'accounting_failed',
})
MAX_COUNT = 2**53 - 1


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def is_digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def _bounded(value, maximum=512):
    return (type(value) is str and 0 < len(value.encode('utf-8')) <= maximum
            and all(ord(char) >= 32 for char in value))


@dataclass(frozen=True)
class AttemptBindingV1:
    attempt_id: str
    idempotency_key: str
    organization_id: str
    owner_id: str
    operation: str
    input_hash: str
    source_revision: str
    policy_generation: int
    review_digest: str
    connection_id: str
    authorization_epoch: str
    store_revision: int
    account_binding_digest: str
    model_slug: str
    endpoint_identity: str
    timeout_seconds: int
    request_sha256: str

    def __post_init__(self):
        hashes = ('input_hash', 'source_revision', 'review_digest',
                  'account_binding_digest', 'request_sha256')
        strings = ('attempt_id', 'idempotency_key', 'organization_id', 'owner_id',
                   'connection_id', 'authorization_epoch', 'model_slug')
        if (any(not is_digest(getattr(self, key)) for key in hashes)
                or any(not _bounded(getattr(self, key)) for key in strings)
                or self.operation not in OPERATIONS
                or not _count(self.policy_generation) or not _count(self.store_revision)
                or self.policy_generation == 0 or self.store_revision == 0
                or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120
                or self.endpoint_identity != ENDPOINT_IDENTITY):
            raise ValueError('invalid_request')

    def to_dict(self):
        return asdict(self)

    def canonical_bytes(self):
        return canonical_bytes(self.to_dict())

    @property
    def binding_digest(self):
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class UsageObservationV1:
    version: int = 1
    token_status: str = 'unknown'
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_status: str = 'unknown'
    cost_usd: None = None
    source: str = 'none'
    unknown_reason: str | None = 'interrupted'

    def __post_init__(self):
        required = (self.input_tokens, self.output_tokens, self.total_tokens)
        optional = (self.cached_input_tokens, self.reasoning_tokens)
        if (type(self.version) is not int or self.version != 1
                or self.cost_status != 'unknown' or self.cost_usd is not None
                or self.source not in {'none', 'response.completed', 'response.failed', 'response.incomplete'}):
            raise ValueError('invalid_usage')
        if self.token_status == 'observed':
            if (not all(_count(value) for value in required)
                    or self.input_tokens + self.output_tokens != self.total_tokens
                    or self.unknown_reason is not None or self.source == 'none'
                    or any(value is not None and not _count(value) for value in optional)
                    or (self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens)
                    or (self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens)):
                raise ValueError('invalid_usage')
        elif (self.token_status != 'unknown' or any(value is not None for value in required + optional)
              or self.unknown_reason not in {'absent', 'invalid', 'interrupted'}):
            raise ValueError('invalid_usage')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_response(cls, response, source):
        usage = response.get('usage')
        if usage is None:
            return cls(source=source, unknown_reason='absent')
        try:
            if type(usage) is not dict:
                raise ValueError
            details = []
            for key, name in (('input_tokens_details', 'cached_tokens'),
                              ('output_tokens_details', 'reasoning_tokens')):
                part = usage.get(key)
                if part is not None and type(part) is not dict:
                    raise ValueError
                details.append(None if part is None else part.get(name))
            return cls(token_status='observed', input_tokens=usage['input_tokens'],
                       output_tokens=usage['output_tokens'], total_tokens=usage['total_tokens'],
                       cached_input_tokens=details[0], reasoning_tokens=details[1],
                       source=source, unknown_reason=None)
        except (KeyError, TypeError, ValueError):
            return cls(source=source, unknown_reason='invalid')


@dataclass(frozen=True)
class TransportObservationV1:
    version: int
    attempt_id: str
    binding_digest: str
    dispatch_nonce_digest: str
    request_sha256: str
    dispatch_state: str
    terminal_kind: str
    response_id_digest: str | None
    usage: UsageObservationV1
    result_content_digest: str | None
    stable_protocol_error_code: str | None
    wire_bytes: int = 0
    entity_bytes: int = 0
    event_count: int = 0
    elapsed_ms: int = 0
    http_status: int | None = None

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 1 or not _bounded(self.attempt_id)
                or any(not is_digest(value) for value in (self.binding_digest, self.dispatch_nonce_digest, self.request_sha256))
                or any(value is not None and not is_digest(value) for value in (self.response_id_digest, self.result_content_digest))
                or type(self.usage) is not UsageObservationV1
                or self.dispatch_state not in {'not_sent', 'possible_send'}
                or self.terminal_kind not in {'none', 'unknown', 'completed', 'failed', 'incomplete'}
                or self.stable_protocol_error_code is not None and self.stable_protocol_error_code not in SAFE_ERRORS
                or any(not _count(value) for value in (self.wire_bytes, self.entity_bytes, self.event_count, self.elapsed_ms))
                or self.wire_bytes > 8 * 1024 * 1024 or self.entity_bytes > 4 * 1024 * 1024 or self.event_count > 20000
                or self.http_status is not None and (type(self.http_status) is not int or not 100 <= self.http_status <= 599)):
            raise ValueError('invalid_observation')
        terminal = self.terminal_kind in {'completed', 'failed', 'incomplete'}
        if (self.dispatch_state == 'not_sent' and self.terminal_kind != 'none'
                or self.dispatch_state == 'possible_send' and self.terminal_kind == 'none'
                or terminal and (self.response_id_digest is None or self.usage.source != 'response.' + self.terminal_kind)
                or not terminal and (self.response_id_digest is not None or self.result_content_digest is not None or self.usage.source != 'none')):
            raise ValueError('invalid_observation')

    def to_dict(self):
        return asdict(self)

    def canonical_bytes(self):
        return canonical_bytes(self.to_dict())

    @property
    def outcome_hash(self):
        value = self.to_dict()
        for key in ('wire_bytes', 'entity_bytes', 'event_count', 'elapsed_ms', 'http_status'):
            value.pop(key)
        return digest(value)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {field.name for field in fields(cls)}:
            raise ValueError('invalid_observation')
        usage = value['usage']
        if type(usage) is not dict or set(usage) != {field.name for field in fields(UsageObservationV1)}:
            raise ValueError('invalid_observation')
        return cls(**dict(value, usage=UsageObservationV1(**usage)))


@dataclass(frozen=True, repr=False)
class TransportResult:
    observation: TransportObservationV1
    text: str | None = None
    accounting_committed: bool = False
    delivery_error_code: str | None = None

    def __post_init__(self):
        if (type(self.observation) is not TransportObservationV1
                or type(self.accounting_committed) is not bool
                or self.delivery_error_code is not None and self.delivery_error_code not in SAFE_ERRORS
                or self.text is not None and (type(self.text) is not str or len(self.text) > 64000
                   or len(self.text.encode('utf-8')) > 256 * 1024
                   or self.observation.terminal_kind != 'completed'
                   or self.observation.stable_protocol_error_code is not None
                   or not self.accounting_committed or self.delivery_error_code is not None)):
            raise ValueError('invalid_result')

    def __repr__(self):
        return '<TransportResult private proposal>'

    def __reduce__(self):
        raise TypeError('Private transport result cannot be serialized')

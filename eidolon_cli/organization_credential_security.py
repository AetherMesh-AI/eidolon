"""Credential boundaries shared by the catalog, secure input, and native backends.

A principal is supplied by a trusted transport/executor, never reconstructed from
model arguments. Metadata is not an environment injection instruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
import unicodedata
import uuid
from typing import Callable, Protocol

PERMISSIONS = frozenset({'discover', 'use', 'create', 'edit_metadata', 'replace_value', 'revoke', 'remove'})


class CredentialError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__('Organization credentials: ' + reason)


@dataclass(frozen=True)
class Principal:
    organization_id: str
    actor_id: str
    owner: bool = False


@dataclass(frozen=True)
class ValueBinding:
    organization_id: str
    entry_id: str
    version: int
    actor_id: str
    operation_id: str


@dataclass(frozen=True)
class BackendState:
    provider: str
    state: str  # available | locked | unavailable

    def public(self):
        return {'provider': self.provider, 'state': self.state}


class SecureBackend(Protocol):
    def status(self) -> BackendState: ...
    def put(self, binding: ValueBinding, value: bytes) -> str: ...
    def consume(self, reference: str, binding: ValueBinding, consumer: Callable[[bytes], None]) -> None: ...


class UnavailableBackend:
    def status(self):
        return BackendState('none', 'unavailable')

    def put(self, binding, value):
        raise CredentialError('backend_unavailable')

    def consume(self, reference, binding, consumer):
        raise CredentialError('backend_unavailable')


def identifier(value, label='identifier'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise CredentialError('invalid_' + label)
    return value


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,47}', value.strip()):
        raise CredentialError('invalid_name')
    return value.strip()


def metadata_fields(fields):
    if not isinstance(fields, dict) or set(fields) != {'service', 'label', 'type', 'description', 'kind', 'expiresAt'}:
        raise CredentialError('invalid_metadata')
    service = component(fields['service'])
    label = component(fields['label'])
    kind_type = component(fields['type'])
    description = fields['description']
    if (not isinstance(description, str) or not description.strip() or len(description) > 1000
            or any(unicodedata.category(char).startswith('C') for char in description)):
        raise CredentialError('invalid_description')
    if fields['kind'] not in {'secret', 'config'}:
        raise CredentialError('invalid_kind')
    expiry = fields['expiresAt']
    if expiry is not None and (type(expiry) not in {int, float} or not 0 < expiry < 253402300800):
        raise CredentialError('invalid_expiry')
    return {'service': service, 'label': label, 'type': kind_type,
            'name': '_'.join((service, label, kind_type)), 'description': description.strip(),
            'kind': fields['kind'], 'expiresAt': expiry}


def create_scope(fields):
    return ':'.join(fields[key] for key in ('service', 'label', 'type', 'kind'))


@dataclass(repr=False)
class _PendingInput:
    binding: ValueBinding
    transport: object
    expires: float
    value: bytearray = field(repr=False)
    timer: object = field(default=None, repr=False)


class SecureInputs:
    """Bounded, process-local handles. Raw values never become catalog arguments.

    No claim of perfect memory erasure: Python/JSON and OS APIs can create copies.
    The owned mutable buffer is cleared on consume, cancel, expiry and disconnect.
    """
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._pending = {}
        self._lock = threading.Lock()

    def _expire(self):
        for handle, item in list(self._pending.items()):
            if item.expires <= self._clock():
                self._discard(handle)

    def _discard(self, handle):
        item = self._pending.pop(handle, None)
        if item:
            if item.timer is not None:
                item.timer.cancel()
            item.value[:] = b'\0' * len(item.value)

    def submit(self, binding: ValueBinding, transport: object, value: str, *, guard=None) -> str:
        if not isinstance(value, str) or not value or len(value) > 65536:
            raise CredentialError('invalid_secure_input')
        try:
            encoded = bytearray(value, 'utf-8')
        except UnicodeError:
            raise CredentialError('invalid_secure_input') from None
        if len(encoded) > 65536:
            encoded[:] = b'\0' * len(encoded)
            raise CredentialError('invalid_secure_input')
        with self._lock:
            self._expire()
            try:
                if guard is not None:
                    guard()
            except Exception:
                encoded[:] = b'\0' * len(encoded)
                raise
            if len(self._pending) >= 32:
                encoded[:] = b'\0' * len(encoded)
                raise CredentialError('secure_input_capacity')
            handle = str(uuid.uuid4())
            timer = threading.Timer(90, self._retire, args=(handle,))
            timer.daemon = True
            self._pending[handle] = _PendingInput(binding, transport, self._clock() + 90, encoded, timer)
            timer.start()
            return handle

    def _retire(self, handle):
        with self._lock:
            self._discard(handle)

    def consume(self, handle, binding, transport, consumer, *, guard=None):
        with self._lock:
            self._expire()
            item = self._pending.get(handle)
            if item is None or item.binding != binding or item.transport is not transport:
                raise CredentialError('secure_input_invalid')
            if guard is not None:
                guard()
            self._pending.pop(handle)
            item.timer.cancel()
        try:
            return consumer(bytes(item.value))
        except CredentialError:
            raise
        except Exception:
            raise CredentialError('backend_unavailable') from None
        finally:
            item.value[:] = b'\0' * len(item.value)

    def cancel_transport(self, transport):
        with self._lock:
            for handle, item in list(self._pending.items()):
                if item.transport is transport:
                    self._discard(handle)

    def available(self, handle, binding, transport):
        with self._lock:
            self._expire()
            item = self._pending.get(handle)
            return bool(item and item.binding == binding and item.transport is transport)

    def retire(self, handle):
        self._retire(handle)

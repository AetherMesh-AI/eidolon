"""One physical, tool-free Responses POST. No SDK, retries, proxy or fallback.

The timeout is an absolute network lifetime, not a bound on arbitrary native/OS
storage stalls. Private capabilities are constructed by trusted backend code only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time

from eidolon_cli.organization_setup_subscription_store import TokenLease, LeaseAttestation, StoreError
from eidolon_cli.organization_setup_subscription_usage import (
    AttemptBindingV1, ENDPOINT_IDENTITY, OPERATIONS, SAFE_ERRORS, TransportObservationV1,
    TransportResult, UsageObservationV1, canonical_bytes, digest, is_digest,
)

MAX_REQUEST = 128 * 1024
MAX_WIRE = 8 * 1024 * 1024
MAX_ENTITY = 4 * 1024 * 1024
MAX_FRAME = 1024 * 1024
MAX_EVENTS = 20000
MAX_TEXT = 256 * 1024
_CONNECT_SLOTS = threading.BoundedSemaphore(4)
_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_HEADER = re.compile(_TOKEN + rb':[^\r\n]*')
_BWS = rb'[ \t]*'
_QUOTED = rb'"(?:[\t\x20\x21\x23-\x5b\x5d-\x7e\x80-\xff]|\\[\t\x20-\x7e\x80-\xff])*"'
_CHUNK = re.compile(rb'([0-9A-Fa-f]+)(?:' + _BWS + rb';' + _BWS + _TOKEN
                    + rb'(?:' + _BWS + rb'=' + _BWS + rb'(?:' + _TOKEN + rb'|' + _QUOTED + rb'))?)*')
_TERMINALS = {'response.completed': 'completed', 'response.failed': 'failed',
              'response.incomplete': 'incomplete'}
_LIFECYCLE = {'response.created', 'response.in_progress', 'response.queued'}
_ITEM = {'response.output_item.added', 'response.output_item.done'}
_PART = {'response.content_part.added', 'response.content_part.done'}
_TEXT = {'response.output_text.delta', 'response.output_text.done'}
_REFUSAL = {'response.refusal.delta', 'response.refusal.done'}
_REASONING = {
    'response.reasoning_summary_part.added', 'response.reasoning_summary_part.done',
    'response.reasoning_summary_text.delta', 'response.reasoning_summary_text.done',
    'response.reasoning_text.delta', 'response.reasoning_text.done',
}
_EVENTS = _TERMINALS.keys() | _LIFECYCLE | _ITEM | _PART | _TEXT | _REFUSAL | _REASONING | {'error'}


class TransportError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code='invalid_sse'):
    raise TransportError(code)


def _pairs(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            _fail()
        value[key] = child
    return value


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: _fail())
        pending = [(value, 0)]
        while pending:
            child, depth = pending.pop()
            if depth > 32 or type(child) is float and not math.isfinite(child):
                _fail()
            if type(child) is dict:
                pending.extend((part, depth + 1) for part in child.values())
            if type(child) is list:
                pending.extend((part, depth + 1) for part in child)
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _fail()


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 512
            and all(33 <= ord(char) <= 126 for char in value))


def _index(value):
    return type(value) is int and 0 <= value < 20000


def _text(value):
    if type(value) is not str or len(value) > 64000:
        _fail('resource_limit' if type(value) is str else 'unsupported_output')
    try:
        if len(value.encode('utf-8')) > MAX_TEXT:
            _fail('resource_limit')
    except UnicodeError:
        _fail('unsupported_output')
    return value


@dataclass(frozen=True, repr=False)
class SetupTextRequest:
    operation: str
    instructions: str
    input_text: str
    model_slug: str
    request_sha256: str
    payload: bytes

    def __repr__(self):
        return '<SetupTextRequest private form>'

    def __reduce__(self):
        raise TypeError('Private setup request cannot be serialized')


def build_setup_text_request(operation, input_text, model_slug):
    """Canonicalize only the existing explicit setup form, with fixed prompts."""
    from eidolon_cli.organization_team_generation import SYSTEM, IMPROVE_SYSTEM, _input
    if operation not in OPERATIONS or not _identifier(model_slug):
        _fail('invalid_request')
    if type(input_text) is not str or len(input_text.encode('utf-8')) > MAX_REQUEST:
        _fail('invalid_request')
    value = _json(input_text)
    expected = {'jobs', 'goals', 'namingStyle'}
    if operation != 'generate':
        expected.add('field')
    if type(value) is not dict or set(value) != expected:
        _fail('invalid_request')
    try:
        if operation == 'generate':
            clean = {'jobs': _input(value['jobs'], 'Jobs', 6000, required=True),
                     'goals': _input(value['goals'], 'Goals', 4000),
                     'namingStyle': _input(value['namingStyle'], 'Naming instructions', 2000)}
        else:
            field = operation.removeprefix('improve_')
            if value['field'] != field:
                _fail('invalid_request')
            clean = {'field': field,
                     'jobs': _input(value['jobs'], 'Jobs', 4000, required=field == 'jobs'),
                     'goals': _input(value['goals'], 'Goals', 4000, required=field == 'goals'),
                     'namingStyle': _input(value['namingStyle'], 'Naming instructions', 500)}
    except (ValueError, TypeError):
        _fail('invalid_request')
    instructions = SYSTEM if operation == 'generate' else IMPROVE_SYSTEM
    content = canonical_bytes(clean).decode('ascii')
    payload = canonical_bytes({'model': model_slug, 'instructions': instructions,
        'input': [{'role': 'user', 'content': content}], 'stream': True, 'store': False})
    if len(payload) > MAX_REQUEST:
        _fail('resource_limit')
    return SetupTextRequest(operation, instructions, content, model_slug,
                            hashlib.sha256(payload).hexdigest(), payload)


_PREPARED_KEY = object()


class PreparedSubscriptionSend:
    def __init__(self, key, admission, lease, request):
        if key is not _PREPARED_KEY:
            _fail('invalid_request')
        self._admission, self._lease, self._request = admission, lease, request
        self._mutex, self._used = threading.Lock(), False

    def __repr__(self):
        return '<PreparedSubscriptionSend server-private>'

    def __reduce__(self):
        raise TypeError('Prepared send cannot be serialized')

    @property
    def request(self):
        return self._request

    def _consume(self):
        with self._mutex:
            if self._used:
                _fail('admission_denied')
            self._used = True
        return self._admission, self._lease, self._request


def prepare_subscription_send(admission, token_lease, request):
    if type(token_lease) is not TokenLease or type(request) is not SetupTextRequest:
        _fail('invalid_request')
    binding, attestation = admission.binding, token_lease.attestation
    if type(binding) is not AttemptBindingV1 or type(attestation) is not LeaseAttestation:
        _fail('invalid_request')
    # Rebuild to reject hand-constructed requests that change the prompt/payload.
    expected = build_setup_text_request(request.operation, request.input_text, request.model_slug)
    if (expected != request or binding.request_sha256 != request.request_sha256
            or binding.operation != request.operation or binding.model_slug != request.model_slug
            or binding.endpoint_identity != ENDPOINT_IDENTITY
            or not is_digest(admission.dispatch_nonce_digest)
            or attestation.account_binding_digest != binding.account_binding_digest
            or attestation.authorization_epoch != binding.authorization_epoch
            or attestation.store_revision != binding.store_revision
            or attestation.owner_generation is not admission.owner_generation):
        _fail('stale_binding')
    return PreparedSubscriptionSend(_PREPARED_KEY, admission, token_lease, request)


class _WireReader:
    """Bound decrypted HTTP wire, including framing that HTTPResponse hides."""
    def __init__(self, sock, check):
        self.sock, self.check = sock, check
        self.buffer = bytearray()
        self.wire_bytes = self.entity_bytes = 0
        self.header_bytes = self.header_count = 0
        self.status = None

    def _read(self, count):
        self.check()
        count = min(count, 8192, MAX_WIRE - self.wire_bytes)
        if count <= 0:
            _fail('resource_limit')
        data = self.sock.recv(count)
        self.wire_bytes += len(data)
        self.check()
        if len(data) > count:
            _fail('resource_limit')
        return data

    def line(self, maximum):
        while True:
            self.check()
            end = self.buffer.find(b'\r\n')
            if end >= 0:
                if end + 2 > maximum:
                    _fail('resource_limit')
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 2]
                if b'\r' in line or b'\n' in line:
                    _fail('invalid_headers')
                return line
            if b'\n' in self.buffer or len(self.buffer) >= maximum:
                _fail('invalid_headers')
            data = self._read(min(maximum - len(self.buffer), 8192 - len(self.buffer)))
            if not data:
                _fail('incomplete_stream')
            self.buffer.extend(data)

    def take(self, count):
        self.check()
        if self.buffer:
            data = bytes(self.buffer[:count])
            del self.buffer[:count]
            return data
        return self._read(min(count, 8192))

    def exact(self, count):
        parts = bytearray()
        while len(parts) < count:
            data = self.take(count - len(parts))
            if not data:
                _fail('incomplete_stream')
            parts.extend(data)
        return bytes(parts)

    def _headers(self, trailers=False):
        result = {}
        size = count = 0
        while True:
            line = self.line(2048 if trailers else 8192)
            size += len(line) + 2
            if trailers:
                if size > 16384 or count > 32:
                    _fail('resource_limit')
            else:
                self.header_bytes += len(line) + 2
                if self.header_bytes > 65536 or self.header_count > 100:
                    _fail('resource_limit')
            if not line:
                return result
            count += 1
            self.header_count += not trailers
            if (trailers and count > 32 or not trailers and self.header_count > 100):
                _fail('resource_limit')
            if not _HEADER.fullmatch(line) or any(byte < 32 and byte != 9 or byte == 127 for byte in line):
                _fail('invalid_headers')
            key, value = line.split(b':', 1)
            key, value = key.lower(), value.strip(b' \t')
            if trailers and key in {b'content-length', b'transfer-encoding', b'content-encoding',
                                    b'content-type', b'host', b'authorization', b'trailer'}:
                _fail('invalid_headers')
            if key in result and key in {b'content-length', b'transfer-encoding', b'content-encoding', b'content-type'}:
                _fail('invalid_headers')
            result[key] = value

    def headers(self):
        interim = 0
        while True:
            line = self.line(8192)
            self.header_bytes += len(line) + 2
            if not re.fullmatch(rb'HTTP/1\.[01] [1-5][0-9]{2} [\x20-\x7e]*', line):
                _fail('invalid_headers')
            status = int(line[9:12])
            headers = self._headers()
            if 100 <= status < 200:
                interim += 1
                if status not in {100, 102, 103} or interim > 4:
                    _fail('invalid_headers')
                if b'content-length' in headers or b'transfer-encoding' in headers:
                    _fail('invalid_headers')
                continue
            self.status = status
            if status != 200:
                _fail('http_rejected')
            if headers.get(b'content-encoding', b'identity').lower() != b'identity':
                _fail('invalid_headers')
            content_type = headers.get(b'content-type', b'').lower()
            if not re.fullmatch(rb'text/event-stream(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?', content_type):
                _fail('invalid_headers')
            length, transfer = headers.get(b'content-length'), headers.get(b'transfer-encoding')
            if length is not None and transfer is not None:
                _fail('invalid_headers')
            if (b'upgrade' in headers or b'upgrade' in headers.get(b'connection', b'').lower().split(b',')
                    or transfer is not None and not line.startswith(b'HTTP/1.1 ')):
                _fail('invalid_headers')
            if transfer is not None and transfer.lower() != b'chunked':
                _fail('invalid_headers')
            if length is not None and (not re.fullmatch(rb'[0-9]+', length)
                    or len(length) > 10 or int(length) > MAX_ENTITY):
                _fail('invalid_headers')
            return headers

    def _entity(self, count):
        remaining = count
        while remaining:
            self.check()
            data = self.take(min(remaining, 8192))
            if not data:
                _fail('incomplete_stream')
            remaining -= len(data)
            self.entity_bytes += len(data)
            if self.entity_bytes > MAX_ENTITY:
                _fail('resource_limit')
            yield data

    def body(self, headers):
        if b'transfer-encoding' in headers:
            chunks = 0
            while True:
                self.check()
                line = self.line(1024)
                chunks += 1
                parsed = _CHUNK.fullmatch(line)
                if not parsed:
                    _fail('invalid_headers')
                if chunks > 65536 or len(line) - len(parsed[1]) > 512:
                    _fail('resource_limit')
                size = int(parsed[1], 16)
                if size > MAX_ENTITY - self.entity_bytes:
                    _fail('resource_limit')
                if size == 0:
                    self._headers(trailers=True)
                    return
                yield from self._entity(size)
                if self.exact(2) != b'\r\n':
                    _fail('invalid_headers')
        elif b'content-length' in headers:
            yield from self._entity(int(headers[b'content-length']))
        else:
            while True:
                self.check()
                data = self.take(8192)
                if not data:
                    return
                self.entity_bytes += len(data)
                if self.entity_bytes > MAX_ENTITY:
                    _fail('resource_limit')
                yield data


class _Events:
    def __init__(self, check):
        self.check = check
        self.event_count = 0
        self.sequence = -1
        self.response_id = None
        self.items = {}
        self.item_ids = {}
        self.item_kinds = {}
        self.parts = {}
        self.snapshots = {}
        self.accumulated_bytes = 0
        self.refused = False

    def _identity(self, response):
        if type(response) is not dict or not _identifier(response.get('id')):
            _fail()
        identity = response['id']
        if self.response_id is not None and self.response_id != identity:
            _fail()
        self.response_id = identity

    def _item_ref(self, event):
        index, identity = event.get('output_index'), event.get('item_id')
        if not _index(index) or not _identifier(identity):
            _fail()
        if (index in self.items and self.items[index] != identity
                or identity in self.item_ids and self.item_ids[identity] != index):
            _fail()
        self.items[index], self.item_ids[identity] = identity, index
        return index

    def _item_type(self, index, kind):
        if index in self.item_kinds and self.item_kinds[index] != kind:
            _fail('unsupported_output')
        self.item_kinds[index] = kind

    def _part_ref(self, event):
        index = self._item_ref(event)
        self._item_type(index, 'message')
        content = event.get('content_index')
        if not _index(content):
            _fail()
        return index, content

    def _piece(self, key, kind, text, delta=False):
        text = _text(text)
        previous = self.parts.get(key)
        scaffold = self.snapshots.get(key)
        if scaffold is not None and scaffold[0] != kind:
            _fail()
        if previous is not None and previous[0] != kind:
            _fail()
        if delta:
            if previous is not None and previous[3]:
                _fail()
            combined = (previous[1] if previous else '') + text
            _text(combined)
            if scaffold and not (combined.startswith(scaffold[1]) or scaffold[1].startswith(combined)):
                _fail()
            self.accumulated_bytes += len(text.encode('utf-8'))
            if self.accumulated_bytes > MAX_TEXT:
                _fail('resource_limit')
            self.parts[key] = (kind, combined, True, False)
        else:
            if scaffold and not text.startswith(scaffold[1]):
                _fail()
            if previous is not None and (previous[2] or previous[3]) and previous[1] != text:
                _fail()
            self.parts[key] = (kind, text, bool(previous and previous[2]), True)
        if kind == 'refusal':
            self.refused = True
        return text

    def _part(self, part, key, final):
        if type(part) is not dict or part.get('type') not in {'output_text', 'refusal'}:
            _fail('unsupported_output')
        kind = part['type']
        value = _text(part.get('text' if kind == 'output_text' else 'refusal'))
        if final:
            return self._piece(key, kind, value)
        # Added parts are metadata, not deltas. Empty scaffolding is optional.
        if value:
            previous = self.parts.get(key) or self.snapshots.get(key)
            if previous and (previous[0] != kind or not
                    (previous[1].startswith(value) or value.startswith(previous[1]))):
                _fail()
            old = self.snapshots.get(key)
            if old is None or len(value) > len(old[1]):
                self.snapshots[key] = (kind, value)
        if kind == 'refusal':
            self.refused = True
        return value

    def _output_item(self, item, index, final):
        if type(item) is not dict or not _identifier(item.get('id')):
            _fail('unsupported_output')
        self._item_ref({'output_index': index, 'item_id': item['id']})
        kind = item.get('type')
        self._item_type(index, kind)
        if kind == 'message':
            if item.get('role') != 'assistant' or type(item.get('content')) is not list:
                _fail('unsupported_output')
            if len(item['content']) > 20000:
                _fail('resource_limit')
            return ''.join(self._part(part, (index, pos), final)
                           for pos, part in enumerate(item['content']))
        if kind == 'reasoning':
            for key, expected in (('summary', 'summary_text'), ('content', 'reasoning_text')):
                parts = item.get(key, [])
                if type(parts) is not list or len(parts) > 20000:
                    _fail('unsupported_output')
                for part in parts:
                    self.check()
                    if type(part) is not dict or part.get('type') != expected:
                        _fail('unsupported_output')
                    _text(part.get('text'))
            if item.get('encrypted_content') is not None:
                _text(item['encrypted_content'])
            return ''
        _fail('unsupported_output')

    def _snapshot(self, response, final):
        output = response.get('output', [])
        if type(output) is not list or len(output) > 20000:
            _fail('unsupported_output')
        texts, final_ids, final_parts = [], set(), set()
        for index, item in enumerate(output):
            self.check()
            texts.append(self._output_item(item, index, final))
            final_ids.add(index)
            if item.get('type') == 'message':
                final_parts.update((index, pos) for pos in range(len(item['content'])))
        if final and (set(self.items) != final_ids or (set(self.parts) | set(self.snapshots)) - final_parts):
            _fail()
        result = ''.join(texts)
        _text(result)
        return result

    def _reasoning(self, event):
        index = self._item_ref(event)
        self._item_type(index, 'reasoning')
        key = 'summary_index' if 'reasoning_summary' in event['type'] else 'content_index'
        if not _index(event.get(key)):
            _fail()
        if '_part.' in event['type']:
            part = event.get('part')
            if type(part) is not dict or part.get('type') != 'summary_text':
                _fail('unsupported_output')
            _text(part.get('text'))
        else:
            _text(event.get('delta' if event['type'].endswith('.delta') else 'text'))

    def accept(self, name, raw):
        self.check()
        self.event_count += 1
        if self.event_count > MAX_EVENTS:
            _fail('resource_limit')
        event = _json(raw)
        if type(event) is not dict or type(event.get('type')) is not str:
            _fail()
        kind = event['type']
        if name and name != kind:
            _fail()
        if kind not in _EVENTS:
            _fail('unsupported_event')
        sequence = event.get('sequence_number')
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1 or sequence <= self.sequence:
            _fail()
        self.sequence = sequence
        if 'response_id' in event:
            self._identity({'id': event['response_id']})
        if kind in _TERMINALS:
            response = event.get('response')
            self._identity(response)
            terminal = _TERMINALS[kind]
            if response.get('status') != terminal:
                _fail()
            # Establish authoritative completion BEFORE supported-output checking.
            usage = UsageObservationV1.from_response(response, kind)
            text, error = None, None
            try:
                text = self._snapshot(response, final=True)
                if self.refused:
                    error = 'refusal'
                if terminal != 'completed':
                    error = 'terminal_' + terminal
            except TransportError as exc:
                error = exc.code
            content_digest = digest(response.get('output'))
            return terminal, self.response_id, usage, None if error else text, error, content_digest
        if kind in _LIFECYCLE:
            response = event.get('response')
            self._identity(response)
            if response.get('status') not in {'queued', 'in_progress'}:
                _fail()
            self._snapshot(response, final=False)
            return None
        if kind in _ITEM:
            index = event.get('output_index')
            if not _index(index):
                _fail()
            self._output_item(event.get('item'), index, kind.endswith('.done'))
            return None
        if kind in _PART:
            self._part(event.get('part'), self._part_ref(event), kind.endswith('.done'))
            return None
        if kind in _TEXT | _REFUSAL:
            part_kind = 'output_text' if kind in _TEXT else 'refusal'
            delta = kind.endswith('.delta')
            field = 'delta' if delta else ('text' if part_kind == 'output_text' else 'refusal')
            self._piece(self._part_ref(event), part_kind, event.get(field), delta)
            return None
        if kind in _REASONING:
            self._reasoning(event)
            return None
        _fail('incomplete_stream')  # Standalone error is not a terminal response.

    def read(self, chunks):
        # A byte buffer preserves split UTF-8; strict decoding occurs per line.
        line = bytearray()
        frame_bytes = 0
        data, name = [], ''
        for chunk in chunks:
            for byte in chunk:
                self.check()
                frame_bytes += 1
                if frame_bytes > MAX_FRAME:
                    _fail('resource_limit')
                if byte != 10:
                    line.append(byte)
                    continue
                try:
                    text = bytes(line).removesuffix(b'\r').decode('utf-8', errors='strict')
                except UnicodeError:
                    _fail()
                line.clear()
                if '\r' in text or '\x00' in text:
                    _fail()
                if not text:
                    if data:
                        terminal = self.accept(name, '\n'.join(data))
                        if terminal is not None:
                            return terminal
                    data, name, frame_bytes = [], '', 0
                    continue
                if text.startswith(':'):
                    continue
                key, sep, value = text.partition(':')
                value = value.removeprefix(' ') if sep else ''
                if key == 'data':
                    data.append(value)
                elif key == 'event':
                    if name:
                        _fail()
                    name = value
                elif key not in {'id', 'retry'}:
                    _fail()
        _fail('incomplete_stream')


class _Connection(http.client.HTTPSConnection):
    """The caller installs its send fence only after the connect worker exits."""
    def __init__(self, timeout):
        super().__init__('api.openai.com', timeout=timeout)
        self.auto_open = 0
        self.send_gate = None

    def send(self, data):
        if self.send_gate is None:
            _fail('admission_denied')
        self.send_gate()
        if self.sock is None:
            _fail('connect_failed')
        return super().send(data)


def _connect_only(connection, deadline, abandoned, connected, slots):
    """No request, bearer, body, lease, admission or persistence callback here."""
    try:
        if not abandoned.is_set() and time.monotonic() < deadline:
            connection.connect()
    except Exception:
        abandoned.set()
    finally:
        try:
            if abandoned.is_set() or time.monotonic() >= deadline:
                connection.close()
        finally:
            slots.release()
            connected.set()


def _interrupt(connection, abandoned):
    abandoned.set()
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    connection.close()


def send_once(prepared):
    """Consume paired authority once; persist text-free truth before reply fencing."""
    if type(prepared) is not PreparedSubscriptionSend:
        _fail('invalid_request')
    admission, lease, request = prepared._consume()
    start = time.monotonic()
    deadline = start + admission.binding.timeout_seconds
    state = {'possible': False, 'permit': None, 'observation': None, 'accounted': False,
             'reader': None, 'events': None, 'callback_error': None}
    abandoned, connected, finished = threading.Event(), threading.Event(), threading.Event()
    connection = _Connection(min(10, admission.binding.timeout_seconds))

    def check():
        if time.monotonic() >= deadline:
            _fail('deadline_exceeded')
        if abandoned.is_set() or lease.network_cancelled() or admission.network_cancelled():
            _fail('cancelled')

    def write_gate():
        check()
        if admission.try_send_allowed() is not True or lease.try_send_allowed() is not True:
            _fail('stale_binding')
        check()

    def observation(terminal='unknown', response_id=None, usage=None, error=None, content=None):
        reader, events = state['reader'], state['events']
        value = TransportObservationV1(
            version=1, attempt_id=admission.binding.attempt_id,
            binding_digest=admission.binding.binding_digest,
            dispatch_nonce_digest=admission.dispatch_nonce_digest,
            request_sha256=request.request_sha256,
            dispatch_state='possible_send' if state['possible'] else 'not_sent',
            terminal_kind=terminal if state['possible'] else 'none',
            response_id_digest=digest(response_id) if response_id else None,
            usage=usage or UsageObservationV1(), result_content_digest=content,
            stable_protocol_error_code=error,
            wire_bytes=min(reader.wire_bytes, MAX_WIRE) if reader else 0,
            entity_bytes=min(reader.entity_bytes, MAX_ENTITY) if reader else 0,
            event_count=min(events.event_count, MAX_EVENTS) if events else 0,
            elapsed_ms=max(0, int((time.monotonic() - start) * 1000)),
            http_status=reader.status if reader else None)
        state['observation'] = value
        return value

    def account(value):
        try:
            state['accounted'] = admission.record_transport_outcome(
                state['permit'], value, deadline=deadline) is True
        except Exception:
            state['accounted'] = False
        return state['accounted']

    def invoke(token, active_lease):
        check()
        # Calling dispatch may commit before throwing or timing out. Never infer
        # a refund or another permit from a missing acknowledgement.
        state['possible'] = True
        permit = admission.begin_dispatch(request.request_sha256, deadline=deadline)
        state['permit'] = permit
        if permit is None:
            proof = getattr(admission, 'dispatch_definitely_not_sent', None)
            if callable(proof) and proof() is True:
                state['possible'] = False
        check()
        if permit is None or permit.consume() is not True:
            _fail('admission_denied')
        connection.send_gate = write_gate
        connection.request('POST', '/v1/responses', body=request.payload,
                           headers={'Authorization': 'Bearer ' + token,
                                    'Accept': 'text/event-stream',
                                    'Content-Type': 'application/json',
                                    'Accept-Encoding': 'identity', 'Connection': 'close'})
        reader = _WireReader(connection.sock, check)
        events = _Events(check)
        state['reader'], state['events'] = reader, events
        terminal, response_id, usage, text, error, content = events.read(reader.body(reader.headers()))
        value = observation(terminal, response_id, usage, error, content)
        # No proposal or response envelope is passed to persistence workers.
        if not account(value):
            return TransportResult(value, delivery_error_code='accounting_failed')
        return TransportResult(value, text=text, accounting_committed=True)

    def callback(token, active_lease):
        try:
            return invoke(token, active_lease)
        except TransportError as exc:
            # The lease deliberately sanitizes callback exceptions. Retain only
            # a closed safe code, never its exception, traceback or message.
            if type(exc) is TransportError and type(exc.code) is str and exc.code in SAFE_ERRORS:
                state['callback_error'] = exc.code
            raise

    def watchdog():
        while not finished.wait(min(.05, max(0, deadline - time.monotonic()))):
            # Event-only predicates. Never call a durable/store/ledger guard here.
            if (time.monotonic() >= deadline or abandoned.is_set()
                    or lease.network_cancelled() or admission.network_cancelled()):
                _interrupt(connection, abandoned)
                return

    error = None
    try:
        check()
        if not _CONNECT_SLOTS.acquire(blocking=False):
            _fail('connect_failed')
        worker = threading.Thread(target=_connect_only,
            args=(connection, deadline, abandoned, connected, _CONNECT_SLOTS), daemon=True)
        try:
            worker.start()
        except Exception:
            _CONNECT_SLOTS.release()
            _fail('connect_failed')
        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        while not connected.wait(min(.05, max(0, deadline - time.monotonic()))):
            check()
        check()
        if connection.sock is None:
            _fail('connect_failed')
        result = lease.run_nonblocking(callback)
        # Ledger and grant fences are sequential; neither remains locked for I/O.
        if admission.try_send_allowed() is not True:
            return replace(result, text=None, delivery_error_code='stale_binding')
        return result
    except TransportError as exc:
        error = exc.code
    except StoreError as exc:
        if exc.reason == 'operation_failed' and state['callback_error'] is not None:
            error = state['callback_error']
        else:
            error = 'cancelled' if exc.reason == 'lease_cancelled' else 'stale_binding'
    except Exception:
        error = 'stale_binding' if state['observation'] is not None else 'incomplete_stream' if state['possible'] else 'connect_failed'
    finally:
        finished.set()
        _interrupt(connection, abandoned)
        lease.close()
    value = state['observation']
    if value is None:
        value = observation(error=error)
        if state['possible']:
            account(value)
    return TransportResult(value, accounting_committed=state['accounted'], delivery_error_code=error)

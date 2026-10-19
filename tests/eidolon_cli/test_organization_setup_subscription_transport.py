"""Real sender/lease imports, fake vault and TLS sockets only. No live inference."""
from dataclasses import replace
import http.client
import json
import os
import pickle
import threading
import uuid

import pytest

from eidolon_cli import organization_setup_subscription_transport as transport
from eidolon_cli.organization_credential_native import binding_reference
from eidolon_cli.organization_setup_subscription_store import SubscriptionStore, TrustedBinding
from eidolon_cli.organization_setup_subscription_usage import (
    AttemptBindingV1, ENDPOINT_IDENTITY, TransportObservationV1, digest,
)


class Socket:
    def __init__(self, data, fragment=8192):
        self.data, self.fragment = bytearray(data), fragment
        self.sent, self.closed, self.received = [], False, 0
        self.rejected_connections = 0
        self.before_send = self.before_recv = None

    def sendall(self, value):
        if self.before_send:
            self.before_send(value)
        if self.closed:
            raise OSError('synthetic-private socket error')
        self.sent.append(bytes(value))

    def recv(self, count):
        assert 0 < count <= 8192
        if self.before_recv:
            self.before_recv()
        if self.closed:
            raise OSError('synthetic-private socket error')
        count = min(count, self.fragment)
        value = bytes(self.data[:count])
        del self.data[:count]
        self.received += len(value)
        return value

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True


class Vault:
    def __init__(self):
        self.values = {}

    def put(self, binding, value):
        reference = binding_reference(binding)
        self.values[reference] = value
        return reference

    def consume(self, reference, binding, callback):
        assert reference == binding_reference(binding)
        callback(self.values[reference])

    def delete(self, reference, binding):
        self.values.pop(reference, None)


class Permit:
    def __init__(self):
        self.used = False

    def consume(self):
        if self.used:
            return False
        self.used = True
        return True


class Admission:
    def __init__(self, binding, generation):
        self.binding, self.owner_generation = binding, generation
        self.dispatch_nonce_digest = 'b' * 64
        self.cancel = threading.Event()
        self.allowed = True
        self.dispatches, self.observations = [], []
        self.persist = True
        self.on_dispatch = self.on_account = None

    def network_cancelled(self):
        return self.cancel.is_set()

    def try_send_allowed(self):
        return self.allowed and not self.cancel.is_set()

    def begin_dispatch(self, request_sha256, *, deadline):
        assert request_sha256 == self.binding.request_sha256
        self.dispatches.append(request_sha256)
        if self.on_dispatch:
            return self.on_dispatch()
        return Permit()

    def record_transport_outcome(self, permit, observation, *, deadline):
        assert type(observation) is TransportObservationV1
        self.observations.append(observation)
        if self.on_account:
            self.on_account()
        return self.persist


def event(kind='response.completed', sequence=7, **changes):
    value = {'type': kind, 'sequence_number': sequence}
    value.update(changes)
    return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf-8')


def response(text='{"suggestion":"Ready","assumptions":[]}', status='completed', **changes):
    value = {'id': 'resp_synthetic', 'status': status, 'output': [
        {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
         'content': [{'type': 'output_text', 'text': text}]}],
        'usage': {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}}
    value.update(changes)
    return value


def wire(body, *, headers=b'', framing='length', status=200):
    prefix = b'HTTP/1.1 ' + str(status).encode() + b' Synthetic\r\nContent-Type: text/event-stream\r\n'
    if framing == 'length':
        return prefix + b'Content-Length: ' + str(len(body)).encode() + b'\r\n' + headers + b'\r\n' + body
    if framing == 'chunked':
        return prefix + b'Transfer-Encoding: chunked\r\n' + headers + b'\r\n' + body
    return prefix + headers + b'\r\n' + body


@pytest.fixture
def prepared(tmp_path):
    if os.name != 'posix':
        pytest.skip('Synthetic protected-store fixture uses the POSIX adapter')
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    binding = TrustedBinding('synthetic-host', str(profile), str(uuid.uuid4()), str(uuid.uuid4()),
                             'synthetic-registration', 'synthetic-client', 'synthetic-account')
    store = SubscriptionStore(profile, backend=Vault())
    store._clock = lambda: 1_900_000_000
    store.connect_grant(binding, access_token='synthetic-private-access',
        refresh_token='synthetic-private-refresh', id_token='synthetic-private-id',
        expires_at=2_000_000_000,
        granted_scopes=('chatgpt.tokens.use.direct', 'openid', 'resource.invoke'),
        auth_attempt_id='synthetic-auth', expected_revision=0,
        identity_evidence={'subject': binding.account_id, 'audience': binding.issued_client_id,
            'nonce': 'synthetic-consent', 'authTime': None, 'authorizedParty': None})
    generation = object()
    lease = store.lease_access_token(binding, 1, owner_generation=generation,
                                    owner_guard=lambda: True, cancel=threading.Event())
    request = transport.build_setup_text_request('improve_jobs', json.dumps(
        {'field': 'jobs', 'jobs': 'Plan', 'goals': '', 'namingStyle': ''}), 'account-model')
    attempt = AttemptBindingV1('attempt', 'idempotency', binding.organization_id, binding.owner_id,
        request.operation, digest(request.input_text), 'a' * 64, 1, 'c' * 64,
        'connection', lease.attestation.authorization_epoch, 1,
        lease.attestation.account_binding_digest, request.model_slug, ENDPOINT_IDENTITY, 5,
        request.request_sha256)
    admission = Admission(attempt, generation)
    return transport.prepare_subscription_send(admission, lease, request), admission, lease


def install(monkeypatch, data, fragment=8192):
    sock = Socket(data, fragment)
    connects = []
    def connect(connection):
        if type(connection) is not transport._Connection or connection.host != 'api.openai.com':
            sock.rejected_connections = min(2, sock.rejected_connections + 1)
            raise OSError('synthetic_connection_refused')
        connects.append(connection.host)
        connection.sock = sock
    monkeypatch.setattr(transport._Connection, 'connect', connect)
    return sock, connects


def test_passive_updater_http_cannot_borrow_sender_fixture_socket(prepared, monkeypatch):
    import io
    from eidolon_cli import banner
    capability, admission, _ = prepared
    body = b'{"ahead_by":2}'
    class UpdateSocket(Socket):
        def makefile(self, mode):
            assert mode == 'rb'
            return io.BytesIO(bytes(self.data))
    ordinary = UpdateSocket(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
        + str(len(body)).encode() + b'\r\n\r\n' + body)
    ordinary_connects = []
    def ordinary_connect(connection):
        assert type(connection) is http.client.HTTPSConnection and connection.host == 'api.github.com'
        ordinary_connects.append(True)
        connection.sock = ordinary
    for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', ordinary_connect)
    monkeypatch.setattr(banner, '_upstream_main_sha', lambda: 'b' * 40)
    sock, connects = install(monkeypatch, wire(event(response=response())))
    update_result = banner._check_via_rev('a' * 40)
    before = {'writes': len(sock.sent), 'closed': sock.closed, 'received': sock.received}
    result = transport.send_once(capability)
    assert before == {'writes': 0, 'closed': False, 'received': 0}, json.dumps(
        {**before, 'terminal': result.observation.terminal_kind, 'delivery': result.delivery_error_code}, sort_keys=True)
    assert update_result == 2 and ordinary_connects == [True]
    assert len(ordinary.sent) == 1 and ordinary.sent[0].startswith(b'GET /repos/AetherMesh-AI/Eidolon/compare/')
    assert connects == ['api.openai.com'] and sock.rejected_connections == 0
    assert len(sock.sent) == 2 and sock.received > 0
    assert result.observation.terminal_kind == 'completed' and result.text is not None
    assert len(admission.dispatches) == len(admission.observations) == 1
    with pytest.raises(OSError, match='synthetic_connection_refused'):
        transport._Connection.connect(http.client.HTTPSConnection('api.github.com'))
    assert sock.rejected_connections == 1 and len(connects) == 1


def test_exact_one_post_fixed_payload_and_text_free_accounting(prepared, monkeypatch):
    capability, admission, lease = prepared
    for key in ('HTTPS_PROXY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'HTTP_PROXY'):
        monkeypatch.setenv(key, 'https://poison.invalid/private')
    sock, connects = install(monkeypatch, wire(event(response=response())), fragment=1)
    result = transport.send_once(capability)
    assert result.text == response()['output'][0]['content'][0]['text']
    assert result.accounting_committed and result.observation.usage.total_tokens == 3
    assert connects == ['api.openai.com'] and len(admission.dispatches) == 1
    assert len(sock.sent) == 2 and sock.sent[0].startswith(b'POST /v1/responses HTTP/1.1\r\n')
    assert b'Authorization: Bearer synthetic-private-access\r\n' in sock.sent[0]
    assert b'Host: api.openai.com\r\n' in sock.sent[0]
    payload = json.loads(sock.sent[1])
    assert set(payload) == {'model', 'instructions', 'input', 'stream', 'store'}
    assert payload['model'] == 'account-model' and payload['stream'] is True and payload['store'] is False
    assert payload['input'] == [{'role': 'user', 'content': capability.request.input_text}]
    serialized = admission.observations[0].canonical_bytes()
    assert b'synthetic-private' not in serialized and b'Ready' not in serialized
    assert b'resp_synthetic' not in serialized and b'Plan' not in serialized
    assert sock.closed and not lease._token
    with pytest.raises(transport.TransportError):
        transport.send_once(capability)
    assert len(sock.sent) == 2


@pytest.mark.parametrize('status', [301, 307, 401, 429, 500])
def test_http_rejection_never_retries_and_keeps_unknown_debt(prepared, monkeypatch, status):
    capability, admission, _ = prepared
    sock, connects = install(monkeypatch, wire(b'private error', status=status))
    result = transport.send_once(capability)
    assert result.text is None and result.observation.terminal_kind == 'unknown'
    assert result.observation.stable_protocol_error_code == 'http_rejected'
    assert len(connects) == 1 and len(admission.dispatches) == 1 and len(sock.sent) == 2


@pytest.mark.parametrize('field,value', [
    ('account_binding_digest', 'f' * 64), ('authorization_epoch', str(uuid.uuid4())),
    ('store_revision', 2),
])
def test_two_independently_valid_bindings_cannot_be_swapped(prepared, field, value):
    capability, admission, lease = prepared
    admission.binding = replace(admission.binding, **{field: value})
    with pytest.raises(transport.TransportError, match='stale_binding'):
        transport.prepare_subscription_send(admission, lease, capability.request)
    assert not admission.dispatches


def test_generation_and_payload_swap_or_serialization_reject(prepared):
    capability, admission, lease = prepared
    admission.owner_generation = object()
    with pytest.raises(transport.TransportError):
        transport.prepare_subscription_send(admission, lease, capability.request)
    admission.owner_generation = lease.attestation.owner_generation
    with pytest.raises(transport.TransportError):
        transport.prepare_subscription_send(admission, lease, replace(capability.request, instructions='poison'))
    for private in (capability, lease.attestation, lease, capability.request):
        with pytest.raises(TypeError):
            pickle.dumps(private)


@pytest.mark.parametrize('stage', ['dispatch', 'header', 'account'])
def test_retirement_at_each_boundary_preserves_charge_suppresses_text(prepared, monkeypatch, stage):
    capability, admission, _ = prepared
    sock, _ = install(monkeypatch, wire(event(response=response())))
    if stage == 'dispatch':
        def dispatch():
            admission.cancel.set()
            return Permit()
        admission.on_dispatch = dispatch
    elif stage == 'header':
        def send(value):
            admission.cancel.set()
        sock.before_send = send
    else:
        admission.on_account = admission.cancel.set
    result = transport.send_once(capability)
    assert result.text is None and result.observation.dispatch_state == 'possible_send'
    assert len(admission.dispatches) == 1
    if stage == 'account':
        assert result.observation.terminal_kind == 'completed'
        assert result.accounting_committed
    else:
        assert result.observation.terminal_kind == 'unknown'
        assert len(sock.sent) <= 1


def test_ambiguous_commit_never_sends_or_reconstructs_permit(prepared, monkeypatch):
    capability, admission, _ = prepared
    sock, _ = install(monkeypatch, wire(event(response=response())))
    def ambiguous():
        raise OSError('synthetic commit acknowledgement loss')
    admission.on_dispatch = ambiguous
    result = transport.send_once(capability)
    assert result.text is None and result.observation.dispatch_state == 'possible_send'
    assert not sock.sent and len(admission.dispatches) == 1


def test_failed_terminal_persistence_keeps_frozen_completion(prepared, monkeypatch):
    capability, admission, _ = prepared
    admission.persist = False
    install(monkeypatch, wire(event(response=response())))
    result = transport.send_once(capability)
    assert result.text is None and result.delivery_error_code == 'accounting_failed'
    assert result.observation.terminal_kind == 'completed'
    assert admission.observations == [result.observation]
    assert result.observation.stable_protocol_error_code is None


def parse_wire(data, fragment=1):
    sock = Socket(data, fragment)
    reader = transport._WireReader(sock, lambda: None)
    events = transport._Events(lambda: None)
    return events.read(reader.body(reader.headers())), reader, events, sock


def test_fragmented_chunked_multibyte_crlf_multiline_and_first_terminal():
    text = '{"suggestion":"café 🦋","assumptions":[]}'
    body = event(response=response(text)).replace(b'data: ', b': comment\r\nid: ignored\r\nretry: 10\r\nevent: response.completed\r\ndata: ', 1)
    body = body.replace(b'"response": {', b'"response":\r\ndata: {').replace(b'\n\n', b'\r\n\r\n')
    chunked = b''.join(b'1;x="ok"\r\n' + bytes([byte]) + b'\r\n' for byte in body)
    # No zero chunk/trailer is required after an authoritative terminal.
    result, reader, _, sock = parse_wire(wire(chunked + b'private unseen invalid tail', framing='chunked'))
    assert result[0] == 'completed' and result[3] == text
    assert reader.wire_bytes > reader.entity_bytes == len(body)
    assert sock.data  # first terminal does not drain the tail


@pytest.mark.parametrize('header', [
    b'Content-Length: 0\r\n', b'Transfer-Encoding: chunked\r\n',
    b'Content-Encoding: gzip\r\n', b'Content-Type: text/plain\r\n',
    b' folded: value\r\n', b'Bad Header: value\r\n', b'X: bad\x00\r\n',
])
def test_ambiguous_or_invalid_headers_fail_closed(header):
    with pytest.raises(transport.TransportError):
        parse_wire(wire(event(response=response()), headers=header))


@pytest.mark.parametrize('body', [
    b'1;' + b'x' * 513 + b'\r\nx\r\n',
    b'1;=x\r\nx\r\n', b'1\r\nxXX', b'10000000\r\n',
    b'0\r\nContent-Length: 1\r\n\r\n',
    b'0\r\n' + b'X: value\r\n' * 33 + b'\r\n',
    b'0\r\nX: ' + b'a' * 2048 + b'\r\n\r\n',
    b'0\r\n\r\n',
])
def test_chunk_extension_trailer_bounds_and_early_end(body):
    with pytest.raises(transport.TransportError):
        parse_wire(wire(body, framing='chunked'))


def test_interim_limits_and_status_101_reject():
    prefix = b'HTTP/1.1 103 Early Hints\r\nX: bounded\r\n\r\n'
    result, _, _, _ = parse_wire(prefix * 4 + wire(event(response=response())))
    assert result[0] == 'completed'
    for value in (prefix * 5, b'HTTP/1.1 101 Upgrade\r\n\r\n'):
        with pytest.raises(transport.TransportError):
            parse_wire(value + wire(event(response=response())))


@pytest.mark.parametrize('body', [
    b'data: [DONE]\n\n', b'data: {"type":"response.completed"}\n\n',
    b'data: {"type":"error","sequence_number":0}\n\n',
    b'data: {"type":"response.completed","type":"response.completed"}\n\n',
    b'data: {"type":"response.completed","sequence_number":NaN}\n\n',
    b'data: {"type":"response.completed","sequence_number":true}\n\n',
    b'event: other\ndata: {"type":"response.completed","sequence_number":0}\n\n',
    b'data: \xff\n\n', b'data: {}',
])
def test_fake_terminal_invalid_utf8_and_partial_frames_are_uncertain(body):
    with pytest.raises(transport.TransportError):
        parse_wire(wire(body, framing='close'))


def test_noncontiguous_sequence_and_optional_lifecycle_with_delta_corroboration():
    text = 'Hello'
    body = event('response.created', 10, response=response('', status='in_progress', output=[]))
    body += event('response.output_text.delta', 20, output_index=0, content_index=0,
                  item_id='msg_synthetic', delta=text)
    body += event('response.output_text.done', 30, output_index=0, content_index=0,
                  item_id='msg_synthetic', text=text)
    body += event(sequence=40, response=response(text, model='canonicalized-model'))
    result, _, _, _ = parse_wire(wire(body))
    assert result[3] == text
    bad = body.replace(b'"text": "Hello"', b'"text": "Different"')
    with pytest.raises(transport.TransportError):
        parse_wire(wire(bad))


@pytest.mark.parametrize('terminal,status,error', [
    ('response.failed', 'failed', 'terminal_failed'),
    ('response.incomplete', 'incomplete', 'terminal_incomplete'),
])
def test_authoritative_failure_is_terminal_even_without_usage(terminal, status, error):
    result, _, _, _ = parse_wire(wire(event(terminal, response=response(status=status, usage=None))))
    assert result[0] == status and result[3] is None and result[4] == error
    assert result[2].token_status == 'unknown' and result[2].input_tokens is None


def test_completed_unsupported_output_retains_authoritative_usage():
    value = response(output=[{'id': 'tool', 'type': 'function_call', 'arguments': 'private'}])
    result, _, _, _ = parse_wire(wire(event(response=value)))
    assert result[0] == 'completed' and result[3] is None
    assert result[4] == 'unsupported_output' and result[2].total_tokens == 3
    unsupported = event('response.output_item.added', item=value['output'][0], output_index=0)
    with pytest.raises(transport.TransportError, match='unsupported_output'):
        parse_wire(wire(unsupported + event(sequence=8, response=response())))


def test_reasoning_is_discarded_refusal_needs_terminal():
    value = response(output=[{'id': 'reason', 'type': 'reasoning',
        'summary': [{'type': 'summary_text', 'text': 'private reasoning'}]},
        response()['output'][0]])
    result, _, _, _ = parse_wire(wire(event(response=value)))
    assert 'private reasoning' not in result[3]
    refusal = response(output=[{'id': 'refused', 'type': 'message', 'role': 'assistant',
        'content': [{'type': 'refusal', 'refusal': 'No'}]}])
    result, _, _, _ = parse_wire(wire(event(response=refusal)))
    assert result[0] == 'completed' and result[4] == 'refusal' and result[3] is None
    with pytest.raises(transport.TransportError, match='incomplete_stream'):
        parse_wire(wire(event('response.refusal.delta', output_index=0, content_index=0,
                             item_id='refused', delta='No')))


def test_completed_invalid_json_is_delivered_for_local_validation_without_repair(prepared, monkeypatch):
    capability, admission, _ = prepared
    sock, _ = install(monkeypatch, wire(event(response=response('not proposal JSON'))))
    result = transport.send_once(capability)
    assert result.text == 'not proposal JSON' and result.observation.terminal_kind == 'completed'
    assert len(admission.dispatches) == 1 and len(sock.sent) == 2


def test_late_connect_never_sends_and_keeps_slot_until_worker_exits(prepared, monkeypatch):
    capability, admission, _ = prepared
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    sock = Socket(wire(event(response=response())))
    original_close = sock.close
    def close():
        original_close()
        closed.set()
    sock.close = close
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(transport, '_CONNECT_SLOTS', slots)
    def connect(connection):
        entered.set()
        assert release.wait(10)
        connection.sock = sock
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', connect)
    results = []
    caller = threading.Thread(target=lambda: results.append(transport.send_once(capability)))
    caller.start()
    assert entered.wait(5)
    admission.cancel.set()
    caller.join(3)
    try:
        assert not caller.is_alive() and not sock.sent and not admission.dispatches
        assert results[0].observation.dispatch_state == 'not_sent'
        assert not slots.acquire(blocking=False)
    finally:
        release.set()
        caller.join(5)
    assert closed.wait(5)
    assert slots.acquire(timeout=5)
    slots.release()
    assert not sock.sent


def test_connect_slot_exhaustion_refuses_before_dispatch(prepared, monkeypatch):
    capability, admission, _ = prepared
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(transport, '_CONNECT_SLOTS', slots)
    sock, connects = install(monkeypatch, wire(event(response=response())))
    result = transport.send_once(capability)
    assert result.observation.dispatch_state == 'not_sent'
    assert not sock.sent and not connects and not admission.dispatches
    slots.release()


def test_deadline_before_body_and_socket_reset_are_unknown(prepared, monkeypatch):
    from types import SimpleNamespace
    capability, admission, _ = prepared
    now = [100.0]
    monkeypatch.setattr(transport, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    sock, _ = install(monkeypatch, wire(event(response=response())))
    def expire_after_header(value):
        now[0] = 106.0
    sock.before_send = expire_after_header
    result = transport.send_once(capability)
    assert result.observation.terminal_kind == 'unknown' and result.text is None
    assert result.delivery_error_code == 'deadline_exceeded'
    assert len(sock.sent) <= 1 and len(admission.dispatches) == 1


def test_raw_frame_event_depth_and_output_resource_limits(monkeypatch):
    cases = [
        (b'HTTP/1.1 200 OK\r\n' + b'X: ' + b'a' * 8192 + b'\r\n\r\n'),
        wire(b': ' + b'a' * transport.MAX_FRAME + b'\n\n'),
        wire(event(response=response(output=[{'id': 'msg', 'type': 'message',
             'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'x' * 64001}]}]))),
    ]
    # Completed envelopes remain known completion even when output exceeds caps.
    for value in cases[:2]:
        with pytest.raises(transport.TransportError):
            parse_wire(value, fragment=8192)
    terminal, _, _, _ = parse_wire(cases[2], fragment=8192)
    assert terminal[0] == 'completed' and terminal[3] is None and terminal[4] == 'resource_limit'
    deep = b'data: {"type":"response.created","sequence_number":0,"x":' + b'[' * 34 + b'0' + b']' * 34 + b'}\n\n'
    with pytest.raises(transport.TransportError):
        parse_wire(wire(deep))
    monkeypatch.setattr(transport, 'MAX_EVENTS', 1)
    body = event('response.created', 0, response=response(status='in_progress', output=[]))
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(body + event(response=response())))


def test_wire_entity_headers_chunks_and_trailers_have_independent_caps(monkeypatch):
    body = event(response=response())
    monkeypatch.setattr(transport, 'MAX_WIRE', 32)
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(body))
    monkeypatch.setattr(transport, 'MAX_WIRE', 8 * 1024 * 1024)
    headers = b'X: ' + b'a' * 7990 + b'\r\n'
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(body, headers=headers * 9))
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(body, headers=b'X: a\r\n' * 101))
    trailer = b'0\r\n' + (b'X: ' + b'a' * 1990 + b'\r\n') * 9 + b'\r\n'
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(trailer, framing='chunked'))
    # Tiny chunks exhaust framing count even when total entity bytes are small.
    tiny = b'1\r\n:\r\n' * 65537
    with pytest.raises(transport.TransportError, match='resource_limit'):
        parse_wire(wire(tiny, framing='chunked'), fragment=8192)
    monkeypatch.setattr(transport, 'MAX_ENTITY', 10)
    with pytest.raises(transport.TransportError):
        parse_wire(wire(body))


def test_snapshot_mismatch_is_known_completion_and_unknown_ids_cannot_rebind():
    delta = event('response.output_text.delta', 0, output_index=0, content_index=0,
                  item_id='msg_synthetic', delta='first')
    result, _, _, _ = parse_wire(wire(delta + event(response=response('second'))))
    assert result[0] == 'completed' and result[3] is None and result[4] == 'invalid_sse'
    start = event('response.created', 0, response=response(status='in_progress', output=[]))
    with pytest.raises(transport.TransportError):
        parse_wire(wire(start + event(response=response(id='different'))))
    with pytest.raises(transport.TransportError):
        parse_wire(wire(start + event(sequence=0, response=response())))


def test_nonterminal_partial_snapshot_corroborates_without_duplicating_deltas():
    body = event('response.in_progress', 0, response=response('Hel', status='in_progress'))
    body += event('response.output_text.delta', 1, output_index=0, content_index=0,
                  item_id='msg_synthetic', delta='Hello')
    body += event(response=response('Hello'))
    result, _, _, _ = parse_wire(wire(body))
    assert result[3] == 'Hello'


def test_valid_chunk_extension_whitespace_and_quoted_octets_are_bounded_metadata():
    body = event(response=response())
    chunk = hex(len(body))[2:].encode() + b' \t; name = "quoted\\\"\t\x80";flag\r\n' + body
    result, _, _, _ = parse_wire(wire(chunk, framing='chunked'))
    assert result[0] == 'completed'


def test_known_no_commit_proof_is_not_sent_but_none_alone_is_ambiguous(prepared, monkeypatch):
    capability, admission, _ = prepared
    sock, _ = install(monkeypatch, wire(event(response=response())))
    admission.on_dispatch = lambda: None
    admission.dispatch_definitely_not_sent = lambda: True
    result = transport.send_once(capability)
    assert result.observation.dispatch_state == 'not_sent'
    assert result.observation.terminal_kind == 'none' and not sock.sent
    assert not admission.observations


@pytest.mark.parametrize('extension', [b';x="bad\x7f"', b';x="bad\\\x7f"'])
def test_chunk_quoted_extension_rejects_del_even_when_escaped(extension):
    with pytest.raises(transport.TransportError, match='invalid_headers'):
        parse_wire(wire(b'1' + extension + b'\r\nx\r\n', framing='chunked'))


@pytest.mark.parametrize('unsafe', [RuntimeError('synthetic-private'),
                                  transport.TransportError('synthetic-private')])
def test_callback_exception_sanitizer_does_not_publish_untrusted_code(prepared, monkeypatch, unsafe):
    capability, admission, lease = prepared
    sock, _ = install(monkeypatch, wire(event(response=response())))

    def fail_receive():
        raise unsafe

    sock.before_recv = fail_receive
    result = transport.send_once(capability)
    assert result.text is None and result.observation.terminal_kind == 'unknown'
    assert result.delivery_error_code == 'stale_binding'
    assert b'synthetic-private' not in result.observation.canonical_bytes()
    assert len(admission.dispatches) == 1 and len(sock.sent) == 2
    assert sock.closed and not lease._token

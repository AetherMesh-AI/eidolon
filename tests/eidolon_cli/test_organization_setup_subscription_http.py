"""All HTTP fixtures are injected; no sockets, credentials or provider calls."""
import json
import threading

import pytest

from eidolon_cli import organization_setup_subscription_http as module


class FakeSocket:
    def shutdown(self, _):
        pass


class Reply:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, limit):
        return self.body[:limit]


def transport(monkeypatch, reply, *, before_send=None):
    calls = []

    class Connection:
        def __init__(self, host, timeout):
            self.host, self.sock, self.auto_open = host, None, 1

        def connect(self):
            self.sock = FakeSocket()

        def close(self):
            self.sock = None

        def send(self, data):
            calls.append(data)

        def request(self, method, path, body=None, headers=None):
            assert self.auto_open == 0
            if before_send:
                before_send()
            self.send((self.host, method, path, body, headers))

        def getresponse(self):
            return reply

    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    return calls


@pytest.mark.parametrize('url,form,bearer,body,expected', [
    (module.TOKEN, {'code': 'synthetic'}, None, b'{"ok":true}', {'ok': True}),
    (module.JWKS, None, None, b'{"keys":[]}', {'keys': []}),
    (module.MODELS, None, 'synthetic-bearer', b'{"models":[]}', {'models': []}),
    (module.REVOKE, {'token': 'synthetic'}, None, b'', True),
])
def test_fixed_operations_one_send(monkeypatch, url, form, bearer, body, expected):
    calls = transport(monkeypatch, Reply(body))
    assert module.request(url, form, bearer=bearer) == expected
    assert len(calls) == 1
    assert calls[0][0] == ('api.openai.com' if url == module.MODELS else 'auth.openai.com')
    assert ('Authorization' in calls[0][4]) == (url == module.MODELS)


@pytest.mark.parametrize('url,form,bearer', [
    ('https://attacker.invalid/token', {'code': 'synthetic'}, None),
    (module.JWKS, {'code': 'synthetic'}, None),
    (module.MODELS, None, None),
    (module.MODELS, None, 'bad\r\nheader'),
    (module.TOKEN, {'code': 'synthetic'}, 'synthetic'),
    (module.REVOKE, None, None),
])
def test_bad_operation_zero_connections(monkeypatch, url, form, bearer):
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', lambda *a, **k: pytest.fail('connection'))
    with pytest.raises(module.HttpError, match='invalid_request'):
        module.request(url, form, bearer=bearer)


@pytest.mark.parametrize('reply', [Reply(b'{}', 302), Reply(b'{}', 429),
    Reply(b'{}', headers={'Content-Encoding': 'gzip'}),
    Reply(b'{}', headers={'Content-Length': '99999999'}),
    Reply(b'{"x":1,"x":2}'), Reply(b'{"x":NaN}'), Reply(b'{"x":1e9999}'),
    Reply(b'\xff'), Reply(b'[]')])
def test_bad_response_never_retries(monkeypatch, reply):
    calls = transport(monkeypatch, reply)
    with pytest.raises(module.HttpError, match='request_failed'):
        module.request(module.JWKS)
    assert len(calls) == 1


def test_revocation_requires_empty_200(monkeypatch):
    calls = transport(monkeypatch, Reply(b'{}'))
    with pytest.raises(module.HttpError):
        module.request(module.REVOKE, {'token': 'synthetic'})
    assert len(calls) == 1


def test_cancel_between_connect_and_send(monkeypatch):
    cancel = threading.Event()
    calls = transport(monkeypatch, Reply(b'{}'), before_send=cancel.set)
    with pytest.raises(module.HttpError):
        module.request(module.MODELS, bearer='synthetic', cancel=cancel)
    assert calls == []


def test_json_depth_bound():
    raw = json.dumps({'x': [[[[0]]]]}).encode()
    assert module._json(raw, len(raw)) == {'x': [[[[0]]]]}
    raw = ('{"x":' + '[' * 34 + '0' + ']' * 34 + '}').encode()
    with pytest.raises(module.HttpError):
        module._json(raw, len(raw))


def test_real_lease_protocol_never_polls_blocking_is_set(monkeypatch):
    calls = transport(monkeypatch, Reply(b'{"models":[]}'))
    class Cancellation:
        def is_set(self):
            pytest.fail('blocking durable poll reached HTTP watchdog')
        def network_cancelled(self):
            return False
        def try_send_allowed(self):
            return False
    with pytest.raises(module.HttpError):
        module.request(module.MODELS, bearer='synthetic', cancel=Cancellation())
    assert calls == []


def test_deadline_rechecked_after_physical_gate(monkeypatch):
    import time
    calls = transport(monkeypatch, Reply(b'{"models":[]}'))
    class Cancellation:
        def network_cancelled(self):
            return False
        def try_send_allowed(self):
            time.sleep(.08)
            return True
    with pytest.raises(module.HttpError):
        module.request(module.MODELS, bearer='synthetic', cancel=Cancellation(), _timeout=.03)
    assert calls == []


def test_incomplete_lease_protocol_never_falls_back(monkeypatch):
    calls = transport(monkeypatch, Reply(b'{}'))
    class Cancellation:
        def is_set(self):
            pytest.fail('unsafe fallback')
        def try_send_allowed(self):
            return True
    with pytest.raises(module.HttpError):
        module.request(module.JWKS, cancel=Cancellation())
    assert calls == []

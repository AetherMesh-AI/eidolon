"""Fixed-operation, one-send subscription HTTP with bounded DNS and reads.

Private backend transport. It accepts no user-selected endpoints or headers.
Connect-only workers never hold forms/bearer credentials or send HTTP requests.
"""
from __future__ import annotations

import http.client
import json
import math
import socket
import threading
import time
from urllib.parse import urlencode

TOKEN = 'https://auth.openai.com/api/accounts/oauth/token'
JWKS = 'https://auth.openai.com/.well-known/jwks.json'
MODELS = 'https://api.openai.com/v1/models'
REVOKE = 'https://auth.openai.com/api/accounts/oauth/revoke'
_CONNECT_SLOTS = threading.BoundedSemaphore(4)


class HttpError(RuntimeError):
    def __init__(self, reason='request_failed'):
        self.reason = reason if reason in {'invalid_request', 'request_failed'} else 'request_failed'
        super().__init__('Subscription HTTP: ' + self.reason)


def _text(value, maximum):
    return (type(value) is str and 0 < len(value) <= maximum
            and all(33 <= ord(char) <= 126 for char in value))


def _operation(url, form, bearer):
    operations = {TOKEN: ('auth.openai.com', '/api/accounts/oauth/token', 65536),
                  JWKS: ('auth.openai.com', '/.well-known/jwks.json', 65536),
                  MODELS: ('api.openai.com', '/v1/models', 1048576),
                  REVOKE: ('auth.openai.com', '/api/accounts/oauth/revoke', 0)}
    if type(url) is not str or url not in operations:
        raise HttpError('invalid_request')
    if url in {TOKEN, REVOKE}:
        if (type(form) is not dict or not 1 <= len(form) <= 12 or bearer is not None
                or any(not _text(key, 128) or not _text(value, 16384)
                       for key, value in form.items())
                or len(urlencode(form).encode('ascii')) > 65536):
            raise HttpError('invalid_request')
    elif form is not None or (url == JWKS and bearer is not None):
        raise HttpError('invalid_request')
    if url == MODELS and not _text(bearer, 16384):
        raise HttpError('invalid_request')
    return operations[url]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HttpError()
        result[key] = value
    return result


def _constant(_):
    raise HttpError()


def _json(raw, limit):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise HttpError()
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise HttpError()
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 32 or (type(item) is float and not math.isfinite(item)):
                raise HttpError()
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
        return value
    except Exception:
        raise HttpError() from None


def request(url, form=None, *, cancel=None, bearer=None, _timeout=15, _slots=None):
    """One request with bounded caller time, including stalled DNS resolution.

    A capped daemon worker may only connect, never send the authorization form.
    Timeout/cancel abandons it without releasing its slot until it really exits.
    A late connection is closed. Auto-connect is disabled for every HTTP send.
    Cancellation after physical send admission cannot undo remote work.
    """
    host, path, limit = _operation(url, form, bearer)
    if type(_timeout) not in {int, float} or not 0 < _timeout <= 15:
        raise HttpError('invalid_request')
    deadline = time.monotonic() + _timeout
    abandoned, connected, finished = threading.Event(), threading.Event(), threading.Event()
    response_socket = [None]

    def network_cancelled():
        if cancel is None:
            return False
        # Real leases expose an event-only query: the watchdog must NEVER wait
        # for a store flock or run filesystem/native/registry inspection.
        predicate = getattr(cancel, 'network_cancelled', None)
        if predicate is None:
            if callable(getattr(cancel, 'try_send_allowed', None)):
                return True  # A malformed real-lease protocol cannot fall back.
            predicate = cancel.is_set  # Plain internal threading.Event fixtures.
        try:
            return predicate() is not False
        except Exception:
            return True

    def send_allowed():
        if abandoned.is_set() or time.monotonic() >= deadline or network_cancelled():
            return False
        # Never reuse a time observation made before a cancellation predicate.
        return not abandoned.is_set() and time.monotonic() < deadline

    class GuardedConnection(http.client.HTTPSConnection):
        def send(self, data):
            # Guard every physical header/body send, not merely request assembly.
            # No auto-connect: a watchdog-closed socket can never start new DNS.
            if not send_allowed() or self.sock is None:
                raise HttpError('request_failed')
            gate = getattr(cancel, 'try_send_allowed', None)
            if gate is not None and (not callable(gate) or gate() is not True):
                raise HttpError('request_failed')
            # The nonblocking durable gate has released its lock before HTTP.
            # A later cross-process retirement cannot atomically undo admission.
            if not send_allowed() or self.sock is None:
                raise HttpError('request_failed')
            return super().send(data)

    connection = GuardedConnection(host, timeout=min(10, _timeout))
    connection.auto_open = 0
    slots = _CONNECT_SLOTS if _slots is None else _slots

    def interrupt():
        abandoned.set()
        sock = response_socket[0] or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except Exception:
            pass

    def connect_only():
        try:
            if send_allowed():
                connection.connect()
        except Exception:
            abandoned.set()
        finally:
            # DNS/TLS may return after the caller has already timed out. This
            # worker cannot request(), and cannot resurrect its abandoned socket.
            try:
                if not send_allowed():
                    connection.close()
            except Exception:
                pass
            finally:
                connected.set()
                slots.release()

    def watchdog():
        while not finished.wait(min(.05, max(0, deadline - time.monotonic()))):
            if not send_allowed():
                interrupt()
                return

    if not send_allowed() or not slots.acquire(blocking=False):
        raise HttpError('request_failed')
    worker = threading.Thread(target=connect_only, daemon=True)
    try:
        worker.start()
    except Exception:
        slots.release()
        connection.close()
        raise HttpError('request_failed') from None
    watch = threading.Thread(target=watchdog, daemon=True)
    try:
        watch.start()
        while not connected.wait(min(.05, max(0, deadline - time.monotonic()))):
            if not send_allowed():
                raise HttpError('request_failed')
        # Only this bounded caller can admit a request. The connect worker never
        # holds the form, invokes request, or releases a still-running worker slot.
        if not send_allowed() or connection.sock is None:
            raise HttpError('request_failed')
        data = urlencode(form).encode('ascii') if form is not None else None
        headers = {'Accept': 'application/json'}
        if form is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if bearer is not None:
            headers['Authorization'] = 'Bearer ' + bearer
        connection.request('POST' if form is not None else 'GET', path, body=data, headers=headers)
        response_socket[0] = connection.sock
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise HttpError('request_failed')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > limit):
            raise HttpError('request_failed')
        body = response.read(limit + 1)
        if not send_allowed():
            raise HttpError('request_failed')
        if url == REVOKE:
            if body != b'':
                raise HttpError('request_failed')
            return True
        return _json(body, limit)
    except Exception:
        raise HttpError('request_failed') from None
    finally:
        finished.set()
        interrupt()


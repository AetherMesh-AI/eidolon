"""Synthetic TLS/native-vault edges for the real, host-native subscription path.

No production constructor, owner gate, store, listener, auth verifier, catalog,
policy, admission or sender is replaced. Never install outside disposable tests.
The private RSA key and vault values are synthetic and live only in this process.
"""
from contextlib import closing
import hashlib
import http.client
import io
import ipaddress
import json
from pathlib import Path
import sqlite3
import socket
import threading
import time
import traceback
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import yaml

MODEL = 'synthetic-subscription-model'
CLIENT = 'synthetic-subscription-client-never-real'
ACCESS = 'synthetic-subscription-access-never-real'
TEAM = {
    'executive': {'name': 'Ash', 'purpose': 'Coordinate synthetic studio work',
                  'responsibilities': ['Review outcomes']},
    'members': [
        {'id': 'studio-lead', 'name': 'Ember', 'role': 'Manager',
         'manager_id': 'executive', 'team': 'studio',
         'capabilities': ['request.plan', 'request.integrate'],
         'purpose': 'Plan studio drafts', 'responsibilities': ['Coordinate drafts']},
        {'id': 'studio-writer', 'name': 'Flint', 'role': 'Worker',
         'manager_id': 'studio-lead', 'team': 'studio', 'capabilities': ['work.draft'],
         'purpose': 'Write studio drafts', 'responsibilities': ['Produce reviewed drafts']},
    ],
}
SUGGESTIONS = {'jobs': 'Prepare clear studio drafts for owner review.',
               'goals': 'Deliver reviewed drafts with explicit acceptance criteria.'}


def snapshot(home):
    """Read evidence independently of the gateway without initializing anything."""
    home = Path(home)
    with closing(sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table):
            return [dict(row) for row in conn.execute('SELECT * FROM ' + table)] if table in tables else []
        return {'model': (yaml.safe_load((home / 'config.yaml').read_text()) or {}).get('model', {}),
                'metadata': rows('organization_metadata'),
                'executionPolicy': rows('organization_policy'),
                'budget': rows('organization_owner_budget_policy'),
                'generations': rows('organization_team_generations'),
                'teams': rows('organization_team_setup'),
                'subscriptionPolicy': rows('organization_setup_subscription_policy_receipts'),
                'attempts': rows('organization_setup_subscription_attempts'),
                'dispatches': rows('organization_setup_subscription_dispatches'),
                'outcomes': rows('organization_setup_subscription_outcomes'),
                'agents': rows('agents'),
                'counts': {table: len(rows(table)) for table in (
                    'objectives', 'requests', 'tasks', 'organization_model_calls', 'owner_chat_threads')}}


def seed_profile(home, *, legacy=False):
    """Empty means no real organization work, including when old setup debt exists."""
    from eidolon_cli.organization_metadata import save_metadata
    from eidolon_cli.organization_store import OrganizationStore
    home = Path(home).resolve(strict=True)
    home.chmod(0o700)
    path = home / 'organization' / 'state.db'
    identity = save_metadata(path, display_name='Synthetic Subscription Studio',
        owner_display_name='Synthetic Owner', expected_organization_id=None,
        expected_revision=None, idempotency_key='synthetic-native-identity')['metadata']
    OrganizationStore(path)
    if legacy:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('INSERT INTO organization_team_generations '
                '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
                'VALUES (?,?,?,?,?,?,?,0)', ('synthetic-legacy', identity['organizationId'],
                    'synthetic-old-input', 'synthetic-old-source', 'failed', 1, 200))
            conn.commit()
    return identity


class Vault:
    """The only replaced storage layer is the native key-value primitive."""
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def put(self, binding, value):
        from eidolon_cli.organization_credential_native import binding_reference
        reference = binding_reference(binding)
        with self.lock:
            self.values[reference] = value
        return reference

    def consume(self, reference, binding, callback):
        from eidolon_cli.organization_credential_native import binding_reference
        assert reference == binding_reference(binding)
        with self.lock:
            value = self.values[reference]
        callback(value)

    def delete(self, reference, binding):
        from eidolon_cli.organization_credential_native import binding_reference
        assert reference == binding_reference(binding)
        with self.lock:
            self.values.pop(reference, None)


class SyntheticEdges:
    def __init__(self, home, *, evidence=None, mode='complete'):
        self.home = Path(home).resolve(strict=True)
        self.evidence = Path(evidence) if evidence else None
        self.mode = mode
        self.vault = Vault()
        self.events = []
        self.codes = {}
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.lock = threading.Lock()
        self.restores = []

    def record(self, kind, **detail):
        value = {'kind': kind, 'physicalNetworkSend': False, **detail}
        with self.lock:
            self.events.append(value)
            if self.evidence:
                with (self.evidence / 'subscription-events.jsonl').open('a') as stream:
                    stream.write(json.dumps(value, sort_keys=True) + '\n')

    def record_denial(self, kind, operation, destination):
        # Function names locate cold-import/background callers without recording
        # arguments, source text, URLs, headers, tokens or user profile paths.
        frames = traceback.extract_stack(limit=16)[:-1]
        public_hosts = {'raw.githubusercontent.com', 'models.dev', 'huggingface.co',
                        'openaipublic.blob.core.windows.net'}
        self.record(kind, operation=operation,
                    knownPublicHost=destination if destination in public_hosts else '<redacted>',
                    destinationHash=hashlib.sha256(str(destination).encode()).hexdigest(),
                    stack=[{'file': Path(frame.filename).name, 'function': frame.name[:120],
                            'line': frame.lineno} for frame in frames])

    def patch(self, obj, name, value):
        self.restores.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def install(self):
        from eidolon_cli import organization_setup_subscription_store as store
        # Keep backend=None, _synthetic=False and every native filesystem/ACL check.
        self.patch(store, 'native_backend', lambda directory: self.vault)
        self.patch(webbrowser, 'open', self.browser_open)
        edges = self
        def connect(connection):
            if connection.host not in {'auth.openai.com', 'api.openai.com'}:
                edges.record_denial('denied-tls', 'HTTPSConnection.connect', connection.host)
                raise AssertionError('Unexpected external TLS endpoint')
            connection.sock = SyntheticSocket(edges, connection.host)
        self.patch(http.client.HTTPSConnection, 'connect', connect)
        self.install_network_guards()
        self.record('installed', nativeStoreMode='production', platform=__import__('sys').platform)
        return self

    def install_network_guards(self):
        # Also installed in registered-RPC pytest, where no desktop bootstrap runs.
        # A regression through another SDK or urllib cannot reach a real provider.
        def loopback(host):
            try:
                if isinstance(host, bytes):
                    host = host.decode('ascii')
                return ipaddress.ip_address(host).is_loopback
            except (ValueError, TypeError, UnicodeError):
                return False

        def deny(operation, destination):
            self.record_denial('denied-network', operation, destination)
            raise AssertionError('Synthetic fixture denied non-loopback network')

        def check(sock, address, operation):
            if sock.family != getattr(socket, 'AF_UNIX', None) and (not isinstance(address, tuple) or not loopback(address[0])):
                deny(operation, address[0] if isinstance(address, tuple) else address)

        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        original_sendto, original_getaddrinfo = socket.socket.sendto, socket.getaddrinfo
        def connect(sock, address):
            check(sock, address, 'socket.connect')
            return original_connect(sock, address)
        def connect_ex(sock, address):
            check(sock, address, 'socket.connect_ex')
            return original_connect_ex(sock, address)
        def sendto(sock, data, *args):
            check(sock, args[-1], 'socket.sendto')
            return original_sendto(sock, data, *args)
        def getaddrinfo(host, *args, **kwargs):
            if host is not None and not loopback(host):
                deny('getaddrinfo', host)
            return original_getaddrinfo(host, *args, **kwargs)
        def gethostbyname(host):
            if not loopback(host):
                deny('gethostbyname', host)
            return host.decode('ascii') if isinstance(host, bytes) else host
        def gethostbyname_ex(host):
            address = gethostbyname(host)
            return address, [], [address]
        def getnameinfo(address, flags):
            if not isinstance(address, tuple) or not loopback(address[0]):
                deny('getnameinfo', address[0] if isinstance(address, tuple) else address)
            return str(address[0]), str(address[1])
        self.patch(socket.socket, 'connect', connect)
        self.patch(socket.socket, 'connect_ex', connect_ex)
        self.patch(socket.socket, 'sendto', sendto)
        self.patch(socket, 'getaddrinfo', getaddrinfo)
        self.patch(socket, 'gethostbyname', gethostbyname)
        self.patch(socket, 'gethostbyname_ex', gethostbyname_ex)
        self.patch(socket, 'gethostbyaddr', gethostbyname_ex)
        self.patch(socket, 'getnameinfo', getnameinfo)
        if hasattr(socket.socket, 'sendmsg'):
            original_sendmsg = socket.socket.sendmsg
            def sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
                if address is not None:
                    check(sock, address, 'socket.sendmsg')
                return original_sendmsg(sock, buffers, ancdata, flags, address)
            self.patch(socket.socket, 'sendmsg', sendmsg)

    def close(self):
        for obj, name, original in reversed(self.restores):
            setattr(obj, name, original)
        self.restores.clear()

    def browser_open(self, url):
        from eidolon_cli.organization_setup_subscription_auth import AUTHORIZE, RESOURCE
        endpoint = urlsplit(url)
        assert endpoint.scheme + '://' + endpoint.netloc + endpoint.path == AUTHORIZE
        query = {key: value[0] for key, value in parse_qs(endpoint.query, strict_parsing=True).items()}
        assert query['resource'] == RESOURCE and query['code_challenge_method'] == 'S256'
        assert query['client_id'] in {'dynamic_agent_client', CLIENT}
        callback = urlsplit(query['redirect_uri'])
        assert callback.hostname == '127.0.0.1' and callback.path == '/auth/callback'
        code = 'synthetic-code-' + str(uuid.uuid4())
        self.codes[code] = query
        from tui_gateway import server
        authority = server._subscription_installation_authority()
        assert authority is not None and authority.root == self.home and authority.profile == self.home
        self.record('consent', dynamicRegistration=query['client_id'] == 'dynamic_agent_client',
                    capturedHostRoot=str(authority.root), capturedProfile=str(authority.profile))
        connection = http.client.HTTPConnection('127.0.0.1', callback.port, timeout=10)
        try:
            connection.request('GET', callback.path + '?' + urlencode({
                'state': query['state'], 'code': code, 'client_id': CLIENT}))
            response = connection.getresponse()
            assert response.status == 200 and response.read() == b'Return to Eidolon.'
        finally:
            connection.close()
        return True

    def oauth(self, path, body):
        from eidolon_cli.organization_setup_subscription_auth import ISSUER, SCOPES
        if path == '/.well-known/jwks.json':
            assert body == b''
            self.record('jwks')
            return {'keys': [{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key())),
                              'kid': 'synthetic-key', 'alg': 'RS256', 'use': 'sig'}]}
        assert path == '/api/accounts/oauth/token'
        form = {key: value[0] for key, value in parse_qs(body.decode('ascii'), strict_parsing=True).items()}
        assert form['grant_type'] == 'authorization_code' and form['client_id'] == CLIENT
        query = self.codes.pop(form['code'])
        import base64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        assert challenge == query['code_challenge'] and form['redirect_uri'] == query['redirect_uri']
        now = int(time.time())
        identity = jwt.encode({'iss': ISSUER, 'aud': CLIENT, 'sub': 'synthetic-account',
            'iat': now, 'exp': now + 3600, 'nonce': query['nonce']}, self.key,
            algorithm='RS256', headers={'kid': 'synthetic-key'})
        self.record('oauth-exchange')
        return {'access_token': ACCESS, 'refresh_token': 'synthetic-refresh-never-real',
                'id_token': identity, 'token_type': 'Bearer', 'expires_in': 3600, 'scope': SCOPES}

    def inference(self, body):
        from eidolon_cli.organization_team_generation import SYSTEM, IMPROVE_SYSTEM
        value = json.loads(body)
        assert set(value) == {'model', 'instructions', 'input', 'stream', 'store'}
        assert value['model'] == MODEL and value['stream'] is True and value['store'] is False
        assert len(value['input']) == 1 and value['input'][0]['role'] == 'user'
        inputs = json.loads(value['input'][0]['content'])
        field = inputs.get('field')
        assert value['instructions'] == (IMPROVE_SYSTEM if field else SYSTEM)
        evidence = snapshot(self.home)
        rows = [row for row in evidence['generations'] if row['status'] == 'running']
        assert len(rows) == 1 and rows[0]['dispatched'] == 1
        assert len(evidence['dispatches']) == len(evidence['attempts'])
        assert not evidence['teams'] and not any(evidence['counts'].values())
        self.record('responses', operation='improve_' + field if field else 'generate',
                    requestSha256=hashlib.sha256(body).hexdigest(),
                    idempotencyKey=rows[0]['idempotency_key'],
                    sourceRevision=rows[0]['source_revision'],
                    durableDispatch=True, model=value['model'])
        if self.mode == 'uncertain':
            event = {'type': 'response.created', 'sequence_number': 1,
                     'response': {'id': 'resp_synthetic', 'status': 'in_progress', 'output': []}}
        else:
            text = json.dumps({'suggestion': SUGGESTIONS[field], 'assumptions': []} if field else TEAM)
            event = {'type': 'response.completed', 'sequence_number': 7, 'response': {
                'id': 'resp_synthetic', 'status': 'completed', 'output': [
                    {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
                     'content': [{'type': 'output_text', 'text': text}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}}
        return ('data: ' + json.dumps(event) + '\n\n').encode()


class SyntheticSocket:
    """Decrypted TLS bytes only: production HTTP/SSE parsers read the result."""
    def __init__(self, edges, host):
        self.edges, self.host = edges, host
        self.sent = bytearray()
        self.data = None
        self.closed = False

    def sendall(self, data):
        assert not self.closed
        self.sent.extend(data)

    def _response(self):
        if self.data is not None:
            return
        head, separator, body = bytes(self.sent).partition(b'\r\n\r\n')
        assert separator
        lines = head.decode('ascii').split('\r\n')
        method, path, protocol = lines[0].split(' ')
        headers = dict(line.lower().split(': ', 1) for line in lines[1:])
        assert protocol == 'HTTP/1.1' and headers['host'] == self.host
        assert len(body) == int(headers.get('content-length', '0'))
        content_type = 'application/json'
        if self.host == 'auth.openai.com':
            assert method == ('GET' if path == '/.well-known/jwks.json' else 'POST')
            data = json.dumps(self.edges.oauth(path, body)).encode()
        else:
            assert headers['authorization'] == 'bearer ' + ACCESS
            if path == '/v1/models':
                assert method == 'GET' and not body
                self.edges.record('models')
                data = json.dumps({'models': [{'slug': MODEL, 'display_name': 'Synthetic subscription model',
                                               'visibility': 'list'}]}).encode()
            else:
                assert path == '/v1/responses' and method == 'POST'
                content_type = 'text/event-stream'
                data = self.edges.inference(body)
        self.data = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: ' + content_type.encode()
            + b'\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data)

    def makefile(self, mode, *args, **kwargs):
        assert mode == 'rb'
        self._response()
        return self.data

    def recv(self, count):
        assert not self.closed and 0 < count <= 8192
        self._response()
        return self.data.read(min(count, 137))

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True

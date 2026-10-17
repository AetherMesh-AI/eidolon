"""Synthetic-only protocol tests. No provider, browser, registration, or vault calls."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from eidolon_cli import organization_setup_subscription_auth as module
from eidolon_cli.organization_setup_subscription_store import TrustedBinding


ORG, OWNER, HOST = (str(uuid.uuid4()) for _ in range(3))


class FakeListener:
    instances = []
    def __init__(self, callback, expires_at, clock):
        self.callback, self.closed, self.started = callback, False, False
        self.port = 12345
        self.redirect_uri = 'http://127.0.0.1:12345/auth/callback'
        self.instances.append(self)
    def start(self):
        self.started = True
    def close(self):
        self.closed = True


class FakeHost:
    def __init__(self):
        self.record = None
        self.events = []
        self.locked = False
    def installation_id(self):
        return HOST
    def inspect(self):
        return self.record
    def begin(self, **kwargs):
        self.record = SimpleNamespace(**kwargs, stage='pending')
        self.events.append('begin')
    def admit_exchange(self, attempt_id):
        assert attempt_id == self.record.attempt_id and self.record.stage == 'pending'
        self.record.stage = 'exchange_admitted'
        self.events.append('admitted')
    def cancel(self, attempt_id):
        assert self.record.attempt_id == attempt_id
        self.record.stage = 'cancelled'
        self.events.append('cancelled')
    @contextmanager
    def publication_guard(self, attempt_id):
        assert self.record.attempt_id == attempt_id and self.record.stage == 'exchange_admitted'
        self.locked = True
        self.events.append('publishing')
        self.record.stage = 'publishing'
        def commit(revision):
            assert revision == self.record.expected_store_revision + 1
            self.events.append('committed')
        try:
            yield SimpleNamespace(record=self.record, commit=commit)
            self.record.stage = 'published'
        finally:
            self.locked = False

    @contextmanager
    def reconciliation_guard(self, attempt_id):
        assert self.record.attempt_id == attempt_id
        assert self.record.stage in {'publishing', 'published'}
        self.locked = True
        committed = []
        def commit(revision):
            assert revision == self.record.expected_store_revision + 1
            committed.append(revision)
        try:
            yield SimpleNamespace(record=self.record, commit=commit)
            assert committed
            self.record.stage = 'published'
        finally:
            self.locked = False


class FakeStore:
    def __init__(self):
        self.writes = []
        self.binding = None
        self.revision, self.phase = 0, 'absent'
        self.uncertain = False
        self.cleanup_pending = False
        self.history = {'issuedThrough': '0', 'retiredThrough': '0', 'state': 'available'}
        self.ticket = None
        self.operation_stage = 'none'
    def status(self):
        return {'revision': self.revision, 'state': self.phase, 'cleanup_pending': self.cleanup_pending}
    def history_status(self):
        return dict(self.history)
    def ticket_status(self):
        return {'state': self.operation_stage, 'ticket': self.ticket, 'revision': self.revision,
                'verification': 'metadata_only'}
    def returning_binding(self):
        return self.binding
    def connect_grant(self, binding, **kwargs):
        assert kwargs['expected_revision'] == self.revision
        self.binding = binding
        self.revision += 1
        self.phase = 'active'
        self.writes.append(kwargs)
        if self.uncertain:
            error = RuntimeError('synthetic-token-must-not-leak')
            error.reason = 'publication_uncertain'
            raise error
        return self.revision
    def disconnect(self, *, expected_revision):
        assert expected_revision == self.revision
        self.phase = 'disconnected'
        self.revision += 1
        return self.status()
    def prepare_ticket(self, binding, revision, *, kind, source_revision, owner_guard, cancel):
        assert binding == self.binding and revision == self.revision
        self.ticket = {'version': 1, 'sequence': '1', 'nonce': 'b' * 64, 'kind': kind,
                       'bindingDigest': 'c' * 64, 'authorizationEpoch': str(uuid.uuid4()),
                       'priorStoreRevision': revision, 'sourceRevision': source_revision}
        self.operation_stage = 'prepared'
        return dict(self.ticket)
    def reconcile_ticket(self, binding, *, ticket, owner_guard, cancel):
        assert binding == self.binding and ticket == self.ticket
        receipt = ({'kind': 'revoke', 'revision': self.revision, 'localRetired': True,
                    'remoteRevocation': 'unconfirmed'} if self.operation_stage == 'completed' else None)
        return {'state': self.operation_stage, 'ticket': dict(ticket), 'receipt': receipt,
                'revision': self.revision, 'verification': 'verified_current'}
    def disconnect_grant(self, binding, *, expected_revision, ticket, owner_guard, cancel):
        assert binding == self.binding and owner_guard() is True and not cancel.is_set()
        self.disconnect(expected_revision=expected_revision)
        self.operation_stage = 'completed'
        return {'kind': 'revoke', 'revision': self.revision, 'localRetired': True,
                'remoteRevocation': 'unconfirmed'}, None
    def local_disconnect(self, binding, *, expected_revision, owner_guard, cancel):
        assert binding == self.binding and owner_guard() is True and not cancel.is_set()
        self.disconnect(expected_revision=expected_revision)
        return {'kind': 'local', 'revision': self.revision, 'localRetired': True,
                'remoteRevocation': 'unconfirmed'}
    def reconcile_auth_attempt(self, binding, **kwargs):
        assert binding == self.binding
        assert kwargs['auth_attempt_id'] == self.writes[-1]['auth_attempt_id']
        assert kwargs['prior_store_revision'] + 1 == self.revision
        assert kwargs['granted_scopes'] == self.writes[-1]['granted_scopes']
        return self.revision


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(module, 'LoopbackListener', FakeListener)
    host, store, opened, calls = FakeHost(), FakeStore(), [], []
    now = [time.time()]
    owner_live = [True]
    setup_live = [True]
    authority = [False]
    @contextmanager
    def publication_authority():
        assert not authority[0]
        authority[0] = True
        try:
            yield
        finally:
            authority[0] = False
    context = module.OwnerContext('/synthetic/profile', ORG, OWNER, 'a' * 64,
                                  object(), object(), lambda: owner_live[0], publication_authority, lambda: setup_live[0])
    tokens = {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
              'id_token': 'synthetic-id', 'token_type': 'Bearer', 'expires_in': 3600,
              'scope': module.SCOPES}
    def http(url, form=None):
        assert not host.locked and not authority[0]
        assert host.events[-1] == 'admitted'
        calls.append((url, form))
        return dict(tokens)
    def browser(url):
        assert not authority[0] and not host.locked
        assert FakeListener.instances[-1].started
        opened.append(url)
        return True
    service = module.SubscriptionAuthService(store, host, context, synthetic=True,
        browser_open=browser, http=http, clock=lambda: now[0])
    service._verifier.verify_initial = lambda token, client, nonce, access: {
        'subject': 'synthetic-account', 'audience': client, 'nonce': nonce,
        'authTime': None, 'authorizedParty': None}
    return SimpleNamespace(service=service, host=host, store=store, opened=opened,
        calls=calls, tokens=tokens, now=now, owner_live=owner_live, setup_live=setup_live, authority=authority)


def begin(rig):
    status = rig.service.begin(rig.store.revision)
    return rig.service._attempt, status


def callback(attempt, **changes):
    fields = {'state': attempt.state, 'code': 'synthetic-code', 'client_id': 'oaiapp_synthetic'}
    fields.update(changes)
    return attempt.listener.callback(fields)


def request(target='/auth/callback?state=synthetic&code=synthetic', host='127.0.0.1:12345', extra=''):
    return ('GET ' + target + ' HTTP/1.1\r\nHost: ' + host + '\r\n' + extra + '\r\n').encode('ascii')


def test_first_authorization_is_backend_only_pkce_and_fixed_resource(rig):
    attempt, status = begin(rig)
    assert set(status) == {'attemptId', 'stage', 'expiresAt'}
    assert status['expiresAt'] <= rig.now[0] + 600
    url = urlsplit(rig.opened[0])
    assert url.scheme + '://' + url.netloc + url.path == module.AUTHORIZE
    query = parse_qs(url.query)
    assert query['client_id'] == ['dynamic_agent_client']
    assert query['agent_name_hint'] == ['Eidolon']
    assert query['ext_agent_host_id'] == [HOST]
    assert query['resource'] == [module.RESOURCE]
    assert query['code_challenge_method'] == ['S256']
    expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge'] == [expected]
    assert query['redirect_uri'] == [attempt.listener.redirect_uri]
    assert not any(secret in repr(rig.service) for secret in (attempt.state, attempt.nonce, attempt.verifier))


def test_one_exchange_then_explicit_publication_commit(rig):
    attempt, _ = begin(rig)
    assert callback(attempt)
    assert len(rig.calls) == len(rig.store.writes) == 1
    url, form = rig.calls[0]
    assert url == module.TOKEN
    assert form['client_id'] == 'oaiapp_synthetic'
    assert 'client_secret' not in form
    assert form['redirect_uri'] == attempt.listener.redirect_uri
    assert form['resource'] == module.RESOURCE
    assert rig.host.events == ['begin', 'admitted', 'publishing', 'committed']
    assert rig.service.status()['stage'] == 'connected'
    assert rig.store.binding.account_id == 'synthetic-account'
    assert not callback(attempt, state='previous-state')
    assert len(rig.calls) == 1


def test_wrong_state_does_not_consume_pending_attempt(rig):
    attempt, _ = begin(rig)
    original = attempt.nonce
    assert not callback(attempt, state='wrong')
    assert attempt.nonce == original and attempt.stage == 'pending'
    assert not rig.calls


def test_callback_scope_cannot_supply_missing_grants(rig):
    attempt, _ = begin(rig)
    rig.tokens['scope'] = 'openid profile email'
    callback(attempt, scope=module.SCOPES)
    assert not rig.store.writes
    assert rig.service.status()['stage'] == 'failed'


@pytest.mark.parametrize('mutation', [
    {'expires_in': 0}, {'expires_in': True}, {'token_type': 'MAC'},
    {'access_token': ''}, {'refresh_token': None}, {'scope': None},
])
def test_invalid_token_response_never_publishes(rig, mutation):
    attempt, _ = begin(rig)
    rig.tokens.update(mutation)
    callback(attempt)
    assert not rig.store.writes


def test_access_denied_validates_state_and_never_exchanges(rig):
    attempt, _ = begin(rig)
    assert not attempt.listener.callback({'state': 'wrong', 'error': 'access_denied'})
    assert attempt.listener.callback({'state': attempt.state, 'error': 'access_denied'})
    assert not rig.calls and attempt.listener.closed
    assert rig.service.status()['stage'] == 'access_denied'


def test_expired_attempt_never_exchanges(rig):
    attempt, _ = begin(rig)
    rig.now[0] += 601
    callback(attempt)
    assert not rig.calls and not rig.store.writes


def test_returning_exact_client_and_account(rig):
    attempt, _ = begin(rig)
    callback(attempt)
    binding = rig.store.binding
    rig.service.execute_operation(rig.service.prepare_operation('revoke', rig.store.revision)['ticket'])
    next_attempt, _ = begin(rig)
    query = parse_qs(urlsplit(rig.opened[-1]).query)
    assert query['client_id'] == [binding.issued_client_id]
    assert 'agent_name_hint' not in query and 'id_token_hint' not in query
    assert next_attempt.listener.callback({'state': next_attempt.state, 'code': 'synthetic-code'})
    assert rig.store.binding == binding


@pytest.mark.parametrize('mismatch', ['client', 'account'])
def test_returning_identity_substitution_fails(rig, mismatch):
    attempt, _ = begin(rig)
    callback(attempt)
    rig.service.execute_operation(rig.service.prepare_operation('revoke', rig.store.revision)['ticket'])
    attempt, _ = begin(rig)
    if mismatch == 'account':
        rig.service._verifier.verify_initial = lambda *args: {'subject': 'other-account'}
        callback(attempt)
    else:
        callback(attempt, client_id='oaiapp_other')
    assert len(rig.store.writes) == 1


def test_cancel_after_admission_suppresses_publication(rig):
    attempt, _ = begin(rig)
    def http(url, form):
        assert rig.host.record.stage == 'exchange_admitted'
        rig.service.cancel(attempt.attempt_id)
        return rig.tokens
    rig.service._http = http
    callback(attempt)
    assert not rig.store.writes
    assert attempt.listener.closed and attempt.stage == 'cancelled'


def test_transport_retirement_fences_outstanding_http(rig):
    attempt, _ = begin(rig)
    def http(url, form):
        module.cancel_transport(rig.service._owner.transport, generation=rig.service._owner.owner_generation)
        return rig.tokens
    rig.service._http = http
    callback(attempt)
    assert not rig.store.writes and attempt.listener.closed
    with pytest.raises(module.AuthError, match='stale_owner'):
        rig.service.status()


def test_exchange_failure_has_no_retry_or_secret_error(rig):
    attempt, _ = begin(rig)
    count = []
    def fail(*args):
        count.append(1)
        raise RuntimeError('synthetic-code synthetic-refresh')
    rig.service._http = fail
    callback(attempt)
    callback(attempt)
    assert count == [1] and not rig.store.writes
    assert rig.service.status()['stage'] == 'failed'
    assert 'synthetic' not in json.dumps(rig.service.status())


def test_uncertain_publication_only_reconciles_exact_attempt(rig):
    attempt, _ = begin(rig)
    rig.store.uncertain = True
    callback(attempt)
    assert rig.service.status()['stage'] == 'publication_uncertain'
    assert rig.service.reconcile_current() == 1
    assert len(rig.calls) == len(rig.store.writes) == 1


def test_owner_guard_must_return_exact_true(rig):
    rig.owner_live[0] = None
    with pytest.raises(module.AuthError, match='stale_owner'):
        begin(rig)
    assert not rig.opened and not rig.calls


def test_parser_accepts_exact_loopback_origin_form():
    assert module.parse_callback(request(), '127.0.0.1', 12345) == {'state': 'synthetic', 'code': 'synthetic'}


@pytest.mark.parametrize('raw,peer', [
    (request(host='localhost:12345'), '127.0.0.1'),
    (request(host='127.0.0.1:23456'), '127.0.0.1'),
    (request(), '127.0.0.2'),
    (request('/callback?state=x&code=y'), '127.0.0.1'),
    (request('http://127.0.0.1:12345/auth/callback?state=x&code=y'), '127.0.0.1'),
    (request('/auth/callback?state=x&code=y#fragment'), '127.0.0.1'),
    (request('/auth/callback?state=x&state=x&code=y'), '127.0.0.1'),
    (request('/auth/callback?state=x&code=y&%73tate=x'), '127.0.0.1'),
    (request('/auth/callback?state=x&code=%zz'), '127.0.0.1'),
    (request('/auth/callback?state=x&code=%0A'), '127.0.0.1'),
    (request(extra='Host: 127.0.0.1:12345\r\n'), '127.0.0.1'),
    (request(extra='Content-Length: 1\r\n'), '127.0.0.1'),
    (request(extra='Transfer-Encoding: chunked\r\n'), '127.0.0.1'),
    (request() + b'body', '127.0.0.1'),
    (request().replace(b'GET ', b'POST ', 1), '127.0.0.1'),
    (request('/auth/callback?state=x&code=' + 'a' * 9000), '127.0.0.1'),
])
def test_parser_rejects_unsafe_requests_without_echo(raw, peer):
    with pytest.raises(module.AuthError) as caught:
        module.parse_callback(raw, peer, 12345)
    assert 'synthetic' not in str(caught.value) and 'callback?' not in str(caught.value)


@pytest.fixture(scope='module')
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed(signing_key, **changes):
    claims = {'iss': module.ISSUER, 'aud': 'oaiapp_synthetic', 'exp': int(time.time()) + 600,
              'sub': 'synthetic-account', 'nonce': 'synthetic-nonce', 'iat': int(time.time())}
    claims.update(changes)
    return jwt.encode(claims, signing_key, algorithm='RS256', headers={'kid': 'synthetic-key'})


def verifier(signing_key):
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    entry.update(kid='synthetic-key', alg='RS256', use='sig')
    calls = []
    def http(url):
        assert url == module.JWKS
        calls.append(url)
        return {'keys': [entry]}
    return module.TokenVerifier(http), calls, entry


def test_valid_signed_jwt_uses_fixed_jwks_and_bounded_cache(signing_key):
    verify, calls, _ = verifier(signing_key)
    token = signed(signing_key)
    assert verify.verify(token, 'oaiapp_synthetic', 'synthetic-nonce') == 'synthetic-account'
    assert verify.verify(token, 'oaiapp_synthetic', 'synthetic-nonce') == 'synthetic-account'
    assert calls == [module.JWKS]


@pytest.mark.parametrize('claims', [
    {'iss': 'https://attacker.invalid'}, {'aud': 'wrong'}, {'aud': ['oaiapp_synthetic', 'wrong']},
    {'exp': 1}, {'nonce': 'wrong'}, {'sub': ''},
])
def test_wrong_verified_claims_fail(signing_key, claims):
    verify, _, _ = verifier(signing_key)
    with pytest.raises(module.AuthError):
        verify.verify(signed(signing_key, **claims), 'oaiapp_synthetic', 'synthetic-nonce')


def test_wrong_signature_rejected(signing_key):
    verify, _, _ = verifier(signing_key)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(module.AuthError):
        verify.verify(signed(other), 'oaiapp_synthetic', 'synthetic-nonce')


def test_jwt_header_urls_never_followed(signing_key):
    verify, calls, _ = verifier(signing_key)
    token = jwt.encode({'iss': module.ISSUER, 'aud': 'oaiapp_synthetic',
        'exp': int(time.time()) + 300, 'sub': 'synthetic-account', 'nonce': 'synthetic-nonce', 'iat': int(time.time())},
        signing_key, algorithm='RS256', headers={'kid': 'synthetic-key', 'jku': 'https://attacker.invalid'})
    assert verify.verify(token, 'oaiapp_synthetic', 'synthetic-nonce') == 'synthetic-account'
    assert calls == [module.JWKS]


def test_non_rs256_rejected_before_fetch(signing_key):
    verify, calls, _ = verifier(signing_key)
    token = jwt.encode({'sub': 'synthetic'}, 'synthetic-only-key-long-enough-32bytes', algorithm='HS256')
    with pytest.raises(module.AuthError):
        verify.verify(token, 'oaiapp_synthetic', 'synthetic-nonce')
    assert not calls


def test_duplicate_json_rejected():
    with pytest.raises(module.AuthError):
        module._json(b'{"access_token":"one","access_token":"two"}')


def test_production_disallows_endpoint_and_browser_injections(rig):
    with pytest.raises(module.AuthError):
        module.SubscriptionAuthService(rig.store, rig.host, rig.service._owner, http=lambda *args: {})
    with pytest.raises(module.AuthError):
        module.SubscriptionAuthService(rig.store, rig.host,
            replace(rig.service._owner, publication_authority=None))


def test_delayed_generation_a_retirement_does_not_cancel_b(rig):
    attempt, _ = begin(rig)
    old = rig.service._owner
    new = replace(old, owner_generation=object())
    service_b = module.SubscriptionAuthService(FakeStore(), FakeHost(), new,
        synthetic=True, browser_open=lambda url: True, http=lambda *args: {})
    module.cancel_transport(old.transport, generation=old.owner_generation)
    assert attempt.listener.closed
    assert rig.service._retired
    assert service_b.status()['stage'] == 'idle'
    assert not service_b._retired


def test_cancellation_does_not_wait_for_browser_launch(rig):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def browser(url):
        assert not rig.authority[0]
        entered.set()
        assert release.wait(3)
        return True
    rig.service._browser = browser
    def start():
        try:
            begin(rig)
        except module.AuthError as error:
            errors.append(error.reason)
        finally:
            finished.set()
    worker = threading.Thread(target=start)
    worker.start()
    try:
        assert entered.wait(3)
        module.cancel_transport(rig.service._owner.transport,
                                generation=rig.service._owner.owner_generation)
        assert rig.service._attempt.listener.closed
    finally:
        release.set()
        worker.join(3)
    assert finished.is_set() and errors
    assert not rig.calls and not rig.store.writes


def test_http_transport_does_not_follow_redirects(monkeypatch):
    calls = []
    class Connection:
        sock = None
        def __init__(self, host, timeout):
            assert host == 'auth.openai.com' and timeout == 10
        def connect(self):
            self.sock = SimpleNamespace(shutdown=lambda how: None)
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
        def getresponse(self):
            return SimpleNamespace(status=302)
        def close(self):
            self.sock = None
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(module.AuthError, match='exchange_failed'):
        module._production_http(module.TOKEN, {'code': 'synthetic-code'})
    assert len(calls) == 1
    assert calls[0][:2] == ('POST', '/api/accounts/oauth/token')


def test_http_transport_refuses_token_selected_endpoint():
    with pytest.raises(module.AuthError, match='invalid_request'):
        module._production_http('https://attacker.invalid/oauth/token', {'code': 'synthetic'})


def test_duplicate_jwks_key_id_refused(signing_key):
    _, _, entry = verifier(signing_key)
    verify = module.TokenVerifier(lambda url: {'keys': [entry, dict(entry)]})
    with pytest.raises(module.AuthError):
        verify.verify(signed(signing_key), 'oaiapp_synthetic', 'synthetic-nonce')


def test_weak_rsa_key_refused():
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    verify, _, _ = verifier(weak)
    with pytest.raises(module.AuthError):
        verify.verify(signed(weak), 'oaiapp_synthetic', 'synthetic-nonce')


@pytest.mark.parametrize('durable_stage, expected', [
    ('pending', 'reauthorization_required'), ('exchange_admitted', 'reauthorization_required'),
    ('publishing', 'reconciliation_required'), ('published', 'completed'),
])
def test_restart_reports_durable_attempt_without_secret_recovery(rig, durable_stage, expected):
    attempt, _ = begin(rig)
    rig.host.record.stage = durable_stage
    rig.service._attempt = None
    status = rig.service.status()
    assert status['stage'] == expected and status['attemptId'] == attempt.attempt_id
    assert status['connectionState'] == 'absent'
    assert not rig.calls and not rig.store.writes


def test_restart_can_cancel_exact_pending_attempt(rig):
    attempt, _ = begin(rig)
    rig.service._attempt = None
    assert rig.service.cancel(attempt.attempt_id)['stage'] == 'cancelled'
    assert rig.host.record.stage == 'cancelled'
    assert not rig.calls


@pytest.fixture
def synthetic_vault():
    from eidolon_cli.organization_credential_native import binding_reference

    class SyntheticVault:
        def __init__(self):
            self.values = {}
            self.reads = 0
        def put(self, binding, key):
            reference = binding_reference(binding)
            self.values[reference] = key
            return reference
        def consume(self, reference, binding, consumer):
            self.reads += 1
            assert reference == binding_reference(binding)
            consumer(self.values[reference])
        def delete(self, reference, binding):
            assert reference == binding_reference(binding)
            self.values.pop(reference, None)

    return SyntheticVault()


@pytest.mark.skipif(os.name != 'posix', reason='Real synthetic store uses POSIX private-file checks')
@pytest.mark.parametrize('uncertain', [False, True])
def test_real_host_store_signed_auth_and_exact_reconciliation(tmp_path, monkeypatch, signing_key, uncertain, request, synthetic_vault):
    from eidolon_cli.organization_setup_subscription_host import HostAuthority, PendingAttempts
    from eidolon_cli.organization_setup_subscription_store import SubscriptionStore, StoreError

    root = tmp_path / 'profile'
    root.mkdir(mode=0o700)
    authority = HostAuthority.capture(lambda: root, lambda: root)
    host = PendingAttempts(authority, synthetic=True)
    store = SubscriptionStore(root, backend=synthetic_vault)
    held = {'host': 0, 'store': 0, 'authority': False}
    guard_calls = []
    database = sqlite3.connect(':memory:')
    request.addfinalizer(database.close)
    def instrument_lock(kind, original):
        @contextmanager
        def locked(*args, **kwargs):
            with original(*args, **kwargs) as value:
                held[kind] += 1
                try:
                    yield value
                finally:
                    held[kind] -= 1
        return locked
    monkeypatch.setattr(host, '_locked', instrument_lock('host', host._locked))
    monkeypatch.setattr(store, '_locked', instrument_lock('store', store._locked))
    def durable_guard():
        # An actual synthetic SQLite query is admitted only above both locks.
        assert not held['host'] and not held['store']
        guard_calls.append('database')
        return database.execute('SELECT 1').fetchone() == (1,)
    def cheap_guard():
        if held['host'] or held['store']:
            assert held['authority']
        guard_calls.append('cheap-under-host' if held['host'] else 'cheap')
        return True
    @contextmanager
    def owner_publication():
        assert not held['host'] and not held['store'] and not held['authority']
        held['authority'] = True
        try:
            yield
        finally:
            held['authority'] = False
    context = module.OwnerContext(str(root), ORG, OWNER, 'a' * 64, object(), object(),
        durable_guard, owner_publication, durable_guard, cheap_guard)
    opened, exchanges = [], []
    monkeypatch.setattr(module, 'LoopbackListener', FakeListener)
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    entry.update(kid='synthetic-key', alg='RS256', use='sig')
    def browser(url):
        opened.append(url)
        return True
    def http(url, form=None):
        if url == module.JWKS:
            assert form is None
            return {'keys': [entry]}
        assert url == module.TOKEN
        assert host.inspect().stage == 'exchange_admitted'
        exchanges.append(form)
        assert len(exchanges) == 1
        nonce = parse_qs(urlsplit(opened[-1]).query)['nonce'][0]
        return {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
                'id_token': signed(signing_key, nonce=nonce), 'token_type': 'Bearer',
                'expires_in': 3600, 'scope': module.SCOPES}
    service = module.SubscriptionAuthService(store, host, context, synthetic=True,
                                            browser_open=browser, http=http)
    if uncertain:
        original_write = store._write
        injected = []
        def uncertain_write(name, data, **kwargs):
            original_write(name, data, **kwargs)
            document = json.loads(data) if name == 'current.json' else {}
            if (name == 'current.json' and kwargs.get('replace')
                    and document.get('body', document).get('phase') == 'active' and not injected):
                injected.append(True)
                raise StoreError('publication_uncertain')
        monkeypatch.setattr(store, '_write', uncertain_write)
    service.begin(0)
    attempt = service._attempt
    assert callback(attempt)
    assert guard_calls.count('cheap-under-host') == 1
    if uncertain:
        assert service.status()['stage'] == 'publication_uncertain'
        assert host.inspect().stage == 'publishing'
        prior_database_checks = guard_calls.count('database')
        assert service.reconcile_current() == 1
        assert guard_calls.count('database') > prior_database_checks
        assert guard_calls.count('cheap-under-host') == 2
    assert service.status()['stage'] == 'connected'
    assert host.inspect().stage == 'published'
    binding, revision = store.connection_descriptor()
    assert revision == 1 and binding.account_id == 'synthetic-account'
    assert binding.issued_client_id == 'oaiapp_synthetic'
    assert len(exchanges) == 1
    assert store.reconcile_auth_attempt(binding, auth_attempt_id=attempt.attempt_id,
        prior_store_revision=0, granted_scopes=frozenset(module.SCOPES.split())) == 1


@pytest.mark.parametrize('stop_reason', ['timeout', 'cancellation'])
def test_delayed_dns_caller_stops_and_late_worker_cannot_send(monkeypatch, stop_reason):
    entered, release, late_closed, caller_finished = (threading.Event() for _ in range(4))
    cancel = threading.Event()
    requests, errors = [], []
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(module, '_CONNECT_SLOTS', slots)
    monkeypatch.setattr(module, '_HTTP_TIMEOUT', .1 if stop_reason == 'timeout' else 10)

    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            entered.set()
            assert release.wait(3)
            self.sock = SimpleNamespace(shutdown=lambda how: None)
        def request(self, *args, **kwargs):
            requests.append(args)
            raise AssertionError('No request may occur after abandonment')
        def close(self):
            if self.sock is not None:
                late_closed.set()
            self.sock = None

    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    def call():
        try:
            module._production_http(module.TOKEN, {'code': 'synthetic-code'}, cancel=cancel)
        except module.AuthError as error:
            errors.append(error.reason)
        finally:
            caller_finished.set()
    caller = threading.Thread(target=call)
    caller.start()
    try:
        assert entered.wait(2)
        if stop_reason == 'cancellation':
            cancel.set()
        # Caller must finish while resolver is STILL stalled, not merely reject
        # a request after resolution eventually finishes.
        assert caller_finished.wait(2)
        assert errors == ['exchange_failed'] and not requests
        assert not release.is_set()
        assert not slots.acquire(blocking=False)
        # Unresolved workers retain their admission slot. More attempts cannot
        # create unbounded threads or bypass the single-worker cap.
        with pytest.raises(module.AuthError, match='exchange_failed'):
            module._production_http(module.JWKS)
        assert not requests
    finally:
        release.set()
        caller.join(3)
    assert late_closed.wait(2)
    assert not requests
    # Wait for the connect worker's actual exit before checking slot recovery.
    assert slots.acquire(timeout=2)
    slots.release()


def test_connected_request_sends_once_and_disables_auto_reconnect(monkeypatch):
    sends = []
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            self.sock = SimpleNamespace(shutdown=lambda how: None)
        def request(self, *args, **kwargs):
            assert self.auto_open == 0
            self.send(b'synthetic-request')
        def send(self, data):
            assert self.sock is not None
            sends.append(data)
        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda key, default=None: default,
                                   read=lambda limit: b'{"synthetic":true}')
        def close(self):
            self.sock = None
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    assert module._production_http(module.JWKS) == {'synthetic': True}
    assert sends == [b'synthetic-request']


def test_cancel_between_connect_and_physical_send_rejects_request(monkeypatch):
    cancel = threading.Event()
    sends = []
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            self.sock = SimpleNamespace(shutdown=lambda how: None)
        def request(self, *args, **kwargs):
            cancel.set()
            self.send(b'synthetic-request')
        def send(self, data):
            sends.append(data)
        def close(self):
            self.sock = None
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(module.AuthError, match='exchange_failed'):
        module._production_http(module.TOKEN, {'code': 'synthetic'}, cancel=cancel)
    assert not sends


def test_passive_status_created_context_cannot_bypass_setup_guard(rig):
    assert rig.service.status()['stage'] == 'idle'
    rig.setup_live[0] = False
    assert rig.service.status()['stage'] == 'idle'
    with pytest.raises(module.AuthError, match='setup_unavailable'):
        begin(rig)
    assert not rig.opened and not rig.calls


def test_setup_end_blocks_callback_but_not_cancel_or_disconnect(rig):
    attempt, _ = begin(rig)
    rig.setup_live[0] = False
    callback(attempt)
    assert not rig.calls and not rig.store.writes
    assert rig.service.status()['stage'] == 'failed'
    assert rig.service.cancel(attempt.attempt_id)['stage'] == 'cancelled'
    assert rig.service.prepare_operation('revoke', 0)['state'] == 'local_retired'


def test_setup_end_after_exchange_blocks_publication(rig):
    attempt, _ = begin(rig)
    def http(url, form):
        rig.setup_live[0] = False
        return rig.tokens
    rig.service._http = http
    callback(attempt)
    assert not rig.store.writes
    assert rig.service.status()['stage'] == 'failed'


def test_source_change_during_initial_local_publication_is_not_connected(rig):
    attempt, _ = begin(rig)
    original = rig.store.connect_grant
    def connect(*args, **kwargs):
        revision = original(*args, **kwargs)
        rig.setup_live[0] = False
        return revision
    rig.store.connect_grant = connect
    assert callback(attempt)
    assert rig.host.record.stage == 'published' and rig.store.revision == 1
    assert rig.service.status()['stage'] == 'publication_uncertain'
    assert attempt.verified_binding == rig.store.binding and len(rig.calls) == 1
    rig.setup_live[0] = True
    assert rig.service.reconcile_current() == 1
    assert rig.service.status()['stage'] == 'connected' and len(rig.calls) == 1


def test_source_change_during_reconciliation_preserves_exact_evidence(rig):
    attempt, _ = begin(rig)
    rig.store.uncertain = True
    assert callback(attempt)
    assert attempt.stage == 'publication_uncertain'
    original = rig.store.reconcile_auth_attempt
    def reconcile(*args, **kwargs):
        revision = original(*args, **kwargs)
        rig.setup_live[0] = False
        return revision
    rig.store.reconcile_auth_attempt = reconcile
    with pytest.raises(module.AuthError, match='setup_unavailable'):
        rig.service.reconcile_current()
    assert attempt.stage == 'publication_uncertain' and rig.host.record.stage == 'published'
    assert len(rig.calls) == len(rig.store.writes) == 1
    rig.setup_live[0] = True
    rig.store.reconcile_auth_attempt = original
    assert rig.service.reconcile_current() == 1
    assert len(rig.calls) == 1


@pytest.mark.parametrize('history', [
    {'issuedThrough': None, 'retiredThrough': None, 'state': 'recovery_required'},
    {'issuedThrough': None, 'retiredThrough': None, 'state': 'legacy'},
    {'issuedThrough': '0', 'retiredThrough': '1', 'state': 'available'},
    {'issuedThrough': '0', 'retiredThrough': '0', 'state': 'available', 'secret': 'synthetic-secret'},
])
def test_invalid_ticket_authority_blocks_begin_before_browser(rig, history):
    rig.store.history = history
    with pytest.raises(module.AuthError, match='storage_recovery_required'):
        begin(rig)
    assert not rig.opened and not rig.calls and not rig.host.events


def test_passive_ticket_status_is_only_metadata_hint(rig):
    status = rig.service.status()
    assert status['operation'] == {'state': 'none', 'ticket': None, 'revision': 0,
                                    'verification': 'metadata_only'}
    assert not rig.calls and not rig.opened and not rig.store.writes


def test_large_issuance_counter_does_not_block_fresh_consent(rig):
    rig.store.history = {'issuedThrough': '1000000', 'retiredThrough': '1000000', 'state': 'available'}
    assert begin(rig)[1]['stage'] == 'pending'
    assert len(rig.opened) == 1 and not rig.calls


@pytest.mark.parametrize('reason', ['ticket_conflict', 'ticket_retired', 'transition_pending',
                                    'storage_recovery_required', 'ticket_sequence_exhausted'])
def test_ticket_store_errors_keep_only_fixed_public_reason(rig, reason):
    from eidolon_cli.organization_setup_subscription_store import StoreError
    def unavailable():
        raise StoreError(reason)
    rig.store.status = unavailable
    with pytest.raises(module.AuthError, match=reason):
        rig.service.status()


def restarted_source(rig):
    return module.SubscriptionAuthService(rig.store, rig.host,
        replace(rig.service._owner, source_digest='b' * 64), synthetic=True,
        browser_open=lambda url: pytest.fail('Restart status/cancel must not launch browser'),
        http=lambda *args: pytest.fail('Restart status/cancel must not send HTTP'))


@pytest.mark.parametrize('phase', ['pending', 'exchange_admitted'])
def test_restarted_changed_source_status_and_exact_local_oauth_cancel(rig, phase):
    attempt, _ = begin(rig)
    rig.host.record.stage = phase
    restarted = restarted_source(rig)
    status = restarted.status()
    assert status['stage'] == 'stale_source' and status['attemptId'] == attempt.attempt_id
    assert status['canCancel'] is True and status['operation']['verification'] == 'metadata_only'
    assert restarted.cancel(attempt.attempt_id)['stage'] == 'cancelled'
    assert rig.host.record.stage == 'cancelled' and not rig.calls


@pytest.mark.parametrize('phase', ['publishing', 'published'])
def test_stale_source_published_attempt_cannot_be_cancelled(rig, phase):
    attempt, _ = begin(rig)
    rig.host.record.stage = phase
    restarted = restarted_source(rig)
    status = restarted.status()
    assert status['stage'] == 'stale_source' and status['canCancel'] is False
    with pytest.raises(module.AuthError, match='stale_attempt'):
        restarted.cancel(attempt.attempt_id)
    assert rig.host.record.stage == phase and not rig.calls


@pytest.mark.parametrize('field', ['organization_id', 'owner_id'])
def test_stale_source_status_and_cancel_still_refuse_foreign_scope(rig, field):
    attempt, _ = begin(rig)
    setattr(rig.host.record, field, str(uuid.uuid4()))
    restarted = restarted_source(rig)
    with pytest.raises(module.AuthError, match='stale_attempt'):
        restarted.status()
    with pytest.raises(module.AuthError, match='stale_attempt'):
        restarted.cancel(attempt.attempt_id)
    assert rig.host.record.stage == 'pending' and not rig.calls


@pytest.mark.parametrize('uncertain', [False, True])
def test_oauth_cancel_never_discards_in_memory_published_proof(rig, uncertain):
    attempt, _ = begin(rig)
    rig.store.uncertain = uncertain
    assert callback(attempt)
    before = attempt.stage
    with pytest.raises(module.AuthError, match='stale_attempt'):
        rig.service.cancel(attempt.attempt_id)
    assert attempt.stage == before and attempt.verified_binding == rig.store.binding
    assert len(rig.calls) == 1


@pytest.fixture
def real_ticket_service(tmp_path, synthetic_vault):
    if os.name != 'posix':
        pytest.skip('Real store fixture requires POSIX private-file checks')
    from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
    root = tmp_path / 'ticket-profile'
    root.mkdir(mode=0o700)
    binding = TrustedBinding(str(uuid.uuid4()), str(root), str(uuid.uuid4()), str(uuid.uuid4()),
                             str(uuid.uuid4()), 'oaiapp_synthetic', 'synthetic-account')
    store = SubscriptionStore(root, backend=synthetic_vault)
    evidence = {'subject': binding.account_id, 'audience': binding.issued_client_id,
                'nonce': 'synthetic-nonce', 'authTime': None, 'authorizedParty': None}
    assert store.connect_grant(binding, access_token='synthetic-access',
        refresh_token='synthetic-refresh', id_token='synthetic-id', expires_at=int(time.time()) + 3600,
        granted_scopes=frozenset(module.SCOPES.split()), auth_attempt_id=str(uuid.uuid4()),
        identity_evidence=evidence, expected_revision=0) == 1
    owner = module.OwnerContext(str(root), binding.organization_id, binding.owner_id,
        'a' * 64, object(), object(), lambda: True, setup_guard=lambda: True,
        lease_generation_guard=lambda: True)
    host = SimpleNamespace(installation_id=lambda: binding.host_id, inspect=lambda: None)
    calls = []
    def forbidden_http(*args, **kwargs):
        calls.append(True)
        pytest.fail('Local ticket integration must never make HTTP calls')
    service = module.SubscriptionAuthService(store, host, owner, synthetic=True, http=forbidden_http)
    return SimpleNamespace(store=store, binding=binding, owner=owner, host=host,
                           service=service, vault=synthetic_vault, calls=calls, http=forbidden_http)


def read_ticket_document(rig, name):
    return json.loads((rig.store.directory / name).read_bytes())


def test_real_store_old_source_prepared_ticket_status_reconcile_and_cancel(real_ticket_service):
    from eidolon_cli.organization_setup_subscription_store import StoreError
    rig = real_ticket_service
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    restarted = module.SubscriptionAuthService(rig.store, rig.host,
        replace(rig.owner, source_digest='b' * 64), synthetic=True, http=rig.http)
    reads = rig.vault.reads
    hint = restarted.status()['operation']
    assert hint['ticket'] == ticket and hint['verification'] == 'metadata_only'
    assert rig.vault.reads == reads
    with pytest.raises(module.AuthError, match='stale_attempt'):
        restarted.execute_operation(ticket)
    result = restarted.reconcile_operation(ticket)
    assert result['state'] == 'prepared' and result['catalogRevision'] is None
    cancelled = restarted.cancel_operation(ticket)
    assert cancelled['state'] == 'none' and cancelled['ticket'] is None
    assert cancelled['catalogRevision'] is None
    head = read_ticket_document(rig, 'current.json')['body']
    assert head['phase'] == 'active' and head['revision'] == 1
    assert head['issuedThrough'] == head['retiredThrough'] == ticket['sequence']
    assert head['ticketWindow'] is None
    name = 'window-' + rig.store._ticket_fingerprint(ticket) + '.json'
    archive = read_ticket_document(rig, 'ticket-archive/' + name)['value']
    assert archive['window']['ticket'] == ticket and archive['disposition'] == 'cancelled'
    with pytest.raises(StoreError, match='ticket_retired'):
        rig.store.reconcile_ticket(rig.binding, ticket=ticket,
            owner_guard=lambda: True, cancel=threading.Event())
    assert not rig.calls


@pytest.mark.parametrize('stage', ['admitted', 'uncertain'])
@pytest.mark.parametrize('old_source', [False, True])
def test_real_store_stranded_refresh_local_disconnect_preserves_evidence(real_ticket_service, stage, old_source):
    from eidolon_cli import organization_setup_subscription_store as storage
    rig = real_ticket_service
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    lease = rig.store.admit_refresh(rig.binding, 1, ticket=ticket,
        owner_generation=rig.owner.owner_generation, owner_guard=lambda: True, cancel=threading.Event())
    if stage == 'uncertain':
        # Synthetic persisted ambiguity fixture; production exposes no endpoint
        # to manufacture this state. Preserve a valid head and matching slot.
        with rig.store._locked():
            head = rig.store._authority(rig.binding)
            head = rig.store._next_head(head,
                ticketWindow=dict(head['body']['ticketWindow'], stage='uncertain'))
            rig.store._write('current.json', storage._json(head), replace=True)
            rig.store._write('ticket-pending.json', storage._json(rig.store._empty_pending(head)), replace=True)
    before = read_ticket_document(rig, 'current.json')['body']
    service = (module.SubscriptionAuthService(rig.store, rig.host,
        replace(rig.owner, source_digest='b' * 64), synthetic=True, http=rig.http)
        if old_source else rig.service)
    recovered = service.reconcile_operation(ticket)
    assert recovered['state'] == stage and recovered['catalogRevision'] is None
    with pytest.raises(module.AuthError, match='ticket_conflict'):
        service.cancel_operation(ticket)
    assert not lease.network_cancelled()
    if old_source:
        with pytest.raises(module.AuthError, match='stale_attempt'):
            service.execute_operation(ticket)
    result = service.prepare_operation('revoke', 1)
    assert result == {'state': 'local_retired', 'ticket': None,
        'receipt': {'kind': 'local', 'revision': 2, 'localRetired': True, 'remoteRevocation': 'unconfirmed'},
        'revision': 2, 'verification': 'verified_current', 'catalogRevision': None}
    head = read_ticket_document(rig, 'current.json')
    assert head['body']['phase'] == 'disconnected' and head['body']['target'] is None
    assert head['body']['revision'] == 2 and head['body']['ticketWindow'] is None
    assert head['body']['issuedThrough'] == head['body']['retiredThrough'] == ticket['sequence']
    barrier = read_ticket_document(rig, 'ticket-barrier.json')['body']
    pending = read_ticket_document(rig, 'ticket-pending.json')['body']
    assert barrier['state'] == 'clear'
    assert pending['state'] == 'empty' and pending['headDigest'] == head['digest']
    name = 'window-' + rig.store._ticket_fingerprint(ticket) + '.json'
    archive = read_ticket_document(rig, 'ticket-archive/' + name)['value']
    assert archive['window'] == before['ticketWindow']
    assert archive['window']['ticket'] == ticket and archive['window']['stage'] == stage
    assert archive['phase'] == 'refreshing' and archive['target'] == before['target']
    assert archive['disposition'] == 'local_retired'
    assert lease.network_cancelled()
    with pytest.raises(storage.StoreError):
        rig.store.lease_access_token(rig.binding, 1, owner_generation=rig.owner.owner_generation,
            owner_guard=lambda: True, cancel=threading.Event())
    with pytest.raises(module.AuthError, match='ticket_retired'):
        service.reconcile_operation(ticket)
    assert not rig.calls


@pytest.mark.parametrize('value', [None, 0, 1, 'false', [], {}])
def test_status_rejects_non_boolean_cleanup_pending(rig, value):
    rig.store.cleanup_pending = value
    with pytest.raises(module.AuthError, match='storage_recovery_required'):
        rig.service.status()
    assert not rig.calls and not rig.opened and not rig.store.writes


def test_pending_cleanup_status_is_passive_and_begin_is_blocked(rig, monkeypatch):
    rig.store.cleanup_pending = True
    rig.store.history = {'issuedThrough': None, 'retiredThrough': None, 'state': 'recovery_required'}
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive status or blocked Begin performed I/O')
    monkeypatch.setattr(module, 'LoopbackListener', forbidden)
    rig.store.connect_grant = rig.store.reconcile_auth_attempt = forbidden
    status = rig.service.status()
    assert status['cleanupPending'] is True
    assert status['operation']['verification'] == 'metadata_only'
    with pytest.raises(module.AuthError, match='storage_recovery_required'):
        begin(rig)
    assert not rig.opened and not rig.calls and not rig.host.events and not rig.store.writes


@pytest.mark.parametrize('pending', [True, None, 0, 1, 'false', [], {}])
def test_begin_refuses_pending_or_malformed_cleanup_even_with_available_history(rig, monkeypatch, pending):
    rig.store.cleanup_pending = pending
    assert rig.store.history['state'] == 'available'
    def forbidden(*args, **kwargs):
        raise AssertionError('Blocked Begin performed listener, browser, or native write')
    monkeypatch.setattr(module, 'LoopbackListener', forbidden)
    rig.store.connect_grant = rig.store.reconcile_auth_attempt = forbidden
    with pytest.raises(module.AuthError, match='storage_recovery_required'):
        begin(rig)
    assert not rig.host.events and not rig.opened and not rig.calls and not rig.store.writes

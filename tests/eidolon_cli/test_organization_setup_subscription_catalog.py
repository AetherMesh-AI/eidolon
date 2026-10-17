"""Synthetic catalog fixtures; never real model discovery or authentication."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_setup_subscription_catalog import SubscriptionCatalog, CatalogError, normalize
from eidolon_cli.organization_setup_subscription_store import TrustedBinding


def row(slug='synthetic-a', label='Synthetic A', visibility='list'):
    return {'slug': slug, 'display_name': label, 'visibility': visibility}


class Lease:
    closed = False

    def run(self, callback):
        return callback('synthetic-access', self)

    def is_set(self):
        return self.closed

    def close(self):
        self.closed = True


@pytest.fixture
def fixture():
    state = {'valid': True, 'time': 100., 'mono': 100., 'calls': 0, 'revision': 2}
    binding = TrustedBinding('00000000-0000-4000-8000-000000000001', '/synthetic',
        '00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000003',
        '00000000-0000-4000-8000-000000000004', 'synthetic-client', 'synthetic-account')
    descriptor = {'binding': binding, 'revision': 2,
                  'authorizationEpoch': '00000000-0000-4000-8000-000000000005', 'expiresAt': 500.}
    leases = []

    def describe(revision):
        if revision != state['revision']:
            raise ValueError('stale')
        return dict(descriptor)

    def lease(_):
        item = Lease()
        leases.append(item)
        return item

    service = SimpleNamespace(current_descriptor=describe, lease_access_current=lease,
                              release_lease=lambda item: item.close())
    owner = SimpleNamespace(owner_guard=lambda: state['valid'], setup_guard=lambda: state['valid'],
                            owner_generation=object(), source_digest='source-one')

    def http(*args, **kwargs):
        state['calls'] += 1
        assert kwargs['bearer'] == 'synthetic-access'
        return {'models': [row()]}

    catalog = SubscriptionCatalog(service, owner, http=http, clock=lambda: state['time'], monotonic=lambda: state['mono'])
    return catalog, state, descriptor, owner, leases


def test_explicit_load_and_private_membership(fixture):
    catalog, state, _, _, leases = fixture
    assert state['calls'] == 0
    result = catalog.load(2)
    assert state['calls'] == 1 and leases[0].closed
    assert result['models'] == [{'slug': 'synthetic-a', 'displayName': 'Synthetic A'}]
    assert result['expiresAt'] == 160
    result['models'].clear()
    assert catalog.require(result['catalogId'], 'synthetic-a', 2)['models']
    assert state['calls'] == 1


@pytest.mark.parametrize('change', ['owner', 'source', 'epoch', 'revision', 'expiry', 'invalidate'])
def test_cache_fences_never_fetch(fixture, change):
    catalog, state, descriptor, owner, _ = fixture
    result = catalog.load(2)
    if change == 'owner':
        state['valid'] = False
    elif change == 'source':
        owner.source_digest = 'source-two'
    elif change == 'epoch':
        descriptor['authorizationEpoch'] = '00000000-0000-4000-8000-000000000006'
    elif change == 'revision':
        state['revision'] = 3
    elif change == 'expiry':
        state['time'] = 160
    else:
        catalog.invalidate()
    with pytest.raises(CatalogError):
        catalog.require(result['catalogId'], 'synthetic-a', 2)
    assert state['calls'] == 1


def test_late_reply_after_owner_retirement_is_discarded(fixture):
    catalog, state, _, _, leases = fixture
    def http(*args, **kwargs):
        state['valid'] = False
        return {'models': [row()]}
    catalog._http = http
    with pytest.raises(CatalogError):
        catalog.load(2)
    assert leases[0].closed and catalog._entry is None


def test_order_hidden_and_identical_duplicates():
    rows = [row('z', 'Z'), row('hidden', 'Hidden', 'hide'), row('a', 'A'), row('z', 'Z')]
    assert normalize({'models': rows}) == [
        {'slug': 'z', 'displayName': 'Z'}, {'slug': 'a', 'displayName': 'A'}]
    assert normalize({'models': []}) == []


@pytest.mark.parametrize('document', [None, {'data': []}, {'models': [None]},
    {'models': [row('a', 'A'), row('a', 'Other')]}, {'models': [row('', 'A')]},
    {'models': [row('a', 'bad\x00')]}, {'models': [row()] * 513},
    {'models': [{'visibility': False}]}, {'models': [row('a' * 129)]}])
def test_malformed_catalog(document):
    original = deepcopy(document)
    with pytest.raises(CatalogError):
        normalize(document)
    assert document == original


def test_wall_clock_rollback_does_not_extend_catalog_age(fixture):
    catalog, state, _, _, _ = fixture
    result = catalog.load(2)
    state['time'], state['mono'] = 50., 161.
    with pytest.raises(CatalogError):
        catalog.require(result['catalogId'], 'synthetic-a', 2)
    assert state['calls'] == 1


def test_older_failed_load_cannot_clear_newer_success(fixture):
    import threading
    catalog, _, _, _, _ = fixture
    entered, release = threading.Event(), threading.Event()
    count, errors = [0], []
    def http(*args, **kwargs):
        count[0] += 1
        if count[0] == 1:
            entered.set()
            assert release.wait(5)
            raise ValueError('synthetic old request failure')
        return {'models': [row()]}
    catalog._http = http
    def older():
        try:
            catalog.load(2)
        except CatalogError:
            errors.append('failed')
    worker = threading.Thread(target=older)
    worker.start()
    try:
        assert entered.wait(2)
        newer = catalog.load(2)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and errors == ['failed']
    assert catalog.require(newer['catalogId'], 'synthetic-a', 2)['catalogId'] == newer['catalogId']
    assert count == [2]


@pytest.mark.parametrize('replacement', ['invalidate', 'reload'])
def test_require_rechecks_catalog_after_deferred_descriptor(fixture, replacement):
    import threading
    catalog, state, _, _, _ = fixture
    original = catalog.load(2)
    descriptor = catalog._descriptor
    entered, release = threading.Event(), threading.Event()
    errors, results = [], []
    worker = None
    def deferred(revision):
        result = descriptor(revision)
        if threading.current_thread() is worker:
            entered.set()
            assert release.wait(5)
        return result
    catalog._descriptor = deferred
    def lookup():
        try:
            results.append(catalog.require(original['catalogId'], 'synthetic-a', 2))
        except CatalogError:
            errors.append('stale')
    worker = threading.Thread(target=lookup)
    worker.start()
    newer = None
    try:
        assert entered.wait(2)
        if replacement == 'reload':
            newer = catalog.load(2)
        else:
            catalog.invalidate()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and errors == ['stale'] and results == []
    if newer is not None:
        assert catalog.require(newer['catalogId'], 'synthetic-a', 2)['catalogId'] == newer['catalogId']
        assert state['calls'] == 2
    else:
        assert catalog._entry is None and state['calls'] == 1

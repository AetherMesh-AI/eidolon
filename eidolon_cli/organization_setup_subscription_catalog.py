"""Explicit, account-bound setup model discovery; never inference or token export."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import re
import threading
import time
import uuid
import unicodedata

from eidolon_cli.organization_setup_subscription_http import MODELS, request


class CatalogError(RuntimeError):
    def __init__(self):
        super().__init__('Setup subscription catalog is unavailable')


def normalize(document):
    if type(document) is not dict or type(document.get('models')) is not list or len(document['models']) > 512:
        raise CatalogError()
    result, seen = [], {}
    for row in document['models']:
        if type(row) is not dict or type(row.get('visibility')) is not str:
            raise CatalogError()
        if row['visibility'] != 'list':
            continue
        slug, label = row.get('slug'), row.get('display_name')
        if (type(slug) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', slug) is None
                or type(label) is not str or not 1 <= len(label) <= 256 or not label.strip()
                or any(unicodedata.category(char).startswith('C') for char in label)):
            raise CatalogError()
        if slug in seen:
            if seen[slug] != label:
                raise CatalogError()
            continue
        seen[slug] = label
        result.append({'slug': slug, 'displayName': label})
    return result


class SubscriptionCatalog:
    """Memory-only evidence, scoped to one service's owner/source generation.

    All network calls require explicit load(). No constructor/status side effects.
    A mutation may chain one load after its receipt, never retry that mutation.
    """
    def __init__(self, service, owner, *, http=request, clock=time.time, monotonic=time.monotonic):
        self._service, self._owner, self._http, self._clock = service, owner, http, clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._entry = None
        self._serial = 0

    def __repr__(self):
        return '<SubscriptionCatalog>'

    def invalidate(self):
        with self._lock:
            self._serial += 1
            self._entry = None

    def _invalidate_if(self, serial):
        with self._lock:
            if serial == self._serial:
                self._serial += 1
                self._entry = None

    def _guard(self):
        if (self._owner.owner_guard() is not True
                or self._owner.setup_guard() is not True):
            raise CatalogError()

    def _descriptor(self, revision):
        self._guard()
        descriptor = self._service.current_descriptor(revision)
        binding = descriptor['binding']
        digest = hashlib.sha256(json.dumps(asdict(binding), sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        key = (digest, descriptor['authorizationEpoch'], descriptor['revision'],
               self._owner.owner_generation, self._owner.source_digest)
        return descriptor, key

    def load(self, expected_revision):
        """One explicit discovery request; no implicit refresh or response retry."""
        lease = None
        with self._lock:
            self._serial += 1
            serial = self._serial
            self._entry = None
        try:
            descriptor, key = self._descriptor(expected_revision)
            lease = self._service.lease_access_current(expected_revision)
            document = lease.run(lambda token, cancel: self._http(MODELS, bearer=token, cancel=cancel))
            models = normalize(document)
            current, current_key = self._descriptor(expected_revision)
            if current_key != key or current['expiresAt'] != descriptor['expiresAt']:
                raise CatalogError()
            now = self._clock()
            expiry = min(now + 60, descriptor['expiresAt'])
            if expiry <= now:
                raise CatalogError()
            result = {'version': 1, 'state': 'ready', 'catalogId': str(uuid.uuid4()),
                      'connectionRevision': expected_revision, 'connectionId': key[0],
                      'authorizationEpoch': descriptor['authorizationEpoch'],
                      'sourceRevision': self._owner.source_digest,
                      'fetchedAt': now, 'expiresAt': expiry, 'models': models}
            with self._lock:
                if serial != self._serial:
                    raise CatalogError()
                self._entry = (key, deepcopy(result), self._monotonic() + min(60, expiry - now))
            # Guard outside cache/store locks; a retired reply is never returned.
            self._guard()
            return result
        except Exception:
            self._invalidate_if(serial)
            raise CatalogError() from None
        finally:
            if lease is not None:
                self._service.release_lease(lease)

    def require(self, catalog_id, slug, expected_revision):
        """Private admission evidence lookup. Does not fetch, refresh or infer."""
        with self._lock:
            serial = self._serial
            original_key = self._entry[0] if self._entry is not None else None
            entry = deepcopy(self._entry[1]) if self._entry is not None else None
            deadline = self._entry[2] if self._entry is not None else 0
        try:
            _, key = self._descriptor(expected_revision)
            if (entry is None or original_key != key or entry['catalogId'] != catalog_id
                    or self._clock() >= entry['expiresAt'] or self._monotonic() >= deadline
                    or slug not in {row['slug'] for row in entry['models']}):
                raise CatalogError()
            self._guard()
            with self._lock:
                if (serial != self._serial or self._entry is None
                        or self._entry[1]['catalogId'] != catalog_id
                        or self._clock() >= entry['expiresAt']
                        or self._monotonic() >= deadline):
                    raise CatalogError()
                return entry
        except Exception:
            self._invalidate_if(serial)
            raise CatalogError() from None

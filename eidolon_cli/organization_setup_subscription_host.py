"""Captured host scope and durable, nonsecret setup-attempt fences.

Capture authority in the process launcher BEFORE dotenv. This module never reads
an environment variable. The caller supplies already authenticated owner intent;
this module is not an owner authorization gate. Only explicit Begin creates an
installation identity. Local publication recovery never retries remote requests.

No HTTP is permitted inside publication_guard: it is only the local store commit
boundary, with lock order attempt then store. Exchange admission releases its
lock before returning. Synthetic mode is solely for filesystem tests.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from functools import wraps
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from eidolon_cli.organization_setup_subscription_store import SubscriptionStore, StoreError, _json, _pairs

INSTALLATION_NAME = '.setup-subscription-installation'
_MAX_TTL = 900
_ACTIVE = threading.local()
_REASONS = frozenset({'invalid_authority', 'invalid_attempt', 'attempt_pending',
    'stale_attempt', 'attempt_expired', 'exchange_already_admitted',
    'recovery_required', 'unsafe_filesystem', 'backend_unavailable',
    'filesystem_protection_unavailable', 'operation_failed', 'recursive_use',
    'host_identity_migration_required'})


class HostError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in _REASONS else 'operation_failed'
        super().__init__('Setup subscription: ' + self.reason)


def _guard(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (HostError, StoreError) as error:
            raise HostError(error.reason) from None
        except Exception:
            raise HostError('operation_failed') from None
    return call


def _uuid(value):
    if type(value) is not str or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise HostError('invalid_attempt')
    return value


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _host_uri(value):
    if type(value) is not str or not value.startswith('urn:uuid:'):
        raise HostError('recovery_required')
    try:
        parsed = uuid.UUID(value[9:])
    except ValueError:
        raise HostError('recovery_required') from None
    if parsed.version != 4 or parsed.urn != value:
        raise HostError('recovery_required')
    return value


@dataclass(frozen=True, repr=False)
class HostAuthority:
    root: Path
    profile: Path
    root_identity: tuple
    profile_identity: tuple

    @classmethod
    @_guard
    def capture(cls, get_default_hermes_root, get_process_eidolon_home):
        """Call once before dotenv; getters must be the trusted launcher helpers."""
        root = Path(get_default_hermes_root())
        profile = Path(get_process_eidolon_home())
        if not root.is_absolute() or not profile.is_absolute():
            raise HostError('invalid_authority')
        if '..' in root.parts or '..' in profile.parts:
            raise HostError('invalid_authority')
        if profile != root:
            if (profile.parent != root / 'profiles'
                    or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', profile.name) is None):
                raise HostError('invalid_authority')
        return cls(root, profile, _identity(root), _identity(profile))

    @property
    def profile_hash(self):
        return hashlib.sha256(('%d:%d' % self.profile_identity).encode('ascii')).hexdigest()


@dataclass(frozen=True, repr=False)
class PendingAttempt:
    attempt_id: str
    organization_id: str
    owner_id: str
    expected_store_revision: int
    source_digest: str
    expires_at: int
    stage: str = 'pending'
    publication_binding_digest: str | None = None
    authorization_host_id: str | None = None
    host_identity_migration: bool = False
    authorization_binding_digest: str | None = None

    def __post_init__(self):
        for value in (self.attempt_id, self.organization_id, self.owner_id):
            _uuid(value)
        if (type(self.expected_store_revision) is not int
                or not 0 <= self.expected_store_revision < 2**63
                or type(self.source_digest) is not str
                or re.fullmatch('[0-9a-f]{64}', self.source_digest) is None
                or type(self.expires_at) is not int or not 0 < self.expires_at < 2**53
                or self.stage not in {'pending', 'exchange_admitted', 'publishing', 'published', 'cancelled', 'locally_retired'}):
            raise HostError('invalid_attempt')
        digest = self.publication_binding_digest
        if digest is not None and (type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None
                or self.stage not in {'publishing', 'published', 'locally_retired'}):
            raise HostError('invalid_attempt')
        if self.stage == 'locally_retired' and digest is None:
            raise HostError('invalid_attempt')
        if type(self.host_identity_migration) is not bool:
            raise HostError('invalid_attempt')
        if self.authorization_host_id is not None:
            _host_uri(self.authorization_host_id)
        elif self.host_identity_migration or self.authorization_binding_digest is not None:
            raise HostError('invalid_attempt')
        digest = self.authorization_binding_digest
        if digest is not None and (type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None):
            raise HostError('invalid_attempt')


class PendingAttempts:
    @_guard
    def __init__(self, authority, profile_home=None, *, synthetic=False, clock=time.time):
        if type(authority) is not HostAuthority or type(synthetic) is not bool:
            raise HostError('invalid_authority')
        self.authority = authority
        self.profile = Path(profile_home) if profile_home is not None else authority.profile
        self._profile_identity = _identity(self.profile)
        self._clock = clock
        self._io = SubscriptionStore(authority.root, backend=object() if synthetic else None)
        self._io.directory = authority.root / INSTALLATION_NAME
        # Physical canonical profile identity also fences case aliases on APFS.
        self._name = hashlib.sha256(('%d:%d' % self._profile_identity).encode('ascii')).hexdigest() + '.json'

    def __repr__(self):
        return '<PendingAttempts>'

    def _check_authority(self):
        a = self.authority
        # Recheck shape even for a manually constructed dataclass.
        if (not a.root.is_absolute() or not a.profile.is_absolute()
                or '..' in a.root.parts or '..' in a.profile.parts
                or (a.profile != a.root and (a.profile.parent != a.root / 'profiles'
                    or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', a.profile.name) is None))):
            raise HostError('invalid_authority')
        if (not self.profile.is_absolute() or '..' in self.profile.parts
                or (self.profile != a.root and (self.profile.parent != a.root / 'profiles'
                    or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', self.profile.name) is None))):
            raise HostError('invalid_authority')
        self._io._platform()
        self._io._check(self.profile, directory=True)
        if self.profile != a.root:
            self._io._check(a.root / 'profiles', directory=True)
        if _identity(self.profile) != self._profile_identity:
            raise HostError('invalid_authority')
        self._io._check(a.root, directory=True)
        self._io._check(a.profile, directory=True)
        if a.profile != a.root:
            self._io._check(a.root / 'profiles', directory=True)
        if _identity(a.root) != a.root_identity or _identity(a.profile) != a.profile_identity:
            raise HostError('invalid_authority')

    def _now(self):
        now = self._clock()
        if type(now) not in {float, int} or not math.isfinite(now) or now < 0:
            raise HostError('operation_failed')
        return now

    @contextmanager
    def _locked(self, *, create=False):
        fd = None
        scope = self.authority.root_identity
        active = getattr(_ACTIVE, 'scopes', set())
        if scope in active:
            raise HostError('recursive_use')
        try:
            self._check_authority()
            import fcntl
            directory = self._io.directory
            if not directory.exists() and not directory.is_symlink():
                if not create:
                    yield False
                    return
                try:
                    directory.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                parent = os.open(self.authority.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            self._io._check(directory, directory=True)
            lock = directory / 'lock'
            if not lock.exists() and not lock.is_symlink():
                if not create:
                    raise HostError('recovery_required')
                try:
                    new = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    try:
                        os.fsync(new)
                    finally:
                        os.close(new)
                    self._io._sync_directory()
                except FileExistsError:
                    pass
            before = self._io._check(lock)
            fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
            if _identity(lock) != (before.st_dev, before.st_ino) or _identity(lock) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                raise HostError('unsafe_filesystem')
            fcntl.flock(fd, fcntl.LOCK_EX)
            active.add(scope)
            _ACTIVE.scopes = active
            self._check_authority()
            self._io._check(directory, directory=True)
            self._io._check(lock)
            if _identity(lock) != (before.st_dev, before.st_ino):
                raise HostError('unsafe_filesystem')
            yield True
        except (HostError, StoreError) as error:
            raise HostError(error.reason) from None
        except Exception:
            raise HostError('operation_failed') from None
        finally:
            if fd is not None:
                active.discard(scope)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _store_evidence(self):
        root = self.authority.root
        if os.path.lexists(root / '.setup-subscription-private'):
            return True
        profiles = root / 'profiles'
        if os.path.lexists(profiles):
            self._io._check(profiles, directory=True)
            # Conservatively refuse oversized/ambiguous inventories.
            for index, profile in enumerate(profiles.iterdir()):
                if index >= 1024:
                    raise HostError('recovery_required')
                self._io._check(profile, directory=True)
                if os.path.lexists(profile / '.setup-subscription-private'):
                    return True
        return False

    def _installation(self, *, create=False):
        if os.path.lexists(self._io.directory / 'identity.json'):
            if (not os.path.lexists(self._io.directory / 'initialized')
                    or self._io._read('initialized', limit=1) != b'1'):
                raise HostError('recovery_required')
            value = self._io._read_json('identity.json')
            if type(value) is not dict or set(value) != {'installation_id'}:
                raise HostError('recovery_required')
            return _uuid(value['installation_id'])
        if (not create or self._store_evidence()
                or any(p.name != 'lock' for p in self._io.directory.iterdir())):
            raise HostError('recovery_required')
        # A durable marker survives missing identity and interrupted creation.
        self._io._write('initialized', b'1')
        value = str(uuid.uuid4())
        self._io._write('identity.json', _json({'installation_id': value}))
        self._provider_host(value, create_origin='fresh')
        return value

    def _provider_host(self, installation, *, create_origin=None):
        """A candidate wire identity is never proof that a binding consented.

        Keep the local installation UUID and all existing binding digests intact.
        The URI is a different provider-visible identifier, not an asserted alias.
        """
        name = 'oauth-host.json'
        if not os.path.lexists(self._io.directory / name):
            if create_origin is None:
                return None
            value = {'version': 1, 'installation_id': installation,
                     'host_id': _host_uri(uuid.UUID(installation).urn), 'origin': create_origin}
            self._io._write(name, _json(value))
        value = self._read_protocol_json(name)
        if (type(value) is not dict or set(value) != {'version', 'installation_id', 'host_id', 'origin'}
                or type(value['version']) is not int or value['version'] != 1
                or value['installation_id'] != installation or type(value['origin']) is not str
                or value['origin'] not in {'fresh', 'legacy'}
                or _host_uri(value['host_id']) != uuid.UUID(installation).urn):
            raise HostError('recovery_required')
        return value

    def _read_protocol_json(self, name):
        try:
            return self._io._read_json(name)
        except (ValueError, UnicodeError):
            raise HostError('recovery_required') from None
        except StoreError as error:
            if error.reason == 'corrupt_store':
                raise HostError('recovery_required') from None
            raise

    def _consent_name(self, binding_digest):
        if type(binding_digest) is not str or re.fullmatch('[0-9a-f]{64}', binding_digest) is None:
            raise HostError('invalid_attempt')
        return 'oauth-consent-' + self._name[:-5] + '-' + binding_digest + '.json'

    def _host_consent(self, binding_digest, candidate):
        name = self._consent_name(binding_digest)
        if not os.path.lexists(self._io.directory / name):
            return None
        value = self._read_protocol_json(name)
        if (candidate is None or type(value) is not dict
                or set(value) != {'version', 'profile_identity', 'installation_id', 'binding_digest', 'attempt'}
                or type(value['version']) is not int or value['version'] != 1
                or value['profile_identity'] != self._name[:-5]
                or value['installation_id'] != candidate['installation_id']
                or value['binding_digest'] != binding_digest
                or type(value['attempt']) is not dict
                or set(value['attempt']) != set(PendingAttempt.__dataclass_fields__)):
            raise HostError('recovery_required')
        try:
            record = PendingAttempt(**value['attempt'])
        except (HostError, TypeError, ValueError):
            raise HostError('recovery_required') from None
        if (record.stage not in {'publishing', 'published'}
                or record.publication_binding_digest != binding_digest
                or record.authorization_host_id != candidate['host_id']
                or record.authorization_binding_digest not in (None, binding_digest)):
            raise HostError('recovery_required')
        return value

    def _host_identity_state(self, installation, binding_digest):
        candidate = self._provider_host(installation)
        if binding_digest is not None:
            proof = self._host_consent(binding_digest, candidate)
            # Fresh installations used this canonical URI from their first request.
            # Their grant/publication authority still comes only from the store.
            return ('current' if proof is not None or candidate is not None and candidate['origin'] == 'fresh'
                    else 'migration_required')
        return 'new' if candidate is not None and candidate['origin'] == 'fresh' else 'migration_required'

    @_guard
    def host_identity_state(self, binding_digest=None):
        """Passive metadata only; never create candidate identity or consent proof."""
        with self._locked() as exists:
            if not exists:
                if binding_digest is not None:
                    raise HostError('recovery_required')
                return 'new'
            return self._host_identity_state(self._installation(), binding_digest)

    def _publish_host_consent(self, record):
        # Old pending-attempt schemas cannot establish new protocol provenance.
        if record.authorization_host_id is None or record.publication_binding_digest is None:
            return
        installation = self._installation()
        candidate = self._provider_host(installation)
        if candidate is None or candidate['host_id'] != record.authorization_host_id:
            raise HostError('recovery_required')
        digest = record.publication_binding_digest
        if record.authorization_binding_digest not in (None, digest):
            raise HostError('stale_attempt')
        if self._host_consent(digest, candidate) is None:
            value = {'version': 1, 'profile_identity': self._name[:-5],
                     'installation_id': installation, 'binding_digest': digest,
                     'attempt': asdict(record)}
            self._io._write(self._consent_name(digest), _json(value))
        # Repair a lost directory-fsync acknowledgment without rewriting proof.
        self._io._sync_directory()

    def _read_attempt(self):
        if not os.path.lexists(self._io.directory / self._name):
            if os.path.lexists(self._io.directory / (self._name + '.seen')):
                raise HostError('recovery_required')
            return None
        value = self._io._read_json(self._name)
        fields = set(PendingAttempt.__dataclass_fields__)
        legacy = fields - {'authorization_host_id', 'host_identity_migration', 'authorization_binding_digest'}
        if type(value) is not dict or set(value) not in (fields, legacy, legacy - {'publication_binding_digest'}):
            raise HostError('recovery_required')
        return PendingAttempt(**value)

    def _save(self, record):
        self._io._write(self._name, _json(asdict(record)), replace=True)

    def _exact(self, attempt_id, *, live=True):
        _uuid(attempt_id)
        record = self._read_attempt()
        if record is None or record.attempt_id != attempt_id:
            raise HostError('stale_attempt')
        if live and record.expires_at <= self._now():
            raise HostError('attempt_expired')
        return record

    @_guard
    def installation_id(self):
        with self._locked() as exists:
            if not exists:
                raise HostError('recovery_required')
            return self._installation()

    @_guard
    def inspect(self):
        with self._locked() as exists:
            if not exists:
                return None
            self._installation()
            return self._read_attempt()

    def _archive_local_retirement(self, record):
        """Retain remote-unknown evidence before replacement; never grant authority.

        Each record is bounded by PendingAttempt's closed schema. Reuse the
        immutable archive publisher: exclusive link, byte-equal retry, fsync.
        No inventory, retention cap, pruning, or old-archive replay is involved.
        """
        if record.stage != 'locally_retired':
            return
        name = self._name + '.' + record.attempt_id + '.retired'
        data = _json(asdict(record))
        self._io._archive_directory(create=True)
        self._io._sync_directory()  # Also repair a lost archive-directory mkdir acknowledgment.
        self._io._archive_put(name, data)
        if self._io._archive_read(name, len(data)) != data:
            raise HostError('recovery_required')

    def _archive_migration_predecessor(self, record):
        # A cancelled legacy exchange can have a remote-unknown outcome even
        # without a store publication. Preserve its exact bytes before replacing
        # the pending slot; this archive never establishes consent or authority.
        name = self._name + '.' + record.attempt_id + '.host-migration'
        data = _json({'version': 1, 'remoteOutcome': 'unknown',
                      'recordJson': self._io._read(self._name).decode('utf-8')})
        self._io._archive_directory(create=True)
        self._io._sync_directory()
        self._io._archive_put(name, data)
        if self._io._archive_read(name, len(data)) != data:
            raise HostError('recovery_required')

    @_guard
    def begin(self, *, attempt_id, organization_id, owner_id,
              expected_store_revision, source_digest, expires_at,
              binding_digest=None, confirm_host_identity_change=False):
        """Explicit owner Begin only. Never call from passive status or startup."""
        record = PendingAttempt(attempt_id, organization_id, owner_id,
                                expected_store_revision, source_digest, expires_at)
        with self._locked(create=True):
            if type(confirm_host_identity_change) is not bool:
                raise HostError('invalid_attempt')
            now = self._now()
            if not now < expires_at <= now + _MAX_TTL:
                raise HostError('invalid_attempt')
            installation = self._installation(create=True)
            identity_state = self._host_identity_state(installation, binding_digest)
            migration = identity_state == 'migration_required'
            if migration and not confirm_host_identity_change:
                raise HostError('host_identity_migration_required')
            if confirm_host_identity_change and not migration:
                raise HostError('stale_attempt')
            previous = self._read_attempt()
            if previous is not None:
                if previous.attempt_id == attempt_id:
                    raise HostError('stale_attempt')
                if previous.expires_at > now and previous.stage not in {'published', 'cancelled', 'locally_retired'}:
                    raise HostError('attempt_pending')
                if migration and previous.stage not in {'published', 'cancelled', 'locally_retired'}:
                    raise HostError('attempt_pending')
                self._archive_local_retirement(previous)
                if migration:
                    self._archive_migration_predecessor(previous)
            candidate = self._provider_host(installation, create_origin='legacy' if migration else None)
            if candidate is None:
                raise HostError('recovery_required')
            record = replace(record, authorization_host_id=candidate['host_id'],
                             host_identity_migration=migration, authorization_binding_digest=binding_digest)
            if previous is None:
                self._io._write(self._name + '.seen', b'1')
            self._save(record)
            return record

    @_guard
    def admit_exchange(self, attempt_id):
        """Persist single-use admission BEFORE HTTP; return with no lock held."""
        with self._locked() as exists:
            if not exists:
                raise HostError('stale_attempt')
            self._installation()
            record = self._exact(attempt_id)
            if record.stage != 'pending':
                raise HostError('exchange_already_admitted')
            record = replace(record, stage='exchange_admitted')
            self._save(record)
            return record

    @_guard
    def cancel(self, attempt_id):
        with self._locked() as exists:
            if not exists:
                raise HostError('stale_attempt')
            self._installation()
            record = self._exact(attempt_id, live=False)
            if record.stage in {'published', 'publishing', 'locally_retired'}:
                raise HostError('stale_attempt')
            record = replace(record, stage='cancelled')
            self._save(record)
            return record

    @contextmanager
    def publication_guard(self, attempt_id, *, binding_digest=None):
        """Hold attempt authority through local CAS publication; NEVER do HTTP.

        The caller MUST use yielded record.expected_store_revision for store.connect,
        then call commit(returned_revision) before leaving the context.
        An exception or process loss leaves publishing durably fenced until expiry.
        Cancel serializes here: cancellation that commits first prevents publish.
        """
        with self._locked() as exists:
            if not exists:
                raise HostError('stale_attempt')
            self._installation()
            record = self._exact(attempt_id)
            if record.stage != 'exchange_admitted':
                raise HostError('stale_attempt')
            record = replace(record, stage='publishing', publication_binding_digest=binding_digest)
            self._save(record)
            publication = Publication(record)
            yield publication
            if not publication._committed:
                raise HostError('recovery_required')
            self._publish_host_consent(record)
            self._save(replace(record, stage='published'))


    @contextmanager
    def reconciliation_guard(self, attempt_id, *, allow_local_retirement=False, legacy_binding_digest=None):
        """Fence exact uncertain local publication, even after attempt expiry.

        Caller MUST verify record owner, organization and source digest against
        current trusted authority, authenticate the exact attempt and prior
        revision with store.reconcile_auth_attempt, and commit only its returned
        revision. No HTTP, new publication or exchange replay is permitted here.
        Normal reconciliation accepts publishing/published. Explicit local
        recovery also revalidates locally_retired and may acknowledge exact
        cleanup at the prior revision. Its digest comes only from the durable
        record or the identical live, verified legacy attempt. The terminal
        save preserves that digest. No branch republishes a grant, and failure
        leaves the prior durable attempt stage unchanged.
        """
        with self._locked() as exists:
            if not exists:
                raise HostError('stale_attempt')
            self._installation()
            record = self._exact(attempt_id, live=False)
            allowed = {'publishing', 'published'} | ({'locally_retired'} if allow_local_retirement else set())
            if record.stage not in allowed:
                raise HostError('stale_attempt')
            if legacy_binding_digest is not None:
                if record.publication_binding_digest is not None or not allow_local_retirement:
                    raise HostError('stale_attempt')
                record = replace(record, publication_binding_digest=legacy_binding_digest)
            publication = Publication(record, allow_local_retirement=allow_local_retirement)
            yield publication
            if not publication._committed:
                raise HostError('recovery_required')
            if publication._stage == 'published':
                self._publish_host_consent(record)
            self._save(replace(record, stage=publication._stage))


class Publication:
    """Explicit local store-success acknowledgment; never accepts a guessed revision."""
    def __init__(self, record, *, allow_local_retirement=False):
        self.record = record
        self._committed = False
        self._stage = None
        self._allow_local_retirement = allow_local_retirement

    def __repr__(self):
        return '<Publication>'

    def commit(self, result):
        if (self._committed or type(result) is not int
                or result != self.record.expected_store_revision + 1):
            raise HostError('stale_attempt')
        if self.record.stage == 'locally_retired':
            raise HostError('stale_attempt')
        self._committed = True
        self._stage = 'published'

    def retire(self, result):
        """Acknowledge exact local cleanup only; remote grant remains unconfirmed."""
        if (not self._allow_local_retirement or self._committed
                or self.record.stage not in {'publishing', 'locally_retired'}
                or self.record.publication_binding_digest is None
                or type(result) is not int or result != self.record.expected_store_revision):
            raise HostError('stale_attempt')
        self._committed = True
        self._stage = 'locally_retired'

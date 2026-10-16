"""Read-only freshness checks of an owner-pinned prepared dependency bundle.

This reads current bytes without copying, installing, importing or executing
packages. It is an observed-at-check boundary, not a filesystem lock or a promise
that mutable source will remain unchanged after the last observation. Execution
must continue to consume its separately verified private copied bytes.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from types import MappingProxyType
from weakref import WeakKeyDictionary

from eidolon_cli import organization_project_dependency_bundle as bundle
from eidolon_cli.organization_project_compiler_bundle import _directory


_READ_BYTES = 65536
_OBSERVATIONS = WeakKeyDictionary()


class _DependencyObservation:
    """An identity capability for trusted host code, never a wire receipt."""

    __slots__ = ('__weakref__',)

    def __new__(cls):
        raise TypeError('Dependency observations are created only by their scoped observer')

    def __reduce_ex__(self, protocol):
        raise TypeError('Dependency observations cannot be copied or serialized')

    def metadata(self):
        state = _observation_state(self)
        return json.loads(state.metadata)


@dataclass(frozen=True)
class _ObservationState:
    source: str
    manifest: MappingProxyType
    expected: MappingProxyType
    children: MappingProxyType
    observed: MappingProxyType
    root: tuple
    metadata: bytes
    cancel: object
    deadline: object


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    if type(value) is set:
        return frozenset(value)
    return value


def _observation_state(observation, *, consume=False):
    if type(observation) is not _DependencyObservation:
        raise ValueError('Dependency observation is not an active private capability')
    state = _OBSERVATIONS.pop(observation, None) if consume else _OBSERVATIONS.get(observation)
    if state is None:
        raise ValueError('Dependency observation is not active or was already consumed')
    return state


def _validate_checkpoint(deadline, check_authorized):
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError('Dependency deadline must be a finite monotonic timestamp')
    if check_authorized is not None and not callable(check_authorized):
        raise ValueError('Dependency authorization checkpoint must be callable')


def _package_identities(manifest, lockfile_bytes, limits, check):
    lock = bundle._json(lockfile_bytes, limits.max_lockfile_bytes, 'Dependency lockfile')
    tools = {'node_modules/' + name: name for name in manifest['toolchain']['tools']}
    identities = {}
    for item in manifest['files']:
        check()
        package = bundle._package_path(item['path'])
        if package and item['path'] == package + '/package.json':
            if item['bytes'] > limits.max_package_manifest_bytes:
                raise ValueError('Installed package manifest exceeds its byte bound')
            locked = lock['packages'][package]
            identities[item['path']] = {
                'version': locked['version'],
                'packageName': locked.get('name', package.rsplit('node_modules/', 1)[-1]),
                'toolName': tools.get(package)}
    return identities


def _verify_file(parent, name, item, package_identity, check):
    with bundle._regular_descriptor(parent, name, item) as (descriptor, before):
        total, hasher = 0, hashlib.sha256()
        package_bytes = bytearray() if package_identity is not None else None
        while True:
            check()
            block = os.read(descriptor, min(_READ_BYTES, item['bytes'] - total + 1))
            if not block:
                break
            total += len(block)
            if total > item['bytes']:
                raise ValueError('Dependency file grew beyond its exact bound')
            hasher.update(block)
            if package_bytes is not None:
                package_bytes.extend(block)
        check()
        fingerprint = bundle._identity(before)
        if (total != item['bytes'] or hasher.hexdigest() != item['sha256']
                or fingerprint != bundle._identity(os.fstat(descriptor))
                or fingerprint != bundle._identity(os.stat(name, dir_fd=parent, follow_symlinks=False))):
            raise ValueError('Dependency file bytes or identity changed during verification')
        if package_bytes is not None:
            package = bundle._json(bytes(package_bytes), item['bytes'], 'Installed package manifest')
            if package.get('version') != package_identity['version']:
                raise ValueError('Installed dependency version differs from the exact lockfile')
            if package_identity['toolName'] is not None and package.get('name') != package_identity['toolName']:
                raise ValueError('Required tool package name differs from its exact root identity')
            if package.get('name') != package_identity['packageName']:
                raise ValueError('Installed dependency package name differs from its exact lock identity')
        check()
        return fingerprint


def _walk(descriptor, prefix, expected, children, identities, check, observed, *, hash_bytes):
    check()
    before = bundle._identity(os.fstat(descriptor))
    if hash_bytes:
        observed['directories'][prefix] = before
    elif observed['directories'].get(prefix) != before:
        raise ValueError('Dependency directory changed after its bytes were verified')
    names = bundle._names(descriptor, children.get(prefix, set()), check)
    for name in names:
        check()
        relative = prefix + '/' + name if prefix else name
        if relative in children:
            child = os.open(name, bundle._DIR_FLAGS, dir_fd=descriptor)
            try:
                opened = bundle._identity(os.fstat(child))
                _walk(child, relative, expected, children, identities, check, observed, hash_bytes=hash_bytes)
                check()
                if opened != bundle._identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)):
                    raise ValueError('Dependency directory path changed during verification')
            finally:
                os.close(child)
        elif hash_bytes:
            observed['files'][relative] = _verify_file(descriptor, name, expected[relative], identities.get(relative), check)
        else:
            # Anchor through the same no-symlink O_PATH gate. This final metadata
            # pass detects an earlier file's later mutation without retaining its
            # bytes or opening a read descriptor for every inventory entry.
            with bundle._regular_anchor(descriptor, name, expected[relative]) as (file_fd, info):
                check()
                fingerprint = observed['files'].get(relative)
                if (fingerprint != bundle._identity(info) or fingerprint != bundle._identity(os.fstat(file_fd))
                        or fingerprint != bundle._identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False))):
                    raise ValueError('Dependency file changed after its bytes were verified')
    check()
    bundle._names(descriptor, children.get(prefix, set()), check)
    if before != bundle._identity(os.fstat(descriptor)):
        raise ValueError('Dependency directory changed during verification')


def _capture_observation(source, manifest, *, lockfile_bytes, package_manifests,
                         expected_toolchain, expected_source_identity, expected_bundle_sha256,
                         limits=None, cancel=None, deadline=None, check_authorized=None):
    if os.name != 'posix' or not all(hasattr(os, flag) for flag in ('O_PATH', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC')):
        raise ValueError('Dependency verification requires Linux O_PATH and procfs descriptor reopening')
    _validate_checkpoint(deadline, check_authorized)
    if (type(expected_source_identity) is not tuple or len(expected_source_identity) != 2
            or any(type(item) is not int or not 0 <= item < 2**64 for item in expected_source_identity)):
        raise ValueError('Expected dependency root identity requires exact device and inode integers')
    expected_bundle_sha256 = bundle._hash(expected_bundle_sha256)

    def check():
        bundle._checkpoint(cancel, deadline)
        if check_authorized is not None and check_authorized() is not True:
            raise ValueError('Current dependency verification authority was denied')
        bundle._checkpoint(cancel, deadline)

    check()
    limits = bundle._limits(limits)
    receipt = bundle.normalize_prepared_dependency_manifest(manifest, lockfile_bytes=lockfile_bytes,
        package_manifests=package_manifests, expected_toolchain=expected_toolchain, limits=limits)
    if receipt['bundleSha256'] != expected_bundle_sha256:
        raise ValueError('Dependency bundle differs from its independently retained identity')
    check()
    value = receipt['manifest']
    expected = {item['path']: item for item in value['files']}
    identities = _package_identities(value, lockfile_bytes, limits, check)
    children = bundle._children(expected)
    observed = {'files': {}, 'directories': {}}
    source = os.fspath(source)
    with _directory(source) as source_fd:
        root = os.fstat(source_fd)
        if (root.st_dev, root.st_ino) != expected_source_identity:
            raise ValueError('Opened dependency root differs from its owner-approved identity')
        _walk(source_fd, '', expected, children, identities, check, observed, hash_bytes=True)
        _walk(source_fd, '', expected, children, identities, check, observed, hash_bytes=False)
        check()
        with _directory(source) as current_source:
            if bundle._identity(root) != bundle._identity(os.fstat(current_source)):
                raise ValueError('Dependency source root identity changed during verification')
        check()
    metadata = {'bundleSha256': receipt['bundleSha256'], 'inventorySha256': receipt['inventorySha256'],
            'lockfileSha256': receipt['lockfileSha256'],
            'rootIdentity': dict(zip(('device', 'inode'), expected_source_identity)),
            'fileCount': len(value['files']), 'totalBytes': sum(item['bytes'] for item in value['files']),
            'verificationScope': 'observed-at-check', 'writesPerformed': False,
            'provenanceAuthenticated': False, 'executionAuthorized': False, 'runtimeCompatibilityVerified': False}
    manifest = _freeze(value)
    state = _ObservationState(source, manifest, MappingProxyType({item['path']: item for item in manifest['files']}),
                              _freeze(children), _freeze(observed), bundle._identity(root),
                              json.dumps(metadata).encode('ascii'), cancel, deadline)
    check()
    return state


@contextmanager
def _observe_dependency_bundle(source, manifest, **kwargs):
    """Retain one bounded host observation only for the surrounding operation.

    The arguments are exactly those of verify_dependency_bundle. No content is
    retained. Normalized authority and fingerprints are detached and immutable;
    only a fresh sanitized metadata projection is exposed. The identity token
    cannot be reconstructed from public/model/RPC data. This is an in-process
    API boundary, not protection against arbitrary hostile Python in this process.
    """
    state = _capture_observation(source, manifest, **kwargs)
    observation = object.__new__(_DependencyObservation)
    _OBSERVATIONS[observation] = state
    try:
        yield observation
    finally:
        _OBSERVATIONS.pop(observation, None)


def _recheck_dependency_observation(observation, *, check_authorized, cancel=None, deadline=None):
    """Consume once, then compare exact descriptor fingerprints without byte reads.

    This checks the original source, inventory, root and normalized policy only;
    no caller-provided replacement state is accepted. The host must separately
    bind that observation to its operation and revalidate current authority/CAS.
    Failure consumes the token too. Cancellation/deadline may be tightened, never
    relaxed. Success is observed-at-check metadata freshness, not authorization
    or a filesystem-wide atomic guarantee.
    """
    state = _observation_state(observation, consume=True)
    _validate_checkpoint(deadline, check_authorized)
    if not callable(check_authorized):
        raise ValueError('Dependency recheck requires a current authorization checkpoint')
    deadlines = [value for value in (state.deadline, deadline) if value is not None]
    deadline = min(deadlines) if deadlines else None

    def check():
        bundle._checkpoint(state.cancel, deadline)
        bundle._checkpoint(cancel, deadline)
        if check_authorized() is not True:
            raise ValueError('Current dependency verification authority was denied')
        bundle._checkpoint(state.cancel, deadline)
        bundle._checkpoint(cancel, deadline)

    check()
    with _directory(state.source) as source_fd:
        if bundle._identity(os.fstat(source_fd)) != state.root:
            raise ValueError('Dependency source root changed after its bytes were verified')
        _walk(source_fd, '', state.expected, state.children, {}, check, state.observed, hash_bytes=False)
        check()
        with _directory(state.source) as current_source:
            if bundle._identity(os.fstat(current_source)) != state.root:
                raise ValueError('Dependency source root changed during its observation recheck')
        check()
    return True


def verify_dependency_bundle(source, manifest, *, lockfile_bytes, package_manifests,
                             expected_toolchain, expected_source_identity, expected_bundle_sha256,
                             limits=None, cancel=None, deadline=None, check_authorized=None):
    """Hash the exact pinned current tree and return bounded observation metadata.

The caller supplies independently retained root and bundle identities, exact
lock/package bytes and toolchain policy. Authorization, when supplied, must
return True at every checkpoint or raise. No copy is allocated and no source
mode, content or directory is changed. Reading may update filesystem atime.

A final descriptor walk compares all observed file/directory fingerprints, then
reopens the absolute root path. This detects mutations observed during the
check, but cannot eliminate the race after any final filesystem observation.
Registry provenance remains a declaration, not archive authentication.
"""
    with _observe_dependency_bundle(source, manifest, lockfile_bytes=lockfile_bytes,
            package_manifests=package_manifests, expected_toolchain=expected_toolchain,
            expected_source_identity=expected_source_identity, expected_bundle_sha256=expected_bundle_sha256,
            limits=limits, cancel=cancel, deadline=deadline, check_authorized=check_authorized) as observation:
        return observation.metadata()

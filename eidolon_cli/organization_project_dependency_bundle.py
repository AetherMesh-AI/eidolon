"""Inert, lock-bound prepared npm dependency inventory and private staging.

This is an ingestion boundary, not a package installer or execution capability.
Registry declarations are compared with lock metadata, never authenticated as
archive contents or independent reproduction. Physical symlinks (including npm
.bin links) are unsupported: workspace links are separate, inert mappings for a
later source assembler. Empty directories are not part of this file-only format.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, fields
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
from urllib.parse import urlsplit

from eidolon_cli.organization_project_compiler_bundle import _directory


@dataclass(frozen=True)
class DependencyBundleLimits:
    """Ingestion ceilings, not measured workload capacity or runtime opt-in."""

    max_files: int = 100_000
    max_bytes: int = 4 * 1024**3
    max_file_bytes: int = 256 * 1024**2
    max_manifest_bytes: int = 32 * 1024**2
    max_lockfile_bytes: int = 16 * 1024**2
    max_package_manifests: int = 128
    max_package_manifest_bytes: int = 2 * 1024**2
    max_depth: int = 32
    max_path_bytes: int = 1024


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')
_DIR_FLAGS = os.O_RDONLY | sum(getattr(os, flag, 0) for flag in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC'))
_FILE_FLAGS = os.O_RDONLY | sum(getattr(os, flag, 0) for flag in ('O_NOFOLLOW', 'O_NONBLOCK', 'O_CLOEXEC'))


def _limits(value):
    value = DependencyBundleLimits() if value is None else value
    if not isinstance(value, DependencyBundleLimits):
        raise ValueError('Dependency limits must be explicit DependencyBundleLimits')
    ceilings = DependencyBundleLimits()
    for field in fields(value):
        size = getattr(value, field.name)
        if type(size) is not int or not 1 <= size <= getattr(ceilings, field.name):
            raise ValueError('Dependency limit exceeds its supported bound: ' + field.name)
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError('Dependency digest must be lowercase SHA-256')
    return value


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(label + ' requires exact fields')
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key in dependency input')
        result[key] = value
    return result


def _json(value, maximum, label):
    """Bound bytes and nesting before decoding; do not accept lossy JSON values."""
    if isinstance(value, dict):
        pending = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > maximum or depth > 64:
                raise ValueError(label + ' exceeds its structural bound')
            if isinstance(item, dict):
                if any(type(key) is not str for key in item):
                    raise ValueError(label + ' requires string keys')
                pending.extend((entry, depth + 1) for entry in item.values())
            elif isinstance(item, list):
                if len(item) > maximum:
                    raise ValueError(label + ' exceeds its structural bound')
                pending.extend((entry, depth + 1) for entry in item)
            elif type(item) not in (str, int, bool, type(None)):
                raise ValueError(label + ' contains unsupported JSON values')
            elif isinstance(item, str) and len(item) > maximum:
                raise ValueError(label + ' exceeds its byte bound')
        try:
            value = _canonical(value)
        except (ValueError, OverflowError, RecursionError) as error:
            raise ValueError(label + ' is not bounded JSON') from error
    if type(value) is not bytes or len(value) > maximum:
        raise ValueError(label + ' requires bounded raw JSON bytes or an object')
    depth = 0
    quoted = escaped = False
    for byte in value:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 64:
                raise ValueError(label + ' exceeds its nesting bound')
        elif byte in (93, 125):
            depth -= 1
    try:
        result = json.loads(value, object_pairs_hook=_unique_pairs,
                            parse_constant=lambda unused: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(label + ' is not unambiguous JSON') from error
    if not isinstance(result, dict):
        raise ValueError(label + ' must be a JSON object')
    return result


def _path(value, limits):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8', errors='replace')) > limits.max_path_bytes
            or '\\' in value or ':' in value or value.startswith('/')
            or len(value.split('/')) > limits.max_depth
            or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in value.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError('Dependency path is ambiguous or exceeds its bound')
    return value


def _path_set(values, limits, label, *, allow_prefixes=False):
    seen = {}
    ancestors = {}
    for value in values:
        path = _path(value, limits)
        if path.casefold() in seen:
            raise ValueError(label + ' contains duplicate casefold paths')
        seen[path.casefold()] = path
        parts = path.split('/')
        for end in range(1, len(parts) + 1):
            ancestor = '/'.join(parts[:end])
            previous = ancestors.setdefault(ancestor.casefold(), ancestor)
            if previous != ancestor:
                raise ValueError(label + ' contains ambiguous casefold directory paths')
    for folded in seen:
        parts = folded.split('/')
        if not allow_prefixes and any('/'.join(parts[:end]) in seen for end in range(1, len(parts))):
            raise ValueError(label + ' contains a file-prefix collision')
    return sorted(seen.values())


def _toolchain(value):
    keys = {'platform', 'arch', 'libc', 'nodeVersion', 'npmVersion', 'nodeAbi', 'tools'}
    _exact(value, keys, 'Dependency toolchain')
    if value['platform'] != 'linux' or value['arch'] not in ('x64', 'arm64') or value['libc'] not in ('glibc', 'musl'):
        raise ValueError('Dependency toolchain platform is unsupported')
    if any(not isinstance(value[key], str) or len(value[key]) > 128 or not _VERSION.fullmatch(value[key])
           for key in ('nodeVersion', 'npmVersion')):
        raise ValueError('Dependency toolchain versions must be exact')
    if not isinstance(value['nodeAbi'], str) or not re.fullmatch(r'[1-9][0-9]{0,5}', value['nodeAbi']):
        raise ValueError('Dependency Node ABI must be exact')
    tools = value['tools']
    if not isinstance(tools, dict) or not 1 <= len(tools) <= 128:
        raise ValueError('Dependency toolchain requires bounded tool versions')
    for name, version in tools.items():
        if (not isinstance(name, str) or not re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+', name)
                or len(name) > 214 or not isinstance(version, str) or len(version) > 128
                or not _VERSION.fullmatch(version)):
            raise ValueError('Dependency tool version must name an exact package version')
    return value


def _package_paths(path):
    """Return every contiguous npm installation boundary, without I/O.

    A package may ship arbitrary ``dist/node_modules`` distribution assets.
    Only ``package/node_modules/child`` starts another installed package; after
    entering an ordinary payload directory, all remaining bytes keep that owner.
    Callers must still require each returned package's exact lockfile record.
    """
    parts = path.split('/')
    if 'node_modules' not in parts:
        return ()
    index = parts.index('node_modules') + 1
    packages = []
    while True:
        if index >= len(parts):
            raise ValueError('Dependency file lacks an installed package owner')
        if parts[index].startswith('.'):
            if index == len(parts) - 1 and parts[index] == '.package-lock.json':
                return tuple(packages)
            raise ValueError('Dependency file lacks an installed package owner')
        end = index + (2 if parts[index].startswith('@') else 1)
        if end >= len(parts):
            raise ValueError('Dependency file lacks an installed package owner')
        packages.append('/'.join(parts[:end]))
        if parts[end] != 'node_modules':
            return tuple(packages)
        index = end + 1


def _package_path(path):
    packages = _package_paths(path)
    return packages[-1] if packages else None


def _provenance(value, packages, installed):
    _exact(value, {'kind', 'preparationRecordSha256', 'registryPackages'}, 'Dependency provenance')
    if value['kind'] not in ('owner-supplied', 'registry-prepared'):
        raise ValueError('Dependency preparation provenance kind is unsupported')
    _hash(value['preparationRecordSha256'])
    entries = value['registryPackages']
    if not isinstance(entries, list) or len(entries) > len(installed):
        raise ValueError('Dependency registry declarations exceed installed packages')
    seen = set()
    for entry in entries:
        _exact(entry, {'path', 'resolved', 'integrity'}, 'Dependency registry declaration')
        path = entry['path']
        if not isinstance(path, str) or path not in installed or path in seen:
            raise ValueError('Dependency registry package is unlisted or repeated')
        seen.add(path)
        resolved, integrity = entry['resolved'], entry['integrity']
        if not isinstance(resolved, str) or len(resolved) > 4096 or not isinstance(integrity, str) or len(integrity) > 1024:
            raise ValueError('Dependency registry metadata exceeds its bound')
        try:
            url = urlsplit(resolved)
            valid_url = (url.scheme == 'https' and url.hostname and not url.username and not url.password
                         and not url.fragment and not url.query and not any(ord(char) <= 32 for char in resolved))
        except ValueError:
            valid_url = False
        valid_integrity = False
        if re.fullmatch(r'sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2}', integrity):
            algorithm, encoded = integrity.split('-', 1)
            try:
                decoded = base64.b64decode(encoded, validate=True)
                valid_integrity = len(decoded) == int(algorithm[3:]) // 8 and base64.b64encode(decoded).decode() == encoded
            except (ValueError, binascii.Error):
                valid_integrity = False
        if not valid_url or not valid_integrity:
            raise ValueError('Dependency registry metadata is not a bounded HTTPS archive declaration')
        if any(entry[key] != packages[path].get(key) for key in ('resolved', 'integrity')):
            raise ValueError('Dependency registry declaration differs from the exact lockfile')
    if value['kind'] == 'registry-prepared' and seen != installed:
        raise ValueError('Registry-prepared provenance requires every installed archive declaration')
    return {**value, 'registryPackages': sorted(entries, key=lambda item: item['path'])}


def normalize_prepared_dependency_manifest(manifest, *, lockfile_bytes, package_manifests,
                                           expected_toolchain, limits=None):
    """Validate supplied bytes and declarations; return their canonical V1 identity.

The expected toolchain is caller policy, never inferred from executable metadata.
Tools are bound to exact root node_modules/{name} package paths and versions.
This validates the declared selected workspace graph, not npm resolution closure.
A downstream profile must separately prove its selected source/dependency closure.
"""
    limits = _limits(limits)
    value = _json(manifest, limits.max_manifest_bytes, 'Dependency manifest')
    _exact(value, {'schemaVersion', 'lockfile', 'packageManifests', 'selectedWorkspaces',
                   'workspaceLinks', 'toolchain', 'provenance', 'files'}, 'Dependency manifest')
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise ValueError('Unsupported prepared dependency manifest version')
    lock = _json(lockfile_bytes, limits.max_lockfile_bytes, 'Dependency lockfile')
    if type(lockfile_bytes) is not bytes or type(lock.get('lockfileVersion')) is not int or lock['lockfileVersion'] != 3:
        raise ValueError('Prepared dependencies require exact raw npm lockfile v3 bytes')
    packages = lock.get('packages')
    if not isinstance(packages, dict) or not isinstance(packages.get(''), dict):
        raise ValueError('Dependency lockfile requires its package graph')
    _exact(value['lockfile'], {'path', 'sha256'}, 'Dependency lockfile binding')
    if value['lockfile']['path'] != 'package-lock.json' or _hash(value['lockfile']['sha256']) != _sha(lockfile_bytes):
        raise ValueError('Dependency lockfile raw identity does not match')
    selected = value['selectedWorkspaces']
    if not isinstance(selected, list) or len(selected) >= limits.max_package_manifests:
        raise ValueError('Dependency workspace graph exceeds its bound')
    selected = _path_set(selected, limits, 'Selected workspaces', allow_prefixes=True)
    if any('node_modules' in path.split('/') or not isinstance(packages.get(path), dict) or packages[path].get('link')
           for path in selected):
        raise ValueError('Selected workspace is absent from the exact lockfile')
    expected_paths = {'package.json', *(path + '/package.json' for path in selected)}
    descriptors = value['packageManifests']
    if not isinstance(descriptors, list) or len(descriptors) != len(expected_paths):
        raise ValueError('Dependency package manifest graph is incomplete')
    if not isinstance(package_manifests, dict) or set(package_manifests) != expected_paths:
        raise ValueError('Dependency inputs must provide exactly the selected package manifests')
    for item in descriptors:
        _exact(item, {'path', 'sha256'}, 'Dependency package manifest')
    paths = _path_set((item['path'] for item in descriptors), limits, 'Package manifests')
    if set(paths) != expected_paths:
        raise ValueError('Dependency package manifest graph does not match its workspaces')
    for item in descriptors:
        raw = package_manifests[item['path']]
        if type(raw) is not bytes:
            raise ValueError('Dependency package manifests require exact raw bytes')
        _json(raw, limits.max_package_manifest_bytes, 'Package manifest')
        if _hash(item['sha256']) != _sha(raw):
            raise ValueError('Dependency package manifest raw identity does not match')
    toolchain = _toolchain(value['toolchain'])
    expected = _toolchain(_json(expected_toolchain, 65536, 'Expected dependency toolchain'))
    if toolchain != expected:
        raise ValueError('Dependency toolchain differs from caller policy')
    entries = value['files']
    if not isinstance(entries, list) or not 1 <= len(entries) <= limits.max_files:
        raise ValueError('Dependency inventory file count exceeds its bound')
    for item in entries:
        _exact(item, {'path', 'bytes', 'sha256', 'mode', 'kind'}, 'Dependency file')
        if type(item['bytes']) is not int or not 0 <= item['bytes'] <= limits.max_file_bytes:
            raise ValueError('Dependency file size exceeds its bound')
        if type(item['mode']) is not int or item['mode'] not in (0o444, 0o555, 0o644, 0o755):
            raise ValueError('Dependency file mode must be read-only or owner-writable without special bits')
        if item['kind'] not in ('file', 'native'):
            raise ValueError('Dependency file kind is unsupported')
        _hash(item['sha256'])
    paths = _path_set((item['path'] for item in entries), limits, 'Dependency files')
    roots = ['node_modules/', *(path + '/node_modules/' for path in selected)]
    if any(not any(path.startswith(root) for root in roots) for path in paths):
        raise ValueError('Dependency inventory must stay inside selected node_modules roots')
    if sum(item['bytes'] for item in entries) > limits.max_bytes:
        raise ValueError('Dependency inventory exceeds its total byte bound')
    path_set = set(paths)
    installed = {package for path in paths for package in _package_paths(path)}
    links = value['workspaceLinks']
    if not isinstance(links, list) or len(links) > limits.max_package_manifests:
        raise ValueError('Dependency workspace links exceed their bound')
    for item in links:
        _exact(item, {'path', 'target'}, 'Dependency workspace link')
        _path(item['path'], limits)
        _path(item['target'], limits)
        record = packages.get(item['path'])
        if (item['target'] not in selected or _package_path(item['path'] + '/package.json') != item['path']
                or not any(item['path'].startswith(root) for root in roots)
                or not isinstance(record, dict) or record.get('link') is not True
                or record.get('resolved') != item['target']):
            raise ValueError('Dependency workspace link differs from the selected lock graph')
        installed.update(_package_paths(item['path'] + '/package.json')[:-1])
    _path_set([*paths, *(item['path'] for item in links)], limits, 'Dependency files and workspace links')
    selected_links = {path for path, entry in packages.items()
                      if isinstance(entry, dict) and entry.get('link') is True and entry.get('resolved') in selected
                      and any(path.startswith(root) for root in roots)}
    if {item['path'] for item in links} != selected_links:
        raise ValueError('Dependency workspace link mapping is incomplete')
    for path in installed:
        package = packages.get(path)
        if (not isinstance(package, dict) or package.get('link') or not isinstance(package.get('version'), str)
                or not isinstance(package.get('name', path.rsplit('node_modules/', 1)[-1]), str)
                or not package.get('name', path.rsplit('node_modules/', 1)[-1])
                or path + '/package.json' not in path_set):
            raise ValueError('Installed dependency package is missing from the inventory or lockfile')
    # V1 tools use only the root installation. Nested/aliased tool resolution is
    # deliberately not inferred from project configuration or PATH.
    for name, version in toolchain['tools'].items():
        path = 'node_modules/' + name
        if path not in installed or packages[path]['version'] != version:
            raise ValueError('Required tool is absent from its exact root package path or version')
    normalized = {**value, 'selectedWorkspaces': selected,
                  'packageManifests': sorted(descriptors, key=lambda item: item['path']),
                  'workspaceLinks': sorted(links, key=lambda item: item['path']),
                  'files': sorted(entries, key=lambda item: item['path']),
                  'provenance': _provenance(value['provenance'], packages, installed)}
    return {'manifest': normalized, 'bundleSha256': _sha(_canonical(normalized)),
            'inventorySha256': _sha(_canonical(normalized['files'])),
            'lockfileSha256': _sha(lockfile_bytes), 'provenanceAuthenticated': False,
            'executionAuthorized': False, 'runtimeCompatibilityVerified': False}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _checkpoint(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise ValueError('Dependency verification cancelled')
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError('Dependency verification deadline exceeded')


def _children(files):
    children = {}
    for path in files:
        parts = path.split('/')
        for index, name in enumerate(parts):
            children.setdefault('/'.join(parts[:index]), set()).add(name)
    return children


def _names(descriptor, expected, check):
    names = set()
    with os.scandir(descriptor) as iterator:
        for entry in iterator:
            check()
            if entry.name in names:
                raise ValueError('Dependency directory enumeration is unstable')
            if entry.name not in expected:
                raise ValueError('Dependency bundle contains undeclared files or directories')
            names.add(entry.name)
    if names != expected:
        raise ValueError('Dependency bundle is incomplete')
    return sorted(names)


@contextmanager
def _regular_descriptor(source, name, item):
    # O_PATH cannot activate a raced-in device. Reopening the anchored regular
    # inode through procfs does not resolve the owner's path for a second time.
    anchor = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source)
    try:
        before = os.fstat(anchor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != item['bytes'] or stat.S_IMODE(before.st_mode) != item['mode']):
            raise ValueError('Dependency file type, links, size or mode does not match')
        descriptor = os.open('/proc/self/fd/' + str(anchor), _FILE_FLAGS & ~os.O_NOFOLLOW)
        try:
            if _identity(before) != _identity(os.fstat(descriptor)):
                raise ValueError('Dependency file identity changed while opening its descriptor')
            yield descriptor, before
        finally:
            os.close(descriptor)
    finally:
        os.close(anchor)


def _copy_file(source, target, name, item, check, package_identity):
    with _regular_descriptor(source, name, item) as (descriptor, before):
        output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=target)
        total, hasher = 0, hashlib.sha256()
        package_data = bytearray() if package_identity is not None else None
        try:
            while True:
                check()
                block = os.read(descriptor, min(65536, item['bytes'] - total + 1))
                if not block:
                    break
                total += len(block)
                if total > item['bytes']:
                    raise ValueError('Dependency file grew beyond its exact bound')
                hasher.update(block)
                if package_data is not None:
                    package_data.extend(block)
                view = memoryview(block)
                while view:
                    check()
                    written = os.write(output, view)
                    if written <= 0:
                        raise OSError('Dependency staging write made no progress')
                    view = view[written:]
            if (total != item['bytes'] or hasher.hexdigest() != item['sha256']
                    or _identity(before) != _identity(os.fstat(descriptor))
                    or _identity(before) != _identity(os.stat(name, dir_fd=source, follow_symlinks=False))):
                raise ValueError('Dependency file bytes or identity changed during verification')
            if package_data is not None:
                package = _json(bytes(package_data), item['bytes'], 'Installed package manifest')
                if package.get('version') != package_identity['version']:
                    raise ValueError('Installed dependency version differs from the exact lockfile')
                if package_identity['toolName'] is not None and package.get('name') != package_identity['toolName']:
                    raise ValueError('Required tool package name differs from its exact root identity')
                if package.get('name') != package_identity['packageName']:
                    raise ValueError('Installed dependency package name differs from its exact lock identity')
            os.fchmod(output, 0o555 if item['mode'] & 0o111 else 0o444)
        finally:
            os.close(output)


def _copy_tree(source, target, prefix, expected, children, check, package_versions):
    check()
    before = os.fstat(source)
    names = _names(source, children.get(prefix, set()), check)
    for name in names:
        check()
        relative = prefix + '/' + name if prefix else name
        if relative not in children:
            _copy_file(source, target, name, expected[relative], check, package_versions.get(relative))
            continue
        child = os.open(name, _DIR_FLAGS, dir_fd=source)
        try:
            opened = os.fstat(child)
            os.mkdir(name, 0o700, dir_fd=target)
            output = os.open(name, _DIR_FLAGS, dir_fd=target)
            try:
                _copy_tree(child, output, relative, expected, children, check, package_versions)
                if _identity(opened) != _identity(os.stat(name, dir_fd=source, follow_symlinks=False)):
                    raise ValueError('Dependency directory identity changed during verification')
                os.fchmod(output, 0o555)
            finally:
                os.close(output)
        finally:
            os.close(child)
    check()
    _names(source, children.get(prefix, set()), check)
    if _identity(before) != _identity(os.fstat(source)):
        raise ValueError('Dependency directory changed during verification')


def _cleanup(parent, name, identity):
    """Unseal only descriptor-anchored staging directories before safe removal."""
    def unseal(descriptor):
        os.fchmod(descriptor, 0o700)
        with os.scandir(descriptor) as iterator:
            names = [entry.name for entry in iterator if entry.is_dir(follow_symlinks=False)]
        for child_name in names:
            child = os.open(child_name, _DIR_FLAGS, dir_fd=descriptor)
            try:
                unseal(child)
            finally:
                os.close(child)
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
    try:
        observed = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) != identity:
            raise ValueError('Dependency staging root was replaced; refusing to remove it')
        unseal(descriptor)
    finally:
        os.close(descriptor)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Descriptor-safe dependency staging cleanup is unsupported')
    shutil.rmtree(name, dir_fd=parent)


def copy_verified_dependency_bundle(source, destination, manifest, *, lockfile_bytes,
                                    package_manifests, expected_toolchain, limits=None,
                                    cancel=None, deadline=None, expected_source_identity=None):
    """Reserve a new private directory, copy/hash/seal, then return inert identity.

The caller owns the parent (0700, current UID) and must not expose or use the
reserved destination until success. No existing path is replaced. Files are
copied from Linux O_PATH/procfs descriptors; ancestors cannot be links.
A failed operation removes its private tree or reports cleanup failure as a note.
When supplied, expected_source_identity is the independently owner-approved
(device, inode), checked against the opened source before allocating any copy.
"""
    if os.name != 'posix' or not all(hasattr(os, flag) for flag in ('O_PATH', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC')):
        raise ValueError('Dependency staging requires Linux O_PATH and procfs descriptor reopening')
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError('Dependency deadline must be a finite monotonic timestamp')
    check = lambda: _checkpoint(cancel, deadline)
    if expected_source_identity is not None and (
            type(expected_source_identity) is not tuple or len(expected_source_identity) != 2
            or any(type(item) is not int or not 0 <= item < 2**64 for item in expected_source_identity)):
        raise ValueError('Expected dependency root identity requires exact device and inode integers')
    check()
    limits = _limits(limits)
    receipt = normalize_prepared_dependency_manifest(
        manifest, lockfile_bytes=lockfile_bytes, package_manifests=package_manifests,
        expected_toolchain=expected_toolchain, limits=limits)
    check()
    value = receipt['manifest']
    expected = {item['path']: item for item in value['files']}
    lock = _json(lockfile_bytes, limits.max_lockfile_bytes, 'Dependency lockfile')
    versions = {}
    tool_names = {'node_modules/' + name: name for name in value['toolchain']['tools']}
    for item in value['files']:
        package = _package_path(item['path'])
        if package and item['path'] == package + '/package.json':
            if item['bytes'] > limits.max_package_manifest_bytes:
                raise ValueError('Installed package manifest exceeds its byte bound')
            versions[item['path']] = {'version': lock['packages'][package]['version'],
                                      'packageName': lock['packages'][package].get('name', package.rsplit('node_modules/', 1)[-1]),
                                      'toolName': tool_names.get(package)}
    raw_destination = os.fspath(destination)
    if (not isinstance(raw_destination, str) or not raw_destination.startswith('/')
            or '\\' in raw_destination or any(part in ('', '.', '..') for part in raw_destination.split('/')[1:])):
        raise ValueError('Dependency staging requires an exact absolute destination')
    destination = Path(raw_destination)
    with ExitStack() as stack:
        parent = stack.enter_context(_directory(destination.parent))
        parent_info = os.fstat(parent)
        if parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise ValueError('Dependency staging parent must be caller-private (0700)')
        source_fd = stack.enter_context(_directory(source))
        source_info = os.fstat(source_fd)
        if (expected_source_identity is not None
                and (source_info.st_dev, source_info.st_ino) != expected_source_identity):
            raise ValueError('Opened dependency root differs from its owner-approved identity')
        os.mkdir(destination.name, 0o700, dir_fd=parent)
        reserved = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        reserved_identity = (reserved.st_dev, reserved.st_ino)
        try:
            target = os.open(destination.name, _DIR_FLAGS, dir_fd=parent)
            try:
                target_info = os.fstat(target)
                if (target_info.st_dev, target_info.st_ino) != reserved_identity:
                    raise ValueError('Dependency staging root was replaced before copying')
                _copy_tree(source_fd, target, '', expected, _children(expected), check, versions)
                with _directory(source) as current_source:
                    if _identity(source_info) != _identity(os.fstat(current_source)):
                        raise ValueError('Dependency source root identity changed during verification')
                with _directory(destination.parent) as current_parent:
                    observed = os.fstat(current_parent)
                    if (parent_info.st_dev, parent_info.st_ino) != (observed.st_dev, observed.st_ino):
                        raise ValueError('Dependency staging parent identity changed during verification')
                observed = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
                if (target_info.st_dev, target_info.st_ino) != (observed.st_dev, observed.st_ino):
                    raise ValueError('Dependency staging root identity changed during verification')
                os.fchmod(target, 0o555)
                check()
            finally:
                os.close(target)
        except BaseException as error:
            try:
                _cleanup(parent, destination.name, reserved_identity)
            except (OSError, ValueError) as cleanup_error:
                error.add_note('Private dependency staging cleanup failed: ' + str(cleanup_error))
            raise
    return receipt

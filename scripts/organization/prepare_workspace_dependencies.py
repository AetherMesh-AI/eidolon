#!/usr/bin/env python3
"""Inventory a preexisting CI npm tree; never install or execute package code.

The caller supplies metadata recorded in the pinned Node container after the
reviewed npm ci command. These records are bound, not independently authenticated.
Candidate mode writes metadata only. Bundle mode additionally requires an exact
reviewed native-file policy. The output's existing parent must be private (0700).
"""
from contextlib import ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
from urllib.parse import urlsplit

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eidolon_cli import organization_project_dependency_bundle as bundle
from eidolon_cli.organization_project_compiler_bundle import _directory
from eidolon_cli.organization_project_node import IMAGE


INSTALL_COMMAND = ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--bin-links=false']
WORKSPACES = ['apps/desktop', 'apps/shared']
TOOLS = ('typescript', 'vitest', 'vite')
FORMATS = {'elf', 'pe', 'macho', 'wasm', 'archive'}
LIMITS = bundle.DependencyBundleLimits()


def _read_at(repository, relative, maximum):
    bundle._path(relative, LIMITS)
    parent_path = repository / posixpath.dirname(relative) if '/' in relative else repository
    with _directory(parent_path) as parent:
        name = posixpath.basename(relative)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if info.st_size > maximum:
            raise ValueError('Preparation input exceeds its byte bound: ' + relative)
        item = {'bytes': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}
        with bundle._regular_descriptor(parent, name, item) as (descriptor, before):
            data = bytearray()
            while True:
                block = os.read(descriptor, min(65536, maximum - len(data) + 1))
                if not block:
                    break
                data.extend(block)
                if len(data) > maximum:
                    raise ValueError('Preparation input grew beyond its byte bound')
            if (bundle._identity(before) != bundle._identity(os.fstat(descriptor))
                    or bundle._identity(before) != bundle._identity(os.stat(name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError('Preparation input changed while reading')
            return bytes(data)


def _json_file(path, maximum):
    return bundle._json(_read_at(path.parent, path.name, maximum), maximum, 'Preparation metadata')


def _runtime_metadata(value):
    bundle._exact(value, {'schemaVersion', 'image', 'platform', 'arch', 'libc', 'glibcVersion',
                         'nodeVersion', 'nodeAbi', 'npmVersion'}, 'Runtime metadata')
    if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['image'] != IMAGE or value['platform'] != 'linux' or value['arch'] != 'x64'
            or value['libc'] != 'glibc' or not isinstance(value['glibcVersion'], str)
            or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value['glibcVersion'])):
        raise ValueError('Runtime metadata must describe the exact pinned Linux x64 glibc image')
    versions = {key: value[key] for key in ('platform', 'arch', 'libc', 'nodeVersion', 'nodeAbi', 'npmVersion')}
    bundle._toolchain(versions | {'tools': {'typescript': '0.0.0'}})
    return value


def _install_record(value, lockfile, runtime):
    bundle._exact(value, {'schemaVersion', 'command', 'exitCode', 'lockfileSha256',
                         'runtimeMetadataSha256', 'registry', 'lifecycleScripts'}, 'Installation record')
    if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['command'] != INSTALL_COMMAND or type(value['exitCode']) is not int or value['exitCode'] != 0
            or value['lockfileSha256'] != bundle._sha(lockfile)
            or value['runtimeMetadataSha256'] != bundle._sha(bundle._canonical(runtime))
            or value['registry'] != 'https://registry.npmjs.org' or value['lifecycleScripts'] is not False):
        raise ValueError('Installation record does not bind the reviewed successful ignore-scripts command')
    return value


def _native_policy(value):
    bundle._exact(value, {'schemaVersion', 'files'}, 'Native policy')
    if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['files'], list) or len(value['files']) > LIMITS.max_files):
        raise ValueError('Native policy requires a bounded versioned explicit file list')
    for item in value['files']:
        bundle._exact(item, {'path', 'sha256', 'format'}, 'Native policy file')
        bundle._path(item['path'], LIMITS)
        bundle._hash(item['sha256'])
        if item['format'] not in FORMATS:
            raise ValueError('Native policy contains an unsupported format')
    bundle._path_set((item['path'] for item in value['files']), LIMITS, 'Native policy files')
    return {'schemaVersion': 1, 'files': sorted(value['files'], key=lambda item: item['path'])}


def _native_format(path, header):
    for signature, kind in ((b'\x7fELF', 'elf'), (b'MZ', 'pe'), (b'\0asm', 'wasm'), (b'!<arch>\n', 'archive')):
        if header.startswith(signature):
            return kind
    if header[:4] in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                      b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                      b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
        return 'macho'
    if re.search(r'\.(?:node|exe|dll|dylib|wasm|so(?:\.[0-9]+)*)$', path, re.IGNORECASE):
        raise ValueError('Native-looking path has an unrecognized format: ' + path)
    return None


def _registry_packages(packages):
    for path, record in packages.items():
        if path:
            bundle._path(path, LIMITS)
        if not isinstance(record, dict):
            raise ValueError('Lock package record must be an object')
        if 'node_modules' not in path.split('/') or record.get('link') is True:
            continue
        resolved = record.get('resolved')
        if not isinstance(resolved, str):
            raise ValueError('Locked dependency is not an official registry archive: ' + path)
        parsed = urlsplit(resolved)
        if (parsed.scheme != 'https' or parsed.netloc != 'registry.npmjs.org'
                or not parsed.path.endswith('.tgz') or parsed.query or parsed.fragment):
            raise ValueError('Locked dependency is not an official registry archive: ' + path)
        declaration = {'path': path, 'resolved': resolved, 'integrity': record.get('integrity')}
        bundle._provenance({'kind': 'registry-prepared', 'preparationRecordSha256': '0' * 64,
                            'registryPackages': [declaration]}, packages, {path})


class Inventory:
    def __init__(self, repository, packages, staging=None):
        self.repository, self.packages, self.staging = repository, packages, staging
        self.files, self.native, self.bin_links = [], [], []
        self.workspace_links, self.omitted_links = [], []
        self.package_data, self.workspace_data = {}, {}
        self.total = self.nodes = 0

    def _file(self, parent, name, relative, info):
        if '/.bin/' in '/' + relative:
            raise ValueError('Only verified symlinks are allowed in npm .bin directories')
        mode = stat.S_IMODE(info.st_mode)
        if not mode & 0o400 or mode & ~0o755 or info.st_size > LIMITS.max_file_bytes:
            raise ValueError(f'Installed file exceeds its mode or byte bound: {relative} (mode={mode:o}, bytes={info.st_size})')
        self.total += info.st_size
        if self.total > LIMITS.max_bytes or len(self.files) >= LIMITS.max_files:
            raise ValueError('Installed dependency inventory exceeds its bound')
        package = bundle._package_path(relative)
        if package is None and not relative.endswith('/node_modules/.package-lock.json') and relative != 'node_modules/.package-lock.json':
            raise ValueError('Installed file is outside an npm package: ' + relative)
        is_package = package is not None and relative == package + '/package.json'
        if is_package and info.st_size > LIMITS.max_package_manifest_bytes:
            raise ValueError('Installed package manifest exceeds its byte bound')
        package_data = bytearray() if is_package else None
        hasher, total, header = hashlib.sha256(), 0, b''
        item = {'bytes': info.st_size, 'mode': mode}
        with bundle._regular_descriptor(parent, name, item) as (descriptor, before), ExitStack() as stack:
            output = None
            if self.staging is not None:
                target = self.staging / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                output = stack.enter_context(target.open('xb'))
            while True:
                block = os.read(descriptor, min(65536, info.st_size - total + 1))
                if not block:
                    break
                total += len(block)
                if total > info.st_size:
                    raise ValueError('Installed dependency grew during inventory')
                hasher.update(block)
                if not header:
                    header = block[:16]
                if package_data is not None:
                    package_data.extend(block)
                if output is not None:
                    output.write(block)
            if (total != info.st_size or bundle._identity(before) != bundle._identity(os.fstat(descriptor))
                    or bundle._identity(before) != bundle._identity(os.stat(name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError('Installed file changed during inventory')
            if output is not None:
                output.flush()
                os.fchmod(output.fileno(), 0o555 if mode & 0o111 else 0o444)
        detected = _native_format(relative, header)
        entry = {'path': relative, 'bytes': total, 'sha256': hasher.hexdigest(),
                 'mode': 0o555 if mode & 0o111 else 0o444, 'kind': 'native' if detected else 'file'}
        self.files.append(entry)
        if detected:
            self.native.append({'path': relative, 'sha256': entry['sha256'], 'format': detected})
        if package_data is not None:
            data = bundle._json(bytes(package_data), LIMITS.max_package_manifest_bytes, 'Installed package manifest')
            locked = self.packages.get(package)
            if not isinstance(locked, dict) or locked.get('link') or data.get('version') != locked.get('version'):
                raise ValueError('Installed package does not match its exact lock version: ' + package)
            self.package_data[package] = data

    def _link(self, parent, name, relative, info):
        target = os.readlink(name, dir_fd=parent)
        if not target or target.startswith('/') or '\\' in target or any(ord(char) < 32 for char in target):
            raise ValueError('Installed symlink target is not an exact relative path')
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
        bundle._path(resolved, LIMITS)
        if bundle._identity(info) != bundle._identity(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise ValueError('Installed symlink changed during inventory')
        if posixpath.basename(posixpath.dirname(relative)) == '.bin':
            self.bin_links.append({'path': relative, 'target': resolved})
            return
        locked, workspace = self.packages.get(relative), self.packages.get(resolved)
        if (not isinstance(locked, dict) or locked.get('link') is not True or locked.get('resolved') != resolved
                or 'node_modules' in resolved.split('/') or not isinstance(workspace, dict) or workspace.get('link')):
            raise ValueError('Unexpected installed symlink: ' + relative)
        raw = _read_at(self.repository, resolved + '/package.json', LIMITS.max_package_manifest_bytes)
        data = bundle._json(raw, LIMITS.max_package_manifest_bytes, 'Workspace link manifest')
        expected_name = relative.rsplit('node_modules/', 1)[-1]
        if (data.get('name') != expected_name or data.get('version') != workspace.get('version')
                or ('name' in workspace and data.get('name') != workspace['name'])):
            raise ValueError('Workspace link manifest differs from its lock identity')
        self.workspace_data[resolved] = data
        record = {'path': relative, 'target': resolved}
        (self.workspace_links if resolved in WORKSPACES else self.omitted_links).append(record)

    def walk(self, descriptor, prefix):
        before, names = os.fstat(descriptor), set()
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                self.nodes += 1
                if self.nodes > LIMITS.max_files * 3 or entry.name in names:
                    raise ValueError('Installed dependency directory enumeration exceeds its bound')
                names.add(entry.name)
        for name in sorted(names):
            relative = prefix + '/' + name
            bundle._path(relative, LIMITS)
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, bundle._DIR_FLAGS, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if bundle._identity(opened) != bundle._identity(info):
                        raise ValueError('Installed directory changed before inventory')
                    self.walk(child, relative)
                    if bundle._identity(opened) != bundle._identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)):
                        raise ValueError('Installed directory changed during inventory')
                finally:
                    os.close(child)
            elif stat.S_ISLNK(info.st_mode):
                self._link(descriptor, name, relative, info)
            elif stat.S_ISREG(info.st_mode):
                self._file(descriptor, name, relative, info)
            else:
                raise ValueError('Installed dependency contains a special file')
        bundle._names(descriptor, names, lambda: None)
        if bundle._identity(before) != bundle._identity(os.fstat(descriptor)):
            raise ValueError('Installed dependency directory changed during inventory')

    def _verify_bin(self, link, entries):
        package = bundle._package_path(link['target'])
        data, target = self.package_data.get(package), link['target']
        if data is None:
            mapping = next((item for item in self.workspace_links + self.omitted_links
                            if target.startswith(item['path'] + '/')), None)
            if mapping is None:
                raise ValueError('npm bin link is outside the declared installed graph')
            package = mapping['target']
            target = package + target[len(mapping['path']):]
            data = self.workspace_data[package]
            # This is an omitted shim, not a dependency file or permission to run it.
            _read_at(self.repository, target, LIMITS.max_file_bytes)
        elif target not in entries:
            raise ValueError('npm bin link target is absent from its installed package')
        bins = data.get('bin', {})
        if isinstance(bins, str):
            bins = {str(data.get('name', '')).split('/')[-1]: bins}
        value = bins.get(posixpath.basename(link['path'])) if isinstance(bins, dict) else None
        if (not isinstance(value, str) or not value or value.startswith('/') or '\\' in value
                or posixpath.normpath(posixpath.join(package, value)) != target
                or not target.startswith(package + '/')):
            raise ValueError('npm bin symlink is not declared by its exact package: ' + link['path'])

    def finish(self):
        entries = {item['path'] for item in self.files}
        bundle._path_set([*entries, *(item['path'] for item in self.workspace_links + self.omitted_links + self.bin_links)],
                         LIMITS, 'Installed files and normalized links')
        for path in entries:
            package = bundle._package_path(path)
            if package is not None and package not in self.package_data:
                raise ValueError('Installed package is missing its exact package manifest: ' + package)
        roots = ['node_modules/', *(value + '/node_modules/' for value in WORKSPACES)]
        expected = {path for path, value in self.packages.items() if value.get('link') is True
                    and value.get('resolved') in WORKSPACES and any(path.startswith(root) for root in roots)}
        if {item['path'] for item in self.workspace_links} != expected:
            raise ValueError('Selected workspace link mapping is incomplete')
        for link in self.bin_links:
            self._verify_bin(link, entries)
        for name in TOOLS:
            if self.package_data.get('node_modules/' + name, {}).get('name') != name:
                raise ValueError('Required root tool package is absent or aliased: ' + name)
        for field in ('files', 'native', 'bin_links', 'workspace_links', 'omitted_links'):
            getattr(self, field).sort(key=lambda item: item['path'])


def _scan_scope(inventory, scope):
    parent_path = inventory.repository / posixpath.dirname(scope) if '/' in scope else inventory.repository
    with _directory(parent_path) as parent:
        try:
            descriptor = os.open(posixpath.basename(scope), bundle._DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            if scope == 'node_modules':
                raise
            return
        try:
            before = os.fstat(descriptor)
            inventory.walk(descriptor, scope)
            current = os.stat(posixpath.basename(scope), dir_fd=parent, follow_symlinks=False)
            if bundle._identity(before) != bundle._identity(current):
                raise ValueError('Installed dependency root changed during inventory')
        finally:
            os.close(descriptor)


def _write_json(directory, name, value):
    data = bundle._canonical(value)
    if len(data) > LIMITS.max_manifest_bytes:
        raise ValueError('Preparation output exceeds its byte bound')
    with (directory / name).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), 0o444)


def _source_inputs(repository):
    lockfile = _read_at(repository, 'package-lock.json', LIMITS.max_lockfile_bytes)
    lock = bundle._json(lockfile, LIMITS.max_lockfile_bytes, 'npm lockfile')
    if type(lock.get('lockfileVersion')) is not int or lock['lockfileVersion'] != 3 or not isinstance(lock.get('packages'), dict):
        raise ValueError('CI preparation requires an exact npm v3 lockfile')
    packages = lock['packages']
    _registry_packages(packages)
    manifests = {path: _read_at(repository, path, LIMITS.max_package_manifest_bytes)
                 for path in ['package.json', *(value + '/package.json' for value in WORKSPACES)]}
    for path, raw in manifests.items():
        parsed = bundle._json(raw, LIMITS.max_package_manifest_bytes, 'Workspace package manifest')
        locked = packages.get(posixpath.dirname(path))
        if not isinstance(locked, dict) or locked.get('link'):
            raise ValueError('Selected package manifest is absent from lockfile')
        if (('name' in locked and parsed.get('name') != locked['name'])
                or any(parsed.get(key) != locked.get(key) for key in ('version', 'dependencies',
                       'devDependencies', 'optionalDependencies', 'peerDependencies'))):
            raise ValueError('Selected package manifest differs from lockfile graph: ' + path)
    return lockfile, packages, manifests


def _recheck_inputs(repository, lockfile, manifests):
    for path, raw in {'package-lock.json': lockfile, **manifests}.items():
        maximum = LIMITS.max_lockfile_bytes if path == 'package-lock.json' else LIMITS.max_package_manifest_bytes
        if _read_at(repository, path, maximum) != raw:
            raise ValueError('Lockfile or selected package manifest changed during preparation')


def inspect_candidate(repository, output):
    """Record inert candidates with unknown preparation/runtime provenance."""
    repository, output = Path(repository), Path(output)
    with _directory(repository) as source_root, _directory(output.parent) as parent:
        root_identity, parent_info = bundle._identity(os.fstat(source_root)), os.fstat(parent)
        if parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise ValueError('Preparation output parent must be caller-private (0700)')
        if output == repository or repository in output.parents:
            raise ValueError('Preparation output must be outside the inspected repository')
        lockfile, packages, manifests = _source_inputs(repository)
        inventory = Inventory(repository, packages)
        for scope in ['node_modules', *(value + '/node_modules' for value in WORKSPACES)]:
            _scan_scope(inventory, scope)
        inventory.finish()
        _recheck_inputs(repository, lockfile, manifests)
        if bundle._identity(os.fstat(source_root)) != root_identity:
            raise ValueError('Repository identity changed during preparation')
        result = {'schemaVersion': 1, 'mode': 'candidate', 'bundleCreated': False,
                  'executionAuthorized': False, 'runtimeCompatibilityVerified': False,
                  'provenanceAuthenticated': False, 'preparationRecordKnown': False,
                  'runtimeMetadata': None, 'installationRecord': None,
                  'nativePolicyCandidate': {'schemaVersion': 1, 'files': inventory.native},
                  'lockfileSha256': bundle._sha(lockfile), 'selectedWorkspaces': WORKSPACES,
                  'packageManifests': [{'path': path, 'sha256': bundle._sha(raw)} for path, raw in manifests.items()],
                  'omittedWorkspaceLinks': inventory.omitted_links, 'removedBinLinks': inventory.bin_links,
                  'inventorySha256': bundle._sha(bundle._canonical(inventory.files)), 'files': inventory.files}
        os.mkdir(output.name, 0o700, dir_fd=parent)
        created = os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        try:
            _write_json(output, 'candidate.json', result)
        except BaseException as error:
            try:
                bundle._cleanup(parent, output.name, (created.st_dev, created.st_ino))
            except (OSError, ValueError) as cleanup_error:
                error.add_note('Candidate artifact cleanup failed: ' + str(cleanup_error))
            raise
        return result


def prepare(repository, output, runtime_metadata, preparation_record, *, native_policy):
    """Create a new CI artifact for desktop/shared, without conferring authority."""
    repository, output = Path(repository), Path(output)
    runtime = _runtime_metadata(bundle._json(runtime_metadata, 65536, 'Runtime metadata'))
    approved = _native_policy(bundle._json(native_policy, LIMITS.max_manifest_bytes, 'Native policy'))
    with _directory(repository) as source_root, _directory(output.parent) as output_parent:
        root_identity = bundle._identity(os.fstat(source_root))
        parent_info = os.fstat(output_parent)
        if parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise ValueError('Preparation output parent must be caller-private (0700)')
        if output == repository or repository in output.parents:
            raise ValueError('Preparation output must be outside the inspected repository')
        lockfile, packages, package_manifests = _source_inputs(repository)
        record = _install_record(bundle._json(preparation_record, 65536, 'Installation record'), lockfile, runtime)
        os.mkdir(output.name, 0o700, dir_fd=output_parent)
        reserved = os.stat(output.name, dir_fd=output_parent, follow_symlinks=False)
        identity = (reserved.st_dev, reserved.st_ino)
        try:
            staging = output / 'inventory'
            staging.mkdir(mode=0o700)
            inventory = Inventory(repository, packages, staging)
            for scope in ['node_modules', *(value + '/node_modules' for value in WORKSPACES)]:
                _scan_scope(inventory, scope)
            inventory.finish()
            if inventory.bin_links:
                raise ValueError('Unexpected npm bin links despite the reviewed --bin-links=false command')
            native = {'schemaVersion': 1, 'files': inventory.native}
            if approved != native:
                raise ValueError('Installed native files differ from the exact approved path/hash/format policy')
            toolchain = {key: runtime[key] for key in ('platform', 'arch', 'libc', 'nodeVersion', 'nodeAbi', 'npmVersion')}
            toolchain['tools'] = {name: inventory.package_data['node_modules/' + name]['version'] for name in TOOLS}
            preparation = {'schemaVersion': 1, 'installationRecord': record, 'runtimeMetadata': runtime,
                           'selectedWorkspaces': WORKSPACES, 'omittedWorkspaceLinks': inventory.omitted_links,
                           'removedBinLinks': inventory.bin_links, 'nativePolicySha256': bundle._sha(bundle._canonical(native)),
                           'metadataAuthenticated': False, 'archiveContentsAuthenticated': False,
                           'runtimeCompatibilityVerified': False}
            installed = set(inventory.package_data)
            manifest = {'schemaVersion': 1, 'lockfile': {'path': 'package-lock.json', 'sha256': bundle._sha(lockfile)},
                        'packageManifests': [{'path': path, 'sha256': bundle._sha(data)} for path, data in package_manifests.items()],
                        'selectedWorkspaces': WORKSPACES, 'workspaceLinks': inventory.workspace_links,
                        'toolchain': toolchain, 'files': inventory.files,
                        'provenance': {'kind': 'registry-prepared', 'preparationRecordSha256': bundle._sha(bundle._canonical(preparation)),
                                       'registryPackages': [{'path': path, 'resolved': packages[path]['resolved'],
                                                             'integrity': packages[path]['integrity']} for path in sorted(installed)]}}
            receipt = bundle.normalize_prepared_dependency_manifest(manifest, lockfile_bytes=lockfile,
                package_manifests=package_manifests, expected_toolchain=toolchain)
            if bundle._identity(os.fstat(source_root)) != root_identity:
                raise ValueError('Repository identity changed during preparation')
            _recheck_inputs(repository, lockfile, package_manifests)
            verified = bundle.copy_verified_dependency_bundle(staging, output / 'bundle', receipt['manifest'],
                lockfile_bytes=lockfile, package_manifests=package_manifests, expected_toolchain=toolchain)
            with _directory(output) as parent:
                staged = os.stat('inventory', dir_fd=parent, follow_symlinks=False)
                bundle._cleanup(parent, 'inventory', (staged.st_dev, staged.st_ino))
            result = {key: value for key, value in verified.items() if key != 'manifest'}
            result.update(schemaVersion=1, mode='bundle', bundleCreated=True)
            for name, value in (('manifest.json', verified['manifest']), ('preparation-record.json', preparation),
                                ('toolchain.json', toolchain), ('receipt.json', result)):
                _write_json(output, name, value)
            return result
        except BaseException as error:
            try:
                bundle._cleanup(output_parent, output.name, identity)
            except (OSError, ValueError) as cleanup_error:
                error.add_note('CI preparation artifact cleanup failed: ' + str(cleanup_error))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--runtime-metadata', type=Path)
    parser.add_argument('--preparation-record', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--candidate', action='store_true', help='Write metadata only; create no prepared bundle')
    mode.add_argument('--native-policy', type=Path, help='Exact reviewed native path/hash/format policy')
    arguments = parser.parse_args()
    if arguments.candidate:
        if arguments.runtime_metadata or arguments.preparation_record:
            parser.error('Candidate inspection does not accept runtime or preparation claims')
        result = inspect_candidate(arguments.repository, arguments.output)
    else:
        if not arguments.runtime_metadata or not arguments.preparation_record:
            parser.error('Bundle creation requires both runtime metadata and a preparation record')
        result = prepare(arguments.repository, arguments.output,
                         _json_file(arguments.runtime_metadata, 65536), _json_file(arguments.preparation_record, 65536),
                         native_policy=_json_file(arguments.native_policy, LIMITS.max_manifest_bytes))
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('files', 'preparation', 'nativePolicyCandidate', 'removedBinLinks', 'packageManifests')}, sort_keys=True))


if __name__ == '__main__':
    main()

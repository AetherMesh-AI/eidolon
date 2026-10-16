"""CI preparation inventories real inert files, never executes package payloads."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import stat

import pytest

from eidolon_cli import organization_project_dependency_bundle as bundle
from scripts.organization import prepare_workspace_dependencies as preparation


def write(root, path, content, mode=0o644):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(mode)
    return target


@pytest.fixture
def installed(tmp_path):
    repository = tmp_path / 'repository'
    repository.mkdir()
    workspaces = {'apps/desktop': {'name': 'desktop', 'version': '1.0.0'},
                  'apps/shared': {'name': '@example/shared', 'version': '1.0.0'},
                  'ui-tui': {'name': 'eidolon-tui', 'version': '1.0.0', 'bin': {'eidolon-tui': './cli.mjs'}}}
    packages = {'': {'name': 'root', 'version': '1.0.0'}, **deepcopy(workspaces)}
    write(repository, 'package.json', bundle._canonical(packages['']))
    for path, data in workspaces.items():
        write(repository, path + '/package.json', bundle._canonical(data))
    write(repository, 'ui-tui/cli.mjs', b'throw new Error("must never execute workspace code");\n', 0o755)
    contents = {}
    tools = {'typescript': ('6.0.3', 'bin/tsc', {'tsc': './bin/tsc'}),
             'vitest': ('4.1.10', 'dist/node.js', {}), 'vite': ('8.2.0', 'dist/node.js', {})}
    for name, (version, entry, bins) in tools.items():
        path = 'node_modules/' + name
        data = {'name': name, 'version': version, 'bin': bins}
        packages[path] = {'version': version, 'resolved': f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz',
                          'integrity': 'sha512-' + 'A' * 86 + '=='}
        contents[path + '/package.json'] = bundle._canonical(data)
        contents[path + '/' + entry] = b'throw new Error("must never execute package code");\n'
    nested = 'apps/desktop/node_modules/helper'
    packages[nested] = {'version': '1.0.0', 'resolved': 'https://registry.npmjs.org/helper/-/helper-1.0.0.tgz',
                        'integrity': 'sha512-' + 'A' * 86 + '=='}
    contents[nested + '/package.json'] = bundle._canonical({'name': 'helper', 'version': '1.0.0'})
    contents[nested + '/index.js'] = b'// inert nested file\n'
    contents['node_modules/.package-lock.json'] = b'{"lockfileVersion":3}'
    contents['node_modules/vitest/.github/notes.txt'] = b'Legitimate package content is retained.\n'
    contents['node_modules/vite/linux.node'] = b'\x7fELFinert Linux bytes, never executable'
    contents['node_modules/vite/windows.exe'] = b'MZinert foreign bytes, never executable'
    for path, data in contents.items():
        write(repository, path, data, 0o754 if path.endswith('/bin/tsc') else 0o644)
    for path, target in {'node_modules/desktop': 'apps/desktop',
                         'node_modules/@example/shared': 'apps/shared',
                         'node_modules/eidolon-tui': 'ui-tui'}.items():
        location = repository / path
        location.parent.mkdir(parents=True, exist_ok=True)
        location.symlink_to(os.path.relpath(repository / target, location.parent))
        packages[path] = {'link': True, 'resolved': target}
    bin_directory = repository / 'node_modules/.bin'
    bin_directory.mkdir()
    # The file-only format ignores empty directories, including an empty cache.
    (repository / 'node_modules/.vite-temp').mkdir()
    lockfile = bundle._canonical({'lockfileVersion': 3, 'packages': packages})
    write(repository, 'package-lock.json', lockfile)
    runtime = {'schemaVersion': 1, 'image': preparation.IMAGE, 'platform': 'linux', 'arch': 'x64',
               'libc': 'glibc', 'glibcVersion': '2.36', 'nodeVersion': '24.11.0', 'nodeAbi': '137', 'npmVersion': '11.17.0'}
    record = {'schemaVersion': 1, 'command': preparation.INSTALL_COMMAND.copy(), 'exitCode': 0,
              'lockfileSha256': bundle._sha(lockfile), 'runtimeMetadataSha256': bundle._sha(bundle._canonical(runtime)),
              'registry': 'https://registry.npmjs.org', 'lifecycleScripts': False}
    native = {'schemaVersion': 1, 'files': [
        {'path': path, 'sha256': bundle._sha(contents[path]), 'format': kind}
        for path, kind in [('node_modules/vite/linux.node', 'elf'), ('node_modules/vite/windows.exe', 'pe')]]}
    return {'repository': repository, 'output': tmp_path / 'prepared', 'candidate': tmp_path / 'candidate',
            'runtime': runtime, 'record': record, 'native': native, 'contents': contents}


def prepare(fixture):
    return preparation.prepare(fixture['repository'], fixture['output'], fixture['runtime'], fixture['record'],
                               native_policy=fixture['native'])


@pytest.mark.linux_only
def test_candidate_is_metadata_only_and_exact_policy_produces_verified_readonly_bundle(installed, tmp_path):
    root = installed['repository']
    # Candidate inspection supports existing installs with verified bin links.
    (root / 'node_modules/.bin/tsc').symlink_to('../typescript/bin/tsc')
    (root / 'node_modules/.bin/eidolon-tui').symlink_to('../eidolon-tui/cli.mjs')
    candidate = preparation.inspect_candidate(installed['repository'], installed['candidate'])
    assert sorted(item.name for item in installed['candidate'].iterdir()) == ['candidate.json']
    assert candidate['bundleCreated'] is False
    assert candidate['preparationRecordKnown'] is False
    assert candidate['runtimeMetadata'] is None and candidate['installationRecord'] is None
    assert candidate['nativePolicyCandidate'] == installed['native']
    assert candidate['omittedWorkspaceLinks'] == [{'path': 'node_modules/eidolon-tui', 'target': 'ui-tui'}]
    assert {item['path'] for item in candidate['removedBinLinks']} == {'node_modules/.bin/tsc', 'node_modules/.bin/eidolon-tui'}
    assert {item['path'] for item in candidate['files']} == set(installed['contents'])
    assert (root / 'node_modules/.bin/tsc').is_symlink()
    # Model a fresh reviewed --bin-links=false preparation for canonical output.
    (root / 'node_modules/.bin/tsc').unlink()
    (root / 'node_modules/.bin/eidolon-tui').unlink()
    result = prepare(installed)
    assert result['bundleCreated'] is True
    for value in (candidate, result):
        assert value['executionAuthorized'] is False
        assert value['runtimeCompatibilityVerified'] is False
        assert value['provenanceAuthenticated'] is False
    output = installed['output']
    assert sorted(item.name for item in output.iterdir()) == [
        'bundle', 'manifest.json', 'preparation-record.json', 'receipt.json', 'toolchain.json']
    manifest_bytes = (output / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest_bytes == bundle._canonical(manifest)
    assert bundle._sha(manifest_bytes) == result['bundleSha256']
    assert candidate['inventorySha256'] == result['inventorySha256']
    assert manifest['selectedWorkspaces'] == ['apps/desktop', 'apps/shared']
    assert {item['target'] for item in manifest['workspaceLinks']} == {'apps/desktop', 'apps/shared'}
    staged = output / 'bundle'
    assert {str(path.relative_to(staged)) for path in staged.rglob('*') if path.is_file()} == set(installed['contents'])
    for item in manifest['files']:
        path = staged / item['path']
        assert not path.is_symlink() and path.read_bytes() == installed['contents'][item['path']]
        assert stat.S_IMODE(path.stat().st_mode) == item['mode']
        assert item['mode'] in (0o444, 0o555)
    assert stat.S_IMODE(staged.stat().st_mode) == 0o555
    assert not (staged / 'node_modules/.bin').exists()
    assert not (staged / 'node_modules/eidolon-tui').exists()
    source = installed['repository']
    manifests = {item['path']: (source / item['path']).read_bytes() for item in manifest['packageManifests']}
    ingested = bundle.copy_verified_dependency_bundle(staged, tmp_path / 'ingested', manifest,
        lockfile_bytes=(source / 'package-lock.json').read_bytes(), package_manifests=manifests,
        expected_toolchain=json.loads((output / 'toolchain.json').read_bytes()))
    assert ingested['bundleSha256'] == result['bundleSha256']
    assert not (source / 'node_modules/.bin/tsc').exists()
    assert stat.S_IMODE((source / 'node_modules/typescript/bin/tsc').stat().st_mode) == 0o754
    assert stat.S_IMODE((source / 'node_modules/vite/linux.node').stat().st_mode) == 0o644


@pytest.mark.linux_only
@pytest.mark.parametrize('attack', ['unexpected_link', 'bin_target', 'hardlink', 'fifo', 'native_omitted',
    'native_hash', 'native_format', 'registry', 'command', 'runtime_record', 'workspace_manifest',
    'missing_tool', 'mode', 'root_link', 'nested_file_disappears', 'file_limit', 'unknown_native', 'hidden_cache', 'unexpected_bin'])
def test_preparation_fails_closed_without_partial_bundle(installed, tmp_path, monkeypatch, attack):
    root = installed['repository']

    def replace_link():
        link = root / 'node_modules/.bin/tsc'
        link.symlink_to('../vitest/dist/node.js')

    def registry():
        lock = json.loads((root / 'package-lock.json').read_bytes())
        lock['packages']['node_modules/vite']['resolved'] = 'https://unreviewed.example/vite.tgz'
        write(root, 'package-lock.json', bundle._canonical(lock))
        installed['record']['lockfileSha256'] = bundle._sha((root / 'package-lock.json').read_bytes())

    def root_link():
        directory = root / 'node_modules'
        replacement = tmp_path / 'other-modules'
        directory.rename(replacement)
        directory.symlink_to(replacement)

    def disappear():
        original = preparation.Inventory._file

        def racing(self, parent, name, relative, info):
            if relative == 'apps/desktop/node_modules/helper/index.js':
                os.unlink(name, dir_fd=parent)
            return original(self, parent, name, relative, info)

        monkeypatch.setattr(preparation.Inventory, '_file', racing)

    changes = {
        'unexpected_link': lambda: (root / 'node_modules/vite/escape').symlink_to('/etc/passwd'),
        'bin_target': replace_link,
        'hardlink': lambda: os.link(root / 'node_modules/vite/dist/node.js', tmp_path / 'hardlink'),
        'fifo': lambda: os.mkfifo(root / 'node_modules/vite/fifo'),
        'native_omitted': lambda: installed['native']['files'].pop(),
        'native_hash': lambda: installed['native']['files'][0].update(sha256='f' * 64),
        'native_format': lambda: installed['native']['files'][0].update(format='macho'),
        'registry': registry,
        'command': lambda: installed['record'].update(command=['npm', 'ci']),
        'runtime_record': lambda: installed['record'].update(runtimeMetadataSha256='e' * 64),
        'workspace_manifest': lambda: write(root, 'apps/desktop/package.json', b'{"name":"changed"}'),
        'missing_tool': lambda: (root / 'node_modules/typescript/package.json').unlink(),
        'mode': lambda: (root / 'node_modules/vite/dist/node.js').chmod(0o777),
        'root_link': root_link,
        'nested_file_disappears': disappear,
        'file_limit': lambda: monkeypatch.setattr(preparation, 'LIMITS', replace(preparation.LIMITS, max_files=2)),
        'unknown_native': lambda: write(root, 'node_modules/vite/extra.node', b'not a recognized native format'),
        'hidden_cache': lambda: write(root, 'node_modules/.cache/unexpected', b'cache'),
        'unexpected_bin': lambda: (root / 'node_modules/.bin/tsc').symlink_to('../typescript/bin/tsc'),
    }
    changes[attack]()
    with pytest.raises((ValueError, OSError)):
        prepare(installed)
    assert not installed['output'].exists()
    assert root.exists()

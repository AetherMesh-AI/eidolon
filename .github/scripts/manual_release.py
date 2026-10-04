"""Manual alpha release: stdlib-only policy, packaging verification and GitHub API.

Inputs arrive ONLY through environment variables. No changelog is checked in.
Publication refuses existing tags/releases, including failed draft attempts.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import shutil

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / 'apps/desktop'
VARIANTS = [('darwin', 'arm64', 'macos', 'pkg'),
            ('linux', 'arm64', 'linux', 'AppImage'), ('linux', 'x64', 'linux', 'AppImage'),
            ('win32', 'arm64', 'windows', 'exe'), ('win32', 'x64', 'windows', 'exe')]


def validate_inputs(tag, body, version, mode='new-release'):
    if mode not in ('new-release', 'replacement-build-only'):
        raise ValueError('Unknown release mode')
    if mode == 'replacement-build-only' and tag != 'alpha-v0.1.0':
        raise ValueError('Replacement build-only is bounded to alpha-v0.1.0')
    if mode == 'new-release':
        if not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-alpha', tag):
            raise ValueError('Tag must be vMAJOR.MINOR.PATCH-alpha (no leading zeroes)')
        if tag != 'v' + version:
            raise ValueError('Tag must match the declared desktop release version with verified Git provenance')
    if not body.strip() or '\0' in body or len(body.encode('utf-8')) > 60000:
        raise ValueError('Changelog Markdown must be nonblank, NUL-free and <= 60000 UTF-8 bytes')
    return body


def verified_build_identity():
    """Use the desktop's authoritative Python owner; never infer from package.json.

    Manual releases require fresh, complete Git evidence, not fallback/stamp reuse.
    This command is local-only and performs no fetches or metadata writes.
    """
    identity = json.loads(subprocess.check_output([
        sys.executable, str(ROOT / 'eidolon_cli/eidolon_version.py'),
        '--repo-root', str(ROOT), '--require-verified'], cwd=ROOT, text=True))
    sha = os.environ.get('GITHUB_SHA', '')
    if (not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40
            or identity.get('commit') != sha or identity.get('versionSource') != 'git-derived'):
        raise ValueError('Release requires fresh Git-derived identity at exact GITHUB_SHA')
    if os.environ.get('GITHUB_ACTIONS') == 'true' and identity.get('dirty') is not False:
        raise ValueError('Release requires a clean source tree at build time')
    return identity


def verify_packaged_identity(stamp, identity):
    # Ignore incidental build timestamps/source labels, never version proof fields.
    fields = ('schemaVersion', 'commit', 'shortCommit', 'version', 'channel',
              'repository', 'updateBranch', 'baseTag', 'baseCommit', 'distance', 'versionSource')
    if not isinstance(stamp, dict) or any(stamp.get(key) != identity.get(key) for key in fields):
        raise ValueError('Packaged install stamp must match verified Git-derived identity')
    if os.environ.get('GITHUB_ACTIONS') == 'true' and stamp.get('dirty') is not False:
        raise ValueError('Release requires a clean source tree at build time')


def asset_names(tag):
    return [f'{label}_{arch}_{tag}.{ext}' for _, arch, label, ext in VARIANTS]


def file_record(path):
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'name': path.name, 'size': path.stat().st_size, 'digest': 'sha256:' + digest}


def local_assets(root, tag):
    paths = list(root.iterdir())
    if sorted(p.name for p in paths) != sorted(asset_names(tag)):
        raise ValueError('Release directory must contain exactly the five expected assets')
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size == 0 for p in paths):
        raise ValueError('Assets must be nonempty regular files, never symlinks')
    return [file_record(p) for p in sorted(paths)]


def verify_remote_assets(local, remote):
    wanted = sorted(local, key=lambda a: a['name'])
    actual = sorted(({k: a.get(k) for k in ('name', 'size', 'digest')} for a in remote), key=lambda a: a['name'])
    if actual != wanted or any(a.get('state') != 'uploaded' for a in remote):
        raise ValueError('GitHub assets must match exact names, sizes and SHA-256 digests, all uploaded')


class API:
    def __init__(self):
        self.base = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GH_TOKEN']

    def request(self, method, path, data=None, file=None, missing=False):
        url = path if path.startswith('https://') else self.base + path
        # Never send the token to arbitrary hosts or follow authenticated redirects.
        if urllib.parse.urlparse(url).hostname not in ('api.github.com', 'uploads.github.com'):
            raise ValueError('Unexpected GitHub API host')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        headers = {'Authorization': 'Bearer ' + self.token,
                   'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'Eidolon-manual-release'}
        payload = None
        handle = None
        if file:
            handle = file.open('rb')
            payload = handle
            headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(file.stat().st_size)})
        elif data is not None:
            payload = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.build_opener(NoRedirect).open(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f'GitHub {method} {path}: HTTP {error.code}; no automatic cleanup/retry') from error
        finally:
            if handle is not None:
                handle.close()


def absent(api, tag):
    if api.request('GET', '/git/ref/tags/' + tag, missing=True) is not None:
        raise ValueError('Tag already exists; never reuse, move or delete it automatically')
    if api.request('GET', '/releases/tags/' + tag, missing=True) is not None:
        raise ValueError('Release/draft already exists; inspect manually before retrying')


def verify_ref(api, tag, sha):
    ref = api.request('GET', '/git/ref/tags/' + tag)
    if ref['object']['sha'] != sha:
        raise ValueError('Tag does not point to the immutable build commit')


def verify_release(release, tag, sha, body, draft):
    expected = dict(tag_name=tag, target_commitish=sha, name='Eidolon ' + tag,
                    body=body, draft=draft, prerelease=True)
    if any(release.get(k) != v for k, v in expected.items()):
        raise ValueError('Release metadata readback differs from requested values')


def publish(api, root, tag, sha, body):
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40:
        raise ValueError('Immutable 40-character source SHA required')
    local = local_assets(root, tag)
    absent(api, tag)
    # Atomic create, never an update: catches collisions after preflight.
    api.request('POST', '/git/refs', {'ref': 'refs/tags/' + tag, 'sha': sha})
    verify_ref(api, tag, sha)
    release = api.request('POST', '/releases', dict(tag_name=tag, target_commitish=sha,
        name='Eidolon ' + tag, body=body, draft=True, prerelease=True,
        generate_release_notes=False, make_latest='false'))
    endpoint = '/releases/' + str(release['id'])
    verify_release(api.request('GET', endpoint), tag, sha, body, True)
    upload = release['upload_url'].split('{')[0]
    for asset in local:
        api.request('POST', upload + '?' + urllib.parse.urlencode({'name': asset['name']}), file=root / asset['name'])
    verify_remote_assets(local, api.request('GET', endpoint + '/assets?per_page=100'))
    verify_ref(api, tag, sha)
    verify_release(api.request('GET', endpoint), tag, sha, body, True)
    api.request('PATCH', endpoint, {'draft': False, 'prerelease': True, 'make_latest': 'false'})
    verify_release(api.request('GET', endpoint), tag, sha, body, False)
    verify_remote_assets(local, api.request('GET', endpoint + '/assets?per_page=100'))
    verify_ref(api, tag, sha)
    print('Published and read back ' + tag + ' with exactly five verified assets')


def replacement_snapshot(api, tag):
    if tag != 'alpha-v0.1.0':
        raise ValueError('Replacement build-only is bounded to alpha-v0.1.0')
    ref = api.request('GET', '/git/ref/tags/' + tag)
    release = api.request('GET', '/releases/tags/' + tag)
    if (not ref or ref.get('object', {}).get('type') not in ('commit', 'tag')
            or not re.fullmatch(r'[0-9a-f]{40}', ref['object'].get('sha', ''))
            or not release or release.get('tag_name') != tag
            or release.get('draft') is not False or release.get('prerelease') is not True):
        raise ValueError('Replacement requires an existing tag and published prerelease')
    assets = api.request('GET', f"/releases/{release['id']}/assets?per_page=100")
    if (sorted(a['name'] for a in assets) != sorted(asset_names(tag))
            or any(a.get('state') != 'uploaded' or a.get('size', 0) <= 0 for a in assets)):
        raise ValueError('Existing release must contain exactly five uploaded assets')
    return dict(ref=ref, release=release, assets=assets)


def write_package_receipt(asset, directory, tag, sha, version, platform, arch):
    # Called only after extraction, architecture, stamp and native PTY checks pass.
    directory.mkdir(parents=True, exist_ok=True)
    receipt = dict(asset=file_record(asset), tag=tag, source_sha=sha,
                   desktop_version=version, platform=platform, arch=arch)
    (directory / f'{platform}-{arch}.json').write_text(json.dumps(receipt), encoding='utf-8')


def prepare_replacement(api, assets, proofs, output, tag, sha, body, version):
    validate_inputs(tag, body, version, 'replacement-build-only')
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40:
        raise ValueError('Immutable 40-character source SHA required')
    local = local_assets(assets, tag)
    expected = {f'{p}-{a}.json' for p, a, _, _ in VARIANTS}
    if {p.name for p in proofs.iterdir()} != expected:
        raise ValueError('Complete native verification receipts required')
    by_name = {a['name']: a for a in local}
    for platform, arch, label, ext in VARIANTS:
        path = proofs / f'{platform}-{arch}.json'
        if path.is_symlink() or not path.is_file():
            raise ValueError('Receipt must be a regular file')
        receipt = json.loads(path.read_text(encoding='utf-8'))
        wanted = dict(asset=by_name[f'{label}_{arch}_{tag}.{ext}'], tag=tag,
                      source_sha=sha, desktop_version=version, platform=platform, arch=arch)
        if receipt != wanted:
            raise ValueError('Native receipt does not match source/version/package bytes')
    existing = replacement_snapshot(api, tag)
    notes = (body + '\n\n## Replacement build provenance\n'
             f'- Retained release/tag: `{tag}` (tag unchanged).\n'
             f'- Build source commit: `{sha}`.\n'
             f'- Internal desktop version: `{version}`.\n'
             '- Asset filenames retain the original release label, not the internal version.\n')
    manifest = dict(mode='replacement-build-only', tag=tag, source_sha=sha,
                    desktop_version=version, assets=local, existing=existing,
                    publication_performed=False)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (output / 'notes.md').write_text(notes, encoding='utf-8', newline='')
    (output / 'REVIEW.txt').write_text(
        'BUILD ONLY: no release, tag, notes or old assets have been modified.\n'
        'Before any separately authorized replacement: download all five new artifacts;\n'
        'verify exact names, sizes and SHA-256 against manifest.json and inspect native job logs.\n'
        'Download all five OLD assets and save old release notes/ref metadata outside GitHub;\n'
        'verify backup byte counts and hashes, retain them through final public verification.\n'
        'Re-read release ID, tag object, old asset IDs/digests and notes against this snapshot;\n'
        'stop on drift. Do not move the tag. GitHub asset replacement is not atomic.\n'
        'Use a separately reviewed replacement/rollback procedure; retain verified old bytes\n'
        'for restoration if any upload fails. Never delete old assets before all five new\n'
        'packages AND all five old backups are locally complete and verified.\n'
        'Apply notes.md with the actual build SHA, then verify public downloaded bytes and\n'
        'exact five-asset inventory. This artifact is NOT a backup or permission to publish.\n',
        encoding='utf-8')
    print('Replacement handoff verified; no GitHub writes performed')


def binary_target(data):
    if data[:4] == b'\x7fELF' and data[4:6] == b'\x02\x01':
        return 'linux', {62: 'x64', 183: 'arm64'}[struct.unpack_from('<H', data, 18)[0]]
    if data[:4] == b'\xcf\xfa\xed\xfe':
        return 'darwin', {0x1000007: 'x64', 0x100000c: 'arm64'}[struct.unpack_from('<I', data, 4)[0]]
    if data[:2] == b'MZ':
        offset = struct.unpack_from('<I', data, 60)[0]
        if data[offset:offset + 4] == b'PE\0\0':
            return 'win32', {0x8664: 'x64', 0xaa64: 'arm64'}[struct.unpack_from('<H', data, offset + 4)[0]]
    raise ValueError('Unknown native binary header')


def run(args, cwd=ROOT, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def validate_pkg_info(text, tag):
    info = ET.fromstring(text)
    bundle = info.find('bundle')
    if (info.get('identifier') != 'com.aethermesh-ai.eidolon'
            or info.get('version') != tag.removeprefix('alpha-v').removeprefix('v')
            or info.get('install-location') != '/Applications'
            or bundle is None or bundle.get('path') not in ('Eidolon.app', './Eidolon.app')
            or info.get('relocatable') != 'false' or list(info.findall('relocate/bundle'))):
        raise ValueError('PKG must install non-relocatable Eidolon.app into /Applications')


def packaged_pty_smoke(executable, pty_root, temp):
    # Windows GUI-subsystem Electron may not expose console output to the runner.
    # Persist the last stage synchronously, including before native calls that
    # could block the JS watchdog; Python's unchanged 30s bound covers those.
    evidence = temp / 'pty-smoke.json'
    evidence.write_text(json.dumps({'stage': 'launching'}), encoding='utf-8')
    smoke_env = dict(os.environ, ELECTRON_RUN_AS_NODE='1',
                     EIDOLON_TEST_PTY=str(pty_root), EIDOLON_TEST_RESULT=str(evidence))
    smoke = """
const fs = require('fs');
const state = {platform: process.platform, arch: process.arch,
  electron: process.versions.electron, abi: process.versions.modules, output: ''};
const record = stage => {
  state.stage = stage;
  fs.writeFileSync(process.env.EIDOLON_TEST_RESULT, JSON.stringify(state));
};
record('started');
let child;
const timer = setTimeout(() => {
  state.waitingAt = state.stage;
  record('timeout');
  try { if (child) child.kill(); } finally { process.exit(2); }
}, 15000);
process.on('uncaughtException', error => {
  state.waitingAt = state.stage;
  state.error = String(error.stack || error).slice(0, 4096);
  record('error');
  process.exit(1);
});
record('requiring');
const pty = require(process.env.EIDOLON_TEST_PTY);
record('spawning');
const win = process.platform === 'win32';
child = pty.spawn(win ? 'cmd.exe' : '/bin/sh',
  win ? ['/d', '/s', '/c', 'echo EIDOLON_RELEASE_PTY'] : ['-c', 'printf EIDOLON_RELEASE_PTY'],
  {env: process.env, cols: 80, rows: 24});
record('spawned');
child.onData(data => {
  state.output = (state.output + data).slice(0, 4096);
  record('data');
});
child.onExit(({exitCode}) => {
  clearTimeout(timer);
  state.exitCode = exitCode;
  const passed = exitCode === 0 && state.output.includes('EIDOLON_RELEASE_PTY');
  record(passed ? 'passed' : 'failed');
  // This disposable ABI/spawn probe is complete; do not wait on PTY handles.
  // Persist the result before exiting: console.log can be lost on Windows.
  process.exit(passed ? 0 : 3);
});
"""
    try:
        subprocess.run([str(executable), '-e', smoke], cwd=temp, env=smoke_env, check=True, timeout=30)
    finally:
        print('Packaged PTY smoke evidence: ' + evidence.read_text(encoding='utf-8'), flush=True)
    result = json.loads(evidence.read_text(encoding='utf-8'))
    if (result.get('stage') != 'passed' or result.get('exitCode') != 0
            or 'EIDOLON_RELEASE_PTY' not in result.get('output', '')):
        raise ValueError('Packaged Electron exited without verified PTY success')
    print('Packaged Electron/node-pty ABI and spawn smoke passed', flush=True)


def package(tag, platform, arch, temp):
    identity = verified_build_identity()
    version = identity['version']
    variant = next(v for v in VARIANTS if v[:2] == (platform, arch))
    actual = subprocess.check_output(['node', '-p', 'process.platform+"/"+process.arch'], text=True).strip()
    if actual != platform + '/' + arch:
        raise ValueError('Must build on native host/Node architecture: ' + actual)
    config = json.loads((DESKTOP / 'package.json').read_text())['build']
    config['artifactName'] = f'{variant[2]}_{arch}_{tag}.${{ext}}'
    output = temp / 'package'
    output.mkdir(parents=True)
    config['directories'] = {'output': str(output)}
    config['publish'] = None
    if platform == 'darwin':
        config['mac']['identity'] = '-'  # ad-hoc only, no invented Developer ID
        config['mac']['notarize'] = False
        config.pop('afterSign', None)
        config['pkg'] = {'installLocation': '/Applications', 'isRelocatable': False,
                         'isVersionChecked': True, 'overwriteAction': 'upgrade'}
    if platform == 'win32':
        config.setdefault('nsis', {}).update({'useZip': False, 'differentialPackage': False,
                                             'runAfterFinish': False})
    cfg = temp / 'electron-builder.json'
    cfg.write_text(json.dumps(config))
    target = {'darwin': '--mac', 'linux': '--linux', 'win32': '--win'}[platform]
    # Invoke npm lifecycle so the existing mac binary patch is applied too.
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    format_target = {'darwin': 'pkg', 'linux': 'AppImage', 'win32': 'nsis'}[platform]
    run([npm, 'run', 'builder', '--', '--config', str(cfg), target, format_target, '--' + arch, '--publish', 'never'], cwd=DESKTOP)
    name = f'{variant[2]}_{arch}_{tag}.{variant[3]}'
    artifact = output / name
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0:
        raise ValueError('Expected package missing: ' + name)
    unpacked = temp / 'verify-installer'
    if platform == 'darwin':
        run(['pkgutil', '--expand-full', str(artifact), str(unpacked)])
        infos = list(unpacked.rglob('PackageInfo'))
        if len(infos) != 1:
            raise ValueError('Expected exactly one PKG component')
        validate_pkg_info(infos[0].read_text(), 'v' + version)
    elif platform == 'linux':
        data = artifact.read_bytes()
        if data[8:11] != b'AI\x02' or binary_target(data) != (platform, arch):
            raise ValueError('Expected native Type 2 AppImage')
        artifact.chmod(0o755)
        unpacked.mkdir()
        run([str(artifact), '--appimage-extract'], cwd=unpacked)
        appdir = unpacked / 'squashfs-root'
        if not (appdir / 'AppRun').is_file() or not list(appdir.glob('*.desktop')):
            raise ValueError('AppImage launcher/desktop integration missing')
    else:
        # NSIS bootstrap is x86 even for native ARM64 payloads. Inspect the
        # embedded archive, not the bootstrap PE architecture or builder folder.
        seven = shutil.which('7z')
        if not seven:
            raise ValueError('NSIS inspection requires full 7-Zip (7z), not 7za')
        outer = temp / 'nsis-container'
        run([seven, 't', str(artifact)])
        run([seven, 'x', str(artifact), '-o' + str(outer), '-y'])
        payloads = list(outer.rglob('app-*.7z'))
        if len(payloads) != 1:
            raise ValueError('Expected one embedded NSIS application archive')
        run([seven, 't', str(payloads[0])])
        run([seven, 'x', str(payloads[0]), '-o' + str(unpacked), '-y'])
    # Validate packaged (not source) Electron and every unpacked native module.
    executables = list(unpacked.rglob('Contents/MacOS/Eidolon')) if platform == 'darwin' else list(unpacked.rglob('Eidolon.exe' if platform == 'win32' else 'Eidolon'))
    executables = [p for p in executables if p.is_file()]
    if len(executables) != 1:
        raise ValueError('Expected one packaged Electron executable')
    native = list(unpacked.rglob('*.node'))
    if not native or not any('node-pty' in p.parts for p in native):
        raise ValueError('Packaged node-pty native payload missing')
    for binary in [*executables, *native]:
        if binary_target(binary.read_bytes()) != (platform, arch):
            raise ValueError('Wrong native architecture: ' + str(binary))
    if platform == 'darwin':
        run(['codesign', '--verify', '--deep', '--strict', str(executables[0].parents[2])])
    stamp_paths = [p for p in unpacked.rglob('install-stamp.json') if p.parent.name.lower() == 'resources']
    if len(stamp_paths) != 1:
        raise ValueError('Expected one packaged install stamp')
    stamp = json.loads(stamp_paths[0].read_text())
    verify_packaged_identity(stamp, identity)
    # Electron Node mode loads the packaged addon with Electron's actual ABI,
    # without starting the app/UI or contacting model/backend services.
    pty_roots = list(unpacked.rglob('app.asar.unpacked/dist/node_modules/node-pty'))
    if len(pty_roots) != 1:
        raise ValueError('Expected exactly one packaged node-pty module')
    packaged_pty_smoke(executables[0], pty_roots[0], temp)
    assets = temp / 'assets'; assets.mkdir()
    artifact.rename(assets / name)
    write_package_receipt(assets / name, temp / 'proofs', tag, os.environ['GITHUB_SHA'],
                          version, platform, arch)
    print(json.dumps(file_record(assets / name)))


def main():
    tag = os.environ['RELEASE_TAG']; body = os.environ['RELEASE_CHANGELOG']
    mode = os.environ.get('RELEASE_MODE', 'new-release')
    command = sys.argv[1]
    if mode == 'replacement-build-only' and command == 'publish':
        raise ValueError('Replacement is build-only; publication requires separate review')
    version = verified_build_identity()['version']
    validate_inputs(tag, body, version, mode)
    if command == 'preflight':
        if mode == 'replacement-build-only':
            replacement_snapshot(API(), tag)
        else:
            absent(API(), tag)
    elif command == 'prepare-replacement':
        if mode != 'replacement-build-only':
            raise ValueError('Explicit replacement-build-only mode required')
        prepare_replacement(API(), Path(os.environ['RELEASE_ASSETS']),
                            Path(os.environ['RELEASE_PROOFS']), Path(os.environ['RELEASE_HANDOFF']),
                            tag, os.environ['GITHUB_SHA'], body, version)
    elif command == 'package':
        package(tag, os.environ['TARGET_PLATFORM'], os.environ['TARGET_ARCH'], Path(os.environ['RUNNER_TEMP']) / 'eidolon-release')
    elif command == 'publish':
        # Only materialized here, outside checkout; exact input bytes, no shell parsing.
        with tempfile.TemporaryDirectory(prefix='eidolon-notes-', dir=os.environ['RUNNER_TEMP']) as td:
            notes = Path(td) / 'notes.md'
            notes.write_text(body, encoding='utf-8', newline='')
            with notes.open(encoding='utf-8', newline='') as source:
                publish(API(), Path(os.environ['RELEASE_ASSETS']), tag, os.environ['GITHUB_SHA'], source.read())
    else:
        raise ValueError('Unknown command')


if __name__ == '__main__':
    main()

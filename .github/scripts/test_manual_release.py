"""Offline release-policy tests; no GitHub credentials or network required."""
import importlib.util
import os
import tempfile
import unittest
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release', Path(__file__).with_name('manual_release.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class SmokeTests(unittest.TestCase):
    def test_outer_timeout_and_missing_success_fail_closed_with_evidence(self):
        import contextlib
        import io
        import subprocess
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for failure in [None, subprocess.TimeoutExpired('Electron', 30)]:
                with self.subTest(failure=failure), patch.object(r.subprocess, 'run', side_effect=failure) as run:
                    log = io.StringIO()
                    with contextlib.redirect_stdout(log), self.assertRaises(
                            ValueError if failure is None else subprocess.TimeoutExpired):
                        r.packaged_pty_smoke(root / 'Eidolon.exe', root / 'node-pty', root)
                    self.assertIn('"stage": "launching"', log.getvalue())
                    self.assertEqual(run.call_args.kwargs['timeout'], 30)
                    self.assertTrue(run.call_args.kwargs['check'])
                    self.assertEqual(run.call_args.kwargs['env']['ELECTRON_RUN_AS_NODE'], '1')
                    self.assertEqual(run.call_args.args[0][0], str(root / 'Eidolon.exe'))

    def test_completion_and_failures_with_real_js_protocol_fixture(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        assert node is not None, 'Offline smoke protocol tests require Node.js'
        # Deliberately retain an event-loop handle after PTY exit. This is a
        # protocol fixture, NOT evidence that native Windows node-pty works.
        cases = [("throw Error('fixture native load failure')", 'error', 1),
                 ('exports.spawn = () => ({onData() {}, onExit() {}, kill() {}})', 'timeout', 2)]
        for output, code in [('EIDOLON_RELEASE_PTY', 0), ('wrong', 0), ('EIDOLON_RELEASE_PTY', 1)]:
            source = ('exports.spawn = () => { setInterval(() => {}, 1000); return { '
                      'onData(fn) { setTimeout(() => fn(' + json.dumps(output) + '), 5); }, '
                      'onExit(fn) { setTimeout(() => fn({exitCode: ' + str(code) + '}), 20); }, '
                      'kill() {} }; };')
            passes = output == 'EIDOLON_RELEASE_PTY' and code == 0
            cases.append((source, 'passed' if passes else 'failed', 0 if passes else 3))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            addon = root / 'fake-pty.cjs'
            for source, stage, code in cases:
                with self.subTest(stage=stage, code=code):
                    addon.write_text(source)
                    if code:
                        with self.assertRaises(subprocess.CalledProcessError) as caught:
                            r.packaged_pty_smoke(Path(node), addon, root)
                        self.assertEqual(caught.exception.returncode, code)
                    else:
                        r.packaged_pty_smoke(Path(node), addon, root)
                    state = json.loads((root / 'pty-smoke.json').read_text())
                    self.assertEqual(state['stage'], stage)
                    if stage == 'passed':
                        self.assertEqual(state['exitCode'], 0)
                        self.assertIn('EIDOLON_RELEASE_PTY', state['output'])
                    elif stage == 'error':
                        self.assertIn('fixture native load failure', state['error'])
                    elif stage == 'timeout':
                        self.assertEqual(state['waitingAt'], 'spawned')


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        identity = patch.object(r, 'verified_build_identity', return_value={'version': json.loads((r.DESKTOP / 'package.json').read_text())['version']})
        identity.start()
        self.addCleanup(identity.stop)

    def test_explicit_build_only_mode_never_reaches_publisher(self):
        version = json.loads((r.DESKTOP / 'package.json').read_text())['version']
        env = {'RELEASE_TAG': 'alpha-v0.1.0', 'RELEASE_CHANGELOG': '# Notes',
               'RELEASE_MODE': 'replacement-build-only'}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(r.sys, 'argv', ['manual_release.py', 'publish']), patch.object(r, 'API') as api:
                with self.assertRaisesRegex(ValueError, 'build-only'):
                    r.main()
                api.assert_not_called()
        # Omitted mode still runs the original absence gate, never replacement lookup.
        with patch.dict(os.environ, {'RELEASE_TAG': 'v' + version,
                                    'RELEASE_CHANGELOG': '# Notes'}, clear=True):
            with patch.object(r.sys, 'argv', ['manual_release.py', 'preflight']), patch.object(r, 'API'):
                with patch.object(r, 'absent', side_effect=ValueError('already exists')) as absent:
                    with patch.object(r, 'replacement_snapshot') as replacement:
                        with self.assertRaisesRegex(ValueError, 'already exists'):
                            r.main()
                        absent.assert_called_once()
                        replacement.assert_not_called()
        with patch.dict(os.environ, env, clear=True), patch.object(r, 'API'):
            with patch.object(r.sys, 'argv', ['manual_release.py', 'preflight']):
                with patch.object(r, 'replacement_snapshot') as snapshot, patch.object(r, 'absent') as absent:
                    r.main()
                    snapshot.assert_called_once()
                    absent.assert_not_called()
        self.assertEqual(r.validate_inputs('alpha-v0.1.0', '# Notes', version, 'replacement-build-only'), '# Notes')
        for mode, tag in [('new-release', 'alpha-v0.1.0'), ('replace', 'alpha-v0.1.0'),
                          ('replacement-build-only', 'alpha-v0.2.0')]:
            with self.subTest(mode=mode, tag=tag), self.assertRaises(ValueError):
                r.validate_inputs(tag, '# Notes', '0.1.1', mode)


class ReplacementHandoffTests(unittest.TestCase):
    def test_complete_verified_matrix_required_before_read_only_handoff(self):
        tag, sha, version = 'alpha-v0.1.0', 'a' * 40, '0.1.1'
        class ExistingAPI:
            def __init__(self):
                self.calls = []
            def request(self, method, path, **kwargs):
                self.calls.append((method, path))
                if method != 'GET':
                    raise AssertionError('Replacement must never mutate GitHub')
                if path.startswith('/git/ref/'):
                    return {'object': {'sha': 'b' * 40, 'type': 'commit'}}
                if path.startswith('/releases/tags/'):
                    return dict(id=7, tag_name=tag, draft=False, prerelease=True, body='old notes')
                if path.startswith('/releases/7/assets'):
                    return [dict(id=i, name=n, size=12, state='uploaded', digest='sha256:' + 'c' * 64)
                            for i, n in enumerate(r.asset_names(tag), 1)]
                raise AssertionError(path)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); assets = base / 'assets'; assets.mkdir()
            proofs = base / 'proofs'; proofs.mkdir()
            output = base / 'handoff'
            for platform, arch, label, ext in r.VARIANTS:
                asset = assets / f'{label}_{arch}_{tag}.{ext}'
                asset.write_bytes(b'offline fixture, not an installer')
                r.write_package_receipt(asset, proofs, tag, sha, version, platform, arch)
            api = ExistingAPI()
            r.prepare_replacement(api, assets, proofs, output, tag, sha, '# New notes', version)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['source_sha'], sha)
            self.assertEqual(manifest['desktop_version'], version)
            self.assertEqual(manifest['existing']['ref']['object']['sha'], 'b' * 40)
            self.assertEqual(manifest['assets'], r.local_assets(assets, tag))
            notes = (output / 'notes.md').read_text()
            for value in [sha, version, tag, 'unchanged']:
                self.assertIn(value, notes)
            self.assertTrue(all(m == 'GET' for m, _ in api.calls))
            # Missing, corrupted and wrong-source packages must not produce a handoff.
            receipt = next(proofs.iterdir()); original = receipt.read_text()
            for mutation in ['missing', 'source', 'digest']:
                if mutation == 'missing':
                    receipt.unlink()
                else:
                    record = json.loads(original)
                    if mutation == 'source': record['source_sha'] = 'd' * 40
                    else: record['asset']['digest'] = 'sha256:' + 'e' * 64
                    receipt.write_text(json.dumps(record))
                target = base / mutation
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    r.prepare_replacement(api, assets, proofs, target, tag, sha, '# Notes', version)
                self.assertFalse(target.exists())
                receipt.write_text(original)
            (assets / r.asset_names(tag)[0]).unlink()
            with self.assertRaises(ValueError):
                r.prepare_replacement(api, assets, proofs, base / 'partial', tag, sha, '# Notes', version)
            self.assertFalse((base / 'partial').exists())


class PolicyTests(unittest.TestCase):
    def test_version_and_changelog_are_data(self):
        body = '# Changes\n$(touch /tmp/never)\n`whoami`\nEOF\n${{ secrets.TOKEN }}'
        self.assertEqual(r.validate_inputs('v0.2.0-alpha', body, '0.2.0-alpha'), body)
        for tag in ['v0.2.0', 'v00.2.0-alpha', 'v0.2.0-alpha\n', '../../oops', 'v0.3.0-alpha', 'alpha-v0.2.0']:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                r.validate_inputs(tag, body, '0.2.0-alpha')
        for body in ['', ' \n', 'a\0b', 'x' * 60001]:
            with self.assertRaises(ValueError):
                r.validate_inputs('v0.2.0-alpha', body, '0.2.0-alpha')

    def test_exact_five_assets(self):
        names = r.asset_names('alpha-v0.1.0')
        self.assertEqual(names, [
            'macos_arm64_alpha-v0.1.0.pkg',
            'linux_arm64_alpha-v0.1.0.AppImage', 'linux_x64_alpha-v0.1.0.AppImage',
            'windows_arm64_alpha-v0.1.0.exe', 'windows_x64_alpha-v0.1.0.exe'])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in names:
                (root / name).write_bytes(b'test archive')
            records = r.local_assets(root, 'alpha-v0.1.0')
            self.assertEqual(len(records), 5)
            (root / 'extra.zip').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                r.local_assets(root, 'alpha-v0.1.0')
            (root / 'extra.zip').unlink()
            (root / names[0]).unlink()
            with self.assertRaises(ValueError):
                r.local_assets(root, 'alpha-v0.1.0')

    def test_native_arch_headers(self):
        import struct
        for arch, machine in [('arm64', 183), ('x64', 62)]:
            data = bytearray(64); data[:5] = b'\x7fELF\x02'; data[5] = 1
            struct.pack_into('<H', data, 18, machine)
            self.assertEqual(r.binary_target(bytes(data)), ('linux', arch))
        for arch, cpu in [('arm64', 0x100000c), ('x64', 0x1000007)]:
            self.assertEqual(r.binary_target(struct.pack('<II', 0xfeedfacf, cpu) + bytes(60)), ('darwin', arch))
        for arch, machine in [('arm64', 0xaa64), ('x64', 0x8664)]:
            data = bytearray(256); data[:2] = b'MZ'; struct.pack_into('<I', data, 60, 128)
            data[128:132] = b'PE\0\0'; struct.pack_into('<H', data, 132, machine)
            self.assertEqual(r.binary_target(bytes(data)), ('win32', arch))
        with self.assertRaises(ValueError):
            r.binary_target(b'not a binary')

    def test_server_assets_fail_closed(self):
        local = [{'name': 'one.zip', 'size': 12, 'digest': 'sha256:abc'}]
        remote = [dict(local[0], state='uploaded')]
        r.verify_remote_assets(local, remote)
        for bad in [[], remote * 2, [dict(remote[0], size=13)], [dict(remote[0], digest=None)], [dict(remote[0], state='starter')]]:
            with self.assertRaises(ValueError):
                r.verify_remote_assets(local, bad)


class InstallerTests(unittest.TestCase):
    def test_pkg_install_metadata(self):
        good = '<pkg-info identifier="com.aethermesh-ai.eidolon" version="0.1.0" install-location="/Applications" relocatable="false"><bundle path="./Eidolon.app"/></pkg-info>'
        r.validate_pkg_info(good, 'alpha-v0.1.0')
        current = good.replace('0.1.0', '0.2.0-alpha')
        r.validate_pkg_info(current, 'v0.2.0-alpha')
        with self.assertRaises(ValueError):
            r.validate_pkg_info(current, 'v0.2.1-alpha')
        for bad in [good.replace('/Applications', '/tmp'), good.replace('0.1.0', '0.2.0'),
                    good.replace('Eidolon.app', 'Other.app'), good.replace('false', 'true')]:
            with self.assertRaises(ValueError):
                r.validate_pkg_info(bad, 'alpha-v0.1.0')


class PackagingCommandTests(unittest.TestCase):
    def setUp(self):
        identity = patch.object(r, 'verified_build_identity', return_value={'version': json.loads((r.DESKTOP / 'package.json').read_text())['version']})
        identity.start()
        self.addCleanup(identity.stop)

    def test_markdown_materialized_only_in_runner_temp_as_exact_data(self):
        body = '# Notes\r\n$(touch NEVER_EXECUTE)\n`echo no`\n雪 "quoted"\n'
        with tempfile.TemporaryDirectory() as td:
            def inspect_notes(api, assets, tag, sha, actual):
                self.assertEqual(actual, body)
                notes = list(Path(td).rglob('notes.md'))
                self.assertEqual(len(notes), 1)
                self.assertEqual(notes[0].read_bytes(), body.encode('utf-8'))
                self.assertFalse(notes[0].is_relative_to(r.ROOT))
            version = json.loads((r.DESKTOP / 'package.json').read_text())['version']
            env = {'RELEASE_TAG': 'v' + version, 'RELEASE_CHANGELOG': body,
                   'RUNNER_TEMP': td, 'RELEASE_ASSETS': td, 'GITHUB_SHA': 'a' * 40}
            with patch.dict(os.environ, env, clear=True), patch.object(r.sys, 'argv', ['manual_release.py', 'publish']):
                with patch.object(r, 'API'), patch.object(r, 'publish', side_effect=inspect_notes) as publish:
                    r.main()
                    publish.assert_called_once()
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_builder_can_never_publish(self):
        class StopAfterBuilder(Exception):
            pass
        with tempfile.TemporaryDirectory() as td:
            with patch.object(r.subprocess, 'check_output', return_value='darwin/arm64\n'):
                with patch.object(r, 'run', side_effect=StopAfterBuilder) as builder:
                    with self.assertRaises(StopAfterBuilder):
                        r.package('alpha-v0.1.0', 'darwin', 'arm64', Path(td))
                    args = builder.call_args.args[0]
                    self.assertIn('--publish', args)
                    self.assertEqual(args[args.index('--publish') + 1], 'never')

    def test_five_native_installer_commands(self):
        class StopAfterBuilder(Exception):
            pass
        for platform, arch, label, ext in r.VARIANTS:
            with self.subTest(platform=platform, arch=arch), tempfile.TemporaryDirectory() as td:
                with patch.object(r.subprocess, 'check_output', return_value=f'{platform}/{arch}\n'), patch.object(r, 'run', side_effect=StopAfterBuilder) as build:
                    with self.assertRaises(StopAfterBuilder):
                        r.package('alpha-v0.1.0', platform, arch, Path(td))
                args = build.call_args.args[0]
                self.assertIn({'darwin': 'pkg', 'linux': 'AppImage', 'win32': 'nsis'}[platform], args)
                self.assertIn('--' + arch, args)
                self.assertEqual(args[-2:], ['--publish', 'never'])
                cfg = json.loads((Path(td) / 'electron-builder.json').read_text())
                self.assertEqual(cfg['artifactName'], f'{label}_{arch}_alpha-v0.1.0.${{ext}}')
                if platform == 'darwin':
                    self.assertEqual(cfg['pkg']['installLocation'], '/Applications')
                    self.assertFalse(cfg['pkg']['isRelocatable'])
                    self.assertFalse(cfg['mac']['notarize'])
                if platform == 'win32':
                    self.assertFalse(cfg['nsis']['useZip'])
                    self.assertFalse(cfg['nsis']['runAfterFinish'])


class FakeAPI:
    def __init__(self, fail_upload=False, collision=False):
        self.calls = []; self.assets = []; self.release: Any = None; self.ref = None
        self.fail_upload = fail_upload; self.collision = collision
    def request(self, method, path, data=None, file=None, missing=False) -> Any:
        self.calls.append((method, path))
        if path.startswith('/git/ref/'):
            return {'object': {'sha': self.ref}} if self.ref else None
        if path.startswith('/releases/tags/'):
            return self.release
        if path == '/git/refs':
            if self.collision: raise RuntimeError('422 existing ref')
            self.ref = data['sha']; return {'object': {'sha': self.ref}}
        if path == '/releases':
            self.release = dict(data, id=7, upload_url='https://uploads.github.com/repos/test/repo/releases/7/assets{?name,label}')
            return self.release.copy()
        if path.startswith('https://uploads.github.com/'):
            if self.fail_upload: raise RuntimeError('upload failed')
            import urllib.parse
            self.assets.append(dict(r.file_record(file), state='uploaded'))
            return self.assets[-1]
        if path.startswith('/releases/7/assets'):
            return self.assets
        if path == '/releases/7':
            if method == 'PATCH': self.release.update(data)
            return self.release.copy()
        raise AssertionError(path)


class PublicationTests(unittest.TestCase):
    def run_release(self, api):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in r.asset_names('alpha-v0.1.0'):
                (root / name).write_bytes(b'archive')
            return r.publish(api, root, 'alpha-v0.1.0', 'a' * 40, '# Notes')

    def test_publish_only_after_five_verified_uploads(self):
        api = FakeAPI(); self.run_release(api)
        self.assertFalse(api.release['draft'])
        self.assertTrue(api.release['prerelease'])
        self.assertEqual(api.release['name'], 'Eidolon alpha-v0.1.0')
        patch = api.calls.index(('PATCH', '/releases/7'))
        self.assertEqual(sum(m == 'POST' and p.startswith('https://uploads.') for m, p in api.calls[:patch]), 5)
        self.assertIn(('GET', '/releases/7/assets?per_page=100'), api.calls[:patch])
        self.assertEqual(api.calls[-3:], [('GET', '/releases/7'), ('GET', '/releases/7/assets?per_page=100'), ('GET', '/git/ref/tags/alpha-v0.1.0')])

    def test_upload_failure_leaves_draft(self):
        api = FakeAPI(fail_upload=True)
        with self.assertRaises(RuntimeError): self.run_release(api)
        self.assertTrue(api.release['draft'])
        self.assertNotIn(('PATCH', '/releases/7'), api.calls)

    def test_atomic_tag_collision_never_creates_release(self):
        api = FakeAPI(collision=True)
        with self.assertRaises(RuntimeError): self.run_release(api)
        self.assertNotIn(('POST', '/releases'), api.calls)

    def test_existing_tag_never_writes(self):
        api = FakeAPI(); api.ref = 'a' * 40
        with self.assertRaises(ValueError): self.run_release(api)
        self.assertTrue(all(method == 'GET' for method, _ in api.calls))

    def test_existing_draft_never_writes(self):
        api = FakeAPI(); api.release = {'draft': True}
        with self.assertRaises(ValueError): self.run_release(api)
        self.assertTrue(all(method == 'GET' for method, _ in api.calls))

    def test_corrupt_server_assets_never_publish(self):
        class CorruptAPI(FakeAPI):
            def request(self, method, path, data=None, file=None, missing=False):
                result = super().request(method, path, data, file, missing)
                if path.startswith('/releases/7/assets'):
                    return [dict(a, digest=None) for a in result]
                return result
        api = CorruptAPI()
        with self.assertRaises(ValueError): self.run_release(api)
        self.assertTrue(api.release['draft'])
        self.assertNotIn(('PATCH', '/releases/7'), api.calls)

    def test_ref_moved_after_uploads_never_publishes(self):
        class MovedRefAPI(FakeAPI):
            def request(self, method, path, data=None, file=None, missing=False):
                result = super().request(method, path, data, file, missing)
                if path.startswith('/git/ref/') and self.assets:
                    return {'object': {'sha': 'b' * 40}}
                return result
        api = MovedRefAPI()
        with self.assertRaises(ValueError): self.run_release(api)
        self.assertTrue(api.release['draft'])
        self.assertNotIn(('PATCH', '/releases/7'), api.calls)

    def test_bad_metadata_never_uploads(self):
        class WrongMetadataAPI(FakeAPI):
            def request(self, method, path, data=None, file=None, missing=False):
                result = super().request(method, path, data, file, missing)
                if method == 'GET' and path == '/releases/7':
                    result['prerelease'] = False
                return result
        api = WrongMetadataAPI()
        with self.assertRaises(ValueError): self.run_release(api)
        self.assertFalse(api.assets)
        self.assertNotIn(('PATCH', '/releases/7'), api.calls)

    def test_bad_source_sha_never_writes(self):
        for sha in ['main', 'a' * 39, '0' * 40]:
            api = FakeAPI()
            with self.assertRaises(ValueError):
                r.publish(api, Path('/unused'), 'alpha-v0.1.0', sha, '# Notes')
            self.assertFalse(api.calls)


if __name__ == '__main__':
    unittest.main()

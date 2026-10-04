"""Offline history and generated-version release contracts."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release', Path(__file__).with_name('manual_release.py'))
assert spec is not None and spec.loader is not None
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class IdentityTests(unittest.TestCase):
    def test_every_manual_checkout_has_full_pinned_history(self):
        workflow = (r.ROOT / '.github/workflows/manual-release.yml').read_text()
        blocks = workflow.split('- uses: actions/checkout@')[1:]
        self.assertTrue(blocks)
        for block in blocks:
            options = block.split('\n      - ', 1)[0]
            with self.subTest(checkout=options):
                self.assertIn('ref: ${{ github.sha }}', options)
                self.assertIn('fetch-depth: 0', options)
                self.assertIn('fetch-tags: true', options)
                self.assertIn('persist-credentials: false', options)

    def test_real_history_drives_package_receipts_and_handoff(self):
        writer = (r.DESKTOP / 'scripts/write-build-stamp.mjs').as_uri()
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
                'HOME': td, 'HERMES_HOME': str(Path(td) / 'hermes'),
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}):
            root = Path(td) / 'repo'; root.mkdir()
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()
            subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout',
                            str(r.ROOT), str(root)], check=True)
            git('update-ref', 'refs/heads/main', git('rev-parse', 'HEAD'))
            git('symbolic-ref', 'HEAD', 'refs/heads/main')
            git('read-tree', '--empty')
            git('config', 'user.email', 'offline@example.invalid')
            git('config', 'user.name', 'Offline fixture')
            desktop = root / 'apps/desktop'; desktop.mkdir(parents=True)
            shutil.copy(r.DESKTOP / 'package.json', desktop / 'package.json')
            owner = root / 'eidolon_cli'; owner.mkdir()
            shutil.copy(r.ROOT / 'eidolon_cli/eidolon_version.py', owner / 'eidolon_version.py')
            git('add', '.'); git('commit', '-m', 'small release fixture')
            git('tag', 'alpha-v2.3.4')
            git('commit', '--allow-empty', '-m', 'next')
            sha = git('rev-parse', 'HEAD'); git('checkout', '--detach', sha)
            env = dict(os.environ, GITHUB_SHA=sha, GITHUB_ACTIONS='true')
            with patch.object(r, 'ROOT', root), patch.object(r, 'DESKTOP', desktop), patch.dict(os.environ, env, clear=True):
                identity = r.verified_build_identity()
                self.assertEqual(identity['version'], json.loads((desktop / 'package.json').read_text())['version'])
                stamp_dir = desktop / 'build'; stamp_dir.mkdir()
                (root / '.gitignore').write_text('apps/desktop/build/\n')
                git('add', '.gitignore'); git('commit', '-m', 'ignore build output')
                # Keep detached commit on a known main first-parent chain.
                sha = git('rev-parse', 'HEAD'); git('branch', '-f', 'main', sha)
                os.environ['GITHUB_SHA'] = sha
                identity = r.verified_build_identity()
                code = f'import {{ writeBuildStamp }} from {json.dumps(writer)}; writeBuildStamp({{repoRoot: process.argv[1]}});'
                subprocess.run(['node', '--input-type=module', '-e', code, str(root)],
                               env=dict(os.environ, HERMES_PYTHON=r.sys.executable), check=True)
                stamp = json.loads((stamp_dir / 'install-stamp.json').read_text())
                r.verify_packaged_identity(stamp, identity)
                for key, value in [('version', '0.1.1'), ('commit', 'a' * 40), ('distance', 999), ('versionSource', 'fallback')]:
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        r.verify_packaged_identity(dict(identity, **{key: value}), identity)
                version = identity['version']
                xml = f'<pkg-info identifier="com.aethermesh-ai.eidolon" version="{version}" install-location="/Applications" relocatable="false"><bundle path="./Eidolon.app"/></pkg-info>'
                r.validate_pkg_info(xml, 'v' + version)
                tag = 'alpha-v0.1.0'; assets = Path(td) / 'assets'; assets.mkdir()
                proofs = Path(td) / 'proofs'
                for platform, arch, label, ext in r.VARIANTS:
                    asset = assets / f'{label}_{arch}_{tag}.{ext}'
                    asset.write_bytes(b'offline fixture, not an installer')
                    r.write_package_receipt(asset, proofs, tag, sha, version, platform, arch)
                class ReadOnlyAPI:
                    def request(self, method, path, **kwargs):
                        if method != 'GET': raise AssertionError('No publication')
                        if path.startswith('/git/'): return {'object': {'sha': 'b' * 40, 'type': 'commit'}}
                        if path.startswith('/releases/tags/'): return dict(id=1, tag_name=tag, draft=False, prerelease=True)
                        return [dict(name=n, size=1, state='uploaded') for n in r.asset_names(tag)]
                output = Path(td) / 'handoff'
                with patch.dict(os.environ, RELEASE_MODE='replacement-build-only', RELEASE_TAG=tag,
                                RELEASE_CHANGELOG='# Notes', RELEASE_ASSETS=str(assets),
                                RELEASE_PROOFS=str(proofs), RELEASE_HANDOFF=str(output)), patch.object(r, 'API', ReadOnlyAPI), patch.object(r.sys, 'argv', ['release', 'prepare-replacement']):
                    r.main()
                self.assertEqual(json.loads((output / 'manifest.json').read_text())['desktop_version'], version)
                proof = proofs / 'darwin-arm64.json'; receipt = json.loads(proof.read_text())
                receipt['desktop_version'] = '0.1.1'; proof.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, 'receipt'):
                    r.prepare_replacement(ReadOnlyAPI(), assets, proofs, Path(td) / 'bad', tag, sha, '# Notes', version)
                shallow = Path(td) / 'shallow'
                subprocess.run(['git', 'clone', '--quiet', '--depth=1', root.as_uri(), str(shallow)], check=True)
                with patch.object(r, 'ROOT', shallow), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    r.verified_build_identity()
                os.environ['GITHUB_SHA'] = 'a' * 40
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    r.verified_build_identity()
                os.environ['GITHUB_SHA'] = sha
                git('tag', '-d', 'alpha-v2.3.4')
                self.assertEqual(r.verified_build_identity()['version'], version)


if __name__ == '__main__':
    unittest.main()

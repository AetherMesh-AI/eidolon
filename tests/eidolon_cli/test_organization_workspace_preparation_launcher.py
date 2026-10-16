"""CI launcher preflight only. These tests never install dependencies."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'scripts/organization/record_prepared_npm_install.mjs'


def node():
    value = shutil.which('node')
    if value is None:
        pytest.skip('Node is required for preparation preflight tests')
    return value


def test_registry_preflight_and_metadata_canonicalization():
    code = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {validateRegistryLock, canonical} = await import(process.argv[1]);
const actual = validateRegistryLock(fs.readFileSync(process.argv[2]));
assert.equal(actual.lockfileVersion, 3);
assert.equal(canonical({nodeVersion:'26.0.0',arch:'x64',nodeAbi:'1'}),
  '{"arch":"x64","nodeAbi":"1","nodeVersion":"26.0.0"}');
const packageRecord = {resolved:'https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz',
  integrity:'sha512-'+Buffer.alloc(64).toString('base64')};
assert.throws(() => validateRegistryLock(JSON.stringify({lockfileVersion:3,packages:1})));
assert.throws(() => validateRegistryLock(JSON.stringify({lockfileVersion:3,packages:{
  'node_modules/fixture': {link:true,resolved:'../../outside'}}})));
const lock = item => JSON.stringify({lockfileVersion:3,packages:{'node_modules/fixture':item}});
validateRegistryLock(lock(packageRecord));
for (const resolved of ['http://registry.npmjs.org/f.tgz','https://evil.invalid/f.tgz',
  'https://registry.npmjs.org.evil.invalid/f.tgz','https://user:pass@registry.npmjs.org/f.tgz',
  'https://registry.npmjs.org/f.tgz?q=secret','file:///fixture.tgz']) {
  assert.throws(() => validateRegistryLock(lock({...packageRecord,resolved})));
}
for (const integrity of [undefined,'','sha1-invalid','sha512-!bad','sha512-AA==']) {
  assert.throws(() => validateRegistryLock(lock({...packageRecord,integrity})));
}
'''
    result = subprocess.run([node(), '--input-type=module', '-e', code, LAUNCHER.as_uri(),
                             str(ROOT / 'package-lock.json')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_direct_launcher_rejects_noncontainer_directory_before_install(tmp_path):
    before = set(tmp_path.iterdir())
    result = subprocess.run([node(), str(LAUNCHER)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'Preparation requires the pinned Linux x64 /work container' in result.stderr
    assert set(tmp_path.iterdir()) == before

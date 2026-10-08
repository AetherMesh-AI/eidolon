"""Build a copyable ZIP from exact approved main without a release or installer."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

root = Path.cwd()
spec = importlib.util.spec_from_file_location('release_checks', root / '.github/scripts/manual_release.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
os.environ['GITHUB_SHA'] = os.environ['SOURCE_COMMIT']
identity = checks.verified_build_identity()
assert subprocess.check_output(['node', '-p', 'process.platform+"/"+process.arch'], text=True).strip() == 'darwin/arm64'
temp = Path(os.environ['RUNNER_TEMP']) / 'mac-commit-build'
temp.mkdir()
deliver = temp / 'deliver'
deliver.mkdir()
config = json.loads((root / 'apps/desktop/package.json').read_text())['build']
config['directories'] = {'output': str(temp / 'package')}
config['artifactName'] = 'Eidolon-mac-arm64-53ab5d6b.${ext}'
config['publish'] = None
config['mac']['identity'] = '-'
config['mac']['notarize'] = False
config.pop('afterSign', None)
config_file = temp / 'electron-builder.json'
config_file.write_text(json.dumps(config))
checks.run(['npm', 'run', 'builder', '--', '--config', str(config_file), '--mac', 'zip', '--arm64', '--publish', 'never'], cwd=root / 'apps/desktop')
archive = temp / 'package/Eidolon-mac-arm64-53ab5d6b.zip'
extracted = temp / 'extracted'
checks.run(['ditto', '-x', '-k', str(archive), str(extracted)])
app = extracted / 'Eidolon.app'
exe = app / 'Contents/MacOS/Eidolon'
natives = list(app.rglob('*.node'))
assert natives, 'Native addon payload missing'
for binary in [exe, *natives]:
    assert checks.binary_target(binary.read_bytes()) == ('darwin', 'arm64'), str(binary)
checks.run(['codesign', '--verify', '--deep', '--strict', str(app)])
signing = subprocess.run(['codesign', '-dvv', str(app)], capture_output=True, text=True, check=True).stderr
assert 'Signature=adhoc' in signing, signing
stamp = json.loads((app / 'Contents/Resources/install-stamp.json').read_text())
checks.verify_packaged_identity(stamp, identity)
pty = app / 'Contents/Resources/app.asar.unpacked/dist/node_modules/node-pty'
checks.packaged_pty_smoke(exe, pty, temp)
archive.rename(deliver / archive.name)
record = checks.file_record(deliver / archive.name)
proof = dict(source=identity, artifact=record, architecture='darwin/arm64', signing='ad-hoc', notarized=False,
             checks=['archive extraction', 'all native addon architectures', 'strict deep code signature', 'exact source stamp', 'packaged Electron PTY spawn'],
             full_gui_update_test='pending, not established by this build')
(deliver / 'build-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
(deliver / 'SHA256SUMS.txt').write_text(record['digest'].split(':', 1)[1] + '  ' + record['name'] + '\n')
print(json.dumps(proof, indent=2))

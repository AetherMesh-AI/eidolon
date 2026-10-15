"""Prepare an integrity-pinned official compiler for disposable CI, without running it."""
import base64
import hashlib
import io
from pathlib import Path
import sys
import tarfile
from urllib.request import urlopen

from eidolon_cli.organization_project_compiler_bundle import compiler_inventory


def prepare(destination):
    inventory = compiler_inventory()
    url = 'https://registry.npmjs.org/typescript/-/typescript-6.0.3.tgz'
    with urlopen(url, timeout=30) as response:
        archive = response.read(8 * 1024 * 1024 + 1)
    if len(archive) > 8 * 1024 * 1024:
        raise ValueError('Compiler archive exceeded fixed download bound')
    if 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode() != inventory['integrity']:
        raise ValueError('Official compiler archive integrity mismatch')
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    expected = {item['path']: item for item in inventory['files']}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        for member in package:
            name = member.name.removeprefix('package/')
            item = expected.get(name)
            if not member.isfile() or item is None or name in seen or member.size != item['bytes']:
                raise ValueError('Unexpected compiler archive member')
            stream = package.extractfile(member)
            content = stream.read(item['bytes'] + 1)
            if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError('Compiler inventory mismatch')
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            seen.add(name)
    if seen != set(expected):
        raise ValueError('Incomplete compiler archive')
    print('Prepared exact TypeScript 6.0.3 package; no compiler code executed.')


if __name__ == '__main__':
    prepare(sys.argv[1])

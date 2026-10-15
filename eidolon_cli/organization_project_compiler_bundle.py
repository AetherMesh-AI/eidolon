"""Inert exact compiler inventory verification; never installs or runs software.

Only the independently pinned TypeScript package tree is accepted. Owner-selected
paths and package metadata are not authority to change the trusted inventory.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import shutil
import tempfile

ASSETS = Path(__file__).with_name('project_typescript_runtime')
INVENTORY_SHA256 = '4c3c7baba37dff293002ab6a64f70007f922441eebd610392391622cb4466eae'
MAX_FILES = 256
MAX_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


def compiler_inventory():
    with (ASSETS / 'compiler-inventory.json').open('rb') as stream:
        data = stream.read(131073)
    if len(data) > 131072 or hashlib.sha256(data).hexdigest() != INVENTORY_SHA256:
        raise ValueError('Trusted compiler inventory identity does not match')
    value = json.loads(data)
    files = value['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Compiler inventory file count exceeds its bound')
    paths = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Compiler inventory requires exact file descriptors')
        path = item['path']
        if (not isinstance(path, str) or not path or len(path) > 512 or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/'))
                or len(path.split('/')) > 8 or path.startswith('/')
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or path.casefold() in paths):
            raise ValueError('Compiler inventory path is ambiguous')
        paths.add(path.casefold())
        if type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_FILE_BYTES:
            raise ValueError('Compiler inventory file size exceeds its bound')
        total += item['bytes']
        if not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
            raise ValueError('Compiler inventory hash is invalid')
    if total > MAX_BYTES:
        raise ValueError('Compiler inventory exceeds its total byte bound')
    return value


@contextmanager
def _directory(path):
    """Anchor every absolute-path component using directory FDs, never symlinks."""
    path = os.fspath(path)
    if (not isinstance(path, str) or len(path) > 4096 or len(path.split('/')) > 64
            or not path.startswith('/') or '\\' in path
            or any(part in ('', '.', '..') for part in path.split('/')[1:])):
        raise ValueError('Compiler bundle must be an exact absolute directory path')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for part in path.split('/')[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def copy_verified_compiler(source, destination, cancel=None):
    """Copy/hash the same opened descriptors into a new private staging directory.

The caller supplies its private staging parent. No compiler code is imported and
no authority or availability to execute it is inferred from successful copying.
"""
    inventory = compiler_inventory()
    expected = {item['path']: item for item in inventory['files']}
    children = {}
    for path in expected:
        parts = path.split('/')
        for index, name in enumerate(parts):
            children.setdefault('/'.join(parts[:index]), set()).add(name)
    final_destination = Path(destination)
    copied = []
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise ValueError('Compiler verification cancelled')
    check_cancel()
    if final_destination.exists() or final_destination.is_symlink():
        raise FileExistsError('Compiler staging destination already exists')
    destination = Path(tempfile.mkdtemp(prefix='.compiler-verify-', dir=final_destination.parent))
    def visit(descriptor, prefix):
        check_cancel()
        names = set()
        with os.scandir(descriptor) as iterator:
            observed = 0
            for entry in iterator:
                check_cancel()
                observed += 1
                if observed > MAX_FILES or entry.name in names:
                    raise ValueError('Compiler directory enumeration is unstable or exceeds its bound')
                names.add(entry.name)
                if entry.name not in children.get(prefix, set()):
                    raise ValueError('Compiler bundle contains unlisted entries')
        if names != children.get(prefix, set()):
            raise ValueError('Compiler bundle is incomplete')
        for name in sorted(names):
            check_cancel()
            relative = prefix + '/' + name if prefix else name
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if relative in children:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
                try:
                    target.mkdir(mode=0o700)
                    visit(child, relative)
                finally:
                    os.close(child)
                continue
            item = expected[relative]
            source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor)
            try:
                before = os.fstat(source_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != item['bytes']:
                    raise ValueError('Compiler bundle file type, links or size does not match')
                total = 0
                hasher = hashlib.sha256()
                with target.open('xb') as stream:
                    while True:
                        check_cancel()
                        block = os.read(source_fd, min(65536, item['bytes'] - total + 1))
                        if not block:
                            break
                        total += len(block)
                        if total > item['bytes']:
                            raise ValueError('Compiler bundle file grew beyond its exact bound')
                        hasher.update(block)
                        stream.write(block)
                after = os.fstat(source_fd)
                if (total != item['bytes'] or hasher.hexdigest() != item['sha256'] or after.st_nlink != 1
                        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                           (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError('Compiler bundle bytes or file identity changed')
                copied.append(dict(item))
            finally:
                os.close(source_fd)
    try:
        with _directory(source) as descriptor:
            visit(descriptor, '')
        check_cancel()
        for directory, _, filenames in os.walk(destination, topdown=False):
            for filename in filenames:
                (Path(directory) / filename).chmod(0o444)
            Path(directory).chmod(0o555)
        check_cancel()
        # The caller owns this private staging parent; expose only a fully verified tree.
        if final_destination.exists() or final_destination.is_symlink():
            raise FileExistsError('Compiler staging destination appeared during verification')
        destination.rename(final_destination)
    except BaseException as error:
        try:
            for directory, _, _ in os.walk(destination):
                Path(directory).chmod(0o700)
            shutil.rmtree(destination)
        except OSError as cleanup_error:
            error.add_note('Private compiler staging cleanup failed: ' + str(cleanup_error))
        raise
    return {'package': inventory['package'], 'version': inventory['version'],
            'registryArchiveIntegrity': inventory['integrity'], 'inventorySha256': INVENTORY_SHA256,
            'files': sorted(copied, key=lambda item: item['path']), 'executionAuthorized': False}

"""Fixed optional pytest bundle; discovery never imports installed package code.

Versions track uv.lock; the Python 3.10 compatibility wheels are pinned in native
CI. Installed metadata is provenance, not an upstream authenticity signature.
Only this allowlist is copied, never site-packages itself or dependency hooks.
"""
from email.parser import BytesParser
import os
import re
from pathlib import Path
import sysconfig


PACKAGES = {
    'pytest': ('9.1.1', ('pytest', '_pytest', 'py.py')),
    'pluggy': ('1.6.0', ('pluggy',)),
    'packaging': ('26.0', ('packaging',)),
    'iniconfig': ('2.3.0', ('iniconfig',)),
    'pygments': ('2.20.0', ('pygments',)),
}
COMPATIBILITY_PACKAGES = {
    'exceptiongroup': ('1.3.0', ('exceptiongroup',)),
    'tomli': ('2.2.1', ('tomli',)),
    'typing_extensions': ('4.15.0', ('typing_extensions.py',)),
}


class PytestBundleUnavailable(ValueError):
    pass


def pytest_bundle(system_version, budget):
    """Select bounded pure-Python files from the application's own installation.

Do not use sys.path/import spec discovery: a project, PYTHONPATH or the user's
site directory must not supply trusted runner code. No executable probing occurs.
"""
    if tuple(system_version[:2]) < (3, 10):
        raise PytestBundleUnavailable('The pytest recipe requires system Python 3.10 or newer')
    root = Path(sysconfig.get_path('purelib')).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise PytestBundleUnavailable('Linked application package directories are unsupported')
    packages = dict(PACKAGES)
    if tuple(system_version[:2]) < (3, 11):
        packages.update(COMPATIBILITY_PACKAGES)
    files, provenance, total, visited = [], [], 0, 0
    for name, (version, modules) in packages.items():
        budget.check()
        info = root / f'{name}-{version}.dist-info'
        metadata_file = info / 'METADATA'
        if info.is_symlink() or metadata_file.is_symlink() or not metadata_file.is_file():
            raise PytestBundleUnavailable(f'Optional pytest bundle requires installed {name}=={version}; no dependency was downloaded')
        with metadata_file.open('rb') as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise PytestBundleUnavailable('Pytest package metadata exceeds its byte bound')
        headers = BytesParser().parsebytes(content, headersonly=True)
        if ((headers.get('Name') or '').lower().replace('-', '_') != name
                or headers.get('Version') != version):
            raise PytestBundleUnavailable(f'Optional pytest bundle requires installed {name}=={version}')
        provenance.append({'name': name, 'version': version})
        pending = [root / module for module in modules]
        while pending:
            budget.check()
            source = pending.pop()
            visited += 1
            if visited > 2000:
                raise PytestBundleUnavailable('Optional pytest bundle exceeds its directory-entry bound')
            if source.is_symlink() or not source.exists():
                raise PytestBundleUnavailable('Linked or missing files are unsupported in the pytest bundle')
            if source.is_dir():
                with os.scandir(source) as entries:
                    for entry in entries:
                        budget.check()
                        if len(pending) + visited >= 2000:
                            raise PytestBundleUnavailable('Optional pytest bundle exceeds its directory-entry bound')
                        if entry.is_symlink():
                            raise PytestBundleUnavailable('Linked files are unsupported in the pytest bundle')
                        if entry.name != '__pycache__':
                            pending.append(Path(entry.path))
                continue
            if source.name == 'py.typed' or source.suffix == '.pyi':
                continue
            if source.suffix != '.py' or not source.is_file():
                raise PytestBundleUnavailable('Only pure-Python pytest package files are supported')
            size = source.stat().st_size
            total += size
            if size > 2 * 1024 * 1024 or total > 32 * 1024 * 1024 or len(files) >= 1000:
                raise PytestBundleUnavailable('Optional pytest bundle exceeds its file or byte bounds')
            files.append((source, source.relative_to(root)))

    return files, provenance


def valid_pytest_identity(identity):
    """Receipt admission requires the fixed installed bundle, not any pytest claim."""
    version = identity.get('versionInfo')
    if (identity.get('stdlibOnly') is not False or not isinstance(version, list)
            or len(version) != 3 or any(type(part) is not int for part in version)
            or tuple(version[:2]) < (3, 10)):
        return False
    packages = dict(PACKAGES)
    if tuple(version[:2]) < (3, 11):
        packages.update(COMPATIBILITY_PACKAGES)
    expected = [{'name': name, 'version': value[0]} for name, value in packages.items()]
    return (identity.get('dependencies') == expected
            and isinstance(identity.get('dependencyFilesSha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', identity['dependencyFilesSha256']) is not None)

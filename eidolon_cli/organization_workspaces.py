"""Crash-recoverable fresh workspace creation, never activation or credential cloning."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import uuid

from eidolon_cli.organization_metadata import read_descriptor, save_metadata, _text, _uuid, _validate_record
from eidolon_cli.profile_isolation import (
    VERSION, MODE, PROVIDER, MARKER, WORKSPACES, ProfileIsolationError,
    local_path, read_isolation, verify_isolation, isolation_registration,
)

_LOCK = threading.RLock()
_IDENTITY_FIELDS = {'capabilityVersion', 'expectedOrganizationId', 'expectedOwnerId', 'idempotencyKey'}
_CREATE_FIELDS = _IDENTITY_FIELDS | {'displayName', 'ownerDisplayName'}


class WorkspaceError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Organization workspace: ' + reason)


def _origin(home):
    from eidolon_cli.profiles import _get_default_eidolon_home
    home = local_path(home)
    root = local_path(_get_default_eidolon_home())
    local_path(root / 'profiles')
    if home == root:
        profile = 'default'
    elif home.parent == root / 'profiles':
        profile = home.name
    else:
        raise WorkspaceError('unsupported_origin')
    value = read_descriptor(local_path(home / 'organization' / 'state.db'))
    if value['state'] != 'ready':
        raise WorkspaceError('origin_unconfigured')
    verify_isolation(home)
    record = value['metadata']
    return root, {'profile': profile, 'organizationId': record['organizationId'],
                  'ownerId': record['owner']['ownerId']}


def workspace_creation(home: Path) -> dict:
    """Read-only feature negotiation. Older runtimes have no matching RPC."""
    try:
        _, origin = _origin(home)
        _publication_api()
    except WorkspaceError as exc:
        if exc.reason not in {'origin_unconfigured', 'atomic_publication_unavailable'}:
            raise
        return {'capabilityVersion': VERSION, 'state': 'unavailable', 'reason': exc.reason}
    return {'capabilityVersion': VERSION, 'state': 'available', 'isolationVersion': VERSION,
            'credentialMode': MODE, 'origin': origin, 'supportedProviders': [PROVIDER],
            'executionMode': 'tool-free-organization', 'activation': 'explicit-client'}


def _parameters(params, *, create):
    if not isinstance(params, dict) or set(params) != (_CREATE_FIELDS if create else _IDENTITY_FIELDS):
        raise ValueError('Unsupported workspace parameters; cloning and caller-selected paths are unavailable')
    if type(params['capabilityVersion']) is not int or params['capabilityVersion'] != VERSION:
        raise WorkspaceError('unsupported_capability')
    payload = {'capabilityVersion': VERSION,
               'expectedOrganizationId': _uuid(params['expectedOrganizationId'], 'expectedOrganizationId'),
               'expectedOwnerId': _uuid(params['expectedOwnerId'], 'expectedOwnerId'),
               'idempotencyKey': _text(params['idempotencyKey'], 'idempotencyKey', 200)}
    if create:
        payload.update(displayName=_text(params['displayName'], 'displayName', 160),
                       ownerDisplayName=_text(params['ownerDisplayName'], 'ownerDisplayName', 160))
    return payload


def _fenced_origin(home, payload):
    root, origin = _origin(home)
    if (origin['organizationId'] != payload['expectedOrganizationId']
            or origin['ownerId'] != payload['expectedOwnerId']):
        raise WorkspaceError('stale_origin')
    return root, origin


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _operation(root, origin, payload):
    key = _hash([origin, payload['idempotencyKey']])
    return key, local_path(root / WORKSPACES / 'operations' / key)


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(str(local_path(path)), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publication_api():
    if os.name == 'nt':
        return None  # Windows rename already refuses an existing destination.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    name = {'linux': 'renameat2', 'darwin': 'renamex_np'}.get(sys.platform)
    function = getattr(libc, name, None) if name else None
    if function is None:
        raise WorkspaceError('atomic_publication_unavailable')
    function.restype = ctypes.c_int
    return function


def _publish_directory(stage, target):
    """Never replace even an empty directory created by another operation."""
    function = _publication_api()
    if function is None:
        os.rename(stage, target)
        return
    import ctypes
    source, destination = os.fsencode(stage), os.fsencode(target)
    if sys.platform == 'linux':
        result = function(-100, ctypes.c_char_p(source), -100, ctypes.c_char_p(destination), 1)
    else:
        result = function(ctypes.c_char_p(source), ctypes.c_char_p(destination), 4)
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _write_json(path, value):
    _write_file(path, json.dumps(value, sort_keys=True, ensure_ascii=False))


def _write_file(path, value):
    path = local_path(path)
    temporary = local_path(path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp'))
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_operation(directory):
    path = local_path(directory / 'operation.json')
    if not path.exists():
        return None
    if path.stat().st_size > 32768:
        raise WorkspaceError('invalid_operation')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'version', 'inputHash', 'payload', 'profile', 'result'}
                or value['version'] != VERSION or value['inputHash'] != _hash(value['payload'])
                or not isinstance(value['profile'], str)
                or len(value['profile']) != 36 or not value['profile'].startswith('org-')
                or uuid.UUID(hex=value['profile'][4:]).hex != value['profile'][4:]):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
        raise WorkspaceError('invalid_operation') from exc


@contextmanager
def _creation_lock(root):
    """One process-independent publication lock; never unlink a held lock inode."""
    with _LOCK:
        directory = local_path(root / WORKSPACES)
        directory.mkdir(mode=0o700, exist_ok=True)
        _sync_directory(root)
        lock_path = local_path(directory / 'creation.lock')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(str(lock_path), flags, 0o600)
        with os.fdopen(fd, 'r+b') as handle:
            if os.name == 'nt':
                import msvcrt
                handle.write(b'0')
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield


def _result(record, marker, metadata):
    return {'capabilityVersion': VERSION, 'state': 'created',
            'idempotencyKey': record['payload']['idempotencyKey'], 'profile': record['profile'],
            'organizationId': marker['organizationId'], 'ownerId': marker['ownerId'],
            'isolationVersion': VERSION, 'credentialMode': MODE,
            'executionMode': 'tool-free-organization', 'descriptor': metadata, 'activated': False}


def _published(root, operation, record):
    target = local_path(root / 'profiles' / record['profile'])
    if not target.exists():
        return None
    marker = verify_isolation(target)
    result = record['result']
    if (not isinstance(result, dict) or marker is None or marker['operation'] != operation
            or result.get('profile') != record['profile']
            or result.get('organizationId') != marker['organizationId']
            or result.get('ownerId') != marker['ownerId']
            or result.get('isolationVersion') != VERSION or result.get('state') != 'created'):
        raise WorkspaceError('publication_conflict')
    descriptor = _validate_record(result.get('descriptor'))
    if (result != _result(record, marker, descriptor)
            or descriptor['organizationId'] != marker['organizationId']
            or descriptor['owner']['ownerId'] != marker['ownerId']
            or descriptor['displayName'] != record['payload']['displayName']
            or descriptor['owner']['displayName'] != record['payload']['ownerDisplayName']):
        raise WorkspaceError('publication_conflict')
    return result


def workspace_creation_status(home: Path, params: dict) -> dict:
    payload = _parameters(params, create=False)
    root, origin = _fenced_origin(home, payload)
    operation, directory = _operation(root, origin, payload)
    record = _read_operation(directory)
    if record is None:
        return {'capabilityVersion': VERSION, 'state': 'unknown'}
    if any(record['payload'].get(key) != value for key, value in payload.items()):
        raise WorkspaceError('idempotency_conflict')
    return _published(root, operation, record) or {'capabilityVersion': VERSION, 'state': 'pending'}


def _prepare_stage(directory, operation, record):
    stage = local_path(directory / 'stage')
    stage.mkdir(mode=0o700, exist_ok=True)
    _sync_directory(directory)
    marker = {'version': VERSION, 'state': 'staging', 'profile': record['profile'],
              'operation': operation, 'organizationId': None, 'ownerId': None}
    if (stage / MARKER).exists() or (stage / MARKER).is_symlink():
        previous = read_isolation(stage)
        if previous['profile'] != marker['profile'] or previous['operation'] != operation:
            raise WorkspaceError('staging_conflict')
    elif any(not (entry.name.startswith(MARKER + '.') and entry.name.endswith('.tmp')) for entry in stage.iterdir()):
        raise WorkspaceError('staging_marker_missing')
    else:
        _write_json(stage / MARKER, marker)
    # No inherited model block, secrets, SOUL, plugins, skills or provider files.
    for name in ('memories', 'sessions', 'skills', 'skins', 'logs', 'plans', 'workspace', 'cron', 'home'):
        local_path(stage / name).mkdir(mode=0o700, exist_ok=True)
    if not local_path(stage / 'config.yaml').exists():
        _write_json(stage / 'config.yaml', {'model': {'provider': '', 'default': ''},
                    'agent': {'environment_probe': False}, 'organization': {'gateway_enabled': False}})
    if not local_path(stage / '.env').exists():
        _write_file(stage / '.env', '')
    if not local_path(stage / 'profile.yaml').exists():
        _write_json(stage / 'profile.yaml', {'display_name': record['payload']['displayName']})
    path = local_path(stage / 'organization' / 'state.db')
    metadata = save_metadata(path, display_name=record['payload']['displayName'],
        owner_display_name=record['payload']['ownerDisplayName'], expected_organization_id=None,
        expected_revision=None, idempotency_key='workspace:' + operation)['metadata']
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    token = set_eidolon_home_override(stage)
    try:
        OrganizationStore(path)
    finally:
        reset_eidolon_home_override(token)
    # Store methods own/close their connections. Checkpoint before directory rename.
    with closing(sqlite3.connect(path)) as conn:
        checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint[0]:
            raise WorkspaceError('staging_database_busy')
    for candidate in (path, stage / 'config.yaml', stage / '.env'):
        # Windows fsync/_commit requires write access; r+b preserves existing bytes.
        with local_path(candidate).open('r+b') as handle:
            os.fsync(handle.fileno())
    marker.update(state='ready', organizationId=metadata['organizationId'], ownerId=metadata['owner']['ownerId'])
    _write_json(stage / MARKER, marker)
    _sync_directory(path.parent)
    _sync_directory(stage)
    record = {**record, 'result': _result(record, marker, metadata)}
    _write_json(directory / 'operation.json', record)
    return stage, marker, record


def create_workspace(home: Path, params: dict) -> dict:
    payload = _parameters(params, create=True)
    _publication_api()
    root, origin = _fenced_origin(home, payload)  # no writes before negotiation/identity validation
    operation, directory = _operation(root, origin, payload)
    with _creation_lock(root):
        _fenced_origin(home, payload)
        record = _read_operation(directory)
        if record is not None and record['inputHash'] != _hash(payload):
            raise WorkspaceError('idempotency_conflict')
        if record is None:
            local_path(directory.parent).mkdir(mode=0o700, exist_ok=True)
            _sync_directory(directory.parent.parent)
            directory.mkdir(mode=0o700, exist_ok=True)
            _sync_directory(directory.parent)
            record = {'version': VERSION, 'inputHash': _hash(payload), 'payload': payload,
                      'profile': 'org-' + uuid.uuid4().hex, 'result': None}
            _write_json(directory / 'operation.json', record)
        published = _published(root, operation, record)
        if published is not None:
            return published
        stage, marker, record = _prepare_stage(directory, operation, record)
        _fenced_origin(home, payload)
        target = local_path(root / 'profiles' / record['profile'])
        target.parent.mkdir(mode=0o700, exist_ok=True)
        _sync_directory(root)
        registration = local_path(isolation_registration(target))
        registration.parent.mkdir(mode=0o700, exist_ok=True)
        _sync_directory(registration.parent.parent)
        if registration.exists() and json.loads(registration.read_text(encoding='utf-8')) != marker:
            raise WorkspaceError('registration_conflict')
        _write_json(registration, marker)
        if target.exists():
            raise WorkspaceError('publication_conflict')
        _publish_directory(stage, target)
        _sync_directory(target.parent)
        _sync_directory(directory)
        return _published(root, operation, record)

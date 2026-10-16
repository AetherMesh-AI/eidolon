"""Exact prepared-run resources and host event boundaries, never authorization.

The caller supplies independently retained identity and a synchronous event sink
returning True only after durable commit. These helpers do not make a model's
identity, event, label, hash or receipt trustworthy. No scheduler is registered.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from eidolon_cli.organization_project_compiler_bundle import _directory
from eidolon_cli.organization_project_dependency_bundle import _cleanup, _json
from eidolon_cli.organization_project_validation import canonical

RESOURCE_CONTRACT = 'prepared-workspace-resource-v1'
EVENT_CONTRACT = 'prepared-workspace-event-v1'
_IDENTITY_FIELDS = {'version', 'contract', 'setId', 'runId', 'nonce', 'authoritySha256'}
_STAGING_INTENT_FIELDS = {'path', 'parentDevice', 'parentInode'}
_STAGING_FIELDS = _STAGING_INTENT_FIELDS | {'device', 'inode'}


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(label + ' requires exact fields')
    return value


def _hash(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Resource requires an exact lowercase SHA-256 identity')
    return value


def directory_identity(value):
    if (type(value) is not tuple or len(value) != 2
            or any(type(item) is not int or not 0 <= item < 2**64 for item in value)):
        raise ValueError('Directory identity requires exact device and inode integers')
    return value


def normalize_runtime_identity(value):
    value = _json(value, 2048, 'Runtime resource identity')
    _exact(value, _IDENTITY_FIELDS, 'Runtime resource identity')
    if type(value['version']) is not int or value['version'] != 1 or value['contract'] != RESOURCE_CONTRACT:
        raise ValueError('Unsupported runtime resource identity version')
    for key in ('setId', 'runId'):
        if type(value[key]) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value[key]):
            raise ValueError('Resource set/run identity must be a bounded exact identifier')
    if value['setId'] == value['runId']:
        raise ValueError('Resource set and run identities must be distinct')
    for key in ('nonce', 'authoritySha256'):
        _hash(value[key])
    return value


def container_name(identity):
    return 'eidolon-prepared-' + normalize_runtime_identity(identity)['nonce']


def container_labels(identity):
    identity = normalize_runtime_identity(identity)
    return {'ai.eidolon.execution': container_name(identity),
            'ai.eidolon.prepared.set': identity['setId'],
            'ai.eidolon.prepared.run': identity['runId'],
            'ai.eidolon.prepared.nonce': identity['nonce'],
            'ai.eidolon.prepared.authority': identity['authoritySha256']}


def verify_container_identity(record, identity, *, image_id, container_id=None):
    """Return exact Docker ID; missing/extra labels or a changed ID fail closed."""
    identity = normalize_runtime_identity(identity)
    if type(image_id) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('Runtime image ID must be exact Docker sha256 identity')
    if container_id is not None:
        _hash(container_id)
    if (type(record) is not dict or type(record.get('Config')) is not dict
            or record.get('Name') != '/' + container_name(identity)
            or record.get('Image') != image_id
            or record['Config'].get('Labels') != container_labels(identity)):
        raise ValueError('Container name, labels or image differ from retained resource identity')
    observed = _hash(record.get('Id'))
    if container_id is not None and observed != container_id:
        raise ValueError('Container ID differs from the exact retained resource')
    return observed


def commit_runtime_event(record_event, identity, kind, data):
    """Require durable acknowledgement, never accepting ambiguous forward work.

The sink receives detached bounded JSON. True acknowledges persistence, not
execution authority. Reconciliation reads trusted history rather than calling
this helper to manufacture it.
"""
    if not callable(record_event):
        raise ValueError('Prepared execution requires a durable host event sink')
    if kind not in {'dispatch_planned', 'staging_allocated', 'container_create_planned',
                    'container_observed', 'container_start_planned', 'cleanup_observed', 'receipt'}:
        raise ValueError('Unsupported prepared runtime event')
    event = {'version': 1, 'contract': EVENT_CONTRACT, 'kind': kind,
             'resource': normalize_runtime_identity(identity), 'data': data}
    try:
        acknowledged = record_event(normalize_runtime_event(event))
    except Exception as error:
        raise ValueError('Prepared runtime event could not be durably committed') from error
    if acknowledged is not True:
        raise ValueError('Prepared runtime event was not durably committed')


def _path(value):
    if (type(value) is not str or not value.startswith('/') or value == '/'
            or len(value.encode('utf-8')) > 4096
            or any(part in {'', '.', '..'} for part in value[1:].split('/'))
            or any(char in value for char in '\\,:*?[]{}')
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('Staging requires an exact absolute directory path')
    return value


def normalize_staging_identity(value, identity, *, allocated=True):
    value = _json(value, 8192, 'Staging identity')
    _exact(value, _STAGING_FIELDS if allocated else _STAGING_INTENT_FIELDS, 'Staging identity')
    path = Path(_path(value['path']))
    if path.name != container_name(identity):
        raise ValueError('Staging path does not belong to the exact resource nonce')
    directory_identity((value['parentDevice'], value['parentInode']))
    if allocated:
        directory_identity((value['device'], value['inode']))
    return value


def _check_private_parent(info, identity=None):
    if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
            or not stat.S_ISDIR(info.st_mode)):
        raise ValueError('Prepared staging parent must be caller-private (0700)')
    if identity is not None and (info.st_dev, info.st_ino) != identity:
        raise ValueError('Prepared staging parent identity changed')


def staging_intent(parent, identity):
    """Read the private parent identity without allocating or replacing paths."""
    parent = Path(_path(os.fspath(parent)))
    with _directory(parent) as descriptor:
        info = os.fstat(descriptor)
        _check_private_parent(info)
    return {'path': str(parent / container_name(identity)),
            'parentDevice': info.st_dev, 'parentInode': info.st_ino}


def allocate_staging(intent, identity):
    """Exclusive allocation only; caller MUST commit intent before invoking."""
    intent = normalize_staging_identity(intent, identity, allocated=False)
    path = Path(intent['path'])
    with _directory(path.parent) as parent:
        _check_private_parent(os.fstat(parent), (intent['parentDevice'], intent['parentInode']))
        os.mkdir(path.name, 0o700, dir_fd=parent)
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _check_private_parent(info)
        return {**intent, 'device': info.st_dev, 'inode': info.st_ino}


def verify_staging_identity(value, identity):
    value = normalize_staging_identity(value, identity)
    path = Path(value['path'])
    with _directory(path.parent) as parent:
        _check_private_parent(os.fstat(parent), (value['parentDevice'], value['parentInode']))
        with _directory(path) as root:
            info = os.fstat(root)
            if (info.st_dev, info.st_ino) != (value['device'], value['inode']):
                raise ValueError('Prepared staging root identity changed')
            _check_private_parent(info)
    return value


def cleanup_owned_staging(value, identity):
    """Remove only the retained allocation in its still-private retained parent.

No prefix search, arbitrary path, PID or replacement directory is eligible.
The parent is host-owned; workload UID 65534 cannot modify this namespace.
"""
    value = verify_staging_identity(value, identity)
    path = Path(value['path'])
    with _directory(path.parent) as parent:
        _check_private_parent(os.fstat(parent), (value['parentDevice'], value['parentInode']))
        _cleanup(parent, path.name, (value['device'], value['inode']))
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return True
        raise ValueError('Prepared staging cleanup is unconfirmed')


def _bounded_json(value, maximum=4 * 1024**2):
    """Detach bounded JSON, including finite fractional host durations."""
    if type(value) is dict:
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > 100_000 or depth > 64:
                raise ValueError('Prepared host record exceeds its structural bound')
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError('Prepared host record requires string keys')
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                if len(item) > 100_000:
                    raise ValueError('Prepared host record exceeds its structural bound')
                pending.extend((child, depth + 1) for child in item)
            elif type(item) not in (str, int, float, bool, type(None)):
                raise ValueError('Prepared host record contains unsupported values')
            elif type(item) is str and len(item) > maximum:
                raise ValueError('Prepared host record exceeds its byte bound')
        try:
            value = canonical(value).encode('utf-8')
        except (ValueError, UnicodeError, RecursionError, OverflowError) as error:
            raise ValueError('Prepared host record is not finite bounded JSON') from error
    return _json(value, maximum, 'Prepared host record')


def _runtime(value):
    from eidolon_cli.organization_project_node import IMAGE, PROFILE_SHA256
    _exact(value, {'image', 'imageId', 'seccompSha256', 'bootstrapSha256'}, 'Runtime identity')
    if (value['image'] != IMAGE or value['seccompSha256'] != PROFILE_SHA256
            or type(value['imageId']) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', value['imageId'])):
        raise ValueError('Prepared runtime image or seccomp identity differs from its reviewed version')
    _hash(value['bootstrapSha256'])
    return value


def normalize_runtime_event(value):
    """Validate event shape, not its origin, ordering, authority or durability."""
    from eidolon_cli.organization_project_node import IMAGE
    from eidolon_cli.organization_project_validation import digest
    value = _bounded_json(value)
    _exact(value, {'version', 'contract', 'kind', 'resource', 'data'}, 'Runtime event')
    if type(value['version']) is not int or value['version'] != 1 or value['contract'] != EVENT_CONTRACT:
        raise ValueError('Unsupported prepared runtime event version')
    identity = normalize_runtime_identity(value['resource'])
    fields = {
        'dispatch_planned': {'containerName', 'labels', 'image', 'stagingIntent', 'dependencyRootIdentity',
            'snapshotSha256', 'profileSha256', 'bundleSha256', 'stepId', 'stepDefinitionSha256', 'recipe', 'checkKind'},
        'staging_allocated': {'staging'},
        'container_create_planned': {'staging', 'runtime'},
        'container_observed': {'containerId', 'runtime', 'staging'},
        'container_start_planned': {'containerId', 'containerConfigSha256'},
        'cleanup_observed': {'containerId', 'containerCleanupConfirmed', 'stagingCleanupConfirmed', 'staging'},
        'receipt': {'receipt', 'receiptSha256'},
    }
    kind = value['kind']
    if type(kind) is not str or kind not in fields:
        raise ValueError('Unsupported prepared runtime event kind')
    data = _exact(value['data'], fields[kind], 'Runtime event data')
    if kind == 'dispatch_planned':
        if (data['containerName'] != container_name(identity) or data['labels'] != container_labels(identity)
                or data['image'] != IMAGE):
            raise ValueError('Dispatch identity is inconsistent')
        normalize_staging_identity(data['stagingIntent'], identity, allocated=False)
        _exact(data['dependencyRootIdentity'], {'device', 'inode'}, 'Dependency root identity')
        directory_identity((data['dependencyRootIdentity']['device'], data['dependencyRootIdentity']['inode']))
        for key in ('snapshotSha256', 'profileSha256', 'bundleSha256', 'stepDefinitionSha256'):
            _hash(data[key])
        if type(data['stepId']) is not str or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', data['stepId']):
            raise ValueError('Dispatch step ID is invalid')
        kinds = {'workspace_typescript': 'project_typecheck', 'workspace_vitest_ui': 'project_tests'}
        if type(data['recipe']) is not str or kinds.get(data['recipe']) != data['checkKind']:
            raise ValueError('Dispatch recipe and check kind do not match')
    if 'runtime' in data:
        _runtime(data['runtime'])
    if 'staging' in data and data['staging'] is not None:
        normalize_staging_identity(data['staging'], identity)
    if kind != 'cleanup_observed' and 'staging' in data and data['staging'] is None:
        raise ValueError('Runtime event is missing its observed staging allocation')
    if 'containerId' in data and (kind != 'cleanup_observed' or data['containerId'] is not None):
        _hash(data['containerId'])
    if 'containerConfigSha256' in data:
        _hash(data['containerConfigSha256'])
    if kind == 'cleanup_observed':
        for key in ('containerCleanupConfirmed', 'stagingCleanupConfirmed'):
            if type(data[key]) is not bool:
                raise ValueError('Cleanup observations require exact booleans')
        if data['containerCleanupConfirmed'] and data['containerId'] is None:
            raise ValueError('Confirmed container cleanup requires its exact observed ID')
        if data['stagingCleanupConfirmed'] and data['staging'] is None:
            raise ValueError('Confirmed staging cleanup requires its exact observed allocation')
    if kind == 'receipt':
        if (type(data['receipt']) is not dict or data['receipt'].get('resourceIdentity') != identity
                or digest(data['receipt']) != _hash(data['receiptSha256'])):
            raise ValueError('Terminal receipt does not match its retained identity and hash')
    return value

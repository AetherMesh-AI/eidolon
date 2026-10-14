"""Read-only review and explicit owner controls for the installation allowance.

Only the shared ledger writer creates storage. This module never constructs an
execution store, initializes profiles, or probes model/provider connections.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from eidolon_cli.organization_metadata import _uuid, _text, read_descriptor

SHARED_ALLOWANCE_CAPABILITY_VERSION = 1


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def source_revision(state, root_identity):
    """Owner decisions change this source; concurrent physical debits do not."""
    return _hash({'revision': state['revision'], 'configuration': state['configuration'],
                  'observation': state['observation'], 'rootIdentity': root_identity})


def _parameters(params, action, *, apply=False):
    allowed = {'expectedOrganizationId', 'expectedOwnerId', 'expectedGeneration', 'sourceRevision',
               'observation' if action == 'observation' else 'configuration'}
    if apply:
        allowed |= {'previewVersion', 'idempotencyKey', 'confirmed'}
    if not isinstance(params, dict) or set(params) != allowed:
        raise ValueError('Unsupported shared allowance parameters')


def _envelope(params, *, apply=False):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    _uuid(params.get('expectedOwnerId'), 'expectedOwnerId')
    if type(params.get('expectedGeneration')) is not int or not 0 <= params['expectedGeneration'] < 2**63:
        raise ValueError('expectedGeneration must be a nonnegative integer')
    for field in ('sourceRevision', 'previewVersion') if apply else ('sourceRevision',):
        value = params.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError(f'{field} requires the exact reviewed source')
    if apply:
        if params.get('confirmed') is not True:
            raise ValueError('Review and confirm this shared allowance change before applying')
        _text(params.get('idempotencyKey'), 'idempotencyKey', 128)


def preview_identity(action, params, candidate):
    field = 'observation' if action == 'observation' else 'configuration'
    return {'action': action, 'organizationId': params['expectedOrganizationId'],
            'ownerId': params['expectedOwnerId'], 'generation': params['expectedGeneration'],
            'sourceRevision': params['sourceRevision'], field: candidate}


def validate_owner_intent(state, root_identity, action, params, candidate):
    """Pure check reused inside the ledger's locked writer after receipt replay."""
    from eidolon_cli.organization_shared_allowance import SharedAllowanceError
    _parameters(params, action, apply=True)
    _envelope(params, apply=True)
    if (params['expectedOrganizationId'] != root_identity['organizationId']
            or params['expectedOwnerId'] != root_identity['ownerId']):
        raise SharedAllowanceError('identity_conflict')
    if params['expectedGeneration'] != state['revision']:
        raise SharedAllowanceError('generation_conflict')
    if params['sourceRevision'] != source_revision(state, root_identity):
        raise SharedAllowanceError('source_conflict')
    field = 'observation' if action == 'observation' else 'configuration'
    if _hash(params.get(field)) != _hash(candidate):
        raise ValueError('Apply requires the exact canonical preview candidate')
    if params['previewVersion'] != _hash(preview_identity(action, params, candidate)):
        raise SharedAllowanceError('preview_conflict')


def _candidate(home, profile):
    """Display only already initialized, canonical, live profile identities."""
    from eidolon_cli.organization_observation import _compatible_schema
    path = home / 'organization' / 'state.db'
    if home.resolve() != home or path.resolve() != path or not path.is_file():
        return None
    descriptor = read_descriptor(path)
    if descriptor['state'] != 'ready':
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                 timeout=.25, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'organization_policy' not in tables or not _compatible_schema(conn, tables):
            return None
        if conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone() is None:
            return None
    identity = descriptor['metadata']
    return {'profile': profile, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'displayName': identity['displayName'],
            'ownerDisplayName': identity['owner']['displayName']}


def _candidates(root):
    from eidolon_cli.profiles import validate_profile_name, normalize_profile_name
    from eidolon_cli.organization_metadata import MetadataError
    result = []
    homes = [('default', root)]
    directory = root / 'profiles'
    if directory.resolve() == directory and directory.is_dir():
        for home in sorted(directory.iterdir()):
            name = home.name
            if name.startswith('.') or not home.is_dir() or (directory / '.deleted' / name).exists():
                continue
            try:
                validate_profile_name(name)
            except ValueError:
                continue
            if normalize_profile_name(name) == name:
                homes.append((name, home))
    if len(homes) > 512:
        from eidolon_cli.organization_shared_allowance import SharedAllowanceError
        raise SharedAllowanceError('too_many_profiles')
    for name, home in homes:
        try:
            candidate = _candidate(home, name)
        except (MetadataError, OSError, sqlite3.Error):
            continue
        if candidate is not None:
            result.append(candidate)
    return result


def _context(home, expected_id):
    from eidolon_cli.organization_shared_allowance import (
        SharedAllowanceError, SharedAllowanceLedger, resolve_authority,
    )
    _uuid(expected_id, 'expectedOrganizationId')
    authority = resolve_authority(home)
    if authority['servedIdentity']['organizationId'] != expected_id:
        raise SharedAllowanceError('identity_conflict')
    ledger = SharedAllowanceLedger(authority['root'], home=authority['home'])
    return authority, ledger


def shared_allowance(home, expected_id):
    from eidolon_cli.organization_shared_allowance import managed_shared_allowance
    authority, ledger = _context(home, expected_id)
    state = ledger.read(organization_id=expected_id)
    owner = authority['role'] == 'installation-owner'
    locked = managed_shared_allowance(authority['root'])
    return {'capabilityVersion': 1, 'state': state['state'],
            'organizationId': authority['servedIdentity']['organizationId'],
            'ownerId': authority['servedIdentity']['ownerId'], 'authority': authority['role'],
            'generation': state['revision'], 'sourceRevision': source_revision(state, authority['rootIdentity']),
            'configuration': state['configuration'] if owner else None,
            'candidates': _candidates(authority['root']) if owner else [],
            'pool': state['pool'] if owner else None, 'member': state['member'],
            'observation': state['observation'], 'pacing': state['pacing'],
            'capabilities': {'configuration': owner and not locked,
                             'observation': owner and not locked and state['state'] == 'ready'},
            'managed': bool(locked)}


def preview_shared_allowance(home, action, params):
    from eidolon_cli.organization_shared_allowance import (
        SharedAllowanceError, normalize_configuration, normalize_observation, managed_shared_allowance,
    )
    if action not in {'configuration', 'observation'}:
        raise ValueError('Unsupported shared allowance action')
    _parameters(params, action)
    _envelope(params)
    authority, ledger = _context(home, params['expectedOrganizationId'])
    if authority['role'] != 'installation-owner':
        raise SharedAllowanceError('installation_owner_required')
    if authority['rootIdentity']['ownerId'] != params['expectedOwnerId']:
        raise SharedAllowanceError('identity_conflict')
    if managed_shared_allowance(authority['root']):
        raise SharedAllowanceError('managed_configuration')
    state = ledger.read(organization_id=params['expectedOrganizationId'])
    if params['expectedGeneration'] != state['revision']:
        raise SharedAllowanceError('generation_conflict')
    if params['sourceRevision'] != source_revision(state, authority['rootIdentity']):
        raise SharedAllowanceError('source_conflict')
    field = 'observation' if action == 'observation' else 'configuration'
    candidate = (normalize_observation(params.get(field)) if action == 'observation'
                 else normalize_configuration(params.get(field)))
    if action == 'configuration':
        known = {(row['profile'], row['organizationId'], row['ownerId']) for row in _candidates(authority['root'])}
        if any((row['profile'], row['organizationId'], row['ownerId']) not in known
               for row in candidate['allocations']):
            raise SharedAllowanceError('enrollment_identity_conflict')
    elif state['state'] != 'ready':
        raise SharedAllowanceError('unconfigured')
    core = preview_identity(action, params, candidate)
    return {'capabilityVersion': 1, **{key: value for key, value in core.items() if key != 'action'},
            'previewVersion': _hash(core), 'changes': [{'field': field, 'before': state[field], 'after': candidate}],
            'warnings': [{'code': code, 'values': {}} for code in (
                'owner_declared_allowance', 'configured_prices_not_invoice', 'no_refunds_or_auto_reset',
                'existing_local_limits_still_apply', 'enrollment_requires_idle')],
            'impact': {'scope': 'installation-allowance', 'reservations': 'unchanged', 'automaticReset': False}}


def apply_shared_allowance(home, action, params):
    from eidolon_cli.organization_shared_allowance import SharedAllowanceError
    if action not in {'configuration', 'observation'}:
        raise ValueError('Unsupported shared allowance action')
    _parameters(params, action, apply=True)
    _envelope(params, apply=True)
    authority, ledger = _context(home, params['expectedOrganizationId'])
    if authority['role'] != 'installation-owner':
        raise SharedAllowanceError('installation_owner_required')
    if authority['rootIdentity']['ownerId'] != params['expectedOwnerId']:
        raise SharedAllowanceError('identity_conflict')
    field = 'observation' if action == 'observation' else 'configuration'
    method = ledger.observe if action == 'observation' else ledger.configure
    receipt = method(params.get(field), expected_revision=params['expectedGeneration'],
                     idempotency_key=params['idempotencyKey'],
                     root_organization_id=params['expectedOrganizationId'],
                     root_owner_id=params['expectedOwnerId'], owner_intent=params)
    # The ledger matches the entire original intent before returning its durable
    # immutable receipt, including after later edits and source changes.
    return {'capabilityVersion': 1, 'receipt': {
        **preview_identity(action, params, params[field]), 'generation': receipt['revision'],
        'idempotencyKey': params['idempotencyKey'], 'previewVersion': params['previewVersion']}}

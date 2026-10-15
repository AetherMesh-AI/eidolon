"""Opt-in installation allowance; conservative debits, never provider quota.

The root owner declares bounded periods and allocation envelopes. Global debit
commits before the caller's local reservation: those databases are NOT atomic.
A failed local commit leaves the global debit intact. Existing debit keys never
authorize replay, and no reconciliation, observation or period reset refunds one.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid


_VERSION = 1
_DIMS = ('modelCalls', 'tokens', 'configuredCostUsd')
_DB = 'shared-allowance.db'
_MARKER = 'shared-allowance.required.json'
_SCHEMA = '''
CREATE TABLE allowance_identity (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity_json TEXT NOT NULL);
CREATE TABLE allowance_configurations (
 revision INTEGER PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
 input_hash TEXT NOT NULL, configuration TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE allowance_debits (
 call_key TEXT PRIMARY KEY, period_id TEXT NOT NULL, profile TEXT NOT NULL,
 organization_id TEXT NOT NULL, receipt TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX allowance_debit_period ON allowance_debits(period_id,profile);
CREATE TABLE allowance_observations (
 revision INTEGER PRIMARY KEY, period_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 input_hash TEXT NOT NULL, observation TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
'''
for _table in ('allowance_identity', 'allowance_configurations', 'allowance_debits', 'allowance_observations'):
    for _operation in ('UPDATE', 'DELETE'):
        _SCHEMA += (f'CREATE TRIGGER {_table}_{_operation.lower()} BEFORE {_operation} ON {_table} '
                    "BEGIN SELECT RAISE(ABORT,'Shared allowance evidence is immutable'); END;\n")


class SharedAllowanceError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f'Shared organization allowance: {reason}')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value, field, maximum=128):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > maximum or any(ord(char) < 32 for char in value)):
        raise ValueError(f'{field} must be bounded nonempty text')
    return value


def _canonical(path):
    path = Path(path).absolute()
    if path != path.resolve():
        raise SharedAllowanceError('noncanonical_path')
    return path


def capture_installation_scope(home, root=None):
    """Use the captured home, never a process-env root under a context override."""
    home = _canonical(home)
    inferred = home.parent.parent if home.parent.name == 'profiles' else home
    root = _canonical(root) if root is not None else inferred
    if root.parent.name == 'profiles':
        raise SharedAllowanceError('invalid_installation_root')
    if home == root:
        profile = 'default'
    elif home.parent == root / 'profiles':
        from eidolon_cli.profiles import validate_profile_name
        validate_profile_name(home.name)
        if home.name == 'default' or (home.parent / '.deleted' / home.name).exists():
            raise SharedAllowanceError('invalid_profile_scope')
        profile = home.name
    else:
        raise SharedAllowanceError('outside_installation')
    _canonical(root / 'organization')
    _canonical(home / 'organization' / 'state.db')
    return {'root': root, 'home': home, 'profile': profile}


def _identity(home, conn=None):
    from eidolon_cli.organization_metadata import MetadataError, _inspect, read_descriptor
    try:
        record = _inspect(conn) if conn is not None else read_descriptor(_canonical(home / 'organization' / 'state.db'))
    except MetadataError as exc:
        raise SharedAllowanceError('organization_identity_' + exc.reason) from None
    if record['state'] != 'ready':
        raise SharedAllowanceError('organization_identity_unavailable')
    value = record['metadata']
    return {'organizationId': value['organizationId'], 'ownerId': value['owner']['ownerId']}


def resolve_authority(home):
    scope = capture_installation_scope(home)
    return {**scope, 'role': 'installation-owner' if scope['home'] == scope['root'] else 'member',
            'rootIdentity': _identity(scope['root']), 'servedIdentity': _identity(scope['home'])}


def _timestamp(value, label):
    from eidolon_cli.organization_pacing import _timestamp as parse
    return parse(value, label)


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _dimensions(value, label, *, positive=False):
    from eidolon_cli.organization_budget import money
    if not isinstance(value, dict) or set(value) != set(_DIMS):
        raise ValueError(f'{label} requires modelCalls, tokens and configuredCostUsd')
    result = {}
    for field in ('modelCalls', 'tokens'):
        upper = 1_000_000 if field == 'modelCalls' else 1_000_000_000_000
        amount = value[field]
        if type(amount) is not int or not int(positive) <= amount <= upper:
            raise ValueError(f'{label}.{field} must be a bounded {"positive" if positive else "nonnegative"} integer')
        result[field] = amount
    result['configuredCostUsd'] = money(value['configuredCostUsd'], label) if value['configuredCostUsd'] is not None else None
    return result


def _envelope(value, label):
    limits = _dimensions(value['limits'], label + '.limits', positive=True)
    protected = _dimensions(value['protected'], label + '.protected')
    for field in _DIMS:
        if protected[field] is not None and (limits[field] is None or Decimal(protected[field]) > Decimal(limits[field])):
            raise ValueError(f'{label} protection cannot exceed its explicit limit')
    return {'limits': limits, 'protected': protected}


def normalize_configuration(configuration):
    from eidolon_cli.profiles import validate_profile_name
    from eidolon_cli.organization_metadata import _uuid
    fields = {'periodId', 'startsAt', 'endsAt', 'limits', 'protected', 'allocations'}
    if not isinstance(configuration, dict) or set(configuration) != fields:
        raise ValueError('Shared allowance configuration has unsupported or missing fields')
    starts = _timestamp(configuration['startsAt'], 'startsAt')
    ends = _timestamp(configuration['endsAt'], 'endsAt')
    if not starts < ends or ends - starts > 366 * 86400:
        raise ValueError('An explicit allowance period must last at most 366 days')
    result = {'periodId': _text(configuration['periodId'], 'periodId'),
              'startsAt': _iso(starts), 'endsAt': _iso(ends), **_envelope(configuration, 'Pool')}
    allocations = configuration['allocations']
    if not isinstance(allocations, list) or not 1 <= len(allocations) <= 64:
        raise ValueError('Declare between one and 64 allocation envelopes')
    result['allocations'], seen, organization_ids = [], set(), set()
    for row in allocations:
        if not isinstance(row, dict) or set(row) != {'profile', 'organizationId', 'ownerId', 'limits', 'protected'}:
            raise ValueError('Each allocation requires exact profile and organization/owner identities and ceilings')
        profile = _text(row['profile'], 'profile', 64)
        validate_profile_name(profile)
        organization_id = _uuid(row['organizationId'], 'organizationId')
        owner_id = _uuid(row['ownerId'], 'ownerId')
        if profile in seen or organization_id in organization_ids:
            raise ValueError('Each profile and organization identity can have one allocation envelope')
        seen.add(profile)
        organization_ids.add(organization_id)
        result['allocations'].append({'profile': profile,
            'organizationId': organization_id, 'ownerId': owner_id, **_envelope(row, 'Allocation')})
    result['allocations'].sort(key=lambda row: row['profile'])
    for field in _DIMS:
        if result['limits'][field] is not None:
            if any(row['limits'][field] is None for row in result['allocations']):
                raise ValueError('Every allocation must carry the pool’s configured-cost ceiling')
            total = sum((Decimal(row['limits'][field]) for row in result['allocations']), Decimal(0))
            if total > Decimal(result['limits'][field]):
                raise ValueError('Allocation envelopes cannot exceed the total pool ceiling')
    return result


def source_revision(revision, configuration, root_identity, observation=None):
    return _hash({'revision': revision, 'configuration': configuration, 'observation': observation, 'rootIdentity': root_identity})


def managed_shared_allowance(root):
    from eidolon_cli import managed_scope
    from eidolon_cli.config_primitives import managed_install_system
    managed_scope.invalidate_managed_cache()
    if managed_install_system(Path(root)) is not None:
        return True
    return any(key == 'organization' or key == 'organization.shared_allowance'
               or key.startswith('organization.shared_allowance.')
               for key in managed_scope.managed_config_keys())


@lru_cache(maxsize=1)
def _expected_schema():
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.executescript(_SCHEMA)
        return dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def _schema_valid(conn):
    actual = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    if actual != _expected_schema():
        raise SharedAllowanceError('unsupported_or_corrupt_schema')


def _payload(value):
    common = {'kind', 'provider', 'model', 'inputLimit', 'outputLimit', 'reservedCostUsd'}
    required = {'objective': {'objectiveId', 'requestId', 'attemptToken', 'callIndex', 'requestType'},
                'owner_chat': {'threadId', 'turnId', 'identityId'},
                'team_setup': {'generationId'},
                'request_intake': {'intakeId'}}
    if not isinstance(value, dict) or value.get('kind') not in required or set(value) != common | required[value['kind']]:
        raise ValueError('A physical call requires the exact bounded objective, owner-chat, team-setup or request-intake descriptor')
    result = dict(value)
    for field in set(value) - {'inputLimit', 'outputLimit', 'reservedCostUsd', 'callIndex'}:
        result[field] = _text(value[field], field, 300)
    for field in ('inputLimit', 'outputLimit'):
        if type(value[field]) is not int or not 1 <= value[field] <= 2_000_000:
            raise ValueError('Physical call token ceilings must be bounded positive integers')
    if value['kind'] == 'objective' and (type(value['callIndex']) is not int or not 1 <= value['callIndex'] <= 1_000_000):
        raise ValueError('Physical call index must be a bounded positive integer')
    from eidolon_cli.organization_budget import money
    if value['reservedCostUsd'] is not None:
        result['reservedCostUsd'] = money(value['reservedCostUsd'], 'reservedCostUsd')
    return result


def physical_call_key(binding, payload):
    payload = _payload(payload)
    fields = {'objective': ('objectiveId', 'requestId', 'attemptToken', 'callIndex'),
              'owner_chat': ('threadId', 'turnId', 'identityId'),
              'team_setup': ('generationId',), 'request_intake': ('intakeId',)}[payload['kind']]
    return 'sharedcall_' + _hash({'home': binding['home'], 'organizationId': binding['organizationId'],
                                'kind': payload['kind'], **{key: payload[key] for key in fields}})


def _receipt(body):
    return {**body, 'sha256': _hash(body)}


def _checked_receipt(value):
    try:
        if not isinstance(value, dict):
            raise ValueError()
        body = {key: data for key, data in value.items() if key != 'sha256'}
        if (set(body) != {'version', 'ledgerId', 'periodId', 'profile', 'organizationId', 'ownerId',
                         'home', 'callKey', 'payload', 'classification', 'reserved', 'revision', 'createdAt'}
                or value.get('sha256') != _hash(body) or body['version'] != _VERSION
                or _payload(body['payload']) != body['payload']
                or type(body['revision']) is not int or body['revision'] < 1):
            raise ValueError()
        from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES
        classification = 'completion' if body['payload']['kind'] == 'objective' and body['payload']['requestType'] in COMPLETION_REQUEST_TYPES else 'ordinary'
        expected = {'modelCalls': 1, 'tokens': body['payload']['inputLimit'] + body['payload']['outputLimit'],
                    'configuredCostUsd': body['payload']['reservedCostUsd']}
        if body['classification'] != classification or body['reserved'] != expected or body['callKey'] != physical_call_key(body, body['payload']):
            raise ValueError()
        _timestamp(body['createdAt'], 'createdAt')
        return value
    except (ValueError, TypeError, KeyError, OverflowError):
        raise SharedAllowanceError('corrupt_debit_receipt') from None


def _projection(envelope, receipts):
    reserved, remaining, protected, ordinary = {}, {}, {}, {}
    for field in _DIMS:
        limit = envelope['limits'][field]
        amounts = [row['reserved'][field] for row in receipts]
        complete = [row['reserved'][field] for row in receipts if row['classification'] == 'completion']
        if any(amount is None for amount in amounts):
            used = None
        else:
            used = sum((Decimal(amount) for amount in amounts), Decimal(0))
        left = max(Decimal(0), Decimal(limit) - used) if limit is not None and used is not None else None
        protected_left = max(Decimal(0), Decimal(envelope['protected'][field] or 0) - sum((Decimal(amount) for amount in complete), Decimal(0))) if left is not None and all(amount is not None for amount in complete) else None
        encode = (lambda value: format(value, 'f')) if field == 'configuredCostUsd' else int
        for target, amount in ((reserved, used), (remaining, left), (protected, protected_left),
                               (ordinary, max(Decimal(0), left - protected_left) if left is not None and protected_left is not None else None)):
            target[field] = encode(amount) if amount is not None else None
    return {'limits': envelope['limits'], 'reserved': reserved, 'remaining': remaining,
            'protectedRemaining': protected, 'ordinaryRemaining': ordinary}


class SharedAllowanceLedger:
    """Construction and read paths never create directories, schema or markers."""
    def __init__(self, root, *, home):
        self.scope = capture_installation_scope(home, root)
        self.root, self.home, self.profile = self.scope['root'], self.scope['home'], self.scope['profile']
        self.path = self.root / 'organization' / _DB
        self.marker = self.root / 'organization' / _MARKER

    def _validate_paths(self):
        if capture_installation_scope(self.home, self.root) != self.scope:
            raise SharedAllowanceError('scope_changed')
        _canonical(self.path)
        _canonical(self.marker)

    def _presence(self, *, required=False):
        self._validate_paths()
        database, marker = self.path.exists(), self.marker.exists()
        if database != marker:
            raise SharedAllowanceError('required_pool_missing')
        if not database and required:
            raise SharedAllowanceError('required_pool_missing')
        return database

    @contextmanager
    def _connection(self, *, write=False, initial_identity=None):
        self._validate_paths()
        if initial_identity is None:
            self._presence(required=True)
        mode = 'rwc' if initial_identity else 'rw' if write else 'ro'
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=' + mode, uri=True,
                                         timeout=10 if write else .25, isolation_level=None)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA foreign_keys=ON')
                if not write:
                    conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                try:
                    if initial_identity:
                        for statement in _schema_statements():
                            conn.execute(statement)
                        conn.execute('INSERT INTO allowance_identity VALUES (1,?)', (_json(initial_identity),))
                    _schema_valid(conn)
                    identity = self._database_identity(conn)
                    yield conn, identity
                    if write:
                        conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        except sqlite3.Error as exc:
            reason = 'locked' if (getattr(exc, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else 'unreadable_or_corrupt'
            raise SharedAllowanceError(reason) from exc

    def _database_identity(self, conn):
        try:
            marker = json.loads(self.marker.read_text(encoding="utf-8"))
            rows = conn.execute('SELECT identity_json FROM allowance_identity').fetchall()
            identity = json.loads(rows[0][0]) if len(rows) == 1 else None
            root_identity = _identity(self.root)
            if (not isinstance(identity, dict) or set(identity) != {'version', 'ledgerId', 'root', 'organizationId', 'ownerId'}
                    or marker != identity or identity['version'] != _VERSION or identity['root'] != str(self.root)
                    or any(identity[key] != value for key, value in root_identity.items())):
                raise ValueError()
            _text(identity['ledgerId'], 'ledgerId')
            return identity
        except (OSError, ValueError, TypeError, IndexError):
            raise SharedAllowanceError('pool_identity_mismatch') from None

    def _current(self, conn):
        row = conn.execute('SELECT * FROM allowance_configurations ORDER BY revision DESC LIMIT 1').fetchone()
        if row is None:
            raise SharedAllowanceError('required_configuration_missing')
        try:
            config = json.loads(row['configuration'])
            if normalize_configuration(config) != config or row['revision'] < 1:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise SharedAllowanceError('corrupt_configuration') from None
        return row['revision'], config

    def _receipts(self, conn, period_id, identity):
        configurations = {}
        for stored in conn.execute('SELECT revision,configuration FROM allowance_configurations'):
            try:
                config = normalize_configuration(json.loads(stored['configuration']))
            except (ValueError, TypeError, KeyError):
                raise SharedAllowanceError('corrupt_configuration') from None
            configurations[stored['revision']] = config
        rows = []
        for row in conn.execute('SELECT * FROM allowance_debits'):
            try:
                receipt = _checked_receipt(json.loads(row['receipt']))
                if (receipt['callKey'] != row['call_key'] or receipt['periodId'] != row['period_id']
                        or receipt['profile'] != row['profile'] or receipt['organizationId'] != row['organization_id']
                        or receipt['ledgerId'] != identity['ledgerId']):
                    raise ValueError()
                config = configurations.get(receipt['revision'])
                member = next((item for item in config['allocations'] if item['profile'] == receipt['profile']), None) if config else None
                expected_home = self.root if receipt['profile'] == 'default' else self.root / 'profiles' / receipt['profile']
                if (member is None or config['periodId'] != receipt['periodId'] or receipt['home'] != str(expected_home)
                        or any(receipt[key] != member[key] for key in ('organizationId', 'ownerId'))):
                    raise ValueError()
                if receipt['periodId'] == period_id:
                    rows.append(receipt)
            except (ValueError, TypeError):
                raise SharedAllowanceError('corrupt_debit_receipt') from None
        return rows

    def _member(self, config, organization_id, *, required=False):
        row = next((row for row in config['allocations'] if row['profile'] == self.profile), None)
        if row is None:
            if organization_id is not None and any(item['organizationId'] == organization_id for item in config['allocations']):
                raise SharedAllowanceError('enrolled_identity_changed')
            if required:
                raise SharedAllowanceError('scope_not_enrolled')
            return None
        identity = _identity(self.home)
        if identity != {key: row[key] for key in ('organizationId', 'ownerId')} or (organization_id is not None and organization_id != identity['organizationId']):
            raise SharedAllowanceError('enrolled_identity_changed')
        return row

    def read(self, organization_id=None, *, required=False):
        if not self._presence(required=required):
            return {'version': 1, 'state': 'unconfigured', 'revision': 0, 'configuration': None,
                    'pool': None, 'member': None, 'observation': None, 'pacing': None}
        with self._connection() as (conn, identity):
            revision, config = self._current(conn)
            member = self._member(config, organization_id, required=required)
            receipts = self._receipts(conn, config['periodId'], identity)
            pool = _projection(config, receipts)
            member_view = _projection(member, [row for row in receipts if row['profile'] == self.profile]) if member else None
            observation = self._observation(conn, config['periodId'])
            return {'version': 1, 'state': 'ready', 'revision': revision,
                    'configuration': config if self.home == self.root else None,
                    'period': {key: config[key] for key in ('periodId', 'startsAt', 'endsAt')},
                    'pool': pool, 'member': member_view, 'observation': observation,
                    'pacing': _pacing(pool, member_view, observation, config, member, receipts)}

    def resolve_binding(self, organization_id, *, required=False):
        if not self._presence(required=required):
            return None
        with self._connection() as (conn, identity):
            _revision, config = self._current(conn)
            member = self._member(config, organization_id, required=required)
            if member is None:
                return None
            return {'version': 1, 'root': str(self.root), 'home': str(self.home), 'profile': self.profile,
                    'ledgerId': identity['ledgerId'], 'periodId': config['periodId'],
                    'organizationId': member['organizationId'], 'ownerId': member['ownerId']}

    def _check_binding(self, binding, config, identity):
        member = self._member(config, binding.get('organizationId'), required=True)
        expected = {'version': 1, 'root': str(self.root), 'home': str(self.home), 'profile': self.profile,
                    'ledgerId': identity['ledgerId'], 'periodId': config['periodId'],
                    'organizationId': member['organizationId'], 'ownerId': member['ownerId']}
        if binding != expected:
            raise SharedAllowanceError('stale_or_invalid_binding')
        now = time.time()
        if not _timestamp(config['startsAt'], 'startsAt') <= now < _timestamp(config['endsAt'], 'endsAt'):
            raise SharedAllowanceError('period_inactive')
        return member

    def reserve_call(self, binding, call_key, payload):
        payload = _payload(payload)
        if call_key != physical_call_key(binding, payload):
            raise SharedAllowanceError('physical_call_key_mismatch')
        with self._connection(write=True) as (conn, identity):
            revision, config = self._current(conn)
            member = self._check_binding(binding, config, identity)
            if conn.execute('SELECT 1 FROM allowance_debits WHERE call_key=?', (call_key,)).fetchone():
                raise SharedAllowanceError('already_debited_no_replay')
            from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES
            classification = 'completion' if payload['kind'] == 'objective' and payload['requestType'] in COMPLETION_REQUEST_TYPES else 'ordinary'
            reserved = {'modelCalls': 1, 'tokens': payload['inputLimit'] + payload['outputLimit'],
                        'configuredCostUsd': payload['reservedCostUsd']}
            receipts = self._receipts(conn, config['periodId'], identity)
            for label, envelope, records in (('pool', config, receipts), ('allocation', member, [row for row in receipts if row['profile'] == self.profile])):
                view = _projection(envelope, records)
                for dimension in _DIMS:
                    if envelope['limits'][dimension] is None:
                        continue
                    remaining = view['ordinaryRemaining' if classification == 'ordinary' else 'remaining'][dimension]
                    if reserved[dimension] is None or remaining is None:
                        raise SharedAllowanceError(label + '_configured_cost_unknown')
                    if Decimal(reserved[dimension]) > Decimal(remaining):
                        raise SharedAllowanceError(label + '_protected_or_exhausted_' + dimension)
            pacing = _pacing(_projection(config, receipts),
                _projection(member, [row for row in receipts if row['profile'] == self.profile]),
                self._observation(conn, config['periodId']), config, member, receipts)
            stage = _stage_key({'profile': self.profile, 'payload': payload})
            admitted = any(_stage_key(row) == stage for row in receipts)
            if classification == 'ordinary' and not admitted and pacing['deferUntil'] is not None:
                from eidolon_cli.organization_pacing import OrganizationPacingDeferred
                raise OrganizationPacingDeferred('Shared allowance pacing is waiting before the next ordinary model-backed stage.',
                                                _timestamp(pacing['deferUntil'], 'deferUntil'))
            body = {'version': 1, 'ledgerId': identity['ledgerId'], 'periodId': config['periodId'],
                    'profile': self.profile, 'organizationId': member['organizationId'], 'ownerId': member['ownerId'],
                    'home': str(self.home), 'callKey': call_key, 'payload': payload, 'classification': classification,
                    'reserved': reserved, 'revision': revision, 'createdAt': _iso(time.time())}
            receipt = _receipt(body)
            conn.execute('INSERT INTO allowance_debits VALUES (?,?,?,?,?,?)',
                         (call_key, config['periodId'], self.profile, member['organizationId'], _json(receipt), time.time()))
            return receipt

    def verify_receipt(self, binding, receipt, call_key, payload):
        payload = _payload(payload)
        _checked_receipt(receipt)
        with self._connection() as (conn, identity):
            revision, config = self._current(conn)
            self._check_binding(binding, config, identity)
            row = conn.execute('SELECT receipt FROM allowance_debits WHERE call_key=?', (call_key,)).fetchone()
            if (row is None or json.loads(row[0]) != receipt or receipt['payload'] != payload
                    or receipt['revision'] != revision or receipt['callKey'] != call_key
                    or receipt['callKey'] != physical_call_key(binding, payload)
                    or receipt['home'] != str(self.home)):
                raise SharedAllowanceError('matching_immutable_receipt_required')
            return receipt

    def _owner(self, organization_id, owner_id):
        if self.home != self.root:
            raise SharedAllowanceError('installation_owner_required')
        identity = _identity(self.root)
        if identity != {'organizationId': organization_id, 'ownerId': owner_id}:
            raise SharedAllowanceError('root_identity_changed')
        return identity

    @contextmanager
    def _scope_writers(self, profiles):
        """Same local→global order as dispatch; never wait for a live OS fence."""
        from eidolon_cli.organization_service import _ExecutionLock
        from eidolon_cli.organization_owner_chat import LIVE
        from eidolon_cli.organization_observation import _compatible_schema
        with ExitStack() as stack:
            scopes = {}
            for profile in sorted(set(profiles) | {'default'}):
                home = self.root if profile == 'default' else self.root / 'profiles' / profile
                capture_installation_scope(home, self.root)
                path = _canonical(home / 'organization' / 'state.db')
                try:
                    conn = stack.enter_context(closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10, isolation_level=None)))
                    conn.row_factory = sqlite3.Row
                    conn.execute('BEGIN IMMEDIATE')
                except sqlite3.Error as exc:
                    raise SharedAllowanceError('scope_unavailable_or_busy') from exc
                scopes[profile] = _identity(home, conn)
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'organization_policy' not in tables or not _compatible_schema(conn, tables):
                    raise SharedAllowanceError('scope_execution_not_initialized')
                if conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone() is None:
                    raise SharedAllowanceError('scope_execution_not_initialized')
                live = ','.join("'" + status + "'" for status in sorted(LIVE))
                for table, predicate in (('requests', "status='running'"),
                                         ('owner_chat_turns', f'status IN ({live})'),
                                         ('organization_team_generations', "status IN ('running','uncertain')"),
                                         ('organization_request_intakes', "status IN ('pending','uncertain')")):
                    if table in tables and conn.execute(f'SELECT 1 FROM {table} WHERE {predicate} LIMIT 1').fetchone():
                        raise SharedAllowanceError('scope_execution_active')
                for directory, keys in (('capacity-locks', [str(index) for index in range(4)]),
                                        ('owner-chat-locks', ['profile-slot-0', 'profile-slot-1'])):
                    _canonical(home / 'organization' / directory)
                    for key in keys:
                        if not stack.enter_context(_ExecutionLock(home / 'organization' / directory, key)):
                            raise SharedAllowanceError('scope_execution_still_stopping')
            yield scopes

    def _replay(self, conn, table, key, digest):
        row = conn.execute(f'SELECT * FROM {table} WHERE idempotency_key=?', (key,)).fetchone()
        if row is None:
            return None
        if row['input_hash'] != digest:
            raise SharedAllowanceError('idempotency_conflict')
        try:
            result = json.loads(row['result'])
            fields = {'revision', 'periodId', 'idempotencyKey'}
            if table == 'allowance_observations':
                fields.add('observationRevision')
            if (not isinstance(result, dict) or set(result) != fields or result['idempotencyKey'] != key
                    or type(result['revision']) is not int or result['revision'] < 1):
                raise ValueError()
            if table == 'allowance_configurations':
                config = normalize_configuration(json.loads(row['configuration']))
                if result['revision'] != row['revision'] or result['periodId'] != config['periodId']:
                    raise ValueError()
            else:
                normalize_observation(json.loads(row['observation']))
                config_row = conn.execute('SELECT configuration FROM allowance_configurations WHERE revision=?', (result['revision'],)).fetchone()
                if (result['observationRevision'] != row['revision'] or result['periodId'] != row['period_id']
                        or config_row is None or json.loads(config_row[0])['periodId'] != result['periodId']):
                    raise ValueError()
            return result
        except (ValueError, TypeError, KeyError):
            raise SharedAllowanceError('corrupt_owner_receipt') from None

    def _review(self, state, identity, action, candidate, owner_intent):
        if managed_shared_allowance(self.root):
            raise SharedAllowanceError('managed_configuration')
        if owner_intent is not None:
            from eidolon_cli.organization_shared_allowance_controls import validate_owner_intent
            validate_owner_intent(state, identity, action, owner_intent, candidate)

    def _owner_state(self, conn, revision, config):
        return {'revision': revision, 'configuration': config,
                'observation': self._observation(conn, config['periodId']) if config else None}

    def configure(self, configuration, *, expected_revision, idempotency_key,
                  root_organization_id, root_owner_id, owner_intent=None, expected_source_revision=None):
        identity = self._owner(root_organization_id, root_owner_id)
        configuration = normalize_configuration(configuration)
        key = _text(idempotency_key, 'idempotencyKey', 200)
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63:
            raise ValueError('expected_revision must be a nonnegative integer')
        digest = _hash({'configuration': configuration, 'expectedRevision': expected_revision,
                        'sourceRevision': expected_source_revision, 'ownerIntent': owner_intent})
        exists = self._presence()
        old = None
        if exists:
            with self._connection() as (conn, _):
                replay = self._replay(conn, 'allowance_configurations', key, digest)
                if replay:
                    return replay
                _, old = self._current(conn)
        state = self.read()
        self._review(state, identity, 'configuration', configuration, owner_intent)
        if state['revision'] != expected_revision:
            raise SharedAllowanceError('revision_conflict')
        if expected_source_revision is not None and expected_source_revision != source_revision(state['revision'], state['configuration'], identity, state['observation']):
            raise SharedAllowanceError('source_revision_conflict')
        if _timestamp(configuration['endsAt'], 'endsAt') <= time.time():
            raise SharedAllowanceError('new_period_must_not_be_expired')
        profiles = [row['profile'] for row in configuration['allocations']]
        profiles += [row['profile'] for row in old['allocations']] if old else []
        with self._scope_writers(profiles) as scopes:
            self._owner(root_organization_id, root_owner_id)
            for row in configuration['allocations']:
                if scopes[row['profile']] != {field: row[field] for field in ('organizationId', 'ownerId')}:
                    raise SharedAllowanceError('allocation_identity_changed')
            exists = self._presence()
            initial = None
            if not exists:
                if expected_revision != 0:
                    raise SharedAllowanceError('revision_conflict')
                if expected_source_revision is not None and expected_source_revision != source_revision(0, None, identity):
                    raise SharedAllowanceError('source_revision_conflict')
                initial = {'version': 1, 'ledgerId': str(uuid.uuid4()), 'root': str(self.root), **identity}
                # This durable marker is deliberately first. An interrupted initial
                # activation is unavailable, never silently treated as opted out.
                with self.marker.open('x', encoding='utf-8') as marker:
                    marker.write(_json(initial))
                    marker.flush()
                    os.fsync(marker.fileno())
            with self._connection(write=True, initial_identity=initial) as (conn, pool_identity):
                replay = self._replay(conn, 'allowance_configurations', key, digest)
                if replay:
                    return replay
                revision, previous = (0, None) if initial else self._current(conn)
                state = self._owner_state(conn, revision, previous)
                self._review(state, identity, 'configuration', configuration, owner_intent)
                if revision != expected_revision:
                    raise SharedAllowanceError('revision_conflict')
                if expected_source_revision is not None and expected_source_revision != source_revision(revision, previous, identity, state['observation']):
                    raise SharedAllowanceError('source_revision_conflict')
                self._validate_configuration_change(conn, configuration, previous, pool_identity)
                result = {'revision': revision + 1, 'periodId': configuration['periodId'], 'idempotencyKey': key}
                conn.execute('INSERT INTO allowance_configurations VALUES (?,?,?,?,?,?)',
                             (revision + 1, key, digest, _json(configuration), _json(result), time.time()))
                if managed_shared_allowance(self.root):
                    raise SharedAllowanceError('managed_configuration')
                return result

    def _validate_configuration_change(self, conn, current, previous, identity):
        now = time.time()
        if _timestamp(current['endsAt'], 'endsAt') <= now:
            raise SharedAllowanceError('new_period_must_not_be_expired')
        if previous:
            old_profiles = {row['profile']: row for row in previous['allocations']}
            new_profiles = {row['profile']: row for row in current['allocations']}
            if not old_profiles.keys() <= new_profiles.keys():
                raise SharedAllowanceError('enrollment_cannot_be_removed')
            for profile, old in old_profiles.items():
                if any(new_profiles[profile][key] != old[key] for key in ('organizationId', 'ownerId')):
                    raise SharedAllowanceError('enrollment_identity_immutable')
            if current['periodId'] == previous['periodId']:
                if any(current[key] != previous[key] for key in ('startsAt', 'endsAt')):
                    raise SharedAllowanceError('period_bounds_immutable')
            else:
                end = _timestamp(previous['endsAt'], 'endsAt')
                if now < end or _timestamp(current['startsAt'], 'startsAt') < end:
                    raise SharedAllowanceError('previous_period_not_closed')
                for row in conn.execute('SELECT configuration FROM allowance_configurations'):
                    if json.loads(row[0])['periodId'] == current['periodId']:
                        raise SharedAllowanceError('period_id_cannot_be_reused')
        receipts = self._receipts(conn, current['periodId'], identity)
        for envelope, records in [(current, receipts), *[(member, [row for row in receipts if row['profile'] == member['profile']]) for member in current['allocations']]]:
            view = _projection(envelope, records)
            for field in _DIMS:
                if envelope['limits'][field] is not None and (view['reserved'][field] is None or Decimal(view['reserved'][field]) > Decimal(envelope['limits'][field])):
                    raise SharedAllowanceError('new_ceiling_below_retained_debits')

    def _observation(self, conn, period_id):
        row = conn.execute('SELECT revision,observation FROM allowance_observations WHERE period_id=? ORDER BY revision DESC LIMIT 1', (period_id,)).fetchone()
        if row is None:
            return None
        try:
            value = normalize_observation(json.loads(row['observation']))
            now = time.time()
            status = 'reset_unconfirmed' if value['resetAt'] is not None and now >= _timestamp(value['resetAt'], 'resetAt') else 'expired' if now >= _timestamp(value['expiresAt'], 'expiresAt') else 'fresh'
            return {**value, 'revision': row['revision'], 'status': status, 'source': 'owner_manual',
                    'scope': 'installation', 'advisory': True}
        except (ValueError, TypeError, KeyError):
            raise SharedAllowanceError('corrupt_observation') from None

    def observe(self, observation, *, expected_revision, idempotency_key, root_organization_id,
                root_owner_id, owner_intent=None, expected_source_revision=None):
        identity = self._owner(root_organization_id, root_owner_id)
        observation = normalize_observation(observation)
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63:
            raise ValueError('expected_revision must be a bounded nonnegative integer')
        key = _text(idempotency_key, 'idempotencyKey', 200)
        digest = _hash({'observation': observation, 'expectedRevision': expected_revision,
                        'sourceRevision': expected_source_revision, 'ownerIntent': owner_intent})
        with self._connection(write=True) as (conn, _):
            replay = self._replay(conn, 'allowance_observations', key, digest)
            if replay:
                return replay
            revision, config = self._current(conn)
            state = self._owner_state(conn, revision, config)
            self._review(state, identity, 'observation', observation, owner_intent)
            if expected_revision != revision:
                raise SharedAllowanceError('revision_conflict')
            if expected_source_revision is not None and expected_source_revision != source_revision(revision, config, identity, state['observation']):
                raise SharedAllowanceError('source_revision_conflict')
            now = time.time()
            if _timestamp(observation['asOf'], 'asOf') > now or _timestamp(observation['expiresAt'], 'expiresAt') <= now or (observation['resetAt'] is not None and _timestamp(observation['resetAt'], 'resetAt') <= now):
                raise ValueError('New owner observations must be fresh, never future-dated')
            previous = self._observation(conn, config['periodId'])
            if previous and _timestamp(observation['asOf'], 'asOf') < _timestamp(previous['asOf'], 'asOf'):
                raise ValueError('A replacement observation cannot precede the latest as-of time')
            number = conn.execute('SELECT coalesce(max(revision),0)+1 FROM allowance_observations').fetchone()[0]
            result = {'revision': revision + 1, 'observationRevision': number, 'periodId': config['periodId'], 'idempotencyKey': key}
            conn.execute('INSERT INTO allowance_observations VALUES (?,?,?,?,?,?,?)',
                         (number, config['periodId'], key, digest, _json(observation), _json(result), now))
            config_key = 'observation:' + _hash(key)
            config_receipt = {'revision': revision + 1, 'periodId': config['periodId'], 'idempotencyKey': config_key}
            conn.execute('INSERT INTO allowance_configurations VALUES (?,?,?,?,?,?)',
                         (revision + 1, config_key, digest, _json(config), _json(config_receipt), now))
            if managed_shared_allowance(self.root):
                raise SharedAllowanceError('managed_configuration')
            return result


def _schema_statements():
    # Trigger bodies contain semicolons, so split only complete SQLite statements.
    pending = ''
    for line in _SCHEMA.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''


def normalize_observation(value):
    if not isinstance(value, dict) or set(value) != {'remainingPercent', 'asOf', 'expiresAt', 'resetAt'}:
        raise ValueError('Observation requires remainingPercent, asOf, expiresAt and resetAt')
    percent = value['remainingPercent']
    if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError('Remaining percent must be finite and between 0 and 100')
    start, expiry = _timestamp(value['asOf'], 'asOf'), _timestamp(value['expiresAt'], 'expiresAt')
    reset = _timestamp(value['resetAt'], 'resetAt') if value['resetAt'] is not None else None
    if not start < expiry <= start + 604800 or (reset is not None and not start <= reset <= start + 604800):
        raise ValueError('Observation expiry and expected reset must be within seven days of as-of')
    return {'remainingPercent': percent, 'asOf': _iso(start), 'expiresAt': _iso(expiry), 'resetAt': _iso(reset) if reset is not None else None}


def _stage_key(receipt):
    payload = receipt['payload']
    fields = {'objective': ('objectiveId', 'requestId', 'attemptToken'),
              'owner_chat': ('threadId', 'turnId', 'identityId'),
              'team_setup': ('generationId',), 'request_intake': ('intakeId',)}[payload['kind']]
    return (receipt['profile'], payload['kind'], *(payload[key] for key in fields))


def _pacing(pool, member, observation, config, member_envelope=None, receipts=()):
    percentages = []
    for projection, envelope in ((pool, config), (member, member_envelope)):
        if projection is None or envelope is None:
            continue
        for field in _DIMS:
            remaining = projection['ordinaryRemaining'][field]
            limit = projection['limits'][field]
            if limit is not None and remaining is not None:
                capacity = Decimal(limit) - Decimal(envelope['protected'][field] or 0)
                percentages.append(float(Decimal(remaining) * 100 / capacity) if capacity else 0)
    percent = min(percentages) if percentages else None
    reason = 'shared_headroom'
    if observation is not None and (percent is None or observation['remainingPercent'] <= percent):
        percent = observation['remainingPercent']
        reason = 'manual_observation' if observation['status'] == 'fresh' else 'stale_observation'
    mode = 'unknown' if percent is None else 'critical' if percent <= 10 else 'conserve' if percent <= 30 else 'normal'
    interval = {'critical': 300, 'conserve': 60}.get(mode, 0)
    starts = {}
    for row in receipts:
        if row['classification'] == 'ordinary':
            stage = _stage_key(row)
            started = _timestamp(row['createdAt'], 'createdAt')
            starts[stage] = min(starts.get(stage, started), started)
    latest = max(starts.values(), default=None)
    until = latest + interval if latest is not None and interval else None
    defer = _iso(until) if until is not None and until > time.time() else None
    return {'policy': 'shared-stage-start-v1', 'enabled': True, 'scope': 'installation', 'mode': mode,
            'remainingPercent': percent, 'reason': reason, 'minIntervalSeconds': interval,
            'deferUntil': defer, 'advisory': False, 'globalInflightLimit': None,
            'limitation': 'stage_start_spacing_only_no_provider_quota_inference_or_automatic_refill'}


def resolve_binding(home, organization_id, *, required=False):
    scope = capture_installation_scope(home)
    return SharedAllowanceLedger(scope['root'], home=scope['home']).resolve_binding(organization_id, required=required)


def debit(binding, call_key, payload):
    return SharedAllowanceLedger(binding['root'], home=binding['home']).reserve_call(binding, call_key, payload)


def verify_receipt(binding, receipt, call_key, payload):
    return SharedAllowanceLedger(binding['root'], home=binding['home']).verify_receipt(binding, receipt, call_key, payload)


def read_member_projection(home, organization_id, *, required=False):
    scope = capture_installation_scope(home)
    return SharedAllowanceLedger(scope['root'], home=scope['home']).read(organization_id, required=required)

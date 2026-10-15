"""Served-owner policy review and atomic, ledger-only changes.

Identity is a precondition, never a routing key. Read/preview do not construct a
store, provision identity, adopt policy, discover providers or start execution.
"""
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eidolon_cli.organization_config import OrganizationSettings, profile_configuration_scope
from eidolon_cli.organization_metadata import _inspect, _uuid, _text
from eidolon_cli.organization_observation import _compatible_schema
from eidolon_cli.organization_owner_policy_config import (
    BUDGET_FIELDS, EDITABLE_FIELDS, OWNER_POLICY_DDL, managed_fields, validated_patch,
)
from eidolon_cli.organization_model_routing import ROUTING_FIELDS, _json
from eidolon_cli.organization_policy import resolve_settings


class OwnerPolicyError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f'Organization owner policy: {reason}')


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identity(conn, expected_id):
    descriptor = _inspect(conn)
    if descriptor['state'] != 'ready':
        raise OwnerPolicyError('identity_required' if descriptor['state'] == 'unconfigured' else 'unsupported')
    record = descriptor['metadata']
    if record['organizationId'] != expected_id:
        raise OwnerPolicyError('identity_conflict')
    return record


def _schema(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'organization_policy' not in tables:
        raise OwnerPolicyError('execution_not_initialized')
    if not _compatible_schema(conn, tables):
        raise OwnerPolicyError('unsupported')
    from eidolon_cli.organization_owner_policy_config import OWNER_POLICY_TABLES
    present = tables & OWNER_POLICY_TABLES
    if present:
        if present != OWNER_POLICY_TABLES:
            raise OwnerPolicyError('unsupported')
        def normalized(sql):
            return ''.join(sql.lower().replace('if not exists', '').split()).rstrip(';')
        for sql in OWNER_POLICY_DDL:
            name = sql.split('IF NOT EXISTS ', 1)[1].split()[0]
            row = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
            if row is None or normalized(row['sql']) != normalized(sql):
                raise OwnerPolicyError('unsupported')
    row = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone()
    if row is None or type(row['generation']) is not int or row['generation'] < 1:
        raise OwnerPolicyError('corrupt')
    return row


@contextmanager
def _connection(home, *, write=False):
    path = Path(home) / 'organization' / 'state.db'
    if path.resolve() != path.absolute():
        raise OwnerPolicyError('identity_conflict')
    if not path.exists():
        raise OwnerPolicyError('unconfigured')
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                     uri=True, timeout=5 if write else .25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON' if write else 'PRAGMA query_only=ON')
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield conn
                if write:
                    conn.commit()
            except BaseException:
                if write:
                    conn.rollback()
                raise
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', 0) & 255
        raise OwnerPolicyError({sqlite3.SQLITE_BUSY: 'locked', sqlite3.SQLITE_LOCKED: 'locked',
            sqlite3.SQLITE_CORRUPT: 'corrupt', sqlite3.SQLITE_NOTADB: 'corrupt'}.get(code, 'unreadable')) from None


def _source(home, conn):
    from eidolon_cli.organization_project_setup import _config_content
    from eidolon_cli.config_primitives import _CONFIG_LOCK, _expand_env_vars, _merge_managed_overlay
    with _CONFIG_LOCK, profile_configuration_scope(home):
        locked = managed_fields()
        content, raw = _config_content(Path(home) / 'config.yaml')
        expanded, _ = _merge_managed_overlay(_expand_env_vars({'organization': raw.get('organization', {})}))
        settings = resolve_settings(conn, OrganizationSettings.from_config(expanded))
        # All file bytes catch edits outside the organization subtree too; the
        # effective projection catches expansion/managed/ledger policy changes.
        revision = _hash([hashlib.sha256(content).hexdigest(), asdict(settings), sorted(locked)])
        return settings, revision, locked


def _configuration(settings):
    raw = json.loads(json.dumps(asdict(settings)))
    return {key: raw[key] for key in EDITABLE_FIELDS}


def _open_objectives(conn):
    return conn.execute("SELECT o.id FROM objectives o JOIN objective_control c ON c.objective_id=o.id "
        "LEFT JOIN objective_history h ON h.objective_id=o.id WHERE o.cancelled=0 "
        "AND c.status NOT IN ('accepted','legacy_completed') AND coalesce(h.archived,0)=0 ORDER BY o.created,o.id").fetchall()


def owner_policy(home, expected_id):
    _uuid(expected_id, 'expectedOrganizationId')
    try:
        with _connection(home) as conn:
            identity = _identity(conn, expected_id)
            policy = _schema(conn)
            settings, revision, locked = _source(home, conn)
            from eidolon_cli.organization_pacing import _observation
            observations = []
            for row in _open_objectives(conn):
                observation = _observation(conn, row['id'], time.time())
                observations.append({'objectiveId': row['id'], 'revision': observation['revision'] if observation else 0,
                                     'observation': observation})
            return {'capabilityVersion': 1, 'state': 'ready',
                'organizationId': identity['organizationId'], 'ownerId': identity['owner']['ownerId'],
                'policyGeneration': policy['generation'], 'sourceRevision': revision,
                'configuration': _configuration(settings), 'managedFields': sorted(locked),
                'capabilities': {'modelRouting': True, 'budgetPolicy': True, 'budgetObservation': True},
                'agents': [dict(row) for row in conn.execute('SELECT id,name,role FROM agents ORDER BY id')],
                'observations': observations}
    except OwnerPolicyError as exc:
        if exc.reason not in {'unconfigured', 'identity_required', 'execution_not_initialized', 'unsupported'}:
            raise
        return {'capabilityVersion': 1, 'state': 'unsupported' if exc.reason == 'unsupported' else 'unconfigured',
                'reason': exc.reason,
                'setup': {'method': 'organization.snapshot'} if exc.reason == 'execution_not_initialized' else None,
                'capabilities': {'modelRouting': False, 'budgetPolicy': False, 'budgetObservation': False}}


def _envelope(params):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    generation = params.get('expectedGeneration')
    if type(generation) is not int or not 1 <= generation < 2**63:
        raise ValueError('expectedGeneration must be a positive integer')
    revision = params.get('sourceRevision')
    if not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('sourceRevision requires the reviewed source version')


def _validate_routing(conn, settings, configuration, locked):
    if not isinstance(configuration, dict) or set(configuration) != set(ROUTING_FIELDS):
        raise ValueError('Routing configuration requires exactly model_routes and model_assignments')
    updated = validated_patch(settings, configuration)
    canonical = _configuration(updated)
    before = _configuration(settings)
    if set(ROUTING_FIELDS) <= locked or any(canonical[key] != before[key] for key in set(ROUTING_FIELDS) & locked):
        raise OwnerPolicyError('managed_configuration')
    known = {row[0] for row in conn.execute('SELECT id FROM agents')}
    if any(row.agent_id not in known for row in updated.model_assignments):
        raise ValueError('Model assignments must identify existing persistent agents')
    priced = {(row.provider, row.model) for row in settings.model_costs}
    routes = {row.id: row for row in updated.model_routes}
    for assignment in updated.model_assignments:
        if assignment.mode == 'auto' and any((routes[key].provider, routes[key].model) not in priced for key in assignment.route_ids):
            raise ValueError('Save exact-route prices separately before enabling Auto assignments')
    return {key: canonical[key] for key in ROUTING_FIELDS}


def _validate_budget(settings, configuration, locked):
    if not isinstance(configuration, dict) or not configuration or set(configuration) - set(BUDGET_FIELDS):
        raise ValueError('Budget policy requires a nonempty allowlisted configuration patch')
    if set(configuration) & locked:
        raise OwnerPolicyError('managed_configuration')
    updated = validated_patch(settings, configuration)
    raw = _configuration(updated)
    return {key: raw[key] for key in configuration}


def _validate_observation(conn, observation):
    from eidolon_cli.organization_pacing import validate_observation_input, validate_observation_current
    from eidolon_cli.organization_store import _iso
    fields = {'objectiveId', 'expectedRevision', 'remainingPercent', 'asOf', 'expiresAt', 'resetAt'}
    if not isinstance(observation, dict) or set(observation) != fields:
        raise ValueError('Observation requires objectiveId, expectedRevision, remainingPercent, asOf, expiresAt and resetAt')
    values = validate_observation_input(observation['objectiveId'], remaining_percent=observation['remainingPercent'],
        as_of=observation['asOf'], expires_at=observation['expiresAt'], reset_at=observation['resetAt'],
        expected_revision=observation['expectedRevision'])
    validate_observation_current(conn, values)
    objective, percent, observed, expiry, reset, revision = values
    return {'objectiveId': objective, 'remainingPercent': percent, 'asOf': _iso(observed),
            'expiresAt': _iso(expiry), 'resetAt': _iso(reset), 'expectedRevision': revision}


def _preview(conn, home, action, params, identity, policy):
    if params['expectedGeneration'] != policy['generation']:
        raise OwnerPolicyError('generation_conflict')
    settings, revision, locked = _source(home, conn)
    if params['sourceRevision'] != revision:
        raise OwnerPolicyError('source_conflict')
    validators = {
        'modelRouting': lambda: _validate_routing(conn, settings, params.get('configuration'), locked),
        'budgetPolicy': lambda: _validate_budget(settings, params.get('configuration'), locked),
        'budgetObservation': lambda: _validate_observation(conn, params.get('observation')),
    }
    canonical = validators[action]()
    field = 'observation' if action == 'budgetObservation' else 'configuration'
    core = {'action': action, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'policyGeneration': policy['generation'],
            'sourceRevision': revision, field: canonical}
    before = _configuration(settings)
    from eidolon_cli.organization_pacing import _observation
    changes = ([{'field': key, 'before': before[key], 'after': value} for key, value in canonical.items()
                if before[key] != value] if field == 'configuration' else
               [{'field': 'observation', 'before': _observation(conn, canonical['objectiveId'], time.time()), 'after': canonical}])
    from eidolon_cli.organization_store import _iso
    scope = {'budgetPolicy': 'objective-defaults', 'modelRouting': 'persistent-agent-routing',
             'budgetObservation': 'objective-advisory'}[action]
    codes = {'budgetPolicy': ['no_topup', 'captured_reserves_unchanged', 'configured_prices_not_invoice'],
             'modelRouting': ['owner_declared_routes', 'direct_chat_only', 'owner_chat_unchanged'],
             'budgetObservation': ['objective_advisory_only', 'no_reset_or_refill']}[action]
    running = conn.execute("SELECT count(*) FROM requests WHERE status='running'").fetchone()[0]
    impact = {'scope': scope, 'evaluatedAt': _iso(time.time()), 'activeObjectiveCount': len(_open_objectives(conn)),
              'runningAttempts': 'unchanged' if action == 'budgetObservation' else 'fenced-to-owner-review',
              'runningAttemptCount': running,
              'existingObjectives': 'may-tighten-never-refill' if action == 'budgetPolicy' else 'advisory-only' if action == 'budgetObservation' else 'new-attempts',
              'futureObjectives': 'new-defaults' if action == 'budgetPolicy' else 'unchanged',
              'reservations': 'unchanged', 'completionReserves': 'captured-unchanged',
              'usageProjection': 'not-estimated'}
    warnings = [{'code': code, 'values': {}} for code in codes]
    if action != 'budgetObservation':
        warnings.append({'code': 'running_attempts_fenced', 'values': {'activeAttempts': running}})
        if not changes:
            warnings.append({'code': 'unchanged_values_pinned', 'values': {'fields': sorted(canonical)}})
    if action == 'budgetPolicy':
        from decimal import Decimal
        ceiling_fields = {'max_model_calls', 'max_total_tokens', 'max_cost_usd', 'objective_timeout_seconds'}
        tightened, relaxed, future = [], [], []
        for change in changes:
            field, old, new = change['field'], change['before'], change['after']
            if field in ceiling_fields:
                (tightened if new is not None and (old is None or Decimal(new) < Decimal(old)) else relaxed).append(field)
            elif field.startswith('completion_reserve_'):
                future.append(field)
        for code, fields in (('existing_caps_may_tighten', tightened),
                             ('captured_caps_still_limit_increase', relaxed), ('future_defaults_only', future)):
            if fields:
                warnings.append({'code': code, 'values': {'fields': sorted(fields)}})
    return ({'capabilityVersion': 1, **{key: value for key, value in core.items() if key != 'action'},
             'previewVersion': _hash(core), 'changes': changes, 'impact': impact,
             'warnings': warnings}, settings)


def preview_policy(home, action, params):
    _envelope(params)
    with _connection(home) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        policy = _schema(conn)
        return _preview(conn, home, action, params, identity, policy)[0]


def _writer(conn, home, settings, policy):
    from eidolon_cli.organization_store import OrganizationStore
    class TransactionStore(OrganizationStore):
        def __init__(self):
            self.path = Path(home) / 'organization' / 'state.db'
            self.settings = settings
            self._policy_generation = policy['generation']
            self._policy_fingerprint = policy['fingerprint']

        @contextmanager
        def _write(self):
            yield conn

        def _connect(self):
            raise RuntimeError('Owner policy may use only its existing ledger transaction')
    return TransactionStore()


def _apply_candidate(conn, store, action, canonical, key):
    if action == 'modelRouting':
        store.configure_model_routing(canonical, expected_generation=store._policy_generation,
                                      idempotency_key='owner-policy:' + _hash(key))
        # An explicit no-op is still a reviewed owner decision and generation.
    elif action == 'budgetPolicy':
        row = conn.execute('SELECT configuration FROM organization_owner_budget_policy WHERE id=1').fetchone()
        override = json.loads(row[0]) if row else {}
        override.update(canonical)
        conn.execute('INSERT INTO organization_owner_budget_policy VALUES (1,?) ON CONFLICT(id) DO UPDATE SET configuration=excluded.configuration', (_json(override),))
        store.settings = validated_patch(store.settings, canonical)
    else:
        return store.record_budget_observation(canonical['objectiveId'],
            remaining_percent=canonical['remainingPercent'], as_of=canonical['asOf'],
            expires_at=canonical['expiresAt'], reset_at=canonical['resetAt'],
            expected_revision=canonical['expectedRevision'], idempotency_key='owner-policy:' + _hash(key))['revision']
    return None


def _replay(conn, key, digest, identity, action, params):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_owner_policy_receipts'").fetchone():
        return None
    row = conn.execute('SELECT * FROM organization_owner_policy_receipts WHERE idempotency_key=?', (key,)).fetchone()
    if row is None:
        return None
    if row['organization_id'] != identity['organizationId'] or row['owner_id'] != identity['owner']['ownerId']:
        raise OwnerPolicyError('identity_conflict')
    if row['input_hash'] != digest:
        raise OwnerPolicyError('idempotency_conflict')
    try:
        result = json.loads(row['result_json'])
        receipt = result['receipt']
        field = 'observation' if action == 'budgetObservation' else 'configuration'
        keys = {'idempotencyKey', 'organizationId', 'ownerId', 'action', 'policyGeneration',
                'sourceRevision', 'previewVersion', field}
        if action == 'budgetObservation':
            keys.add('observationRevision')
        expected_generation = params['expectedGeneration'] + int(action != 'budgetObservation')
        if (not isinstance(result, dict) or set(result) != {'capabilityVersion', 'receipt'}
                or type(result['capabilityVersion']) is not int or result['capabilityVersion'] != 1
                or not isinstance(receipt, dict) or set(receipt) != keys
                or receipt['idempotencyKey'] != key or receipt['action'] != action
                or type(receipt['policyGeneration']) is not int or receipt['policyGeneration'] != expected_generation
                or receipt['sourceRevision'] != params['sourceRevision']
                or receipt['previewVersion'] != params['previewVersion']
                or _json(receipt[field]) != _json(params[field])
                or receipt['organizationId'] != row['organization_id'] or receipt['ownerId'] != row['owner_id']):
            raise ValueError()
        if action == 'budgetObservation' and (type(receipt['observationRevision']) is not int
                or receipt['observationRevision'] != params[field]['expectedRevision'] + 1):
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError):
        raise OwnerPolicyError('corrupt') from None


def apply_policy(home, action, params):
    _envelope(params)
    if params.get('confirmed') is not True:
        raise ValueError('Review and confirm this owner policy change before applying')
    key = _text(params.get('idempotencyKey'), 'idempotencyKey', 128)
    version = params.get('previewVersion')
    if not isinstance(version, str) or len(version) != 64:
        raise ValueError('previewVersion requires the reviewed candidate')
    digest = _hash({'action': action, 'params': params})
    with _connection(home, write=True) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        previous = _replay(conn, key, digest, identity, action, params)
        if previous is not None:
            return previous
        policy = _schema(conn)
        if (action != 'budgetObservation'
                and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_team_generations'").fetchone()
                and conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') LIMIT 1").fetchone()):
            raise OwnerPolicyError('team_generation_in_progress_or_uncertain')
        preview, settings = _preview(conn, home, action, params, identity, policy)
        from eidolon_cli.organization_policy import persisted_settings
        retained = persisted_settings(conn)
        reviewed_fields = set(preview.get('configuration', {}))
        if retained is None or any(getattr(settings, key) != getattr(retained, key)
                                   for key in OrganizationSettings.__dataclass_fields__ if key not in reviewed_fields):
            # A narrow editor cannot approve unrelated source grants, staffing
            # or operational settings. Their established reload owns adoption.
            raise OwnerPolicyError('source_policy_conflict')
        if version != preview['previewVersion']:
            raise OwnerPolicyError('preview_conflict')
        field = 'observation' if action == 'budgetObservation' else 'configuration'
        # Only the exact normalized reviewed intent is accepted for committing.
        if _json(params[field]) != _json(preview[field]):
            raise ValueError('Apply requires the exact canonical preview candidate')
        for sql in OWNER_POLICY_DDL:
            conn.execute(sql)
        store = _writer(conn, home, settings, policy)
        store._require_current_policy(conn)
        revision = _apply_candidate(conn, store, action, preview[field], key)
        if action != 'budgetObservation' and store._policy_generation == policy['generation']:
            store._adopt_policy(conn, force=True, resume=False)
        if action != 'budgetObservation' and store.settings != validated_patch(settings, preview[field]):
            # Adoption rechecks managed pins. A concurrent administrator edit
            # must not substitute different values behind a successful receipt.
            raise OwnerPolicyError('source_conflict')
        if action != 'budgetObservation':
            latest_locks = managed_fields()
            before_values = _configuration(settings)
            locked_change = (bool(set(preview[field]) & latest_locks) if action == 'budgetPolicy' else
                set(ROUTING_FIELDS) <= latest_locks or any(preview[field][key] != before_values[key]
                    for key in set(ROUTING_FIELDS) & latest_locks))
            if locked_change:
                raise OwnerPolicyError('managed_configuration')
        receipt = {'idempotencyKey': key, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'action': action,
            'policyGeneration': store._policy_generation, 'sourceRevision': preview['sourceRevision'],
            'previewVersion': version, field: preview[field]}
        if revision is not None:
            receipt['observationRevision'] = revision
        result = {'capabilityVersion': 1, 'receipt': receipt}
        conn.execute('INSERT INTO organization_owner_policy_receipts VALUES (?,?,?,?,?)',
            (key, identity['organizationId'], identity['owner']['ownerId'], digest, _json(result)))
        if action == 'budgetPolicy':
            store._event(conn, None, 'Owner updated budget and pacing defaults; captured objective caps and reservations were retained.', 'configuration', 'owner')
    try:
        _refresh_existing(home)
    except Exception:
        # The ledger fence and receipt are already committed. A failed local
        # cache refresh cannot turn success into a rejection or invite replay.
        import logging
        logging.getLogger(__name__).exception('Owner policy committed; cached service refresh deferred')
    return result


def _refresh_existing(home):
    """Refresh only an already-cached host; never create or wake scheduling."""
    import sys
    services = sys.modules.get('eidolon_cli.organization_service')
    if services is None:
        return
    with services._services_lock:
        service = services._services.get(Path(home).resolve() / 'organization' / 'state.db')
    if service is not None:
        service.refresh_configuration(wake=False)

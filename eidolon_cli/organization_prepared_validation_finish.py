"""One-finish observed-byte preflight; retained runtime/review proofs stay uncached.

Large dependency reads happen before the SQLite write lock. A private one-shot
scope binds their exact target to fresh SQL authority, then the commit boundary
rechecks original source bytes and every observed dependency fingerprint. This
is observed-at-check evidence, never a filesystem lock or an execution grant.

Static JSON validation reuse retains at most 128 MiB of raw UTF-8 grant/candidate
records across the whole finish. A grant contains one manifest representation;
a candidate contains two. This is a raw-record capacity limit, not a Python heap
limit: detached decoding and existing source/dependency observations amplify
live heap usage, and some observations remain retained for the whole finish.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import math
import re
import threading
import time

from eidolon_cli.organization_project_validation import canonical, digest

_ACTIVE = ContextVar('prepared_finish_verification', default=None)
_PREFLIGHT = ContextVar('prepared_finish_static_records', default=None)
_MAX_STATIC_RECORD_BYTES = 128 * 1024**2
_TYPES = {'request.project_test', 'request.test_review', 'request.integrate', 'request.accept'}
_REQUEST_FIELDS = ('id', 'objective_id', 'task_id', 'type', 'team', 'agent_id', 'token',
                   'attempts', 'execution_count', 'payload')


@dataclass
class _FinishToken:
    store: object
    request: str
    operation: str
    claim: dict
    objective_id: str = ''
    projects: tuple = ()
    static_projects: tuple = ()
    static_records: dict = field(default_factory=dict)
    static_bytes: int = 0
    preflight_connection: object = None
    targets: dict = field(default_factory=dict)
    agents: str = ''
    agent_ids: tuple = ()
    connection: object = None
    thread: int = field(default_factory=threading.get_ident)
    alive: bool = True
    entered: bool = False
    finalized: bool = False


@contextmanager
def _static_preflight_scope(token, conn):
    if _PREFLIGHT.get() is not None or _ACTIVE.get() is not None:
        raise ValueError('Prepared static observations require a nonnested finish')
    token.preflight_connection = conn
    context = _PREFLIGHT.set(token)
    try:
        yield
    finally:
        _PREFLIGHT.reset(context)
        token.preflight_connection = None


def _static_slot(conn, identity):
    token = _ACTIVE.get() or _PREFLIGHT.get()
    if token is None:
        return None, None
    _require_token(token, token.store)
    expected = token.connection if token.entered else token.preflight_connection
    if conn is not expected:
        raise ValueError('Prepared static observation belongs to a foreign connection')
    if type(identity) is not tuple or len(identity) != 3:
        raise ValueError('Prepared static observation has a foreign identity')
    kind, owner, project = identity
    if kind == 'candidate':
        project, sequence = owner, project
        if type(sequence) is not int or sequence < 1:
            raise ValueError('Prepared static registration identity is malformed')
    elif kind != 'grant' or owner != token.objective_id:
        raise ValueError('Prepared static observation has no selected authority')
    if project not in token.static_projects or (kind == 'candidate' and project not in token.projects):
        raise ValueError('Prepared static observation has no exact target for the selected project')
    return token, (kind, project)


def observed_finish_static_record(conn, identity, record, sha256):
    """Reuse only JSON validation of exact bytes, never an authority/proof verdict.

    Callers retain their SQL type/size fences and re-read the complete record and
    hash. Fresh detached decoding prevents mutation of one reader's value from
    affecting any subsequent reader. Only two static records per target qualify.
    """
    token, key = _static_slot(conn, identity)
    if token is None:
        return None
    retained = token.static_records.get(key)
    if retained is None:
        if token.entered:
            raise ValueError('Prepared static record was not observed before the write transaction')
        return None
    if (type(record) is not str or type(sha256) is not str
            or retained != (identity, record, sha256)):
        raise ValueError('Prepared static record bytes or hash changed after preflight')
    return json.loads(record)


def retain_finish_static_record(conn, identity, record, sha256):
    """Called only after the ordinary bounded JSON/hash validator succeeds."""
    token, key = _static_slot(conn, identity)
    if token is None:
        return
    maximum = (80 if identity[0] == 'candidate' else 40) * 1024**2
    size = len(record.encode('utf-8')) if type(record) is str else 0
    if (token.entered or type(record) is not str or not 0 < size <= maximum
            or type(sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', sha256) is None):
        raise ValueError('Prepared static observation requires bounded validated preflight bytes')
    value = (identity, record, sha256)
    if key in token.static_records and token.static_records[key] != value:
        raise ValueError('Prepared static record changed during preflight')
    if key not in token.static_records:
        if token.static_bytes + size > _MAX_STATIC_RECORD_BYTES:
            raise ValueError('Prepared finish static records exceed the 128 MiB aggregate raw-byte capacity')
        token.static_bytes += size
    token.static_records[key] = value


def _request_binding(request):
    if type(request['payload']) is not str or len(request['payload'].encode()) > 2 * 1024**2:
        raise ValueError('Prepared finish request exceeds its payload bound')
    return canonical({key: request[key] for key in _REQUEST_FIELDS})


def _operation_binding(conn, objective_id):
    from eidolon_cli.organization_outcome_contracts import contract_view
    from eidolon_cli.organization_prepared_validation_verification import _read_view, _scalar_bounds
    with _read_view(conn):
        _scalar_bounds(conn, 'objective_contract_revisions', 'objective_id=?', (objective_id,),
                       {'record': 1048576, 'sha256': 64})
        if conn.execute("SELECT 1 FROM objective_contract_revisions WHERE objective_id=? AND "
                        "(typeof(revision)<>'integer' OR revision<1 OR typeof(created) NOT IN ('integer','real')) LIMIT 1",
                        (objective_id,)).fetchone():
            raise ValueError('Prepared finish contract metadata is malformed')
        _scalar_bounds(conn, 'objective_control', 'objective_id=?', (objective_id,),
                       {'delivery_mode': 32, 'required_checks': 65536, 'criteria': 1048576, 'amended_scope': 1048576})
        row = conn.execute("SELECT CASE WHEN typeof(round)='integer' THEN round END AS round,"
            'delivery_mode,required_checks,criteria,amended_scope FROM objective_control WHERE objective_id=?',
            (objective_id,)).fetchone()
        if row is None or any(row[key] is None for key in ('round', 'delivery_mode', 'required_checks', 'criteria')):
            raise ValueError('Prepared finish objective scope is missing or malformed')
        return canonical({'control': dict(row), 'contract': contract_view(conn, objective_id)})


def _agent_binding(conn, identifiers):
    from eidolon_cli.organization_prepared_validation_verification import _read_view, _scalar_bounds
    result = []
    with _read_view(conn):
        for identifier in identifiers:
            _scalar_bounds(conn, 'agents', 'id=?', (identifier,),
                           {'id': 128, 'name': 1024, 'role': 64, 'manager_id': 128, 'team': 128, 'accepts': 65536})
            _scalar_bounds(conn, 'staff_state', 'agent_id=?', (identifier,),
                           {'agent_id': 128, 'source': 128, 'policy': 65536})
            _scalar_bounds(conn, 'agent_identity', 'agent_id=?', (identifier,),
                           {'agent_id': 128, 'identity_id': 128})
            if conn.execute('SELECT 1 FROM agent_identity WHERE agent_id=? AND identity_id IS NULL',
                            (identifier,)).fetchone():
                raise ValueError('Prepared finish agent identity is malformed')
            if conn.execute("SELECT 1 FROM staff_state WHERE agent_id=? AND "
                            "(typeof(active)<>'integer' OR active NOT IN (0,1))", (identifier,)).fetchone():
                raise ValueError('Prepared finish staff activation is malformed')
            row = conn.execute('SELECT a.id,a.name,a.role,a.manager_id,a.team,a.accepts,s.active,s.source,s.policy,i.identity_id '
                'FROM agents a LEFT JOIN staff_state s ON s.agent_id=a.id '
                'LEFT JOIN agent_identity i ON i.agent_id=a.id WHERE a.id=?', (identifier,)).fetchone()
            if (row is None or any(row[index] is None for index in (0, 1, 2, 4, 5))
                    or (any(value is not None for value in row[6:9]) and any(value is None for value in row[6:9]))):
                raise ValueError('Prepared finish agent authority is missing or malformed')
            result.append(tuple(row))
    return canonical(result)


def _check_claim(store, claim):
    from eidolon_cli.organization_budget import budget_view
    with store._connect() as conn:
        request = store._owned(conn, claim)
        if request is None:
            raise ValueError('Prepared finish lost its original lease or deadline during verification')
        if time.time() >= budget_view(conn, request['objective_id'], store.settings)['deadlineTimestamp']:
            raise ValueError('Prepared finish lost its original lease or deadline during verification')
    return True


def _proof_binding(verified):
    return digest({key: verified[key] for key in (
        'envelope', 'aggregate', 'evidenceHashes', 'contributorIds', 'sourceEvidence')})


def _projects(conn, request, result):
    from eidolon_cli.organization_prepared_validation_objectives import prepared_selection_references
    references = prepared_selection_references(conn, request['objective_id'])
    if not references:
        return None
    if request['type'] == 'request.project_test':
        if set(result) - {'usage', 'memory'}:
            return None  # The existing backend-only finish rejects model outcomes.
        project = json.loads(request['payload']).get('projectId')
        # Existing failure handling authenticates and exposes the retained failure;
        # it does not need successful-pair/source authority or a byte observation.
        if conn.execute('SELECT 1 FROM prepared_validation_results r JOIN prepared_validation_steps p '
                        'ON p.run_id=r.run_id JOIN prepared_validation_sets s ON s.id=p.set_id '
                        "WHERE s.request_id=? AND r.status<>'passed' LIMIT 1", (request['id'],)).fetchone():
            return None
        references = [item for item in references if item['projectId'] == project]
    projects = []
    for item in references:
        row = conn.execute("SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=128 THEN id END AS id,"
                           "CASE WHEN typeof(round)='integer' THEN round END AS round FROM prepared_validation_sets WHERE objective_id=? AND project_id=? "
                           'ORDER BY generation DESC LIMIT 1', (request['objective_id'], item['projectId'])).fetchone()
        current = conn.execute("SELECT CASE WHEN typeof(round)='integer' THEN round END AS round FROM objective_control WHERE objective_id=?", (request['objective_id'],)).fetchone()
        if current is None or current['round'] is None or (row is not None and (row['id'] is None or row['round'] is None)):
            raise ValueError('Prepared finish project target has malformed scalar identity')
        if row is not None and row['round'] == current['round']:
            if (request['type'] == 'request.test_review'
                    and item['projectId'] != json.loads(request['payload']).get('projectId')
                    and not conn.execute('SELECT 1 FROM prepared_validation_pair_reviews p JOIN requests r '
                        "ON r.id=p.request_id WHERE p.set_id=? AND p.approved=1 AND r.status='completed'",
                        (row['id'],)).fetchone()):
                continue
            projects.append(item['projectId'])
    return projects


@contextmanager
def prepare_finish_verification(store, claim, result):
    """Observe before BEGIN IMMEDIATE; always invalidate on early return or failure."""
    token = None
    with ExitStack() as resources:
        try:
            if (not isinstance(result, dict) or result.get('intervention') or 'messages' in result or 'requests' in result):
                yield None
                return
            with store._connect() as conn:
                request = store._owned(conn, claim)
                if request is None or request['type'] not in _TYPES:
                    yield None
                    return
                projects = _projects(conn, request, result)
                if projects is None:
                    yield None
                    return
                # Selected-but-empty is still an exact operation scope. A set
                # arriving before BEGIN must miss this token and force retry,
                # never start an uncached byte scan while holding the lock.
                token = _FinishToken(store, _request_binding(request),
                    _operation_binding(conn, request['objective_id']), {'id': request['id'], 'token': request['token']})
                token.objective_id, token.projects = request['objective_id'], tuple(projects)
                from eidolon_cli.organization_prepared_validation_objectives import prepared_selection_references
                token.static_projects = tuple(item['projectId'] for item in
                    prepared_selection_references(conn, request['objective_id']))
                with _static_preflight_scope(token, conn):
                    from eidolon_cli.organization_prepared_validation_ledger import _binding
                    # Repair history may read another selected project's grant
                    # without establishing that project's runtime/review success.
                    for project in token.static_projects:
                        _binding(conn, request['objective_id'], project)
                    contributors = {request['agent_id']}
                    from eidolon_cli.organization_prepared_validation_current import observe_current_prepared_authority
                    from eidolon_cli.organization_prepared_validation_verification import verify_latest_prepared_validation_set
                    for project in projects:
                        def observe(connection, envelope, snapshot):
                            observed = resources.enter_context(observe_current_prepared_authority(
                                store, connection, envelope, snapshot, check_claim=lambda: _check_claim(store, token.claim)))
                            token.targets[project] = {'envelope': canonical(envelope), 'snapshot': snapshot,
                                'observation': observed}
                            return json.loads(observed.value)
                        # No approval is cached: true/false mode verification remains
                        # the existing strict transaction-time verifier's obligation.
                        verified = verify_latest_prepared_validation_set(conn, request['objective_id'], project,
                            current_authority=observe, require_review=False)
                        token.targets[project]['proof'] = _proof_binding(verified)
                        contributors.update(verified['contributorIds'])
                    token.agent_ids = tuple(sorted(contributors))
                    token.agents = _agent_binding(conn, token.agent_ids)
            _check_claim(store, token.claim)
            yield token
        finally:
            if token is not None:
                token.alive = False
                token.targets.clear()
                token.static_records.clear()
                token.static_bytes = 0
                token.connection = None


def _require_token(token, store, conn=None):
    if (type(token) is not _FinishToken or not token.alive or token.store is not store
            or token.thread != threading.get_ident() or token.finalized
            or (conn is not None and token.connection is not conn)):
        raise ValueError('Prepared finish observation is expired, foreign or already consumed')


@contextmanager
def finish_verification_scope(token, store, conn, request):
    if token is None:
        yield
        return
    _require_token(token, store)
    if token.entered or _ACTIVE.get() is not None or not conn.in_transaction:
        raise ValueError('Prepared finish observation requires one nonnested transaction scope')
    token.entered = True
    token.connection = conn
    if (request['status'] != 'running' or _request_binding(request) != token.request
            or type(request['lease']) not in (int, float) or not math.isfinite(request['lease'])
            or request['lease'] <= time.time()
            or _operation_binding(conn, request['objective_id']) != token.operation
            or _agent_binding(conn, token.agent_ids) != token.agents):
        raise ValueError('Prepared finish request, contract, round or agent authority changed after preflight')
    active = _ACTIVE.set(token)
    try:
        from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
        for project, target in token.targets.items():
            verified = verify_current_prepared_pair(store, conn, request['objective_id'], project, require_review=False)
            if _proof_binding(verified) != target['proof']:
                raise ValueError('Prepared finish retained evidence changed after preflight')
        yield
    finally:
        _ACTIVE.reset(active)
        token.connection = None


def observed_finish_authority(store, conn, envelope, retained_snapshot, *, require_review):
    token = _ACTIVE.get()
    if token is None:
        return None
    _require_token(token, store, conn)
    if type(require_review) is not bool:
        raise ValueError('Prepared finish requires an explicit review mode')
    target = token.targets.get(envelope['projectId'])
    if target is None or target['envelope'] != canonical(envelope):
        raise ValueError('Prepared finish observation has no exact target; expensive fallback is forbidden')
    from eidolon_cli.organization_prepared_validation_current import recheck_observed_prepared_authority
    value = recheck_observed_prepared_authority(store, conn, envelope, retained_snapshot, target['observation'])
    return value


def finalize_finish_verification(token, store, conn, original_request):
    if token is None:
        return
    _require_token(token, store, conn)
    if _ACTIVE.get() is not token or _request_binding(original_request) != token.request:
        raise ValueError('Prepared finish finalization belongs to another operation')
    from eidolon_cli.organization_prepared_validation_current import recheck_finish_external_observation
    for target in token.targets.values():
        recheck_finish_external_observation(store, target['observation'])
    token.finalized = True

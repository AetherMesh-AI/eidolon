"""One-finish strict proof and observed-byte preflight with exact SQL CAS.

Large dependency reads happen before the SQLite write lock. A private one-shot
scope binds their exact target to fresh SQL authority, then the commit boundary
rechecks original source bytes and every observed dependency fingerprint. This
is observed-at-check evidence, never a filesystem lock or an execution grant.

The entire retained raw proof/input payload is capped at 128 MiB across the
operation, including exact source BLOBs, grant/candidate records and serialized
proof/artifact observations. Equal immutable source and static bodies share
storage after exact byte comparison. A grant contains one manifest representation;
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
_MAX_RETAINED_RAW_BYTES = 128 * 1024**2
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
    artifacts: dict = field(default_factory=dict)
    inventory: object = None
    inventory_stamp: tuple = ()
    agents: str = ''
    agent_ids: tuple = ()
    connection: object = None
    thread: int = field(default_factory=threading.get_ident)
    alive: bool = True
    entered: bool = False
    finalized: bool = False
    purpose: str = 'finish'
    rejection: object = None
    decision: object = None
    required_reviews: set = field(default_factory=set)
    retained_raw_bytes: int = 0
    claim_policy: int = 0


@contextmanager
def _static_preflight_scope(token, conn):
    from eidolon_cli.organization_prepared_validation_records import _SCOPE
    if _PREFLIGHT.get() is not None or _ACTIVE.get() is not None or _SCOPE.get() is not None:
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
        from eidolon_cli.organization_prepared_validation_records import observed_static_record
        return observed_static_record(conn, identity, record, sha256)
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
        from eidolon_cli.organization_prepared_validation_records import retain_static_record
        return retain_static_record(conn, identity, record, sha256)
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
            "CASE WHEN typeof(max_replans)='integer' AND max_replans>=0 THEN max_replans END AS max_replans,"
            'delivery_mode,required_checks,criteria,amended_scope FROM objective_control WHERE objective_id=?',
            (objective_id,)).fetchone()
        if row is None or any(row[key] is None for key in ('round', 'max_replans', 'delivery_mode', 'required_checks', 'criteria')):
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
    from eidolon_cli.organization_prepared_validation_records import _SCOPE
    from eidolon_cli.organization_prepared_validation_verification import _read_view
    if _SCOPE.get() is not None or _PREFLIGHT.get() is not None or _ACTIVE.get() is not None:
        raise ValueError("Prepared finish requires a nonnested operation")
    token = None
    with ExitStack() as resources:
        try:
            if (not isinstance(result, dict) or result.get('intervention') or 'messages' in result or 'requests' in result):
                yield None
                return
            with store._connect() as conn, _read_view(conn):
                request = store._owned(conn, claim)
                if request is None or request['type'] not in _TYPES:
                    conn.rollback()
                    yield None
                    return
                projects = _projects(conn, request, result)
                if projects is None:
                    conn.rollback()
                    yield None
                    return
                # Selected-but-empty is still an exact operation scope. A set
                # arriving before BEGIN must miss this token and force retry,
                # never start an uncached byte scan while holding the lock.
                token = _FinishToken(store, _request_binding(request),
                    _operation_binding(conn, request['objective_id']), {'id': request['id'], 'token': request['token']})
                token.claim_policy = _claim_policy(conn, request['id'])
                token.objective_id, token.projects = request['objective_id'], tuple(projects)
                if request['type'] in {'request.test_review', 'request.accept'} and type(result.get('approved')) is bool:
                    token.decision = canonical({key: value for key, value in result.items() if key not in {'usage', 'memory'}})
                    if result['approved'] is False:
                        token.rejection = token.decision
                from eidolon_cli.organization_prepared_validation_objectives import prepared_selection_references
                token.static_projects = tuple(item['projectId'] for item in
                    prepared_selection_references(conn, request['objective_id']))
                from eidolon_cli.organization_prepared_validation_inventory import check_prepared_inventory_bounds
                check_prepared_inventory_bounds(conn, token.objective_id, token.static_projects)
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
                        # Immutable runtime/source proof is checked in this same
                        # read snapshot. Authority and review approval remain live.
                        verified = verify_latest_prepared_validation_set(conn, request['objective_id'], project,
                            current_authority=observe, require_review=False)
                        token.targets[project]['proof'] = canonical(verified)
                        for _, artifact in verified['checks']:
                            token.artifacts[artifact['id']] = canonical(artifact)
                        from eidolon_cli.organization_prepared_validation_review_scope import review_scope_artifact
                        for identifier in verified['sourceEvidence']:
                            if identifier.startswith('prepared_scope_'):
                                token.artifacts[identifier] = canonical(review_scope_artifact(conn, identifier))
                        contributors.update(verified['contributorIds'])
                    if token.rejection is not None:
                        # Repair may expose other failed/unreviewed generations.
                        # Prevalidate their static artifacts without promoting them
                        # to successful pairs or current execution authority.
                        from eidolon_cli.organization_prepared_validation_history import prepared_replan_history
                        prepared_replan_history(conn, token.objective_id, json.loads(token.operation)['control']['round'])
                    token.agent_ids = tuple(sorted(contributors))
                    token.agents = _agent_binding(conn, token.agent_ids)
                    from eidolon_cli.organization_prepared_validation_inventory import capture_prepared_inventory
                    token.inventory = capture_prepared_inventory(conn, token.objective_id, token.static_projects,
                        shared_static_records=token.static_records,
                        shared_source_files={(json.loads(target['envelope'])['setId'], item.path): item.content
                            for target in token.targets.values() for item in target['snapshot'].files})
                    _check_retained_capacity(token)
            _check_claim(store, token.claim)
            yield token
        finally:
            if token is not None:
                token.alive = False
                token.targets.clear()
                token.artifacts.clear()
                token.inventory = None
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
    from eidolon_cli.organization_prepared_validation_records import _SCOPE
    if token.entered or _ACTIVE.get() is not None or _SCOPE.get() is not None or not conn.in_transaction:
        raise ValueError('Prepared finish observation requires one nonnested transaction scope')
    token.entered = True
    token.connection = conn
    if (request['status'] != 'running' or _request_binding(request) != token.request
            or type(request['lease']) not in (int, float) or not math.isfinite(request['lease'])
            or request['lease'] <= time.time()
            or _operation_binding(conn, request['objective_id']) != token.operation
            or _agent_binding(conn, token.agent_ids) != token.agents):
        raise ValueError('Prepared finish request, contract, round or agent authority changed after preflight')
    marker = f'prepared_operation_{id(token):x}'
    conn.execute(f'SAVEPOINT {marker}')
    active = _ACTIVE.set(token)
    try:
        _require_inventory(token, conn, force=True)
        for target in token.targets.values():
            envelope = json.loads(target['envelope'])
            observed_finish_authority(store, conn, envelope, target['snapshot'], require_review=False)
        yield
        conn.execute(f'RELEASE SAVEPOINT {marker}')
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
    _require_inventory(token, conn)
    from eidolon_cli.organization_prepared_validation_current import recheck_observed_prepared_authority
    value = recheck_observed_prepared_authority(store, conn, envelope, retained_snapshot, target['observation'], exact_retained_inputs=True)
    return value


def finalize_finish_verification(token, store, conn, original_request):
    if token is None:
        return
    _require_token(token, store, conn)
    if _ACTIVE.get() is not token or _request_binding(original_request) != token.request:
        raise ValueError('Prepared finish finalization belongs to another operation')
    _require_inventory(token, conn, force=True)
    _require_finish_transition(token, conn, original_request)
    if _agent_binding(conn, token.agent_ids) != token.agents:
        raise ValueError("Prepared finish agent authority changed during its transaction")
    from eidolon_cli.organization_prepared_validation_current import (
        recheck_finish_external_observation, recheck_finish_owner_source,
    )
    for target in token.targets.values():
        recheck_finish_external_observation(store, target['observation'])
    _require_inventory(token, conn, force=True)
    for target in token.targets.values():
        recheck_finish_owner_source(store, conn, target['observation'])
    _require_inventory(token, conn, force=True)
    _require_finish_transition(token, conn, original_request)
    if _agent_binding(conn, token.agent_ids) != token.agents:
        raise ValueError('Prepared finish agent authority changed during its final observation')
    from eidolon_cli.organization_prepared_validation_verification import _verify_review
    for project in token.required_reviews:
        _verify_review(conn, json.loads(token.targets[project]['proof']))
    token.finalized = True


def _require_inventory(token, conn, *, force=False):
    """Compare every input after writes; the write lock excludes other writers."""
    _require_token(token, token.store, conn)
    if not conn.in_transaction:
        raise ValueError('Prepared proof reuse requires the original write transaction')
    from eidolon_cli.organization_prepared_validation_inventory import _connection
    _connection(conn)  # TEMP shadows and connection-mode changes never use the stamp shortcut.
    stamp = (conn.total_changes, conn.execute('PRAGMA main.schema_version').fetchone()[0],
             conn.execute('PRAGMA temp.schema_version').fetchone()[0])
    if force or stamp != token.inventory_stamp:
        from eidolon_cli.organization_prepared_validation_inventory import require_prepared_inventory
        require_prepared_inventory(conn, token.inventory)
        if token.purpose == 'finish' and token.targets:
            from eidolon_cli.organization_project_effective_snapshot import _check_history_bounds
            _check_history_bounds(conn, token.objective_id, lambda: None)
        token.inventory_stamp = stamp


def observed_finish_pair(conn, objective_id, project_id, *, current_authority, require_review):
    """Reuse one strict immutable proof, with fresh authority and review checks."""
    token = _ACTIVE.get()
    if token is None:
        return None
    _require_token(token, token.store, conn)
    target = token.targets.get(project_id)
    if objective_id != token.objective_id or target is None:
        raise ValueError('Prepared finish observation has no exact target; expensive fallback is forbidden')
    _require_inventory(token, conn)
    verified = json.loads(target['proof'])
    from eidolon_cli.organization_prepared_validation_verification import _authority, _verify_review
    value = current_authority(conn, verified['envelope'], target['snapshot'])
    context, contributors, source_evidence = _authority(value, verified['envelope'])
    if canonical(value) != target['observation'].value:
        raise ValueError('Prepared current authority differs from its exact observed context')
    from eidolon_cli.organization_prepared_validation_verification import _scalar_bounds
    _scalar_bounds(conn, 'organization_policy', 'id=1', (), {'fingerprint': 64})
    policy = conn.execute("SELECT CASE WHEN typeof(generation)='integer' THEN generation END,fingerprint "
                          'FROM organization_policy WHERE id=1').fetchone()
    round_row = conn.execute("SELECT CASE WHEN typeof(round)='integer' THEN round END FROM objective_control WHERE objective_id=?",
                             (objective_id,)).fetchone()
    if (policy is None or tuple(policy) != (context['policy_generation'], context['policy_fingerprint'])
            or round_row is None or type(round_row[0]) is not int or round_row[0] != context['round_number']):
        raise ValueError('Prepared current policy or round changed after proof preflight')
    if contributors != verified['contributorIds'] or source_evidence != verified['sourceEvidence']:
        raise ValueError('Prepared current proof authority differs from its exact observation')
    if require_review:
        _verify_review(conn, verified)
        verified['independentReviewVerified'] = True
        token.required_reviews.add(project_id)
    verified['checks'] = [tuple(pair) for pair in verified['checks']]
    return verified


def observed_prepared_artifact(conn, identifier):
    """Exact retained artifact projection; this never supplies current authority."""
    token = _ACTIVE.get()
    if token is None:
        return None
    _require_token(token, token.store, conn)
    _require_inventory(token, conn)
    if identifier not in token.artifacts:
        raise ValueError('Prepared artifact was not observed before the write transaction')
    return json.loads(token.artifacts[identifier])


def retain_prepared_artifact(conn, identifier, artifact):
    """Retain a normal verified static getter result in its exact read view."""
    token = _PREFLIGHT.get()
    if token is None or artifact is None:
        return artifact
    _require_token(token, token.store)
    if conn is not token.preflight_connection:
        raise ValueError('Prepared artifact observation belongs to a foreign connection')
    value = canonical(artifact)
    if identifier in token.artifacts and token.artifacts[identifier] != value:
        raise ValueError('Prepared artifact changed during its read observation')
    token.artifacts[identifier] = value
    return artifact


@contextmanager
def prepare_context_verification(store, claim):
    """Prevalidate retained context artifacts outside its acknowledgement write."""
    from eidolon_cli.organization_prepared_validation_records import _SCOPE
    from eidolon_cli.organization_prepared_validation_verification import _read_view
    from eidolon_cli.organization_prepared_validation_objectives import prepared_selection_references
    from eidolon_cli.organization_prepared_validation_inventory import capture_prepared_inventory
    if _ACTIVE.get() is not None or _PREFLIGHT.get() is not None or _SCOPE.get() is not None:
        raise ValueError('Prepared context observation requires a nonnested operation')
    token = None
    try:
        with store._connect() as conn, _read_view(conn):
            request = store._owned(conn, claim)
            if request is None or request['type'] == 'request.message':
                conn.rollback()
                yield None
                return
            selected = prepared_selection_references(conn, request['objective_id'])
            if not selected:
                conn.rollback()
                yield None
                return
            token = _FinishToken(store, _request_binding(request),
                _operation_binding(conn, request['objective_id']), dict(claim), purpose='context')
            token.claim_policy = _claim_policy(conn, request['id'])
            token.objective_id = request['objective_id']
            token.projects = token.static_projects = tuple(item['projectId'] for item in selected)
            from eidolon_cli.organization_prepared_validation_inventory import check_prepared_inventory_bounds
            check_prepared_inventory_bounds(conn, token.objective_id, token.static_projects)
            with _static_preflight_scope(token, conn):
                from eidolon_cli.organization_prepared_validation_ledger import _binding
                for project in token.static_projects:
                    _binding(conn, token.objective_id, project)
                # Rebuild once using ordinary getters. Only their exact artifact
                # results are retained; prompts, receipts and authorizations are not.
                store._context_with_connection(conn, claim)
                token.agent_ids = (request['agent_id'],)
                token.agents = _agent_binding(conn, token.agent_ids)
                token.inventory = capture_prepared_inventory(conn, token.objective_id, token.static_projects,
                    shared_static_records=token.static_records)
                _check_retained_capacity(token)
        yield token
    finally:
        if token is not None:
            token.alive = False
            token.artifacts.clear()
            token.static_records.clear()
            token.inventory = None
            token.connection = None


def finalize_context_verification(token, store, conn):
    if token is None:
        return
    _require_token(token, store, conn)
    if token.purpose != 'context' or _ACTIVE.get() is not token:
        raise ValueError('Prepared context belongs to another operation')
    _require_inventory(token, conn, force=True)
    request = store._owned(conn, token.claim)
    if (request is None or _request_binding(request) != token.request
            or _operation_binding(conn, token.objective_id) != token.operation
            or _agent_binding(conn, token.agent_ids) != token.agents):
        raise ValueError('Prepared context authority changed during acknowledgement')
    token.finalized = True


def _check_retained_capacity(token):
    """Bound unique retained raw values, including serialized proof and source.

    Inventory scalars are counted by SQLite. Exact shared strings/BLOBs are not
    counted a second time. Decoded JSON/object overhead and transient reads are
    intentionally outside this raw-retention limit, not claimed as a heap bound.
    """
    size = token.inventory.raw_bytes
    seen = {id(value) for table in token.inventory.rows for row in table for kind, value in row
            if kind in {'text', 'blob'}}
    def add(value):
        nonlocal size
        if id(value) not in seen:
            seen.add(id(value))
            size += len(value.encode('utf-8')) if type(value) is str else len(value)
    for _, raw, hashed in token.static_records.values():
        add(raw)
        add(hashed)
    for value in (token.request, token.operation, token.agents, *token.artifacts.values(),
                  *(value for value in token.claim.values() if type(value) is str),
                  *((token.decision,) if token.decision is not None else ())):
        add(value)
    for target in token.targets.values():
        for value in (target['envelope'], target['proof']):
            add(value)
        observation = target['observation']
        # The dependency capability privately retains this small sanitized JSON.
        size += len(json.dumps(observation.dependency.metadata()).encode('ascii'))
        for value in (observation.envelope, observation.retained_manifest, observation.authority_identity, observation.value, observation.source_binding):
            add(value)
        matcher, prefixes = observation.classifier_policy
        for value in (matcher.pattern, *prefixes):
            add(value)
        for snapshot in (target['snapshot'], observation.fresh.original, observation.fresh.effective):
            for item in snapshot.files:
                add(item.content)
    token.retained_raw_bytes = size
    if size > _MAX_RETAINED_RAW_BYTES:
        raise ValueError('Prepared observation exceeds the 128 MiB aggregate retained raw-byte capacity')


def _require_finish_transition(token, conn, original_request):
    from eidolon_cli.organization_prepared_validation_verification import _scalar_bounds
    _scalar_bounds(conn, 'organization_policy', 'id=1', (), {'fingerprint': 64})
    if conn.execute("SELECT 1 FROM organization_policy WHERE id=1 AND (typeof(generation)<>'integer' OR generation<1)").fetchone():
        raise ValueError('Prepared finish current policy is malformed')
    token.store._require_current_policy(conn)
    if _claim_policy(conn, original_request['id']) != token.claim_policy:
        raise ValueError('Prepared finish claim policy changed after preflight')
    limits = {field: (2 * 1024**2 if field == 'payload' else 128)
              for field in _REQUEST_FIELDS if field not in {'attempts', 'execution_count'}}
    _scalar_bounds(conn, 'requests', 'id=?', (original_request['id'],), limits)
    if conn.execute("SELECT 1 FROM requests WHERE id=? AND (typeof(attempts)<>'integer' OR attempts<0 "
                    "OR typeof(execution_count)<>'integer' OR execution_count<0 OR typeof(status)<>'text' "
                    "OR status<>'completed' OR token IS NOT NULL OR lease IS NOT NULL OR reason IS NOT NULL)",
                    (original_request['id'],)).fetchone():
        raise ValueError('Prepared finish did not retain its exact permitted request completion')
    current = conn.execute('SELECT '+','.join(_REQUEST_FIELDS)+',status,lease,reason FROM requests WHERE id=?',
                           (original_request['id'],)).fetchone()
    if (current is None or current['status'] != 'completed' or current['token'] is not None
            or current['lease'] is not None or current['reason'] is not None
            or _request_binding({**dict(current), 'token': original_request['token']}) != token.request):
        raise ValueError('Prepared finish did not retain its exact permitted request completion')
    cancelled = conn.execute("SELECT CASE WHEN typeof(cancelled)='integer' THEN cancelled END FROM objectives WHERE id=?", (token.objective_id,)).fetchone()
    if cancelled is None or type(cancelled[0]) is not int or cancelled[0] != 0:
        raise ValueError('Prepared finish objective was cancelled during its transaction')
    expected = json.loads(token.operation)
    if token.decision is not None and original_request['type'] == 'request.test_review':
        from eidolon_cli.organization_prepared_validation_verification import _review_identity, _summary
        from eidolon_cli.organization_prepared_validation_ledger import _bounded_text, _checked_json
        result = json.loads(token.decision)
        project = json.loads(original_request['payload'])['projectId']
        verified = json.loads(token.targets[project]['proof'])
        record = {**_review_identity(verified, original_request), 'approved': result['approved'], 'summary': _summary(result['summary'])}
        retained = conn.execute(f'SELECT {_bounded_text("request_id", 128)},{_bounded_text("reviewer_id", 128)},'
            f"CASE WHEN typeof(approved)='integer' THEN approved END,{_bounded_text('record', 1000000)},{_bounded_text('sha256', 64)} "
            'FROM prepared_validation_pair_reviews WHERE set_id=?', (verified['envelope']['setId'],)).fetchone()
        if (retained is None or tuple(retained[:3]) != (original_request['id'], original_request['agent_id'], int(result['approved']))
                or type(retained[2]) is not int or canonical(_checked_json(retained[3], retained[4])) != canonical(record)):
            raise ValueError('Prepared rejection transition lacks its exact independent review')
        if result['approved'] is False:
            expected['control']['round'] += int(expected['control']['round'] <
                min(expected['control']['max_replans'], token.store.settings.max_replans))
    elif token.decision is not None and original_request['type'] == 'request.accept':
        from eidolon_cli.organization_prepared_validation_ledger import _bounded_text
        result = json.loads(token.decision)
        row = conn.execute(f'SELECT {_bounded_text("objective_id", 128)},'
            "CASE WHEN typeof(round)='integer' THEN round END,"
            f"{_bounded_text('agent_id', 128)},CASE WHEN typeof(approved)='integer' THEN approved END,"
            f'{_bounded_text("summary", 40000)},{_bounded_text("evidence_ids", 2 * 1024**2)},'
            f'{_bounded_text("criteria_results", 2 * 1024**2)},{_bounded_text("conflicts", 200000)} '
            'FROM objective_acceptances WHERE request_id=?', (original_request['id'],)).fetchone()
        if (row is None or tuple(row[:4]) != (token.objective_id, expected['control']['round'], original_request['agent_id'], int(result['approved']))
                or type(row[1]) is not int or type(row[3]) is not int
                or row[4] != result['summary'].strip() or any(type(value) is not str for value in row[5:])
                or sorted(json.loads(row[5])) != sorted(result['evidenceIds'])
                or canonical(json.loads(row[6])) != canonical(result['criteriaResults'])
                or canonical(json.loads(row[7])) != canonical(result['conflicts'])):
            raise ValueError('Prepared executive rejection lacks its exact retained acceptance decision')
        advances = result['approved'] is False and expected['control']['round'] < min(
            expected['control']['max_replans'], token.store.settings.max_replans)
        state = conn.execute(f'SELECT {_bounded_text("status", 32)} FROM objective_control WHERE objective_id=?',
                             (token.objective_id,)).fetchone()
        expected_state = 'accepted' if result['approved'] else ('replanning' if advances else 'blocked')
        if state is None or state[0] != expected_state:
            raise ValueError('Prepared executive rejection has an unexpected control transition')
        expected['control']['round'] += int(advances)
    if _operation_binding(conn, token.objective_id) != canonical(expected):
        raise ValueError('Prepared finish contract or round changed outside its permitted transition')


def _claim_policy(conn, request_id):
    row = conn.execute("SELECT CASE WHEN typeof(generation)='integer' AND generation>=1 THEN generation END "
                       'FROM request_policy WHERE request_id=?', (request_id,)).fetchone()
    if row is None or type(row[0]) is not int:
        raise ValueError('Prepared finish requires its exact retained claim policy')
    return row[0]

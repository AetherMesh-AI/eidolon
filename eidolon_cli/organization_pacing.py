"""Opt-in, objective-local pacing of new model-backed request attempts.

An admitted attempt may make further calls under the unchanged hard caps. Pacing
never interrupts it for replay, converts an observation into quota, or refunds a
reservation. Manual observations are owner statements, not provider telemetry.
"""
from datetime import datetime
from decimal import Decimal
import math
import time

from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES

PACING_FIELDS = ('adaptive_pacing_enabled', 'pacing_conserve_below_percent',
                 'pacing_critical_below_percent', 'pacing_conserve_interval_seconds',
                 'pacing_critical_interval_seconds', 'pacing_conserve_max_inflight',
                 'pacing_critical_max_inflight')
NON_MODEL_REQUEST_TYPES = ('request.hire', 'request.apply', 'request.validate',
                           'request.project_test', 'request.source_integrate')
PACING_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_budget_observations (
 objective_id TEXT NOT NULL REFERENCES objectives(id), revision INTEGER NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, remaining_percent REAL NOT NULL,
 as_of REAL NOT NULL, expires_at REAL NOT NULL, reset_at REAL,
 actor_id TEXT NOT NULL REFERENCES agents(id), created REAL NOT NULL,
 PRIMARY KEY(objective_id,revision));
CREATE TRIGGER IF NOT EXISTS budget_observation_immutable_update
 BEFORE UPDATE ON organization_budget_observations
 BEGIN SELECT RAISE(ABORT,'Budget observation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS budget_observation_immutable_delete
 BEFORE DELETE ON organization_budget_observations
 BEGIN SELECT RAISE(ABORT,'Budget observation is immutable'); END;
CREATE INDEX IF NOT EXISTS model_calls_attempt ON organization_model_calls(request_id,token,created);
"""


class OrganizationPacingDeferred(RuntimeError):
    """A zero-call attempt can return to the queue without a failed attempt."""
    def __init__(self, reason, defer_until):
        super().__init__(reason)
        self.defer_until = defer_until


def paced_request(kind):
    return kind not in (*COMPLETION_REQUEST_TYPES, *NON_MODEL_REQUEST_TYPES)


def _observation(conn, objective_id, now):
    from eidolon_cli.organization_store import _iso
    row = conn.execute('SELECT * FROM organization_budget_observations WHERE objective_id=? '
                       'ORDER BY revision DESC LIMIT 1', (objective_id,)).fetchone()
    if row is None:
        return None
    status = ('reset_unconfirmed' if row['reset_at'] is not None and now >= row['reset_at']
              else 'expired' if now >= row['expires_at'] else 'fresh')
    return {'revision': row['revision'], 'remainingPercent': row['remaining_percent'],
            'asOf': _iso(row['as_of']), 'expiresAt': _iso(row['expires_at']),
            'resetAt': _iso(row['reset_at']), 'status': status, 'source': 'owner_manual',
            'scope': 'objective', 'advisory': True}


def _remaining_percent(budget):
    reserve = budget['completionReserve']
    if not reserve['admissionUsageKnown']:
        return None
    values = []
    for key, limit in (('modelCalls', 'modelCallLimit'), ('tokens', 'tokenLimit'),
                       ('configuredCostUsd', 'configuredCostLimitUsd')):
        if budget[limit] is None:
            continue
        capacity = max(Decimal(0), Decimal(budget[limit]) - Decimal(reserve['captured'][key] or 0))
        remaining = reserve['ordinaryRemaining'][key]
        if remaining is None:
            return None
        values.append(min(Decimal(100), max(Decimal(0), Decimal(remaining) * 100 / capacity)) if capacity else Decimal(0))
    return round(float(min(values)), 6) if values else None


def _last_start(conn, objective_id):
    # Inner calls must not keep moving the stage-start clock forward.
    return conn.execute("SELECT max(started) FROM (SELECT min(m.created) AS started "
        "FROM organization_model_calls m JOIN organization_model_call_classes c ON c.model_call_id=m.id "
        "WHERE m.objective_id=? AND c.classification='ordinary' GROUP BY m.request_id,m.token)",
        (objective_id,)).fetchone()[0]


def pacing_view(conn, objective_id, settings, budget, *, now=None):
    from eidolon_cli.organization_store import _iso
    now = time.time() if now is None else now
    observation = _observation(conn, objective_id, now)
    percent = _remaining_percent(budget)
    reason = 'local_headroom' if percent is not None else 'budget_unknown'
    if observation is not None and percent is not None:
        if observation['remainingPercent'] <= percent:
            percent = observation['remainingPercent']
            # Unknown reset/expiry cannot make a low observation disappear.
            reason = 'manual_observation' if observation['status'] == 'fresh' else 'stale_observation'
    mode, interval, slots = 'normal', 0, settings.max_inflight
    if not settings.adaptive_pacing_enabled:
        mode, reason = 'off', 'disabled'
    elif percent is None:
        mode = 'unknown'
    elif percent <= settings.pacing_critical_below_percent:
        mode, interval, slots = ('critical', settings.pacing_critical_interval_seconds,
                                 min(slots, settings.pacing_critical_max_inflight))
    elif percent <= settings.pacing_conserve_below_percent:
        mode, interval, slots = ('conserve', settings.pacing_conserve_interval_seconds,
                                 min(slots, settings.pacing_conserve_max_inflight))
    last = _last_start(conn, objective_id) if interval else None
    defer = last + interval if last is not None and last + interval > now else None
    return {'version': 1, 'policy': 'local-stage-start-v1', 'enabled': settings.adaptive_pacing_enabled,
            'scope': 'objective', 'mode': mode, 'reason': reason, 'evaluatedAt': _iso(now),
            'deferUntil': _iso(defer), 'maxInflight': slots, 'minIntervalSeconds': interval,
            'remainingPercent': percent, 'remaining': budget['completionReserve']['ordinaryRemaining'],
            'protectedRemaining': budget['completionReserve']['protectedRemaining'],
            'observation': observation, 'limitation': 'stage_start_only_no_quota_inference'}


def pacing_wait(conn, request, settings, budget, *, final=False):
    """Call inside the admission writer; completion and local controls can pass."""
    if not settings.adaptive_pacing_enabled or not paced_request(request['type']):
        return None
    if final and conn.execute('SELECT 1 FROM organization_model_calls WHERE request_id=? AND token=?',
                              (request['id'], request['token'])).fetchone():
        return None
    view = budget['adaptivePacing']
    if view['mode'] not in ('conserve', 'critical'):
        return None
    now = time.time()
    if view['deferUntil'] is not None:
        return OrganizationPacingDeferred('Adaptive pacing is waiting before the next ordinary model-backed stage.',
                                          datetime.fromisoformat(view['deferUntil']).timestamp())
    # Queue admission counts claimed attempts. Final admission counts only
    # already-admitted attempts, so two unadmitted claims cannot deadlock.
    rows = conn.execute("SELECT r.id,r.type,r.lease FROM requests r WHERE r.objective_id=? "
                        "AND r.status='running' AND r.id!=? AND r.lease>? " +
                        ("AND EXISTS (SELECT 1 FROM organization_model_calls m WHERE m.request_id=r.id AND m.token=r.token) " if final else ''),
                        (request['objective_id'], request['id'], now)).fetchall()
    ordinary = [row for row in rows if paced_request(row['type'])]
    if len(ordinary) >= view['maxInflight']:
        return OrganizationPacingDeferred('Adaptive pacing is waiting for an ordinary model-backed stage slot.',
                                          min(now + 1, min(row['lease'] for row in ordinary)))
    return None


def _timestamp(value, label):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f'{label} must be an ISO timestamp with a timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        result = parsed.timestamp()
        if not math.isfinite(result) or not 0 <= result <= 253402300799:
            raise ValueError()
        return result
    except (ValueError, OverflowError):
        raise ValueError(f'{label} must be an ISO timestamp with a timezone') from None


class OrganizationPacingStore:
    def record_budget_observation(self, objective_id, *, remaining_percent, as_of, expires_at,
                                  reset_at=None, expected_revision, idempotency_key):
        from eidolon_cli.organization_store import _text
        objective_id = _text(objective_id, 'Objective ID', 128)
        key = _text(idempotency_key, 'Idempotency key', 128)
        if type(remaining_percent) not in (int, float) or not math.isfinite(remaining_percent) or not 0 <= remaining_percent <= 100:
            raise ValueError('Remaining percentage must be finite and between 0 and 100')
        if type(expected_revision) is not int or not 0 <= expected_revision < 1000:
            raise ValueError('Observation revision must be an integer from 0 to 999')
        observed, expiry = _timestamp(as_of, 'As-of'), _timestamp(expires_at, 'Expiry')
        reset = _timestamp(reset_at, 'Reset') if reset_at is not None else None
        if not observed < expiry <= observed + 604800 or (reset is not None and not observed <= reset <= observed + 604800):
            raise ValueError('Expiry must follow as-of by at most seven days; optional reset must be within that window')
        with self._write() as conn:
            self._require_current_policy(conn)
            old = conn.execute('SELECT * FROM organization_budget_observations WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if (old['objective_id'], old['revision'], old['remaining_percent'], old['as_of'], old['expires_at'], old['reset_at']) != (
                        objective_id, expected_revision + 1, remaining_percent, observed, expiry, reset):
                    raise ValueError('Observation retry key belongs to a different observation')
                return {'objectiveId': objective_id, 'revision': old['revision'], 'idempotencyKey': key}
            now = time.time()
            if observed > now:
                raise ValueError('As-of cannot be in the future')
            if expiry <= now or (reset is not None and reset <= now):
                raise ValueError('A new observation must be fresh, with expiry and optional reset still in the future')
            row = conn.execute('SELECT o.cancelled,c.status,coalesce(h.archived,0) AS archived FROM objectives o '
                'JOIN objective_control c ON c.objective_id=o.id LEFT JOIN objective_history h ON h.objective_id=o.id '
                'WHERE o.id=?', (objective_id,)).fetchone()
            if row is None:
                raise ValueError('Objective not found in this profile')
            if row['cancelled'] or row['archived'] or row['status'] in ('accepted', 'legacy_completed'):
                raise ValueError('Only nonterminal, unarchived objectives can receive observations')
            latest = conn.execute('SELECT revision,as_of FROM organization_budget_observations WHERE objective_id=? '
                                  'ORDER BY revision DESC LIMIT 1', (objective_id,)).fetchone()
            if expected_revision != (latest['revision'] if latest else 0):
                raise ValueError('Observation changed; refresh and review the current value')
            if latest and observed < latest['as_of']:
                raise ValueError('A replacement observation cannot precede the latest as-of time')
            revision = expected_revision + 1
            conn.execute('INSERT INTO organization_budget_observations VALUES (?,?,?,?,?,?,?,?,?)',
                         (objective_id, revision, key, remaining_percent, observed, expiry, reset, 'owner', now))
            self._event(conn, objective_id, f'Owner recorded advisory remaining allowance {remaining_percent:g}% '
                        f'(observation revision {revision}). Objective caps and reservations are unchanged.', 'budget', 'owner')
            return {'objectiveId': objective_id, 'revision': revision, 'idempotencyKey': key}

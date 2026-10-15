"""Local, read-only route evidence. Reservations are never actual spending.

The report deliberately does not recommend a different route: existing ledgers
lack per-call invoice costs and controlled, comparable quality observations.
Stage completion and objective acceptance are separate, non-causal signals.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


OBSERVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_route_observations (
 model_call_id TEXT PRIMARY KEY REFERENCES organization_model_calls(id),
 input_tokens INTEGER, output_tokens INTEGER, elapsed_seconds REAL NOT NULL,
 outcome TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS organization_route_observations_immutable_update
 BEFORE UPDATE ON organization_route_observations
 BEGIN SELECT RAISE(ABORT,'Route observation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_route_observations_immutable_delete
 BEFORE DELETE ON organization_route_observations
 BEGIN SELECT RAISE(ABORT,'Route observation is immutable'); END;
"""


def provider_usage(response):
    """Provider-reported counts only. Missing/malformed counters remain unknown."""
    usage = getattr(response, 'usage', None)
    def count(name):
        value = getattr(usage, name, None)
        return value if type(value) is int and 0 <= value <= 100_000_000 else None
    return count('prompt_tokens'), count('completion_tokens')


def persist_observation(path, claim, call_id, **measurement):
    # Optional diagnostics must not spend the transport's deadline waiting for
    # the organization writer. Fail immediately under contention, never retry.
    with closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=rw', uri=True,
                                 timeout=0, isolation_level=None)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = record_observation(conn, claim, call_id, **measurement)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise


def record_observation(conn, claim, call_id, *, input_tokens, output_tokens, elapsed_seconds, outcome):
    import math
    import time
    if (not isinstance(call_id, str) or outcome not in {'response', 'error'}
            or type(elapsed_seconds) not in (int, float) or not math.isfinite(elapsed_seconds)
            or not 0 <= elapsed_seconds <= 86400
            or any(value is not None and (type(value) is not int or not 0 <= value <= 100_000_000)
                   for value in (input_tokens, output_tokens))):
        raise ValueError('Invalid physical-call observation')
    row = conn.execute('SELECT 1 FROM organization_model_calls m '
        'JOIN organization_model_call_dispatches d ON d.model_call_id=m.id '
        'WHERE m.id=? AND m.request_id=? AND m.token=?', (call_id, claim['id'], claim['token'])).fetchone()
    if row is None:
        raise ValueError('Observation requires the matching routed call reservation')
    values = (input_tokens, output_tokens, float(elapsed_seconds), outcome)
    old = conn.execute('SELECT input_tokens,output_tokens,elapsed_seconds,outcome '
        'FROM organization_route_observations WHERE model_call_id=?', (call_id,)).fetchone()
    if old:
        if tuple(old) != values:
            raise ValueError('Physical-call observation is immutable')
        return False
    conn.execute('INSERT INTO organization_route_observations VALUES (?,?,?,?,?,?)',
        (call_id, *values, time.time()))
    return True


def routing_observations(path, *, limit=200):
    """Read one bounded, WAL-aware snapshot without provisioning an organization."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Observation limit must be between 1 and 1000')
    path = Path(path)
    if not path.is_file():
        return {'state': 'unconfigured', 'observations': [], 'shadowRanking': None}
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                    timeout=0.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            rows = conn.execute('''SELECT m.*, d.id AS dispatch_id, d.snapshot,
                u.completed AS stage_completed, u.input_tokens, u.output_tokens,
                c.status AS objective_status, c.round AS objective_round,
                (SELECT count(*) FROM organization_model_calls p
                 WHERE p.request_id=m.request_id AND p.created<=m.created) AS request_call_count
                FROM organization_model_calls m
                JOIN organization_model_call_dispatches l ON l.model_call_id=m.id
                JOIN organization_model_dispatches d ON d.id=l.dispatch_id
                LEFT JOIN objective_usage u ON u.request_id=m.request_id AND u.token=m.token
                LEFT JOIN objective_control c ON c.objective_id=m.objective_id
                ORDER BY m.created DESC, m.id DESC LIMIT ?''', (limit + 1,)).fetchall()
            has_measurements = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_route_observations'").fetchone()
            observations = []
            for row in rows[:limit]:
                dispatch = json.loads(row['snapshot'])
                measurement = conn.execute('SELECT * FROM organization_route_observations WHERE model_call_id=?', (row['id'],)).fetchone() if has_measurements else None
                # Hash rather than expose endpoint identity or complete policy data.
                identity = {key: dispatch.get(key) for key in (
                    'selectedProvider', 'model', 'reasoning', 'endpointIdentity',
                    'policyGeneration', 'maxContextTokens', 'maxOutputTokens', 'pricing')}
                observations.append({
                    'callId': row['id'], 'requestId': row['request_id'],
                    'objectiveId': row['objective_id'], 'dispatchId': row['dispatch_id'],
                    'routeId': dispatch['routeId'], 'provider': row['provider'], 'model': row['model'],
                    'reasoning': dispatch['reasoning'], 'stage': dispatch['requestType'],
                    'configurationFingerprint': hashlib.sha256(json.dumps(identity, sort_keys=True,
                        separators=(',', ':')).encode()).hexdigest(),
                    'reservedCostUsd': row['reserved_cost_usd'], 'actualCostUsd': None,
                    'latencySeconds': measurement['elapsed_seconds'] if measurement else None,
                    'physicalRetries': 0 if measurement else None,
                    'transportOutcome': measurement['outcome'] if measurement else 'unobserved',
                    'providerReportedUsage': {'inputTokens': measurement['input_tokens'],
                        'outputTokens': measurement['output_tokens']} if measurement else None,
                    'requestReservedCallCountAtTime': row['request_call_count'],
                    'ownerRetryEpoch': dispatch.get('ownerRetryEpoch'),
                    'stageCompleted': None if row['stage_completed'] is None else bool(row['stage_completed']),
                    'attemptUsage': {'inputTokens': row['input_tokens'], 'outputTokens': row['output_tokens'],
                        'scope': 'request-attempt-not-physical-call'},
                    'objectiveStatus': row['objective_status'],
                    'objectiveAccepted': row['objective_status'] == 'accepted',
                    'objectiveRound': row['objective_round'],
                    'qualityAttribution': 'unproven',
                })
            from eidolon_cli.organization_routing_readiness import evidence_readiness
            return {'state': 'available', 'observations': observations, 'truncated': len(rows) > limit,
                    'evidenceReadiness': evidence_readiness(observations, truncated=len(rows) > limit),
                    'shadowRanking': None, 'rankingReason': 'insufficient-comparable-verified-cost-and-quality',
                    'selectionChanged': False,
                    'scope': 'local-routed-call-reservations; observations-are-not-proof-of-physical-send'}
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise ValueError('Routing observations unavailable: incompatible or unreadable ledger') from exc

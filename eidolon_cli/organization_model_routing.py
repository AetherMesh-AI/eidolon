"""Owner-declared model assignments and immutable, budget-linked dispatches.

Tiers describe owner assertions, never measured model quality. Selection is local
and deterministic; no classifier, discovery call or inferred provider price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import hashlib
import json
import re
import time
import uuid

MODEL_TYPES = frozenset({'work.draft', 'work.analyze', 'work.inspect', 'work.edit',
    'request.decompose', 'request.plan', 'request.review', 'request.test_review',
    'request.integrate', 'request.accept', 'request.question', 'request.decision', 'request.message'})
REASONING = frozenset({'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'})
ROUTING_FIELDS = ('model_routes', 'model_assignments')
ROUTING_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_model_routing_config (
 id INTEGER PRIMARY KEY CHECK(id=1), configuration TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS organization_model_routing_changes (
 idempotency_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, actor_id TEXT NOT NULL,
 generation INTEGER NOT NULL, configuration TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_model_dispatches (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 snapshot TEXT NOT NULL, created REAL NOT NULL, UNIQUE(request_id,token));
CREATE TABLE IF NOT EXISTS organization_model_call_dispatches (
 model_call_id TEXT PRIMARY KEY REFERENCES organization_model_calls(id),
 dispatch_id TEXT NOT NULL REFERENCES organization_model_dispatches(id));
CREATE TABLE IF NOT EXISTS organization_model_route_failures (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 dispatch_id TEXT NOT NULL REFERENCES organization_model_dispatches(id),
 evidence TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(request_id,token));
"""
for _table in ('organization_model_dispatches', 'organization_model_call_dispatches',
               'organization_model_route_failures', 'organization_model_routing_changes'):
    ROUTING_SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_table}_immutable_update BEFORE UPDATE ON {_table}
 BEGIN SELECT RAISE(ABORT,'Model routing audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS {_table}_immutable_delete BEFORE DELETE ON {_table}
 BEGIN SELECT RAISE(ABORT,'Model routing audit is immutable'); END;
"""


from eidolon_cli.organization_routing_observations import OBSERVATION_SCHEMA
ROUTING_SCHEMA += OBSERVATION_SCHEMA


@dataclass(frozen=True)
class OrganizationModelRoute:
    id: str
    provider: str
    model: str
    reasoning: str
    request_types: tuple[str, ...]
    evidence_level: int
    max_context_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class OrganizationModelAssignment:
    agent_id: str
    mode: str
    route_ids: tuple[str, ...]
    minimum_evidence: int = 1
    max_fallbacks: int = 0


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _name(value, label, maximum=300):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > maximum or '*' in value or any(ord(c) < 32 for c in value)):
        raise ValueError(f'{label} requires exact bounded text')
    return value


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}')
    return value


def parse_model_routes(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('model_routes must be a list of at most 64 owner routes')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != set(OrganizationModelRoute.__dataclass_fields__):
            raise ValueError('Each model route requires exactly id, provider, model, reasoning, request_types, evidence_level, max_context_tokens and max_output_tokens')
        ident = _name(item['id'], 'Route id', 64)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', ident) or ident in seen:
            raise ValueError('Model route ids must be unique simple identifiers')
        seen.add(ident)
        provider, model = (_name(item[key], key) for key in ('provider', 'model'))
        if provider.lower() == 'auto' or not isinstance(item['reasoning'], str) or item['reasoning'] not in REASONING:
            raise ValueError('Routes require an exact provider and explicit supported reasoning mode')
        types = item['request_types']
        if (not isinstance(types, list) or not types or any(not isinstance(t, str) or t not in MODEL_TYPES for t in types)
                or len(types) != len(set(types))):
            raise ValueError('Route request_types must contain unique supported model stages')
        tier = _integer(item['evidence_level'], 'evidence_level', 1, 3)
        context = _integer(item['max_context_tokens'], 'route max_context_tokens', 4096, 2_000_000)
        output = _integer(item['max_output_tokens'], 'route max_output_tokens', 256, 16000)
        if context <= output + 2048:
            raise ValueError('Route context must leave input space beyond output and protocol reserves')
        result.append(OrganizationModelRoute(ident, provider, model, item['reasoning'], tuple(types), tier, context, output))
    return tuple(result)


def parse_model_assignments(value, routes):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('model_assignments must be a list of at most 64 persistent identity assignments')
    known = {route.id for route in routes}
    result, seen = [], set()
    for item in value:
        if (not isinstance(item, dict) or set(item) - set(OrganizationModelAssignment.__dataclass_fields__)
                or not {'agent_id', 'mode', 'route_ids'} <= set(item)):
            raise ValueError('Model assignments require agent_id, mode and route_ids with optional minimum_evidence and max_fallbacks')
        ident = _name(item['agent_id'], 'Assigned agent', 128)
        if ident in seen or ident in {'owner', 'control:apply', 'control:project'}:
            raise ValueError('Model assignments require unique model-running persistent identities')
        seen.add(ident)
        mode, ids = item['mode'], item['route_ids']
        if not isinstance(mode, str) or mode not in {'fixed', 'auto'} or (ident == 'executive' and mode != 'fixed'):
            raise ValueError('Assignments must be Fixed or Auto; the built-in executive must remain Fixed')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 16 or any(not isinstance(r, str) or r not in known for r in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Assignments require unique exact catalogue route ids')
        floor = _integer(item.get('minimum_evidence', 1), 'minimum_evidence', 1, 3)
        fallbacks = _integer(item.get('max_fallbacks', 0), 'max_fallbacks', 0, min(2, len(ids) - 1))
        result.append(OrganizationModelAssignment(ident, mode, tuple(ids), floor, fallbacks))
    return tuple(result)


def assignment_for(settings, agent_id):
    return next((row for row in settings.model_assignments if row.agent_id == agent_id), None)


def assignment_view(settings, agent_id):
    row = assignment_for(settings, agent_id)
    return {'mode': row.mode if row else 'legacy', 'routeIds': list(row.route_ids) if row else [],
            'minimumEvidence': row.minimum_evidence if row else None, 'maxFallbacks': row.max_fallbacks if row else 0,
            'label': ('Fixed owner route' if row.mode == 'fixed' else 'Auto: cheapest eligible owner-declared route') if row else 'Legacy roster/profile inheritance'}


def routing_view(conn, settings):
    policy = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()
    priced = {(rate.provider, rate.model) for rate in settings.model_costs}
    return {'enabled': bool(settings.model_assignments), 'configurationMethod': 'owner-cli',
        'ownerChat': 'separate-legacy-route', 'transport': 'direct-chat-completions-only',
        'fallback': 'invalid-complete-json-tool-free-only', 'evidenceTiers': 'owner-declared',
        'supportedAdapters': ['custom', 'openai'],
        'policyGeneration': policy[0] if policy else 0,
        'routes': [{'id': r.id, 'provider': r.provider, 'model': r.model, 'reasoning': r.reasoning,
                    'requestTypes': list(r.request_types), 'evidenceLevel': r.evidence_level,
                    'maxContextTokens': r.max_context_tokens, 'maxOutputTokens': r.max_output_tokens,
                    'priced': (r.provider, r.model) in priced} for r in settings.model_routes]}


def public_dispatch(row):
    from eidolon_cli.organization_store import _iso
    snapshot = json.loads(row['snapshot'])
    return {key: value for key, value in snapshot.items() if key not in {'endpointIdentity', 'ownerRetryEpoch'}} | {
        'id': row['id'], 'requestId': row['request_id'], 'attemptToken': row['token'], 'createdAt': _iso(row['created'])}


def latest_dispatch(conn, request_id):
    row = conn.execute('SELECT * FROM organization_model_dispatches WHERE request_id=? ORDER BY created DESC LIMIT 1', (request_id,)).fetchone()
    return public_dispatch(row) if row else None


def _floor(kind):
    return 3 if kind == 'request.accept' else 2 if kind in {'request.review', 'request.test_review', 'request.integrate'} else 1


def _candidate(settings, assignment, kind, input_bound, excluded=(), supported=None):
    rates = {(r.provider, r.model): r for r in settings.model_costs}
    routes = {r.id: r for r in settings.model_routes}
    eligible = []
    for ident in assignment.route_ids:
        route = routes[ident]
        if (supported is not None and ident not in supported) or ident in excluded or kind not in route.request_types or route.evidence_level < max(_floor(kind), assignment.minimum_evidence):
            continue
        if route.max_output_tokens < settings.max_output_tokens:
            continue
        output = settings.max_output_tokens
        window = min(settings.max_context_tokens, route.max_context_tokens)
        rate = rates.get((route.provider, route.model))
        if assignment.mode == 'auto' and (rate is None or input_bound + output + 2048 > window):
            continue
        cost = None if rate is None else (Decimal(input_bound + 2048) * Decimal(rate.input_usd_per_million)
                                         + Decimal(output) * Decimal(rate.output_usd_per_million))
        eligible.append((route, rate, cost))
    if assignment.mode == 'auto':
        eligible.sort(key=lambda row: (row[2], row[0].id))
    elif eligible and not excluded and eligible[0][0].id != assignment.route_ids[0]:
        # A capability/configuration denial never authorizes Fixed fallback.
        eligible = []
    if not eligible:
        raise ValueError('No eligible owner model route meets the exact stage, evidence tier, context and configured-price requirements; no model call was sent')
    return eligible[0]


def chain_failures(conn, request_id, generation):
    """Owner retries and policy changes start a new bounded fallback chain.

    Previous attempts, reservations and evidence remain retained. Only automatic
    escalation evidence from this chain can exclude a route or consume fallback.
    """
    epoch = conn.execute('SELECT count(*) FROM retry_receipts WHERE request_id=?', (request_id,)).fetchone()[0]
    rows = conn.execute('SELECT d.snapshot FROM organization_model_route_failures f '
        'JOIN organization_model_dispatches d ON d.id=f.dispatch_id WHERE f.request_id=? '
        'ORDER BY f.rowid', (request_id,)).fetchall()
    snapshots = [json.loads(row[0]) for row in rows]
    return [row for row in snapshots if row['policyGeneration'] == generation
            and row.get('ownerRetryEpoch', 0) == epoch]



class OrganizationModelRoutingStore:
    def record_model_observation(self, claim, call_id, **measurement):
        from eidolon_cli.organization_routing_observations import persist_observation
        # Late transport completion remains valid evidence after lease expiry.
        # Match immutable request/token/call identity, never authorize new work.
        return persist_observation(self.path, claim, call_id, **measurement)

    def model_routing_configuration(self):
        with self._connect() as conn:
            return {**routing_view(conn, self.settings), 'assignments': [asdict(row) for row in self.settings.model_assignments]}

    def configure_model_routing(self, configuration, *, expected_generation, idempotency_key, home=None):
        if not isinstance(configuration, dict) or set(configuration) != set(ROUTING_FIELDS):
            raise ValueError('Routing configuration requires exactly model_routes and model_assignments; prices and grants are separate owner policy')
        _integer(expected_generation, 'expected_generation', 1, 2**63 - 1)
        key = _name(idempotency_key, 'Routing idempotency key', 128)
        digest = hashlib.sha256(_json({'configuration': configuration, 'generation': expected_generation}).encode()).hexdigest()
        with self._write() as conn:
            previous = conn.execute('SELECT * FROM organization_model_routing_changes WHERE idempotency_key=?', (key,)).fetchone()
            if previous:
                if previous['input_hash'] != digest:
                    raise ValueError('Routing idempotency key belongs to another configuration')
                return {'changed': False, 'policyGeneration': previous['generation']}
            if home is not None:
                from eidolon_cli.organization_config import load_profile_configuration, profile_configuration_scope
                with profile_configuration_scope(home):
                    settings, _ = load_profile_configuration(home)
                    self._reload_configuration(conn, settings)
            self._require_current_policy(conn)
            if expected_generation != self._policy_generation:
                raise ValueError('Organization policy changed; reload before configuring model routes')
            routes = parse_model_routes(configuration['model_routes'])
            assignments = parse_model_assignments(configuration['model_assignments'], routes)
            known = {row[0] for row in conn.execute('SELECT id FROM agents')}
            if any(a.agent_id not in known for a in assignments):
                raise ValueError('Model assignments must identify existing persistent agents')
            updated = replace(self.settings, model_routes=routes, model_assignments=assignments)
            from eidolon_cli.organization_owner_policy_config import managed_fields, resolve_owner_overrides
            locked = managed_fields()
            authoritative = resolve_owner_overrides(conn, self.settings)
            if set(ROUTING_FIELDS) <= locked or any(getattr(updated, field) != getattr(authoritative, field)
                                                    for field in set(ROUTING_FIELDS) & locked):
                raise ValueError('Model routing configuration is managed and cannot be changed here')
            conn.execute('INSERT INTO organization_model_routing_config VALUES (1,?) ON CONFLICT(id) DO UPDATE SET configuration=excluded.configuration', (_json(configuration),))
            self.settings = updated
            self._adopt_policy(conn, resume=True)
            conn.execute('INSERT INTO organization_model_routing_changes VALUES (?,?,?,?,?,?)',
                         (key, digest, 'owner', self._policy_generation, _json(configuration), time.time()))
            self._event(conn, None, 'Owner updated model catalogue and assignments; persistent identities and memory were retained.', 'configuration', 'owner')
            return {'changed': True, 'policyGeneration': self._policy_generation}

    def select_model_route(self, claim, *, input_bound):
        _integer(input_bound, 'Exact input bound', 1, 100_000_000)
        with self._connect() as conn:
            request = self._owned(conn, claim)
            if request is None:
                raise ValueError('Request is no longer owned; no route was selected')
            return self._selected_model_route(conn, request, input_bound)

    def _selected_model_route(self, conn, request, input_bound):
        assignment = assignment_for(self.settings, request['agent_id'])
        if assignment is None:
            return None
        failures = chain_failures(conn, request['id'], self._policy_generation)
        excluded = [row['routeId'] for row in failures]
        if len(excluded) > assignment.max_fallbacks:
            raise ValueError('The configured model fallback allowance is exhausted')
        from eidolon_cli.config import load_config_readonly
        from eidolon_cli.organization_model_routing_executor import require_supported_route
        config = load_config_readonly()
        supported = set()
        for item in self.settings.model_routes:
            try:
                require_supported_route({'selectedProvider': item.provider}, config)
            except ValueError:
                continue
            supported.add(item.id)
        if assignment.mode == 'fixed' and assignment.route_ids[len(excluded)] not in supported:
            raise ValueError('The assigned Fixed route uses an unsupported transport; only generic custom/named-custom and OpenAI direct chat-completions are supported')
        if failures and assignment.mode == 'auto':
            assignment = replace(assignment, minimum_evidence=max(assignment.minimum_evidence, *(r['evidenceLevel'] for r in failures)))
        route, rate, _ = _candidate(self.settings, assignment, request['type'], input_bound, excluded, supported)
        return {'routeId': route.id, 'mode': assignment.mode, 'selectedProvider': route.provider, 'model': route.model,
            'reasoning': route.reasoning, 'policyGeneration': self._policy_generation,
            'agentId': request['agent_id'], 'requestType': request['type'], 'evidenceLevel': route.evidence_level,
            'minimumEvidence': max(_floor(request['type']), assignment.minimum_evidence),
            'maxContextTokens': min(self.settings.max_context_tokens, route.max_context_tokens),
            'maxOutputTokens': self.settings.max_output_tokens,
            'ownerRetryEpoch': conn.execute('SELECT count(*) FROM retry_receipts WHERE request_id=?', (request['id'],)).fetchone()[0],
            'inputBound': input_bound, 'selectionReason': 'ordered-fixed-fallback' if excluded and assignment.mode == 'fixed' else 'cheapest-eligible-configured-ceiling' if assignment.mode == 'auto' else 'owner-fixed-route',
            'pricing': {'inputUsdPerMillion': rate.input_usd_per_million if rate else None,
                        'outputUsdPerMillion': rate.output_usd_per_million if rate else None}}

    def _bind_model_dispatch(self, conn, request, snapshot, *, provider, model, input_limit, output_limit):
        assignment = assignment_for(self.settings, request['agent_id'])
        if assignment is None:
            if snapshot is not None:
                raise ValueError('Legacy requests cannot inject a model dispatch')
            return None
        if not isinstance(snapshot, dict):
            raise ValueError('An assigned model route requires a verified dispatch at the final send boundary')
        candidate = dict(snapshot)
        endpoint = candidate.pop('endpointIdentity', None)
        adapter = candidate.pop('adapterProvider', None)
        expected = self._selected_model_route(conn, request, candidate.get('inputBound'))
        if candidate != expected or not isinstance(endpoint, str) or not re.fullmatch('[0-9a-f]{64}', endpoint) or not isinstance(adapter, str) or not adapter:
            raise ValueError('Model dispatch changed after selection; no model call was sent')
        if (provider != snapshot['selectedProvider'] or model != snapshot['model']
                or input_limit + output_limit > snapshot['maxContextTokens'] or output_limit != snapshot['maxOutputTokens']):
            raise ValueError('The model wire route or token cap disagrees with its immutable dispatch')
        old = conn.execute('SELECT * FROM organization_model_dispatches WHERE request_id=? AND token=?', (request['id'], request['token'])).fetchone()
        serialized = _json(snapshot)
        if old:
            if old['snapshot'] != serialized:
                raise ValueError('Model dispatch is immutable throughout an attempt and all evidence passes')
            return old['id']
        ident = 'dispatch_' + uuid.uuid4().hex
        conn.execute('INSERT INTO organization_model_dispatches VALUES (?,?,?,?,?)', (ident, request['id'], request['token'], serialized, time.time()))
        return ident

    def finish_model_route_failure(self, claim, failure):
        """Called only by the coordinator after its worker and provider exited."""
        from eidolon_cli.organization_model_routing_executor import ModelRouteFailure
        if not isinstance(failure, ModelRouteFailure):
            raise ValueError('Model fallback requires typed backend evidence')
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            row = conn.execute('SELECT * FROM organization_model_dispatches WHERE request_id=? AND token=?', (request['id'], request['token'])).fetchone()
            assignment = assignment_for(self.settings, request['agent_id'])
            calls = conn.execute('SELECT count(*) FROM organization_model_calls WHERE request_id=? AND token=?', (request['id'], request['token'])).fetchone()[0]
            tools = conn.execute('SELECT 1 FROM tool_receipts WHERE request_id=? AND attempt=?', (request['id'], request['execution_count'])).fetchone()
            count = len(chain_failures(conn, request['id'], self._policy_generation))
            if (row is None or assignment is None or failure.kind != 'invalid-complete-json' or calls != 1 or tools
                    or request['type'] in {'work.inspect', 'work.edit'} or not re.fullmatch('[0-9a-f]{64}', failure.response_sha256)):
                raise ValueError('This outcome cannot authorize automatic model fallback')
            evidence = {'kind': failure.kind, 'responseSha256': failure.response_sha256,
                        'finishReason': 'stop', 'toolFree': True, 'providerRejected': False}
            conn.execute('INSERT INTO organization_model_route_failures VALUES (?,?,?,?,?)', (request['id'], request['token'], row['id'], _json(evidence), time.time()))
            current = json.loads(row['snapshot'])
            reason = 'The provider returned complete invalid JSON; its reservation is retained.'
            try:
                self._selected_model_route(conn, request, current['inputBound'])
                eligible = count < assignment.max_fallbacks and request['attempts'] < self.settings.max_attempts
            except ValueError:
                eligible = False
            if not eligible:
                self._pending(conn, request, reason + ' No eligible authorized fallback remains.')
                return False
            conn.execute("UPDATE requests SET status='queued',reason=?,token=NULL,lease=NULL,available=? WHERE id=?", (reason + ' A fresh bounded model fallback is queued.', time.time(), request['id']))
            if request['task_id']:
                conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (request['task_id'],))
            self._event(conn, request['objective_id'], reason + ' A fresh bounded model fallback is queued.', 'model_fallback', request['agent_id'])
            return True

"""Bounded accepted dependency choices; historical receipts confer no new access.

Collection and validation only read the existing ledger. The public projection
contains no artifact bytes or private project bindings. Its private digest binds
all offered receipts, owner identity and currently authorized route/project scope.
Semantic suggestions, including still-open objectives, always require review.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from eidolon_cli.organization_dependency_receipts import accepted_dependency_receipt
from eidolon_cli.organization_identity import _leader_enabled
from eidolon_cli.organization_intake_evidence import _safe_text
from eidolon_cli.organization_projects import selected_projects

MAX_CANDIDATES = 32
MAX_PUBLIC_BYTES = 12_000
MAX_TITLE_CHARS = 120
MAX_DESCRIPTION_CHARS = 240
_CONTEXT_FIELDS = ('ownerIdentityId', 'dependencyCandidates', 'dependencyContextDigest',
                   'dependencyContextStatus', 'dependencyContextTruncated', 'dependencyContextLimitations')
_UNAVAILABLE = 'Accepted dependency context is unavailable; resolve dependencies before admission.'
_TRUNCATED = 'Accepted dependency context is bounded and omits objectives or metadata; omitted work is not eligible automatically.'
_INELIGIBLE = 'Some accepted objectives lack a current authorized route/project binding or an eligible historical receipt.'
_REDACTED = 'Private paths or sensitive text were removed from accepted objective metadata.'
_INVALID = 'Accepted dependency storage or authority could not be verified; no dependency is eligible automatically.'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _authority(store, conn):
    store._require_current_policy(conn)
    rows = conn.execute('SELECT a.*,i.identity_id FROM agents a '
                        'JOIN agent_identity i ON i.agent_id=a.id ORDER BY a.id').fetchall()
    agents = {row['id']: row for row in rows}
    owner = agents.get('owner')
    if owner is None or not isinstance(owner['identity_id'], str) or not owner['identity_id']:
        raise ValueError('Current owner identity is unavailable')
    projects = []
    for project in store.settings.projects:
        if project.root not in store.settings.revoked_root_aliases:
            projects.extend(selected_projects([project.id], store.settings))
    routes = {}
    for manager in rows:
        if manager['role'] != 'Manager' or not _leader_enabled(conn, manager):
            continue
        accepts = json.loads(manager['accepts'])
        if not isinstance(accepts, list) or any(not isinstance(item, str) for item in accepts):
            raise ValueError('Manager authority is malformed')
        if not {'request.plan', 'request.integrate'}.issubset(accepts):
            continue
        executive = agents.get(manager['manager_id'])
        if executive is None or executive['role'] != 'Executive' or not _leader_enabled(conn, executive):
            continue
        accepts = json.loads(executive['accepts'])
        if not isinstance(accepts, list) or any(not isinstance(item, str) for item in accepts):
            raise ValueError('Executive authority is malformed')
        if 'request.accept' not in accepts:
            continue
        routes[manager['id']] = {'managerId': manager['id'], 'executiveId': executive['id'],
            'managerIdentityId': manager['identity_id'], 'executiveIdentityId': executive['identity_id'],
            'projects': sorted((item for item in projects if item['team'] == manager['team']), key=lambda item: item['id'])}
    return owner['identity_id'], routes


def _metadata(row, receipt, roots):
    texts = {}
    truncated = redacted = False
    for name, maximum, stored_limit in [('title', MAX_TITLE_CHARS, 500),
                                        ('description', MAX_DESCRIPTION_CHARS, 30_000)]:
        value = row[name]
        if not isinstance(value, str) or not value or len(value) > stored_limit or '\x00' in value:
            raise ValueError('Accepted objective metadata is malformed')
        value.encode('utf-8')
        safe = _safe_text(value, roots)
        redacted = redacted or safe != value
        truncated = truncated or len(safe) > maximum
        texts[name] = safe[:maximum]
    return {'objectiveId': row['id'], **texts, 'managerId': row['manager_id'],
            'executiveId': row['executive_id'],
            'projectIds': [item['id'] for item in receipt['projects']],
            'receiptSha256': _digest_receipt(receipt),
            'metadataTruncated': truncated, 'metadataRedacted': redacted}


def _digest_receipt(receipt):
    # Receipt identity uses the existing canonical outcome-contract hash format.
    from eidolon_cli.organization_outcome_contracts import digest
    return digest(receipt)


def _collect(store, conn):
    """Return the safe payload and private pins, in the caller's read snapshot."""
    candidates, pins, bindings, limitations = [], {}, [], []
    owner_id, routes = None, {}
    truncated = False
    status = 'unavailable'
    try:
        owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
        owner_id = owner[0] if owner else None
        owner_id, routes = _authority(store, conn)
        rows = conn.execute('SELECT o.id,o.title,o.description,a.manager_id,a.executive_id '
            'FROM objectives o JOIN objective_control c ON c.objective_id=o.id '
            'JOIN objective_assignments a ON a.objective_id=o.id '
            'LEFT JOIN objective_history h ON h.objective_id=o.id '
            "WHERE o.cancelled=0 AND coalesce(h.archived,0)=0 AND c.status='accepted' "
            'ORDER BY o.id LIMIT ?', (MAX_CANDIDATES + 1,)).fetchall()
        truncated = len(rows) > MAX_CANDIDATES
        for row in rows[:MAX_CANDIDATES]:
            route = routes.get(row['manager_id'])
            if route is None or route['executiveId'] != row['executive_id']:
                limitations.append(_INELIGIBLE)
                continue
            receipt = accepted_dependency_receipt(conn, row['id'])
            if receipt is None:
                limitations.append(_INELIGIBLE)
                continue
            value = receipt.to_dict()
            project_ids = [item['id'] for item in value['projects']]
            if (not set(project_ids).issubset(item['id'] for item in route['projects'])
                    or selected_projects(project_ids, store.settings) != value['projects']):
                limitations.append(_INELIGIBLE)
                continue
            candidate = _metadata(row, value, store.settings.read_roots)
            if len(_json([*candidates, candidate]).encode('utf-8')) > MAX_PUBLIC_BYTES:
                truncated = True
                break
            candidates.append(candidate)
            truncated = truncated or candidate['metadataTruncated']
            if candidate['metadataRedacted']:
                limitations.append(_REDACTED)
            pin = {'objectiveId': row['id'], 'receiptSha256': receipt.sha256, 'receipt': value,
                   'ownerIdentityId': owner_id, 'managerId': row['manager_id'],
                   'executiveId': row['executive_id'], 'projectIds': project_ids}
            pins[row['id']] = pin
            bindings.append({'metadata': dict(row), 'pin': pin})
        status = 'available' if candidates else 'unavailable'
    except (ValueError, sqlite3.DatabaseError, KeyError, IndexError, TypeError, UnicodeError, RecursionError):
        # Do not silently turn malformed acceptance into eligibility. A caller
        # can still triage unrelated requests, but cannot consume any dependency.
        candidates, pins, bindings = [], {}, []
        status = 'unavailable'
        limitations.append(_INVALID)
    if truncated:
        limitations.append(_TRUNCATED)
    if status == 'unavailable':
        limitations.append(_UNAVAILABLE)
    limitations = sorted(set(limitations))
    payload = {'ownerIdentityId': owner_id, 'dependencyCandidates': candidates,
               'dependencyContextStatus': status, 'dependencyContextTruncated': truncated,
               'dependencyContextLimitations': limitations}
    payload['dependencyContextDigest'] = _digest({'schemaVersion': 1, **payload,
        'policyGeneration': store._policy_generation, 'policyFingerprint': store._policy_fingerprint,
        'routes': routes, 'bindings': bindings})
    return payload, pins, routes


def collect_dependency_context(store, conn):
    """Collect bounded public choices plus a private receipt/authority digest.

    No schema migrations, source reads, model calls, activation or writes occur.
    Preserve any existing transaction; otherwise keep all reads in one snapshot.
    """
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        return _collect(store, conn)[0]
    finally:
        if own_transaction:
            conn.rollback()


# Deliberately a small explicit language, not a semantic intent classifier. An
# exact ID in a question, quotation, hypothetical or negative sentence is data.
_USE_DIRECTIVE = re.compile(r'(?i:use objective) (?P<id>[A-Za-z0-9_-]+) (?i:to) (?P<purpose>[^\r\n]+)')
_PREREQUISITE_DIRECTIVE = re.compile(r'(?i:prerequisite): (?P<id>[A-Za-z0-9_-]+)')
_AMBIGUOUS_DIRECTIVE = re.compile(
    r"\b(?:not|never|no|without|unless|if|whether|maybe|perhaps|might|could|would|should|"
    r"except|instead|rather|hypothetical|hypothetically|example|quote|quoted|said|says|"
    r"told|avoid|exclude|ignore|skip|pretend)\b|n['’]t\b", re.IGNORECASE)


def _directive_ids(text):
    """Recognize only complete, affirmative, owner-authored directive lines."""
    # Apostrophes inside words permit ordinary possessives. Remaining quotes,
    # code delimiters and question punctuation make the message review-only.
    without_apostrophes = re.sub(r"(?<=\w)['’](?=\w)", '', text)
    if (any(char in without_apostrophes for char in '\'"`“”‘’«»?？؟')
            or _AMBIGUOUS_DIRECTIVE.search(text)):
        return None
    identifiers = set()
    for raw in text.splitlines():
        if not raw.strip():
            continue
        # Do not reinterpret indented code, quotations or surrounding prose as
        # an owner directive. Every nonempty line must use the explicit syntax.
        line = raw.rstrip()
        match = _PREREQUISITE_DIRECTIVE.fullmatch(line) or _USE_DIRECTIVE.fullmatch(line)
        if match is None:
            return None
        if 'purpose' in match.groupdict() and not any(char.isalnum() for char in match['purpose']):
            return None
        identifiers.add(match['id'])
    return identifiers or None


_DECLARED_DEPENDENCY = re.compile(
    r'(?m)^(?:(?i:use objective) (?P<use>[A-Za-z0-9_-]+) (?i:to)(?:[ \t]|$)|'
    r'(?i:prerequisite): (?P<prerequisite>[A-Za-z0-9_-]+)(?![\w-]))')


def _declared_dependency_ids(text):
    # This conservative presence check can force review, but can never authorize
    # a dependency. Keep it separate from the stricter affirmative language so
    # uncertain punctuation, purpose or surrounding prose cannot hide a stated
    # prerequisite simply by making the model return an empty selection.
    return {match['use'] or match['prerequisite'] for match in _DECLARED_DEPENDENCY.finditer(text)}


def _owner_directed_dependency(owner_texts, identifier):
    pattern = re.compile(r'(?<![\w-])' + re.escape(identifier) + r'(?![\w-])')
    mentions = [text for text in owner_texts if pattern.search(text)]
    # Any conflicting or ambiguous owner mention needs resolution. The model
    # cannot discard it and keep only a convenient affirmative-looking quote.
    return bool(mentions) and all(identifier in (_directive_ids(text) or ()) for text in mentions)


def validate_automatic_dependencies(store, conn, request_text, proposal, context_payload, *, owner_texts=None):
    """Return (private pins, None), or (None, explicit review reason), no writes.

    The proposal and request must be the normalized proposal and owner-authored
    request stored by intake. ``context_payload`` must be its server-owned saved
    snapshot, not model response data. Call again inside admission's transaction.
    ``owner_texts`` may supply exact independently retained owner messages for a
    clarification child. Never derive them by parsing generated question text.
    An empty selection is allowed only when no explicit owner prerequisite is
    present. The model cannot omit an owner constraint to bypass dependency checks.
    """
    selected = proposal.get('dependencyCandidateIds')
    if not isinstance(selected, list) or any(not isinstance(item, str) or not item for item in selected):
        return None, 'Dependency selection is malformed; review is required.'
    if len(selected) > 12 or len(set(selected)) != len(selected):
        return None, 'Dependency selection is duplicated or exceeds its bound; review is required.'
    if not isinstance(request_text, str):
        return None, 'Dependency owner request is unavailable; review is required.'
    if owner_texts is None:
        if '\n\nClarification question: ' in request_text or '\nOwner answer: ' in request_text:
            # Delimiters are not provenance. A declaration anywhere in combined
            # history can only force review, never establish owner authority.
            if selected or _declared_dependency_ids(request_text):
                return None, 'Clarification dependency intent needs independently retained owner messages; review is required.'
            return [], None
        owner_texts = [request_text]
    if (not isinstance(owner_texts, (list, tuple)) or not owner_texts or len(owner_texts) > 13
            or any(not isinstance(text, str) or not text.strip() or len(text) > 12_000 for text in owner_texts)):
        return None, 'Dependency owner-message provenance is unavailable; review is required.'
    # Find every explicit declaration before the empty-selection fast path.
    # A line in surrounding prose/quoted material is only a possible constraint:
    # _owner_directed_dependency still requires whole-message affirmative intent.
    required = {identifier for text in owner_texts for identifier in _declared_dependency_ids(text)}
    if required:
        candidates = context_payload.get('dependencyCandidates', []) if isinstance(context_payload, dict) else []
        offered = {item['objectiveId'] for item in candidates
                   if isinstance(item, dict) and isinstance(item.get('objectiveId'), str)} if isinstance(candidates, list) else set()
        if not required.issubset(offered):
            return None, 'An explicit owner prerequisite is unknown, unavailable or omitted from the bounded context; review is required.'
        if not required.issubset(selected):
            return None, 'The proposal omitted an explicit owner prerequisite; every declared dependency must be selected or reviewed.'
    if set(selected) - required:
        return None, ('The owner request must explicitly authorize the exact objective with an affirmative dependency directive '
                      '(Use objective <id> to ... or Prerequisite: <id>); ambiguous references require review.')
    if not selected:
        return [], None
    if not isinstance(context_payload, dict):
        return None, 'Dependency context is unavailable; review is required.'
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        fresh, pins, routes = _collect(store, conn)
        if context_payload.get('ownerIdentityId') != fresh['ownerIdentityId']:
            return None, 'Dependency owner identity changed; reevaluate the request before admission.'
        if fresh['dependencyContextStatus'] != 'available':
            return None, _UNAVAILABLE
        if any(context_payload.get(key) != fresh[key] for key in _CONTEXT_FIELDS):
            return None, 'Accepted dependency context or receipt changed; reevaluate the request before admission.'
        if any(identifier not in pins for identifier in selected):
            return None, 'A dependency is open, unaccepted, omitted or unavailable; resolve it before admission.'
        route = routes.get(proposal.get('routeId'))
        if (route is None or proposal.get('managerId') != route['managerId']
                or proposal.get('executiveId') != route['executiveId']):
            return None, 'Dependency destination route is unavailable or changed; review is required.'
        try:
            projects = selected_projects(proposal.get('projectIds'), store.settings)
        except (ValueError, KeyError, TypeError):
            return None, 'Dependency destination projects are unavailable or changed; review is required.'
        if (not isinstance(proposal.get('projectIds'), list)
                or not {item['id'] for item in projects}.issubset(item['id'] for item in route['projects'])):
            return None, 'Dependency destination projects are outside the selected route; review is required.'
        result = []
        for identifier in selected:
            pin = pins[identifier]
            if pin['managerId'] != route['managerId'] or pin['executiveId'] != route['executiveId']:
                return None, 'Dependency and destination must have the same current manager and executive; review is required.'
            if pin['receipt']['projects'] != projects:
                return None, 'Dependency and destination require identical selected project bindings; review is required.'
            if not _owner_directed_dependency(owner_texts, identifier):
                return None, ('The owner request must explicitly authorize the exact objective with an affirmative dependency directive '
                              '(Use objective <id> to ... or Prerequisite: <id>); ambiguous references require review.')
            result.append(pin)
        return result, None
    finally:
        if own_transaction:
            conn.rollback()

"""Bounded, historical source observations for intake, never a new read grant.

Only already-reviewed task receipts can be reused. Configured roots, staff and
leaders do not authorize pre-objective filesystem access. Fresh inspection needs
its own audited worker stage; this module never opens a project file or calls a
provider. Public excerpts are routing context, never acceptance proof.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from agent.redact import redact_sensitive_text
from eidolon_cli.organization_intake_contract import _object
from eidolon_cli.organization_projects import selected_projects, task_project
from eidolon_cli.organization_receipts import successful_observation
from eidolon_cli.organization_root_grants import expected_root_identities
from tools.organization_file_read import (
    OrganizationFileReadError, _check_lexical_path, _check_project_write_path,
    _path_parts,
)

MAX_CANDIDATES = 24
MAX_ITEMS = 4
MAX_PUBLIC_BYTES = 6000
MAX_EXCERPT_BYTES = 900
_MAX_RESULT_CHARS = 20_000
_MAX_ARTIFACT_CHARS = 100_000
_CONTEXT_NAMES = frozenset({'readme', 'readme.md', 'readme.rst', 'readme.txt',
    'package.json', 'pyproject.toml', 'cargo.toml', 'go.mod', 'context.md',
    'context.txt', 'architecture.md', 'contributing.md'})
_HOST_PATH = re.compile(r"(?<![\w:/])(?:/[\w.~-][^\s<>\"'`]*|[A-Za-z]:[\\/][^\s<>\"'`]+|\\\\[^\s<>\"'`]+|~[\\/][^\s<>\"'`]+)")
_FRESH_REASON = ('No independently authorized pre-objective inspection stage exists. '
                 'Configured folders and retained task receipts do not grant fresh file access.')
_LIMITATIONS = {
    'unavailable': 'No eligible retained observation is available under the current grants.',
    'ambiguous': 'Some observations lack an unambiguous current owner, project, task or review binding.',
    'stale': 'Some observations have changed or revoked policy, project, evidence or review bindings.',
    'invalid': 'Some retained observations are malformed, protected, unresolved or fail their recorded hash.',
    'truncated': 'The bounded projection omits observations or source text; it is not complete evidence.',
}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_encoded(value).encode('utf-8')).hexdigest()


def _parse(raw, maximum):
    if not isinstance(raw, str) or len(raw) > maximum:
        raise ValueError('Retained data exceeds its bound')
    raw.encode('utf-8')
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _safe_text(text, roots):
    # Redact the complete retained text before truncation. The non-code pass also
    # removes opaque password/config assignments that source-mode leaves intact.
    text = redact_sensitive_text(text, force=True, file_read=True, redact_url_credentials=True)
    text = '\n'.join(
        line if redact_sensitive_text(line, force=True, redact_url_credentials=True) == line
        else '[redacted sensitive line]' for line in text.split('\n'))
    for root in sorted(roots, key=len, reverse=True):
        text = text.replace(root, '[configured root]')
    return _HOST_PATH.sub('[host path]', text)


def _alias(path, binding, *, directory=False):
    parts = _path_parts(path, absolute=False)
    if len(path) > 1024 or parts[0] != binding['root'] or (len(parts) < 2 and not directory):
        raise ValueError('Retained observation has an invalid root alias')
    # These checks are lexical only: historical projection does not resolve a
    # mutable source path or reinterpret a symlink as historical authority.
    absolute = (*_path_parts(binding['readRoot'], absolute=True), *parts[1:])
    _check_lexical_path(absolute, ())
    _check_project_write_path(absolute)
    if redact_sensitive_text(path, force=True, redact_url_credentials=True) != path:
        raise ValueError('Retained alias contains sensitive text')
    return path


def _relevant(path):
    return path.rsplit('/', 1)[-1].lower() in _CONTEXT_NAMES


def _excerpt(row, binding, roots):
    name = row['tool_name']
    args = _parse(row['arguments'], 4096)
    result = _parse(row['result'], _MAX_RESULT_CHARS)
    if not isinstance(args, dict) or not isinstance(result, dict):
        raise ValueError('Retained observation is not an object')
    fields = {'read_file': {'path', 'offset', 'limit'}, 'list_files': {'path', 'limit'},
              'search_files': {'path', 'limit', 'query'}}[name]
    if set(args) - fields:
        raise ValueError('Retained observation arguments are invalid')
    path = _alias(args.get('path'), binding, directory=name != 'read_file')
    limits = {'offset': 100_000, 'limit': 2000 if name == 'read_file' else 100}
    for key, maximum in limits.items():
        if key in args and (type(args[key]) is not int or not 1 <= args[key] <= maximum):
            raise ValueError('Retained observation range is invalid')
    if name == 'search_files':
        query = args.get('query')
        if (not isinstance(query, str) or not 1 <= len(query) <= 256
                or any(ord(char) < 32 for char in query)
                or redact_sensitive_text(query, force=True, file_read=True, redact_url_credentials=True) != query):
            raise ValueError('Retained search query is invalid')
    if not successful_observation(row['result'], name) or type(result.get('truncated')) is not bool:
        raise ValueError('Retained observation has no successful bounded source range')
    truncated = result['truncated']
    if name == 'read_file':
        if not _relevant(path):
            return None
        text = result['content']
        if result.get('contentFormat') != 'raw':
            text = re.sub(r'(?m)^\d+\|', '', text)
        if result.get('sourceExists') is False:
            return None
        truncated = truncated or args.get('offset', 1) != 1
    else:
        entries = result['files' if name == 'list_files' else 'matches']
        if len(entries) > 100:
            raise ValueError('Retained discovery exceeds its source bound')
        lines = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError('Retained discovery entry is malformed')
            target = _alias(item.get('path'), binding)
            if not _relevant(target):
                continue
            if name == 'list_files':
                if item.get('kind') == 'file':
                    lines.append(target)
            else:
                if (not isinstance(item.get('text'), str) or type(item.get('line')) is not int
                        or item['line'] < 1 or type(item.get('truncated')) is not bool):
                    raise ValueError('Retained search entry is malformed')
                lines.append(f"{target}:{item['line']} {_safe_text(item['text'], roots)}")
        if not lines:
            return None
        text = '\n'.join(lines)
        # A listing or match never represents complete source-file contents.
        truncated = True
    text.encode('utf-8')
    if any(ord(char) < 32 and char not in '\n\r\t\f' for char in text):
        raise ValueError('Retained content has binary or control text')
    safe = _safe_text(text, roots)
    raw = safe.encode('utf-8')
    excerpt = raw[:MAX_EXCERPT_BYTES].decode('utf-8', errors='ignore')
    return {'path': path, 'excerpt': excerpt, 'sourceTruncated': truncated,
            'excerptTruncated': len(raw) > MAX_EXCERPT_BYTES, 'redacted': safe != text}


def _review(conn, row):
    review = conn.execute(
        'SELECT v.*,r.status AS request_status,r.type AS request_type,r.task_id AS request_task_id, '
        'r.objective_id AS request_objective_id FROM reviews v JOIN requests r ON r.id=v.request_id '
        'WHERE v.task_id=? ORDER BY v.created DESC,v.request_id DESC LIMIT 1', (row['task_id'],)).fetchone()
    if review is None:
        return None
    evidence_ids = _parse(review['evidence_ids'], 20_000)
    if (review['request_status'] != 'completed' or review['approved'] != 1
            or review['request_type'] != 'request.review' or review['request_task_id'] != row['task_id']
            or review['request_objective_id'] != row['objective_id']
            or not isinstance(evidence_ids, list) or row['evidence_id'] not in evidence_ids):
        return None
    # Completion already validates context audits when present. A retained
    # overflow can never be recast as accepted input, even if a row was damaged.
    reports = conn.execute('SELECT report FROM organization_context_receipts WHERE request_id=? '
                           'ORDER BY created DESC LIMIT 2', (review['request_id'],)).fetchall()
    for retained in reports:
        report = _parse(retained['report'], 100_000)
        if (not isinstance(report, dict) or report.get('status') != 'complete'
                or type(report.get('inputTokenUpperBound')) is not int
                or type(report.get('inputLimitTokens')) is not int
                or not 0 <= report['inputTokenUpperBound'] <= report['inputLimitTokens']):
            return None
    return {'requestId': review['request_id'], 'evidenceIdsSha256': _hash(review['evidence_ids']),
            'contextSha256': [_hash(retained['report']) for retained in reports]}


def _candidates(conn, identifiers):
    placeholders = ','.join('?' for _ in identifiers)
    # SQL bounds the fetched bodies as well as candidate count. Invalid large
    # bodies are represented as null, never silently shortened and accepted.
    return conn.execute(f'''SELECT tr.id,tr.request_id,tr.attempt,tr.tool_name,tr.status,
        CASE WHEN length(tr.arguments)<=4096 THEN tr.arguments END AS arguments,
        CASE WHEN length(tr.result)<={_MAX_RESULT_CHARS} THEN tr.result END AS result,
        tr.result_sha256,tr.completed,r.objective_id,r.task_id,r.status AS request_status,
        r.execution_count,r.type AS request_type,t.status AS task_status,p.project_id,
        e.id AS evidence_id,e.sha256 AS evidence_sha256,
        CASE WHEN length(e.content)<={_MAX_ARTIFACT_CHARS} THEN e.content END AS evidence_content,
        i.owner_identity_id,i.status AS intake_status,rp.generation AS request_generation
        FROM tool_receipts tr JOIN requests r ON r.id=tr.request_id
        JOIN tasks t ON t.id=r.task_id AND t.objective_id=r.objective_id
        JOIN task_projects p ON p.task_id=t.id AND p.objective_id=r.objective_id
        LEFT JOIN evidence_tools et ON et.receipt_id=tr.id
        LEFT JOIN evidence e ON e.id=et.evidence_id AND e.request_id=r.id
            AND e.task_id=t.id AND e.objective_id=r.objective_id
        LEFT JOIN organization_request_intakes i ON i.objective_id=r.objective_id
        LEFT JOIN request_policy rp ON rp.request_id=r.id
        WHERE p.project_id IN ({placeholders}) AND tr.tool_name IN ('read_file','list_files','search_files')
        ORDER BY tr.completed DESC,tr.id DESC LIMIT ?''', (*identifiers, MAX_CANDIDATES + 1)).fetchall()


def collect_intake_evidence(store, conn, *, project_ids=None):
    """Return separate public context and server-only current binding receipts.

    ``conn`` is the caller's existing ledger transaction. ``None`` considers all
    explicitly registered projects without choosing a project for the request.
    No policy is fabricated, no filesystem read is replayed, and no receipt can
    establish a verified current blocker or completion/acceptance evidence.
    """
    public = {'status': 'unavailable', 'freshReadStatus': 'unavailable',
              'freshReadReason': _FRESH_REASON, 'items': [], 'limitations': [], 'truncated': False}
    owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
    private = {'ownerIdentityId': owner[0] if owner else None,
               'policyGeneration': store._policy_generation, 'policyFingerprint': store._policy_fingerprint,
               'projectBindings': [], 'rootIdentities': expected_root_identities(store.settings), 'receipts': []}
    issues = set()
    try:
        store._require_current_policy(conn)
        identifiers = project_ids if project_ids is not None else [p.id for p in store.settings.projects]
        bindings = selected_projects(identifiers, store.settings)
        private['projectBindings'] = bindings
    except ValueError:
        bindings = []
        issues.add('stale')
    registry = {binding['id']: binding for binding in bindings}
    rows = _candidates(conn, tuple(registry)) if registry and owner else []
    if len(rows) > MAX_CANDIDATES:
        issues.add('truncated')
    for candidate in rows[:MAX_CANDIDATES]:
        row = dict(candidate)
        binding = registry[row['project_id']]
        proof = {'receiptId': row['id'] if isinstance(row['id'], str) else None,
                 'resultSha256': row['result_sha256'] if isinstance(row['result_sha256'], str) else None,
                 'bindingSha256': _hash(binding)}
        private['receipts'].append(proof)
        try:
            proof['recordSha256'] = _hash(row)
            if (not isinstance(row['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', row['id'])
                    or type(row['completed']) not in (int, float)
                    or not math.isfinite(row['completed']) or row['completed'] < 0):
                raise ValueError('Retained receipt identity or time is malformed')
            if (row['owner_identity_id'] != owner[0] or row['intake_status'] != 'admitted'
                    or row['evidence_id'] is None):
                issues.add('ambiguous')
                continue
            if (row['request_generation'] != store._policy_generation
                    or task_project(conn, row['task_id']) != binding):
                issues.add('stale')
                continue
            request = conn.execute('SELECT * FROM requests WHERE id=?', (row['request_id'],)).fetchone()
            policy = store._tool_policy(conn, request)
            if (row['tool_name'] not in policy['tools'] or binding['root'] not in policy['readRootAliases']):
                issues.add('stale')
                continue
            latest = conn.execute('SELECT id FROM evidence WHERE task_id=? ORDER BY created DESC,id DESC LIMIT 1',
                                  (row['task_id'],)).fetchone()
            review = _review(conn, row)
            if (row['request_status'] != 'completed' or row['task_status'] != 'completed'
                    or row['request_type'] not in {'work.inspect', 'work.edit'}
                    or row['attempt'] != row['execution_count'] or row['status'] != 'completed'
                    or latest is None or latest[0] != row['evidence_id'] or review is None):
                issues.add('ambiguous')
                continue
            if (not isinstance(row['result'], str) or not isinstance(row['evidence_content'], str)
                    or hashlib.sha256(row['result'].encode()).hexdigest() != row['result_sha256']
                    or hashlib.sha256(row['evidence_content'].encode()).hexdigest() != row['evidence_sha256']):
                issues.add('invalid')
                continue
            projection = _excerpt(row, binding, store.settings.read_roots)
            if projection is None:
                continue
            item = {'id': 'retained:' + row['id'], 'projectId': binding['id'],
                    'provenance': 'reviewed_retained_tool_observation', 'operation': row['tool_name'],
                    'freshness': 'historical_unverified', 'observedAt': row['completed'],
                    'verifiedCurrentBlocker': False, 'acceptanceEvidence': False, **projection}
            if (len(public['items']) >= MAX_ITEMS or len(_encoded({**public,
                    'items': [*public['items'], item]}).encode()) > MAX_PUBLIC_BYTES - 1000):
                issues.add('truncated')
                continue
            public['items'].append(item)
            proof['review'] = review
            if projection['sourceTruncated'] or projection['excerptTruncated']:
                issues.add('truncated')
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OrganizationFileReadError):
            issues.add('invalid')
    if not public['items'] and not issues:
        issues.add('unavailable')
    public['limitations'] = [_LIMITATIONS[key] for key in sorted(issues)]
    public['truncated'] = 'truncated' in issues
    public['status'] = ('partial' if issues else 'available') if public['items'] else next(
        (key for key in ('stale', 'ambiguous', 'invalid') if key in issues), 'unavailable')
    private['sha256'] = _hash({'bindings': private, 'public': public})
    return {'public': public, 'private': private}

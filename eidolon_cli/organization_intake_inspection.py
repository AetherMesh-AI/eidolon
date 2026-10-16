"""Opt-in pre-objective text inspection under an existing worker's read grants.

This module has no activation endpoint. A served-owner policy controller must
persist the delegation before calling it. Model output can never supply policy.
The reader performs no inference, writes, commands or automatic grant creation.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import time

from eidolon_cli.organization_intake_context import digest
from eidolon_cli.organization_intake_evidence import _safe_text
from eidolon_cli.organization_projects import selected_projects
from eidolon_cli.organization_root_grants import revoked_aliases
from eidolon_cli.organization_roster import staff_unavailability
from tools.organization_file_read import organization_file_read_scope, _path_parts

MAX_FILES = 4
MAX_SOURCE_BYTES = 32768
MAX_TOTAL_READ_BYTES = 2 * MAX_FILES * MAX_SOURCE_BYTES
MAX_PUBLIC_BYTES = 6000
# Cooperative cutoff between synchronous operations; not a hard I/O interruption deadline.
MAX_SECONDS = 5
_CONTEXT_NAMES = frozenset({'readme', 'readme.md', 'readme.rst', 'readme.txt',
    'package.json', 'pyproject.toml', 'cargo.toml', 'go.mod', 'context.md', 'architecture.md'})
_POLICY_KEYS = {'enabled', 'ownerIdentityId', 'workerId', 'projectIds', 'paths', 'rootIdentities', 'revision'}


def inspection_authority(store, conn, policy):
    """Validate a trusted, persisted opt-in against current owner and grants.

    This only reads the ledger. Callers must supply the current policy again on
    every authority check, rather than closing over an obsolete policy copy.
    """
    if not isinstance(policy, dict) or set(policy) != _POLICY_KEYS or policy['enabled'] is not True:
        raise ValueError('Fresh inspection requires an explicit current owner opt-in')
    store._require_current_policy(conn)
    if conn.execute('SELECT 1 FROM organization_policy_pause').fetchone():
        raise ValueError('Organization policy is paused')
    owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
    if owner is None or owner[0] != policy['ownerIdentityId']:
        raise ValueError('Inspection owner changed')
    if type(policy['revision']) is not int or policy['revision'] < 1:
        raise ValueError('Inspection delegation revision is invalid')
    worker = store._staff(policy['workerId'])
    if (worker is None or worker.role != 'Worker'
            or staff_unavailability(worker, store.settings, 'work.inspect')
            or store._staff_reason(conn, {'id': worker.id}, 'work.inspect')
            or 'read_file' not in worker.tool_grants or 'read_file' not in store.settings.tool_grants):
        raise ValueError('Inspection worker lacks an existing enabled read grant')
    projects = selected_projects(policy['projectIds'], store.settings)
    if not projects or any(project['team'] != worker.team for project in projects):
        raise ValueError('Inspection projects must belong to the designated worker team')
    roots = {project['root'] for project in projects}
    if roots & (set(store.settings.revoked_root_aliases) | set(revoked_aliases(conn))):
        raise ValueError('Inspection root access was revoked')
    paths = policy['paths']
    if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES
            or len(set(paths)) != len(paths)):
        raise ValueError('Inspection requires one to four distinct explicit context paths')
    for path in paths:
        parts = _path_parts(path, absolute=False)
        if len(parts) != 2 or parts[0] not in roots or parts[-1].lower() not in _CONTEXT_NAMES:
            raise ValueError('Inspection paths must name approved project context files')
    identities = policy['rootIdentities']
    if (not isinstance(identities, dict) or set(identities) != roots
            or any(not isinstance(value, (list, tuple)) or len(value) != 2
                   or any(type(n) is not int or n < 0 for n in value) for value in identities.values())):
        raise ValueError('Inspection needs exact owner-reviewed root identities')
    binding = {'ownerIdentityId': owner[0], 'policyGeneration': store._policy_generation,
               'policyFingerprint': store._policy_fingerprint, 'delegation': policy,
               'worker': asdict(worker), 'projects': projects}
    return {'sha256': digest(binding), 'binding': binding,
            'readRoots': tuple(store.settings.read_roots), 'rootIdentities': identities,
            'aliases': sorted(roots), 'paths': list(paths)}


def inspect_context(authority, *, cancel, clock=time.monotonic):
    """Collect and revalidate bounded sources; reject the entire stale result.

    ``authority`` is a trusted callback that rereads the current delegation and
    owner/policy/grant state. Revocation cannot undo bytes already read, but stops
    subsequent reads and prevents acceptance. Source bytes are never persisted by
    this function. Two bounded passes reject changes observed before acceptance;
    they cannot promise that a source will remain unchanged afterwards. The
    elapsed-time cutoff is cooperative: an in-flight synchronous filesystem or
    authority read cannot be interrupted by this function. The caller must
    revalidate authoritySha256 in its durable acceptance transaction.
    """
    if not callable(authority):
        raise ValueError('Inspection requires a current authority reader')
    started = clock()
    first = authority()
    byte_count = 0

    def check():
        if cancel.is_set():
            raise ValueError('Inspection was cancelled')
        if clock() - started >= MAX_SECONDS:
            raise ValueError('Inspection time limit exceeded')
        current = authority()
        if current['sha256'] != first['sha256']:
            raise ValueError('Inspection authority changed')

    def read_pass():
        nonlocal byte_count
        check()
        result = []
        with organization_file_read_scope(first['readRoots'], MAX_PUBLIC_BYTES,
                allowed_root_aliases=first['aliases'], expected_root_identities=first['rootIdentities']) as scope:
            for path in first['paths']:
                check()
                source = scope.read_exact_source(path)
                byte_count += source['size']
                if source['size'] > MAX_SOURCE_BYTES or byte_count > MAX_TOTAL_READ_BYTES:
                    raise ValueError('Inspection byte limit exceeded')
                check()
                result.append((path, source))
        return result

    sources = read_pass()
    verified = read_pass()  # Reopens roots so path/root replacements cannot hide behind old descriptors.
    if [(p, s['sha256']) for p, s in sources] != [(p, s['sha256']) for p, s in verified]:
        raise ValueError('Inspection source changed')
    check()
    items = []
    remaining = MAX_PUBLIC_BYTES
    for path, source in sources:
        safe = _safe_text(source['content'], first['readRoots'])
        encoded = safe.encode('utf-8')
        excerpt = encoded[:min(1200, remaining)].decode('utf-8', errors='ignore')
        remaining -= len(excerpt.encode('utf-8'))
        project = next(p for p in first['binding']['projects'] if p['root'] == path.split('/')[0])
        items.append({'id': 'inspection-' + hashlib.sha256(path.encode()).hexdigest()[:16],
            'projectId': project['id'], 'path': path, 'excerpt': excerpt,
            'sourceSha256': source['sha256'], 'sourceBytes': source['size'],
            'truncated': len(encoded) > len(excerpt.encode('utf-8')),
            'untrusted': True, 'verifiedCurrentBlocker': False, 'acceptanceEvidence': False})
    return {'public': {'status': 'observed', 'items': items,
                      'limitation': 'Bounded context inspection only; source text is untrusted and does not verify a live outage or completed work.'},
            'receipt': {'authoritySha256': first['sha256'], 'observedAt': time.time(), 'readBytes': byte_count,
                        'files': [{'path': path, 'sha256': source['sha256'], 'bytes': source['size']} for path, source in sources],
                        'elapsedSeconds': max(0, clock() - started)}}

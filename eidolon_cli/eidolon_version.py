"""Eidolon development identity: Git only at build time, JSON only at runtime.

The release number is declared here; Git proves the build's source identity.
This stdlib-only file is executable directly by Node and setuptools, without
importing the CLI package or inspecting any profile. No command fetches history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

RELEASE_VERSION = '0.3.0-alpha'
COMPATIBILITY_VERSION = RELEASE_VERSION
CHANNEL = 'alpha'
REPOSITORY = 'AetherMesh-AI/Eidolon'
UPDATE_BRANCH = 'main'
SCHEMA_VERSION = 1
# Immutable original 0.1.0 release; tags never participate in derivation.
ANCHOR_COMMIT = '437db7394d78a178966fb2ae42792f2978133a9d'
STAMP_PATH = Path('eidolon_cli/_build_identity.json')
_NUMBER = r'(0|[1-9][0-9]*)'
_TAG = re.compile(rf'alpha-v{_NUMBER}\.{_NUMBER}\.{_NUMBER}')
_SHA = re.compile(r'[0-9a-f]{40}')


def valid_sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value)) and value != '0' * 40


def read_head(root: Path) -> str | None:
    """Read loose/packed refs, detached HEAD and linked worktrees; never spawn."""
    try:
        git_dir = Path(root) / '.git'
        if git_dir.is_file():
            pointer = git_dir.read_text().strip()
            if not pointer.startswith('gitdir:'): return None
            git_dir = Path(pointer[7:].strip())
            if not git_dir.is_absolute(): git_dir = Path(root) / git_dir
        common = git_dir
        if (git_dir / 'commondir').is_file():
            common = Path((git_dir / 'commondir').read_text().strip())
            if not common.is_absolute(): common = git_dir / common
        head = (git_dir / 'HEAD').read_text().strip()
        if head.startswith('ref: '):
            ref = head[5:]
            if not ref.startswith('refs/') or '..' in ref.split('/'): return None
            if (common / ref).is_file():
                head = (common / ref).read_text().strip()
            else:
                for line in (common / 'packed-refs').read_text().splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        head = parts[0]
                        break
        return head if valid_sha(head) else None
    except (OSError, ValueError):
        return None


def _git(root, *args):
    try:
        result = subprocess.run(['git', *args], cwd=root, text=True,
                                capture_output=True, timeout=15)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def fallback(commit=None, dirty=None, reason='No verified build identity', branch=None):
    commit = commit if valid_sha(commit) else None
    return dict(schemaVersion=SCHEMA_VERSION, version=RELEASE_VERSION,
                channel=CHANNEL, repository=REPOSITORY, updateBranch=UPDATE_BRANCH,
                commit=commit, shortCommit=commit[:12] if commit else None,
                dirty=dirty if type(dirty) is bool else None, branch=branch,
                baseTag=None, baseCommit=None, distance=None,
                versionSource='fallback', versionReason=reason)


def validate_identity(value):
    """Validate the complete version proof, not just a plausible version string.

    A stamp is build metadata, not a cryptographic attestation. It is trusted
    only after schema validation and (when Git is present) exact HEAD matching.
    Legacy/fallback stamps cannot authorize a distributable version override.
    """
    if not isinstance(value, dict): return None
    if type(value.get('schemaVersion')) is not int or value['schemaVersion'] != SCHEMA_VERSION: return None
    if value.get('channel') != CHANNEL or value.get('repository') != REPOSITORY or value.get('updateBranch') != UPDATE_BRANCH: return None
    if value.get('versionSource') not in ('git-derived', 'stamp'): return None
    if not valid_sha(value.get('commit')) or not valid_sha(value.get('baseCommit')): return None
    if value.get('shortCommit') != value['commit'][:12]: return None
    if value.get('dirty') is not None and type(value['dirty']) is not bool: return None
    distance = value.get('distance')
    tag = value.get('baseTag')
    match = _TAG.fullmatch(tag) if isinstance(tag, str) else None
    if not match or type(distance) is not int or distance < 0: return None
    if tag != 'alpha-v0.1.0' or value['baseCommit'] != ANCHOR_COMMIT: return None
    if value.get('version') != RELEASE_VERSION: return None
    if (distance == 0) != (value['commit'] == value['baseCommit']): return None
    return dict(value)


def _read_stamp(root):
    try:
        return validate_identity(json.loads((Path(root) / STAMP_PATH).read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return None


def runtime_identity(root=None):
    """Spawn-free, fresh read. Consumers may cache the returned identity."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    head = read_head(root)
    stamp = _read_stamp(root)
    if stamp and (not (root / '.git').exists() or (head and stamp['commit'] == head)):
        return {**stamp, 'versionSource': 'stamp'}
    return fallback(head, reason='Missing, invalid or stale build identity')


class _UnavailableHistory(ValueError):
    """Missing evidence permits stamp reuse; invalid provenance does not."""


def _derive(root, head, branch):
    # A named side branch is never an official development target. Detached
    # builds must be on an explicitly known main first-parent chain.
    if branch not in (UPDATE_BRANCH, 'HEAD'):
        raise ValueError('Build branch is not main')
    if branch == 'HEAD':
        eligible = False
        for ref in ('refs/heads/main', 'refs/remotes/origin/main'):
            chain = _git(root, 'rev-list', '--first-parent', ref)
            if chain is not None and head in chain.splitlines(): eligible = True
        if not eligible: raise ValueError('Detached build lacks main provenance')
    if _git(root, 'rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('Missing or shallow Git history')
    chain = _git(root, 'rev-list', '--first-parent', head)
    if chain is None or ANCHOR_COMMIT not in chain.splitlines():
        raise ValueError('Fixed version anchor absent from main first-parent history')
    distance = chain.splitlines().index(ANCHOR_COMMIT)
    return dict(version=RELEASE_VERSION, baseTag='alpha-v0.1.0',
                baseCommit=ANCHOR_COMMIT, distance=distance, versionSource='git-derived',
                versionReason='Declared release with complete local main first-parent provenance')


def resolve_identity(root=None, env=None):
    """Build-time derivation, validated stamp reuse, then explicit fallback.

    CI SHA is only a consistency check; it never supplies missing ancestry.
    Nothing here changes refs, fetches, checks out code or chooses update policy.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    env = os.environ if env is None else env
    has_git = (root / '.git').exists()
    head = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}') if has_git else None
    branch = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD') if valid_sha(head) else None
    status = _git(root, 'status', '--porcelain', '--untracked-files=all') if valid_sha(head) else None
    dirty = bool(status) if status is not None else None
    ci = env.get('GITHUB_SHA')
    result = fallback(head or ci, dirty, branch=None if branch == 'HEAD' else branch)
    # Inconsistent CI and side-branch provenance cannot be rescued by a stamp.
    if ci and (not valid_sha(ci) or (has_git and ci != head)):
        return {**result, 'versionReason': 'Invalid or inconsistent CI SHA'}
    if has_git and branch not in (UPDATE_BRANCH, 'HEAD'):
        return {**result, 'versionReason': 'Unknown or unsupported build branch'}
    try:
        if not valid_sha(head): raise _UnavailableHistory('No local Git build commit')
        return {**result, **_derive(root, head, branch)}
    except _UnavailableHistory as error:
        result['versionReason'] = str(error)
    except ValueError as error:
        return {**result, 'versionReason': str(error)}
    stamp = _read_stamp(root)
    if stamp and (not has_git or (valid_sha(head) and stamp['commit'] == head)) and (not ci or stamp['commit'] == ci):
        reused = {**stamp, 'versionSource': 'stamp', 'versionReason': 'Validated matching build stamp'}
        if has_git: reused.update(dirty=dirty, branch=None if branch == 'HEAD' else branch)
        return reused
    return result


def write_identity(root=None, output=None, require_verified=False, env=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    identity = resolve_identity(root, env=env)
    if require_verified and not validate_identity(identity):
        raise ValueError('Verified development version required: ' + identity['versionReason'])
    target = Path(output) if output is not None else root / STAMP_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    return identity


def format_identity(identity):
    commit = identity.get('commit')
    short = commit[:12] if valid_sha(commit) else 'unknown commit'
    verified = validate_identity(identity)
    version = verified['version'] if verified else RELEASE_VERSION
    label = f'{version} · {short}'
    if not verified: label += ' (build provenance unverified)'
    if identity.get('dirty') is True: label += ' (dirty source)'
    elif identity.get('dirty') is not False: label += ' (source status unknown)'
    return label


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-verified', action='store_true')
    args = parser.parse_args()
    try:
        identity = (write_identity(args.repo_root, args.output, args.require_verified)
                    if args.output else resolve_identity(args.repo_root))
        if args.require_verified and not validate_identity(identity):
            raise ValueError(identity['versionReason'])
    except ValueError as error:
        parser.exit(1, f'eidolon-version: {error}\n')
    print(json.dumps(identity))


if __name__ == '__main__':
    main()

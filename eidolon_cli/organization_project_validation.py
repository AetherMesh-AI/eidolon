"""Bounded declarative checks of exact managed bytes; never a command runner.

Parsing a document or checking source syntax cannot establish functional behavior.
Every receipt states this limitation so final acceptance cannot mistake these
observations for a test suite or a verified deployment.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re

MAX_PROJECT_FILES = 8
MAX_VALIDATIONS = 32
MAX_PROJECT_BYTES = 131072
_HASH = re.compile(r'[0-9a-f]{64}')
_CHECK_FIELDS = {
    'sha256': {'kind', 'path', 'equals'},
    'text_contains': {'kind', 'path', 'text'},
    'text_absent': {'kind', 'path', 'text'},
    'json_valid': {'kind', 'path'},
    'json_value': {'kind', 'path', 'pointer', 'equals'},
    'python_syntax': {'kind', 'path'},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def normalize_validations(value, paths):
    if not isinstance(value, list) or len(value) > MAX_VALIDATIONS:
        raise ValueError('Validations must contain at most 32 declarative checks')
    result = []
    for check in value:
        if not isinstance(check, dict) or not isinstance(check.get('kind'), str):
            raise ValueError('Each validation must be a declarative check object')
        fields = _CHECK_FIELDS.get(check['kind'])
        if fields is None or set(check) != fields or not isinstance(check.get('path'), str) or check['path'] not in paths:
            raise ValueError('Validation kind, exact fields, or edited source path is invalid')
        if check['kind'] == 'sha256' and (not isinstance(check['equals'], str) or not _HASH.fullmatch(check['equals'])):
            raise ValueError('SHA-256 validation requires an exact lowercase digest')
        if 'text' in check and (not isinstance(check['text'], str) or not check['text'] or len(check['text'].encode('utf-8')) > 32768):
            raise ValueError('Text validation requires nonempty bounded UTF-8 text')
        if 'pointer' in check and (not isinstance(check['pointer'], str) or len(check['pointer']) > 1024
                                  or (check['pointer'] and not check['pointer'].startswith('/'))
                                  or re.search(r'~(?![01])', check['pointer'])):
            raise ValueError('JSON validation requires a bounded RFC 6901 pointer')
        try:
            encoded = canonical(check)
            if len(encoded.encode('utf-8')) > 32768:
                raise ValueError('Validation exceeds the bounded data limit')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('Validation must contain bounded finite JSON data') from None
        from agent.redact import redact_sensitive_text
        if redact_sensitive_text(encoded, force=True, file_read=True, redact_url_credentials=True) != encoded:
            raise ValueError('Validation data requiring secret redaction cannot be retained')
        result.append(dict(check))
    if len(canonical(result).encode('utf-8')) > MAX_PROJECT_BYTES:
        raise ValueError('Combined validation data exceeds the project byte limit')
    return result


def _edit_text(value, field):
    from eidolon_cli.organization_edits import _exact_text
    try:
        return _exact_text(value, field)
    except ValueError as error:
        if 'byte limit' in str(error):
            raise ValueError('Edit text exceeds the 32768-byte UTF-8 limit') from None
        raise


def parse_edit_result(value):
    from eidolon_cli.organization_edits import _alias_path
    if not isinstance(value, dict) or not isinstance(value.get('summary'), str) or not value['summary'].strip():
        raise ValueError('Edit output requires a nonempty summary')
    if len(value['summary']) > 10000:
        raise ValueError('Edit summary exceeds the supported text limit')
    legacy = 'edit' in value
    if set(value) not in ({'summary', 'edit'}, {'summary', 'edit', 'validations'},
                          {'summary', 'edits'}, {'summary', 'edits', 'validations'}):
        raise ValueError('Edit output must contain only summary, edit or edits, and optional validations')
    edits = [value['edit']] if legacy else value['edits']
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_PROJECT_FILES:
        raise ValueError('A project proposal requires 1–8 exact file edits')
    paths = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path', 'baseRevision', 'baseSha256', 'oldText', 'newText'}:
            raise ValueError('Edit must specify exactly path, baseRevision, baseSha256, oldText, and newText')
        _alias_path(edit['path'])
        if edit['path'] in paths:
            raise ValueError('A project proposal cannot edit the same path more than once')
        paths.add(edit['path'])
        if type(edit['baseRevision']) is not int or not 0 <= edit['baseRevision'] <= 2**63 - 1:
            raise ValueError('Edit baseRevision must be a nonnegative bounded integer')
        if not isinstance(edit['baseSha256'], str) or not _HASH.fullmatch(edit['baseSha256']):
            raise ValueError('Edit baseSha256 must be the exact source SHA-256')
        if not _edit_text(edit['oldText'], 'oldText'):
            raise ValueError('oldText must be nonempty')
        _edit_text(edit['newText'], 'newText')
    if sum(len((edit['oldText'] + edit['newText']).encode('utf-8')) for edit in edits) > MAX_PROJECT_BYTES * 2:
        raise ValueError('Combined replacements exceed the project byte limit')
    checks = normalize_validations(value.get('validations', []), paths)
    result = {'summary': value['summary'].strip(), 'edit' if legacy else 'edits': dict(edits[0]) if legacy else [dict(e) for e in edits]}
    if 'validations' in value:
        result['validations'] = checks
    return result


def _json_document(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('Non-finite JSON number')
    return json.loads(content, object_pairs_hook=unique, parse_constant=reject)


def _pointer(document, path):
    if not path:
        return document
    value = document
    for token in path[1:].split('/'):
        key = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', key):
                raise ValueError('Invalid JSON array index')
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError('JSON pointer does not resolve')
    return value


def _python_syntax(content):
    # ast.parse does not import or execute project content. Filenames are logical
    # aliases; no filesystem lookup, bytecode write, or project hooks are used.
    ast.parse(content.removeprefix('\ufeff'), filename='<managed source>', mode='exec')
    return True


def _check(check, content):
    handlers = {
        'sha256': lambda: hashlib.sha256(content.encode('utf-8')).hexdigest() == check['equals'],
        'text_contains': lambda: check['text'] in content,
        'text_absent': lambda: check['text'] not in content,
        'json_valid': lambda: (_json_document(content), True)[1],
        'json_value': lambda: canonical(_pointer(_json_document(content), check['pointer'])) == canonical(check['equals']),
        'python_syntax': lambda: _python_syntax(content),
    }
    return handlers[check['kind']]()


def validate_manifest(files, checks):
    """Return reproducible observations, including exact content-integrity checks."""
    paths = {item['path'] for item in files}
    checks = normalize_validations(checks, paths)
    results = []
    for item in files:
        passed = hashlib.sha256(item['content'].encode('utf-8')).hexdigest() == item['sha256']
        results.append({'kind': 'managed_content_integrity', 'path': item['path'], 'passed': passed})
    by_path = {item['path']: item['content'] for item in files}
    for check in checks:
        try:
            passed = _check(check, by_path[check['path']])
            reason = None if passed else 'The declared assertion does not hold for the applied bytes.'
        except (ValueError, TypeError, KeyError, IndexError, SyntaxError, RecursionError, OverflowError):
            passed, reason = False, 'The applied document does not satisfy this declared check.'
        results.append({'kind': check['kind'], 'path': check['path'], 'passed': passed,
                        'checkSha256': digest(check), **({'reason': reason} if reason else {})})
    return {'runner': 'eidolon.declarative-validation', 'runnerVersion': 1,
            'scope': 'managed_workspace', 'status': 'passed' if all(r['passed'] for r in results) else 'failed',
            'inputs': [{k: item[k] for k in ('path', 'revision', 'sha256')} for item in files],
            'checks': results, 'notExecuted': ['project_commands', 'functional_tests'],
            'limitations': 'Deterministic content assertions and optional parsing only. No project commands, imports, functional tests, network, or source writes were executed.'}

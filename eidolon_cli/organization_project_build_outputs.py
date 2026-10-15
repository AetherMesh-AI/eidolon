"""Bounded data-only compiler output validation; never writes or executes output."""
import base64
import binascii
import hashlib
import json
from pathlib import PurePosixPath

from eidolon_cli.organization_project_validation import digest

MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_FRAME_BYTES = 1024 * 1024
OUTPUT_FIELDS = {'path', 'sources', 'bytes', 'sha256', 'base64'}


def expected_outputs(files, root):
    """Derive the exact initial compiler mapping from selected non-declaration TS."""
    result = {}
    for item in files:
        name = item['path']
        if not isinstance(name, str) or not name.startswith(root + '/'):
            raise ValueError('Build source must belong to its selected root')
        relative = name[len(root) + 1:]
        if not relative.endswith('.ts') or relative.endswith('.d.ts'):
            continue
        parts = relative.split('/')
        if any(part in {'', '.', '..', 'node_modules'} for part in parts) or '\\' in relative or '\x00' in relative:
            raise ValueError('Build source path is unsupported')
        target = relative[:-3] + '.js'
        if target in result:
            raise ValueError('Build output collision')
        result[target] = ['/source/' + name]
    if not 1 <= len(result) <= MAX_FILES:
        raise ValueError('Build requires a bounded nonempty source set')
    if any(str(parent) in result for path in result for parent in PurePosixPath(path).parents):
        raise ValueError('Build output directory collision')
    return result


def validate_build_outputs(record, files, root):
    """Return verified immutable byte descriptors; no filesystem effects.

    Transport parsing itself must enforce MAX_FRAME_BYTES before JSON decoding.
    The decoded-object size check here is a second bound, not that transport cap.
    Runtime/cleanup/input/configuration verification remains the caller's duty.
    """
    expected = expected_outputs(files, root)
    outputs = record.get('outputs')
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_FILES:
        raise ValueError('Build output count is invalid')
    total = 0
    verified = []
    seen = set()
    for output in outputs:
        if not isinstance(output, dict) or set(output) != OUTPUT_FIELDS:
            raise ValueError('Build output fields are invalid')
        path, size, encoded = output['path'], output['bytes'], output['base64']
        if (not isinstance(path, str) or path not in expected or path in seen
                or PurePosixPath(path).as_posix() != path or path.startswith('/')
                or output['sources'] != expected[path]):
            raise ValueError('Build output identity or source mapping is invalid')
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError('Build output byte count is invalid')
        if not isinstance(encoded, str) or len(encoded) != 4 * ((size + 2) // 3):
            raise ValueError('Build output encoded length is invalid')
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Build aggregate output limit exceeded')
        try:
            data = base64.b64decode(encoded, validate=True)
            text = data.decode('utf-8')
        except (ValueError, binascii.Error, UnicodeError) as error:
            raise ValueError('Build output encoding is invalid') from error
        if (len(data) != size or base64.b64encode(data).decode('ascii') != encoded
                or hashlib.sha256(data).hexdigest() != output['sha256'] or text.startswith('\ufeff')):
            raise ValueError('Build output bytes do not match their descriptor')
        seen.add(path)
        verified.append({'path': path, 'sources': expected[path], 'bytes': size,
                         'sha256': output['sha256'], 'content': text})
    if seen != set(expected) or [item['path'] for item in verified] != sorted(seen):
        raise ValueError('Build output set is incomplete or not canonical')
    manifest = [{key: item[key] for key in ('path', 'sources', 'bytes', 'sha256')} for item in verified]
    if record.get('outputManifestSha256') != digest(manifest):
        raise ValueError('Build output manifest changed')
    if len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_FRAME_BYTES:
        raise ValueError('Build output frame limit exceeded')
    return verified

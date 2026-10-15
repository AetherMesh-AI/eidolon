"""Test-only observation preserves transactions, factories and failure outcomes."""
import json
import sqlite3

import pytest

from scripts.ci.organization_sqlite_diagnostics import install


def test_diagnostics_preserve_transactions_and_exclude_private_values(tmp_path):
    records = []
    original = sqlite3.connect
    restore = install(records.append)
    path = tmp_path / 'private-user-database.db'
    try:
        conn = sqlite3.connect(path, timeout=0.25, isolation_level=None)
        conn.execute('CREATE TABLE data(value TEXT)')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT INTO data VALUES (?)', ('private-payload',))
        conn.commit()
        conn.close()
    finally:
        restore()
    assert sqlite3.connect is original
    assert [(r['phase'], r['boundary']) for r in records] == [
        ('connect','start'), ('connect','end'), ('commit','start'),
        ('commit','end'), ('close','start'), ('close','end')]
    assert len({r['connection'] for r in records}) == 1
    encoded = json.dumps(records)
    assert str(path) not in encoded and 'private-payload' not in encoded and 'INSERT' not in encoded
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT value FROM data').fetchone()[0] == 'private-payload'


def test_explicit_factories_and_errors_are_preserved(tmp_path):
    class Custom(sqlite3.Connection):
        pass
    records = []
    restore = install(records.append)
    try:
        conn = sqlite3.connect(':memory:', factory=Custom)
        assert type(conn) is Custom
        conn.close()
        conn = sqlite3.connect(':memory:', 5, 0, '', True, Custom)
        assert type(conn) is Custom
        conn.close()
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect(tmp_path / 'missing-parent' / 'private.db')
    finally:
        restore()
    assert records[-1]['errorType'] == 'OperationalError'
    assert records[-1]['sqliteErrorCode'] == sqlite3.SQLITE_CANTOPEN
    assert 'private.db' not in json.dumps(records)


def test_diagnostic_write_failure_does_not_change_database_result():
    def fail(_):
        raise OSError('diagnostic storage unavailable')
    restore = install(fail)
    try:
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE data(value INTEGER)')
        conn.commit()
        conn.close()
    finally:
        restore()


def test_retained_stacks_print_without_host_paths(tmp_path, capsys):
    from scripts.ci.organization_timing import print_evidence
    (tmp_path/'organization-timing-1.jsonl').write_text('{"event":"start"}\n')
    (tmp_path/'organization-stacks-1.txt').write_text('File "C:\\private\\home\\organization_store.py", line 174 in _connect\n')
    print_evidence(tmp_path)
    output = capsys.readouterr().out
    assert 'organization_store.py' in output and 'line 174' in output
    assert 'private' not in output and 'home' not in output
    assert 'private' not in (tmp_path/'sanitized'/'organization-stacks-1.txt').read_text()

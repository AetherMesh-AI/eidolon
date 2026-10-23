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


def test_python_snapshot_observes_blocked_worker_without_private_values():
    import io
    import threading
    from scripts.ci import organization_timing as timing

    entered, release = threading.Event(), threading.Event()

    def blocked_worker():
        private_value = 'synthetic-secret-not-for-evidence'
        entered.set()
        release.wait(5)
        return private_value

    worker = threading.Thread(target=blocked_worker)
    worker.start()
    try:
        assert entered.wait(5)
        output = io.StringIO()
        timing._write_python_stacks(output)
        evidence = output.getvalue()
        assert str(worker.ident) in evidence
        assert 'blocked_worker' in evidence
        assert 'test_organization_sqlite_diagnostics.py' in evidence
        assert 'synthetic-secret-not-for-evidence' not in evidence
        assert str(__file__) not in evidence
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_snapshot_worker_owns_output_until_capture_finishes(monkeypatch):
    import io
    import threading
    import importlib.util
    from scripts.ci import organization_timing

    # The canonical Windows lane already runs the real plugin. Keep injected
    # failures and interval changes isolated from its active evidence writer.
    spec = importlib.util.spec_from_file_location('isolated_timing', organization_timing.__file__)
    timing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(timing)

    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    output = io.StringIO()

    def interrupted_write(stream):
        entered.set()
        assert release.wait(5)
        assert not stream.closed
        raise OSError('synthetic evidence write failure')

    monkeypatch.setattr(timing, '_write_python_stacks', interrupted_write)
    monkeypatch.setattr(timing, '_STACK_INTERVAL_SECONDS', 0)
    worker = threading.Thread(target=timing._capture_stacks, args=(stop, output))
    worker.start()
    try:
        assert entered.wait(5)
        stop.set()
        worker.join(timeout=0)
        assert worker.is_alive()
        assert not output.closed
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert output.closed

"""Read-only metadata discovery distinguishes absence, incompatibility and failure."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

import pytest

from eidolon_cli.organization_metadata import MetadataError, read_descriptor
from tests.eidolon_cli.test_organization_metadata import save


def files(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


def test_missing_legacy_empty_and_unsupported_reads_never_initialize_or_migrate(tmp_path):
    path = tmp_path / 'organization' / 'state.db'
    before = files(tmp_path)
    assert read_descriptor(path)['reason'] == 'database_missing'
    assert files(tmp_path) == before
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE legacy (value TEXT)')
        conn.execute("INSERT INTO legacy VALUES ('retain')")
    before = files(tmp_path)
    assert read_descriptor(path)['reason'] == 'table_missing'
    assert files(tmp_path) == before
    save(path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE organization_metadata SET schema_version=99')
    before = files(tmp_path)
    assert read_descriptor(path) == {'capabilityVersion': 1, 'state': 'unsupported', 'metadata': None, 'reason': 'schema_version'}
    with pytest.raises(MetadataError, match='unsupported_schema'):
        save(path, 'unsupported')
    assert files(tmp_path) == before
    with sqlite3.connect(path) as conn:
        for name in ('organization_metadata', 'organization_metadata_audit', 'organization_metadata_receipts'):
            conn.execute(f'DELETE FROM {name}')
    before = files(tmp_path)
    assert read_descriptor(path)['reason'] == 'row_missing'
    assert files(tmp_path) == before


def test_locked_corrupt_and_unreadable_are_errors_not_unconfigured(tmp_path):
    path = tmp_path / 'state.db'; save(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('BEGIN EXCLUSIVE')
        with pytest.raises(MetadataError, match='locked'):
            read_descriptor(path)
        conn.rollback()
    bad = tmp_path / 'corrupt.db'; bad.write_bytes(b'This is not SQLite')
    before = files(tmp_path)
    with pytest.raises(MetadataError, match='corrupt'):
        read_descriptor(bad)
    with pytest.raises(MetadataError, match='unreadable'):
        read_descriptor(tmp_path)  # Existing directory cannot be opened as a database.
    assert files(tmp_path) == before


def test_live_wal_read_sees_committed_revision_without_mutating_rows_or_wal(tmp_path):
    path = tmp_path / 'state.db'; first = save(path)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('UPDATE organization_metadata SET display_name=?, revision=revision+1', ('Committed WAL',))
        writer.commit()
        # Keep writer open so the committed update remains in WAL. Do not use immutable=1.
        main_before = path.stat().st_mtime_ns
        wal = Path(str(path)+'-wal'); wal_before = (wal.stat().st_size, wal.stat().st_mtime_ns)
        result = read_descriptor(path)
        assert result['metadata']['displayName'] == 'Committed WAL'
        assert result['metadata']['revision'] == first['metadata']['revision']+1
        assert path.stat().st_mtime_ns == main_before
        assert (wal.stat().st_size, wal.stat().st_mtime_ns) == wal_before
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE organization_metadata SET display_name='Uncommitted'")
        assert read_descriptor(path) == result
        writer.rollback()


def test_incomplete_or_unknown_schema_is_unsupported_without_changes(tmp_path):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE organization_metadata (future_format BLOB)')
    before = files(tmp_path)
    assert read_descriptor(path)['state'] == 'unsupported'
    with pytest.raises(MetadataError, match='unsupported_schema'):
        save(path)
    assert files(tmp_path) == before


@pytest.mark.parametrize('table,old,new', [
    (0, 'singleton INTEGER PRIMARY KEY', 'singleton INTEGER'),
    (1, 'idempotency_key TEXT NOT NULL UNIQUE', 'idempotency_key TEXT NOT NULL'),
    (1, ', PRIMARY KEY(organization_id, revision)', ''),
    (2, 'idempotency_key TEXT PRIMARY KEY', 'idempotency_key TEXT'),
    (0, 'display_name TEXT NOT NULL', 'display_name TEXT'),
    (0, ' CHECK(singleton=1)', ''),
    (0, ' CHECK(revision>0)', ''),
    (2, 'idempotency_key TEXT PRIMARY KEY', 'idempotency_key TEXT COLLATE NOCASE PRIMARY KEY'),
])
def test_same_columns_with_wrong_constraints_are_unsupported_without_repair(tmp_path, table, old, new):
    from eidolon_cli.organization_metadata import _DDL
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as conn:
        for index, statement in enumerate(_DDL):
            conn.execute(statement.replace(old, new) if index == table else statement)
    before = files(tmp_path)
    assert read_descriptor(path) == {'capabilityVersion': 1, 'state': 'unsupported', 'metadata': None, 'reason': 'schema'}
    with pytest.raises(MetadataError, match='unsupported_schema'):
        save(path)
    assert files(tmp_path) == before

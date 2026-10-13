"""Schema initialization must be atomic on fresh and retained real ledgers."""
import sqlite3

import pytest

from eidolon_cli import organization_store


@pytest.mark.parametrize('existing', [False, True])
def test_late_schema_failure_preserves_the_entire_previous_database(tmp_path, monkeypatch, existing):
    path = tmp_path / 'organization.db'
    if existing:
        store = organization_store.OrganizationStore(path)
        store.create_objective('Retained owner objective', idempotency_key='retained')
    with sqlite3.connect(path) as conn:
        before = list(conn.iterdump())
    monkeypatch.setattr(
        organization_store, 'ORGANIZATION_SCHEMA',
        organization_store.ORGANIZATION_SCHEMA + '\nCREATE TABLE initialization_probe(value TEXT);\nSELECT FROM;',
    )
    with pytest.raises(sqlite3.OperationalError, match='syntax'):
        organization_store.OrganizationStore(path)
    with sqlite3.connect(path) as conn:
        assert list(conn.iterdump()) == before

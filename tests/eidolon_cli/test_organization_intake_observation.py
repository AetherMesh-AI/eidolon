"""Passive intake reads preserve real interrupted ledgers without provisioning."""
import sqlite3
import pytest

from eidolon_cli.organization_intake_observation import read_intakes
from eidolon_cli.organization_store import OrganizationStore


def files(home):
    # WAL read-lock bookkeeping may create empty WAL/SHM files; durable bytes
    # and every nonempty WAL remain covered. Never use immutable=1 on live WAL.
    return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob('*') if path.is_file() and not path.name.endswith('-shm')
            and not (path.name.endswith('-wal') and path.stat().st_size == 0)}


def test_missing_profile_does_not_create_anything(tmp_path):
    home = tmp_path / 'missing'
    assert read_intakes(home) == []
    with pytest.raises(ValueError, match='not available'):
        read_intakes(home, intake_id='missing')
    assert not home.exists()


def test_orphans_and_profile_switch_are_read_only(tmp_path, monkeypatch):
    homes = [tmp_path / name for name in ('one', 'two')]
    stores, ids = [], []
    for home in homes:
        store = OrganizationStore(home / 'organization' / 'state.db')
        row, _ = store.begin_intake('Interrupted request', idempotency_key='same-key')
        store.reserve_intake_call(row['id'], provider='fixture', model='small', input_limit=1000, output_limit=1000)
        stores.append(store)
        ids.append(row['id'])
    before = [files(home) for home in homes]
    connect = sqlite3.connect
    mutations = []
    def readonly_connect(database, *args, **kwargs):
        conn = connect(database, *args, **kwargs)
        if database != ':memory:':
            assert kwargs.get('uri') and str(database).endswith('?mode=ro')
            def authorize(action, arg1, arg2, db, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}:
                    mutations.append((action, arg1))
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
        return conn
    monkeypatch.setattr(sqlite3, 'connect', readonly_connect)
    for index in (0, 1, 0):
        view = read_intakes(homes[index], intake_id=ids[index])
        assert view['status'] == 'pending' and view['usage']['reservedTokens'] == 2000
        assert [item['id'] for item in read_intakes(homes[index])] == [ids[index]]
        with pytest.raises(ValueError, match='not available'):
            read_intakes(homes[index], intake_id=ids[1-index])
    assert not mutations
    assert [files(home) for home in homes] == before


def test_owner_rotation_hides_previous_owner_without_mutation(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    row, _ = store.begin_intake('Previous owner', idempotency_key='prior')
    with store._write() as conn:
        conn.execute("UPDATE agent_identity SET identity_id='replacement' WHERE agent_id='owner'")
    before = files(tmp_path)
    assert read_intakes(tmp_path) == []
    with pytest.raises(ValueError, match='not available'):
        read_intakes(tmp_path, intake_id=row['id'])
    assert files(tmp_path) == before


def test_legacy_absence_is_empty_but_malformed_present_fails_closed(tmp_path):
    path = tmp_path / 'organization' / 'state.db'
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE legacy (id TEXT)')
    before = files(tmp_path)
    assert read_intakes(tmp_path) == []
    assert files(tmp_path) == before
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE organization_request_intakes (id TEXT)')
    before = files(tmp_path)
    with pytest.raises(RuntimeError, match='schema'):
        read_intakes(tmp_path)
    assert files(tmp_path) == before

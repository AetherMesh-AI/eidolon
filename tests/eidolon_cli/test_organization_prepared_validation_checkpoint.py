"""Final-fence connection reuse keeps every authority query and closes its scope."""
from contextlib import contextmanager
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_checkpoint as checkpoint
from eidolon_cli import organization_prepared_validation_current as current
from eidolon_cli import organization_prepared_validation_finish as finish
from tests.eidolon_cli.test_organization_prepared_validation_finish import (
    ready, running, confirmed, folder_profile, owner, prepared,  # noqa: F401
)

pytestmark = pytest.mark.linux_only


def test_final_fence_reuses_separate_autocommit_reader_and_closes_it(running, monkeypatch):
    ready(running, monkeypatch)
    original = current.recheck_finish_external_observation
    observed = []

    def inspect(store, observation):
        scope = checkpoint._SCOPE.get()
        assert scope is not None and scope.connection is not scope.writer
        assert scope.connection.in_transaction is False
        assert scope.connection.execute('PRAGMA query_only').fetchone()[0] == 1
        # The writer has legitimately completed its request; the reader keeps
        # seeing the committed running request needed for original lease checks.
        assert scope.writer.execute('SELECT status FROM requests WHERE id=?',
                                    (running.claim['id'],)).fetchone()[0] == 'completed'
        assert scope.connection.execute('SELECT status FROM requests WHERE id=?',
                                        (running.claim['id'],)).fetchone()[0] == 'running'
        opened = []
        registrations = []
        from eidolon_cli import organization_prepared_validation_setup as setup
        registration = setup.check_current_prepared_registration
        def read_registration(*args, **kwargs):
            registrations.append(True)
            return registration(*args, **kwargs)
        check = current._checkpoint(store, observation.authority, running.objective,
                                    lambda: finish._check_claim(store, running.claim))
        connect = store._connect
        @contextmanager
        def counted():
            opened.append(True)
            with connect() as conn:
                yield conn
        with monkeypatch.context() as scoped:
            scoped.setattr(store, '_connect', counted)
            scoped.setattr(setup, 'check_current_prepared_registration', read_registration)
            for _ in range(4):
                with checkpoint.checkpoint_connection(store) as reader:
                    assert reader is scope.connection and reader.in_transaction is False
                assert check(force=True)
            assert opened == []
            assert len(registrations) == 4  # Connection reuse never caches authority.
        observed.append(scope)
        return original(store, observation)

    monkeypatch.setattr(current, 'recheck_finish_external_observation', inspect)
    assert running.store.finish(running.claim, {})
    assert len(observed) == 1 and checkpoint._SCOPE.get() is None
    with pytest.raises(sqlite3.ProgrammingError):
        observed[0].connection.execute('SELECT 1')
    retained = checkpoint._SCOPE.set(observed[0])
    try:
        with pytest.raises(ValueError, match='another finish'):
            with checkpoint.checkpoint_connection(running.store):
                pytest.fail('A retained scope escaped its finish operation')
    finally:
        checkpoint._SCOPE.reset(retained)
    with checkpoint.checkpoint_connection(running.store) as after:
        assert after is not observed[0].connection


@pytest.mark.parametrize('mutation', ['read_transaction', 'write_permission', 'foreign_store', 'nested',
    'path_identity', 'exception', 'thread', 'expired_token', 'foreign_writer'])
def test_final_reader_rejects_boundary_changes_and_discards_scope(running, monkeypatch, mutation):
    ready(running, monkeypatch)
    seen = []

    def interrupt(store, observation):
        scope = checkpoint._SCOPE.get()
        seen.append(scope.connection)
        if mutation == 'read_transaction':
            scope.connection.execute('BEGIN')
        elif mutation == 'write_permission':
            scope.connection.execute('PRAGMA query_only=OFF')
        elif mutation == 'foreign_store':
            with checkpoint.checkpoint_connection(SimpleNamespace(path=store.path)):
                pytest.fail('Foreign store reused a finish reader')
        elif mutation == 'nested':
            with checkpoint.final_checkpoint_reader(store, scope.token):
                pytest.fail('Nested final reader was allowed')
        elif mutation == 'path_identity':
            monkeypatch.setattr(checkpoint, '_identity', lambda _store: (-1, -1))
        elif mutation == 'expired_token':
            scope.token.alive = False
        elif mutation == 'foreign_writer':
            scope.token.connection = scope.connection
        elif mutation == 'thread':
            context = copy_context()
            def foreign_thread():
                with checkpoint.checkpoint_connection(store):
                    pytest.fail('Another thread reused the reader')
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(ValueError, match='another finish'):
                    pool.submit(context.run, foreign_thread).result()
            raise ValueError('Confirmed copied-thread context rejection')
        else:
            raise ValueError('Injected final-fence exception')
        with checkpoint.checkpoint_connection(store):
            pytest.fail('Changed reader boundary was accepted')

    monkeypatch.setattr(current, 'recheck_finish_external_observation', interrupt)
    with pytest.raises(ValueError):
        running.store.finish(running.claim, {})
    assert checkpoint._SCOPE.get() is None
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute('SELECT 1')
    with running.store._connect() as conn:
        assert conn.execute('SELECT status FROM requests WHERE id=?',
                            (running.claim['id'],)).fetchone()[0] == 'running'
        assert conn.execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 0

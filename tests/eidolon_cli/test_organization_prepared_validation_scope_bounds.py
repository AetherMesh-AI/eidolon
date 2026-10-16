"""Known static scope records reject oversized dependencies before raw loading."""
import pytest

from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_prepared_validation_review_scope import review_scope_artifact
from tests.eidolon_cli.test_organization_prepared_validation_activity import mutate
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import (  # noqa: F401
    running, owner, prepared, folder_profile, host,
)

pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize('table,column', [
    ('prepared_validation_bindings', 'grant_sha256'),
    ('prepared_validation_bindings', 'binding_sha256'),
    ('objective_projects', 'binding'),
])
@pytest.mark.parametrize('binary', [False, True])
def test_static_scope_preflights_binding_scalars_before_raw_loader(running, monkeypatch, table, column, binary):
    calls = host(monkeypatch, running)
    assert running.store.run_project_stage(running.claim, running.cancel) == {}
    assert len(calls) == 2
    with running.store._connect() as conn:
        identifier = conn.execute('SELECT id FROM prepared_validation_review_scopes').fetchone()[0]
        assert review_scope_artifact(conn, identifier)['objectiveId'] == running.objective
        oversized = b'private-scalar-' * 32768
        value = oversized if binary else oversized.decode('ascii')
        mutate(conn, table, f'UPDATE {table} SET {column}=? WHERE objective_id=? AND project_id=?',
               (value, running.objective, 'notes'))

        # This inherited loader fetches the raw companion hashes and legacy
        # binding. The scope reader must reject inside SQL before reaching it.
        def forbid_raw_binding(*_args):
            pytest.fail('Static scope reached the raw binding loader before rejecting an oversized scalar')

        monkeypatch.setattr(ledger, '_binding', forbid_raw_binding)
        changes = conn.total_changes
        with pytest.raises(ValueError, match='scalar metadata exceeds its byte bound'):
            review_scope_artifact(conn, identifier)
        assert conn.total_changes == changes


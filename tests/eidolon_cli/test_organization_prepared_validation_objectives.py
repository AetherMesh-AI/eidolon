"""Prepared selection and admission invariants; no runtime proof is claimed."""
from copy import deepcopy

import pytest

from eidolon_cli import organization_prepared_validation_objectives as objectives
from eidolon_cli.organization_project_validation import digest
from tests.eidolon_cli.test_organization_prepared_validation_ledger import state  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_contract import prepared, normalize  # noqa: F401


def test_prepared_references_are_explicit_exact_and_copy_safe():
    assert objectives.normalize_prepared_selections(None) == []
    assert objectives.normalize_prepared_selections([]) == []
    refs = [{'projectId': 'desktop', 'revision': 2, 'registrationSha256': 'a' * 64}]
    normalized = objectives.normalize_prepared_selections(refs)
    refs[0]['revision'] = 3
    assert normalized[0]['revision'] == 2
    for key, value in [('revision', True), ('revision', 0), ('registrationSha256', 'A' * 64),
                       ('projectId', '../desktop')]:
        invalid = deepcopy(normalized)
        invalid[0][key] = value
        with pytest.raises(ValueError):
            objectives.normalize_prepared_selections(invalid)
    with pytest.raises(ValueError):
        objectives.normalize_prepared_selections(normalized * 2)
    with pytest.raises(ValueError):
        objectives.normalize_prepared_selections([{**normalized[0], 'enabled': True}])


def test_omitted_selection_preserves_legacy_checks_without_owner_lookup(state):
    checks = ['project_tests']
    selected, result = objectives.select_prepared_validations(state['conn'], None,
        projects=[], settings=state['store'].settings, delivery_mode='source_project', checks=checks)
    assert selected == []
    assert result is checks


def test_prepared_binding_and_objective_share_atomic_admission(state):
    store, conn, fixture = state['store'], state['conn'], state['prepared']
    conn.executescript(objectives.PREPARED_OBJECTIVE_SCHEMA)
    grant = normalize(fixture)
    selected = [{'reference': {'projectId': 'desktop', 'revision': grant['revision'],
                              'registrationSha256': digest({'candidate': grant})}, 'contract': grant}]
    with pytest.raises(RuntimeError, match='rollback admission'):
        with store._write() as write:
            identifier = store._create_objective(write, 'Prepared selection', idempotency_key='selection',
                project_ids=['desktop'], delivery_mode='managed_artifact')
            objectives.bind_selected_prepared_validations(write, identifier, selected)
            assert write.execute('SELECT count(*) FROM prepared_validation_registration_bindings').fetchone()[0] == 1
            raise RuntimeError('rollback admission')
    assert conn.execute('SELECT count(*) FROM prepared_validation_registration_bindings').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM prepared_validation_bindings').fetchone()[0] == 0
    assert conn.execute("SELECT 1 FROM objectives WHERE idempotency_key='selection'").fetchone() is None

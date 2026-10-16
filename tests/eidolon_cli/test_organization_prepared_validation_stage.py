"""Reservation identity tests; these do not claim native execution."""
from dataclasses import replace
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_stage as stage
from eidolon_cli.organization_project_effective_snapshot import EffectiveProjectSnapshot
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    state, prepared, objective, proposal, reserve,  # noqa: F401
)


def test_new_pair_is_exact_and_distinct(state):
    obj = objective(state)
    first = stage.new_validation_envelope(obj['ctx'], 'request-one', 1)
    second = stage.new_validation_envelope(obj['ctx'], 'request-two', 2)
    assert first['identity'] == second['identity']
    assert len({first['setId'], second['setId'], *(x['runId'] for x in first['steps']),
                *(x['runId'] for x in second['steps'])}) == 6
    assert {item['checkKind'] for item in first['steps']} == {'project_typecheck', 'project_tests'}
    assert first['generation'] == 1 and second['generation'] == 2


def test_source_proof_requires_transaction_exact_identity_and_is_immutable(state):
    conn = state['conn']
    conn.executescript(stage.SOURCE_PROOF_SCHEMA)
    obj = objective(state)
    snapshot = EffectiveProjectSnapshot(obj['ctx']['objective_id'], obj['snapshot'], obj['snapshot'], ())
    obj['ctx'].update(preimage_sha256=snapshot.preimage_sha256,
                      managed_revisions_sha256=snapshot.managed_revisions_sha256)
    value = proposal(state, obj)
    reserve(state, obj, value)
    with pytest.raises(ValueError, match='transaction'):
        stage.retain_source_proof(conn, value, snapshot)
    conn.execute('BEGIN IMMEDIATE')
    stage.retain_source_proof(conn, value, snapshot)
    conn.commit()
    row = conn.execute('SELECT * FROM prepared_validation_source_proofs').fetchone()
    assert 'reviewed fixture' not in row['record']
    for sql in ('DELETE FROM prepared_validation_source_proofs',
                "UPDATE prepared_validation_source_proofs SET sha256='forged'",
                'INSERT OR REPLACE INTO prepared_validation_source_proofs VALUES (?,?,?)'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute(sql, tuple(row) if '?' in sql else ())
    conn.execute('BEGIN IMMEDIATE')
    with pytest.raises(ValueError, match='differs'):
        stage.retain_source_proof(conn, {**value, 'source': {**value['source'], 'snapshotSha256': 'a'*64}}, snapshot)
    conn.rollback()


def test_source_proof_byte_limit_fails_before_insert(state, monkeypatch):
    conn = state['conn']
    conn.executescript(stage.SOURCE_PROOF_SCHEMA)
    obj = objective(state)
    snapshot = EffectiveProjectSnapshot(obj['ctx']['objective_id'], obj['snapshot'], obj['snapshot'], ())
    obj['ctx'].update(preimage_sha256=snapshot.preimage_sha256,
                      managed_revisions_sha256=snapshot.managed_revisions_sha256)
    value = proposal(state, obj)
    reserve(state, obj, value)
    monkeypatch.setattr(stage, 'MAX_SOURCE_PROOF_BYTES', 10)
    conn.execute('BEGIN IMMEDIATE')
    with pytest.raises(ValueError, match='limit'):
        stage.retain_source_proof(conn, value, snapshot)
    conn.rollback()
    assert conn.execute('SELECT count(*) FROM prepared_validation_source_proofs').fetchone()[0] == 0

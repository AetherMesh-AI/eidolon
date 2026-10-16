"""Private proof reuse stays tied to exact SQL inputs and live finish authority."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_finish as finish
from eidolon_cli import organization_prepared_validation_current as current
from eidolon_cli import organization_prepared_validation_inventory as inventory
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_prepared_validation_verification as verification
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import (
    running, confirmed, folder_profile, owner, prepared,  # noqa: F401
    finish_host, review_result, finish_acceptance,
)
from tests.eidolon_cli.test_organization_prepared_validation_finish import ready, after_preflight
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import scalar

pytestmark = pytest.mark.linux_only


def test_strict_snapshot_and_runtime_verification_only_run_before_each_write(running, monkeypatch):
    ready(running, monkeypatch)
    state = {'write': False}
    calls = []
    write = running.store._write
    @contextmanager
    def writing():
        with write() as conn:
            state['write'] = True
            try:
                yield conn
            finally:
                state['write'] = False
    monkeypatch.setattr(running.store, '_write', writing)
    for module, name in ((ledger, '_snapshot'), (verification, '_run')):
        original = getattr(module, name)
        def verified(*args, _original=original, _name=name, **kwargs):
            assert not state['write'], 'Expensive deterministic verification must precede the write lock'
            calls.append(_name)
            with running.store._connect() as competing:
                competing.execute('BEGIN IMMEDIATE')
                competing.rollback()
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, verified)
    assert running.store.finish(running.claim, {})
    review = running.store.claim_next(recoverable=True)
    result = review_result(running, review)
    assert running.store.finish(review, result)
    finish_acceptance(running.store)
    assert calls.count('_snapshot') >= 4 and calls.count('_run') >= 8


def test_reused_result_is_detached_and_true_mode_still_requires_current_review(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                result = current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                original_hashes = dict(result['evidenceHashes'])
                result['envelope']['generation'] = 999
                result['evidenceHashes'].clear()
                result['checks'][0][1]['content'] = 'forged'
                result['independentReviewVerified'] = True
                fresh = current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                assert fresh['evidenceHashes'] == original_hashes
                assert fresh['envelope']['generation'] != 999
                assert fresh['independentReviewVerified'] is False
                with pytest.raises(ValueError, match='whole-pair review'):
                    current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes')


@pytest.mark.parametrize('change', ['file', 'event', 'scope', 'schema', 'temp_shadow'])
def test_same_connection_mutation_after_entry_rejects_next_proof(running, monkeypatch, change):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                conn.execute('SAVEPOINT tamper')
                if change == 'temp_shadow':
                    conn.execute('CREATE TEMP VIEW prepared_validation_results AS SELECT * FROM main.prepared_validation_results')
                elif change == 'schema':
                    conn.execute('CREATE INDEX unexpected_proof_index ON prepared_validation_files(set_id)')
                else:
                    table = {'file': 'prepared_validation_files', 'event': 'prepared_validation_runtime_events',
                             'scope': 'prepared_validation_review_scopes'}[change]
                    for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
                        conn.execute(f'DROP TRIGGER "{row[0]}"')
                    column = {'file': 'content', 'event': 'record', 'scope': 'content'}[change]
                    assignment = f'{column}=?' + (',byte_count=6' if change == 'file' else '')
                    conn.execute(f'UPDATE {table} SET {assignment} WHERE rowid=(SELECT min(rowid) FROM {table})',
                                 (b'forged' if change == 'file' else 'forged',))
                with pytest.raises(ValueError, match='changed|TEMP|temporary'):
                    current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                conn.execute('ROLLBACK TO tamper')
                conn.execute('RELEASE tamper')


@pytest.mark.parametrize('sql', [
    "UPDATE objective_control SET round=round+1 WHERE objective_id=?",
    "UPDATE objective_control SET criteria='[\"forged\"]' WHERE objective_id=?",
    "UPDATE objectives SET cancelled=1 WHERE id=?",
    "UPDATE requests SET payload='{}' WHERE id=?",
])
def test_final_transition_checks_reject_changes_after_last_proof_use(running, monkeypatch, sql):
    ready(running, monkeypatch)
    original = running.store._maybe_integrate
    def changed(conn, objective_id):
        original(conn, objective_id)
        conn.execute(sql, (running.claim['id'] if 'requests' in sql else objective_id,))
    monkeypatch.setattr(running.store, '_maybe_integrate', changed)
    with pytest.raises(ValueError, match='transition|cancelled|completion|changed'):
        running.store.finish(running.claim, {})
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_steps') == 2


def test_final_unconditional_inventory_check_rejects_late_evidence_mutation(running, monkeypatch):
    ready(running, monkeypatch)
    original = running.store._maybe_integrate
    def changed(conn, objective_id):
        original(conn, objective_id)
        conn.execute("UPDATE evidence SET summary='changed after last proof' WHERE objective_id=?", (objective_id,))
    monkeypatch.setattr(running.store, '_maybe_integrate', changed)
    with pytest.raises(ValueError, match='changed'):
        running.store.finish(running.claim, {})
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'


def test_inventory_and_strict_verification_share_one_read_snapshot(running, monkeypatch):
    ready(running, monkeypatch)
    checked = verification.verify_latest_prepared_validation_set
    captured = inventory.capture_prepared_inventory
    views = []
    def strict(conn, *args, **kwargs):
        assert conn.in_transaction
        views.append(conn)
        return checked(conn, *args, **kwargs)
    def capture(conn, *args, **kwargs):
        assert conn.in_transaction and views == [conn]
        return captured(conn, *args, **kwargs)
    monkeypatch.setattr(verification, 'verify_latest_prepared_validation_set', strict)
    monkeypatch.setattr(inventory, 'capture_prepared_inventory', capture)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        assert token.inventory is not None
    assert len(views) == 1


def test_capacity_rejects_before_strict_materialization_and_has_no_finish_effect(running, monkeypatch):
    ready(running, monkeypatch)
    monkeypatch.setattr(inventory, '_MAX_INVENTORY_BYTES', 1)
    monkeypatch.setattr(verification, 'verify_latest_prepared_validation_set',
                        lambda *a, **kw: pytest.fail('Over-cap proof must reject before loading proof bodies'))
    with pytest.raises(ValueError, match='bound'):
        running.store.finish(running.claim, {})
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_steps') == 2


def test_source_and_static_raw_values_are_shared_and_capacity_is_inclusive(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        target = token.targets['notes']
        source = {item.path: item.content for item in target['snapshot'].files}
        for bundle in (target['observation'].fresh.original, target['observation'].fresh.effective):
            assert all(item.content is source[item.path] for item in bundle.files)
        cells = [value for table in token.inventory.rows for row in table for kind, value in row if kind in {'text', 'blob'}]
        assert all(any(value is item for item in cells) for value in source.values())
        assert all(any(raw is item for item in cells) for _, raw, _ in token.static_records.values())
        exact = token.retained_raw_bytes
        assert exact >= token.inventory.raw_bytes > token.static_bytes
        monkeypatch.setattr(finish, '_MAX_RETAINED_RAW_BYTES', exact)
        finish._check_retained_capacity(token)
        monkeypatch.setattr(finish, '_MAX_RETAINED_RAW_BYTES', exact - 1)
        with pytest.raises(ValueError, match='capacity'):
            finish._check_retained_capacity(token)


def test_rollback_and_restart_cannot_reuse_same_finish_transaction(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with pytest.raises(sqlite3.OperationalError, match='savepoint'):
            with running.store._write() as conn:
                request = running.store._owned(conn, running.claim)
                with finish.finish_verification_scope(token, running.store, conn, request):
                    conn.rollback()
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute("UPDATE objectives SET title='must rollback' WHERE id=?", (running.objective,))
    assert scalar(running, 'SELECT title FROM objectives WHERE id=?', (running.objective,)) != 'must rollback'
    assert running.store.finish(running.claim, {})


def test_current_callback_is_never_skipped_and_cannot_change_observed_context(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                def denied(*args):
                    raise ValueError('Current caller denied authority')
                with pytest.raises(ValueError, match='caller denied'):
                    verification.verify_latest_prepared_validation_set(conn, running.objective, 'notes',
                        current_authority=denied, require_review=False)
                value = json.loads(token.targets['notes']['observation'].value)
                conn.execute('SAVEPOINT policy_change')
                conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
                with pytest.raises(ValueError, match='policy or round'):
                    verification.verify_latest_prepared_validation_set(conn, running.objective, 'notes',
                        current_authority=lambda *args: value, require_review=False)
                conn.execute('ROLLBACK TO policy_change')
                conn.execute('RELEASE policy_change')


def test_public_context_retains_exact_evidence_and_rejects_gap_mutation(running, monkeypatch):
    _, review = finish_host(running, monkeypatch)
    before = running.store.context(review, mark_read=False)
    after = running.store.context(review)
    assert before['evidence'] == after['evidence']
    original = finish.prepare_context_verification
    @contextmanager
    def changed(*args):
        with original(*args) as token:
            with running.store._write() as conn:
                conn.execute("UPDATE evidence SET summary='changed' WHERE objective_id=?", (running.objective,))
            yield token
    monkeypatch.setattr(finish, 'prepare_context_verification', changed)
    with pytest.raises(ValueError, match='changed'):
        running.store.context(review)


def test_unrelated_managed_history_growth_still_rejects_cached_pair(running, monkeypatch):
    from eidolon_cli import organization_project_effective_snapshot as effective
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                # An unreferenced completed planning request is deliberately not
                # frozen in source-body inventory, but still counts for its cap.
                conn.execute('SAVEPOINT history_growth')
                request_id = conn.execute("SELECT id FROM requests WHERE objective_id=? AND type='request.plan' LIMIT 1",
                                          (running.objective,)).fetchone()[0]
                conn.execute('UPDATE requests SET reason=? WHERE id=?', ('x' * 200000, request_id))
                monkeypatch.setattr(effective, '_MAX_MANAGED_HISTORY_BYTES', 150000)
                with pytest.raises(ValueError, match='Managed source history'):
                    current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                conn.execute('ROLLBACK TO history_growth')
                conn.execute('RELEASE history_growth')


def test_retained_input_shortcut_requires_exact_private_active_scope(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        target = token.targets['notes']
        with running.store._connect() as conn, pytest.raises(ValueError, match='private active'):
            current.recheck_observed_prepared_authority(running.store, conn, json.loads(target['envelope']),
                target['snapshot'], target['observation'], exact_retained_inputs=True)


@pytest.mark.parametrize('purpose', ['finish', 'context'])
def test_legacy_bypass_closes_its_owned_read_transaction_before_caller_write(running, monkeypatch, purpose):
    from eidolon_cli import organization_prepared_validation_objectives as objectives
    connection = running.store._connect
    active = []
    @contextmanager
    def tracked():
        with connection() as conn:
            active.append(conn)
            try:
                yield conn
            finally:
                active.remove(conn)
    monkeypatch.setattr(running.store, '_connect', tracked)
    if purpose == 'finish':
        monkeypatch.setattr(finish, '_projects', lambda *a: None)
        scope = finish.prepare_finish_verification(running.store, running.claim, {})
    else:
        monkeypatch.setattr(objectives, 'prepared_selection_references', lambda *a: [])
        scope = finish.prepare_context_verification(running.store, running.claim)
    with scope as token:
        assert token is None and all(not conn.in_transaction for conn in active)
        with connection() as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.rollback()


@pytest.mark.parametrize('column', ['attempts', 'execution_count', 'status', 'lease', 'reason', 'cancelled'])
@pytest.mark.parametrize('blob', [False, True])
def test_final_transition_rejects_large_scalars_before_materialization(running, column, blob):
    with running.store._write() as conn:
        request = running.store._owned(conn, running.claim)
        token = finish._FinishToken(running.store, finish._request_binding(request),
            finish._operation_binding(conn, running.objective), dict(running.claim), objective_id=running.objective,
            claim_policy=finish._claim_policy(conn, request['id']))
        conn.execute("UPDATE requests SET status='completed',token=NULL,lease=NULL,reason=NULL WHERE id=?", (request['id'],))
        raw = b'x' * (1024 * 1024 + 1)
        table, key = ('objectives', running.objective) if column == 'cancelled' else ('requests', request['id'])
        conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (raw if blob else raw.decode(), key))
        def text_factory(value):
            assert value != raw, 'Malformed transition scalar reached Python decoding'
            return value.decode()
        def row_factory(cursor, values):
            assert not any(type(value) is bytes and value == raw for value in values), 'Malformed BLOB was materialized'
            return sqlite3.Row(cursor, values)
        conn.text_factory, conn.row_factory = text_factory, row_factory
        with pytest.raises(ValueError, match='completion|cancelled'):
            finish._require_finish_transition(token, conn, request)
        conn.rollback()


@pytest.mark.parametrize('exhausted', [False, True])
def test_executive_rejection_retains_decision_and_exact_permitted_round(running, monkeypatch, exhausted):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import acceptance_claim
    _, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    if exhausted:
        with running.store._write() as conn:
            conn.execute('UPDATE objective_control SET max_replans=0 WHERE objective_id=?', (running.objective,))
    acceptance, result = acceptance_claim(running.store)
    result['approved'] = False
    result['summary'] = 'The integrated report still leaves a material question unanswered.'
    result['conflicts'] = ['The report does not explain the selected coverage limitation.']
    result['criteriaResults'][0]['satisfied'] = False
    assert running.store.finish(acceptance, result)
    with running.store._connect() as conn:
        decision = conn.execute('SELECT * FROM objective_acceptances WHERE request_id=?', (acceptance['id'],)).fetchone()
        assert decision['approved'] == 0 and decision['summary'] == result['summary']
        assert json.loads(decision['criteria_results']) == result['criteriaResults']
        assert json.loads(decision['conflicts']) == result['conflicts']
        control = conn.execute('SELECT round,status FROM objective_control WHERE objective_id=?', (running.objective,)).fetchone()
        assert tuple(control) == ((0, 'blocked') if exhausted else (1, 'replanning'))
        assert conn.execute('SELECT sum(units) FROM prepared_validation_steps').fetchone()[0] == 2
        assert conn.execute('SELECT status FROM requests WHERE id=?', (acceptance['id'],)).fetchone()[0] == 'completed'


def test_cached_pair_matches_uncached_internal_return_types(running, monkeypatch):
    ready(running, monkeypatch)
    with running.store._connect() as conn:
        uncached = current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                cached = current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes', require_review=False)
                assert cached == uncached
                assert all(type(pair) is tuple for pair in cached['checks'])


@pytest.mark.parametrize('profiles', [1, 2])
def test_measured_full_renderer_capacity_is_one_profile_without_refunding_prior_execution(running, monkeypatch, profiles):
    """Use measured raw-size accounting, not fabricated native runtime evidence.

    The local actual-source/manifest synthetic ledger measured 76,155,749 bytes
    of inventory and 76,618,974 total retained raw bytes for one full profile.
    Envelope/host metadata were synthetic. Two comparable inventories alone
    exceed 128 MiB. This finish guard cannot undo prior two-unit execution cost.
    """
    from dataclasses import replace
    ready(running, monkeypatch)
    capture = inventory.capture_prepared_inventory
    def measured(*args, **kwargs):
        observed = capture(*args, **kwargs)
        return replace(observed, raw_bytes=76_155_749 * profiles)
    monkeypatch.setattr(inventory, 'capture_prepared_inventory', measured)
    if profiles == 1:
        with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
            assert 76_155_749 <= token.retained_raw_bytes <= finish._MAX_RETAINED_RAW_BYTES
    else:
        write = running.store._write
        monkeypatch.setattr(running.store, '_write', lambda: pytest.fail('Over-cap finish must fail before its write'))
        with pytest.raises(ValueError, match='retained raw-byte capacity'):
            running.store.finish(running.claim, {})
        monkeypatch.setattr(running.store, '_write', write)
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'


def test_pair_rejection_at_exhausted_replan_limit_keeps_round_and_charge(running, monkeypatch):
    _, review = finish_host(running, monkeypatch)
    with running.store._write() as conn:
        conn.execute('UPDATE objective_control SET max_replans=0 WHERE objective_id=?', (running.objective,))
    result = review_result(running, review)
    result['approved'] = False
    assert running.store.finish(review, result)
    assert scalar(running, 'SELECT round FROM objective_control WHERE objective_id=?', (running.objective,)) == 0
    assert scalar(running, 'SELECT approved FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.project_failed'") == 1


@pytest.mark.parametrize('change', ['agent', 'request', 'control', 'cancel', 'review_boolean', 'review_request', 'policy', 'pause', 'claim_policy'])
def test_final_external_callback_cannot_change_mutable_authority_or_review(running, monkeypatch, change):
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    from eidolon_cli.organization_project_validation import canonical, digest
    _, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    original = current.recheck_finish_external_observation
    def changed(store, observation):
        original(store, observation)
        conn = finish._ACTIVE.get().connection
        if change == 'policy':
            conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
        elif change == 'pause':
            conn.execute('INSERT INTO organization_policy_pause VALUES (1)')
        elif change == 'claim_policy':
            conn.execute('UPDATE request_policy SET generation=generation+1 WHERE request_id=?', (review['id'],))
        elif change == 'agent':
            conn.execute("UPDATE staff_state SET active=0 WHERE agent_id='inspector'")
        elif change == 'request':
            conn.execute("UPDATE requests SET payload='{}' WHERE id=?", (review['id'],))
        elif change == 'control':
            conn.execute("UPDATE objective_control SET criteria='[\"forged\"]' WHERE objective_id=?", (running.objective,))
        elif change == 'cancel':
            conn.execute('UPDATE objectives SET cancelled=1 WHERE id=?', (running.objective,))
        elif change == 'review_request':
            conn.execute("UPDATE requests SET agent_id='inspector' WHERE id=?", (review['id'],))
        else:
            row = conn.execute('SELECT set_id,record FROM prepared_validation_pair_reviews').fetchone()
            for item in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name='prepared_validation_pair_reviews'").fetchall():
                conn.execute(f'DROP TRIGGER "{item[0]}"')
            forged = json.loads(row['record'])
            forged['approved'] = 1  # Python equality must never equate this with True.
            conn.execute('UPDATE prepared_validation_pair_reviews SET record=?,sha256=? WHERE set_id=?',
                         (canonical(forged), digest(forged), row['set_id']))
    monkeypatch.setattr(current, 'recheck_finish_external_observation', changed)
    with pytest.raises((ValueError, OwnerPolicyError), match='changed|completion|cancelled|transition|review|policy'):
        running.store.finish(review, result)
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (review['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


@pytest.mark.parametrize('change', ['prior_review', 'acceptance_boolean'])
def test_acceptance_final_fence_rechecks_prior_review_and_exact_typed_decision(running, monkeypatch, change):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import acceptance_claim
    _, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    acceptance, result = acceptance_claim(running.store)
    original = current.recheck_finish_external_observation
    def changed(store, observation):
        original(store, observation)
        conn = finish._ACTIVE.get().connection
        if change == 'prior_review':
            conn.execute("UPDATE requests SET status='running' WHERE id=?", (review['id'],))
        else:
            row = conn.execute('SELECT criteria_results FROM objective_acceptances WHERE request_id=?',
                               (acceptance['id'],)).fetchone()
            forged = json.loads(row[0])
            forged[0]['satisfied'] = 1
            conn.execute('UPDATE objective_acceptances SET criteria_results=? WHERE request_id=?',
                         (json.dumps(forged), acceptance['id']))
    monkeypatch.setattr(current, 'recheck_finish_external_observation', changed)
    with pytest.raises(ValueError, match='review|decision'):
        running.store.finish(acceptance, result)
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (acceptance['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0


@pytest.mark.parametrize('change', ['owner', 'settings', 'configuration', 'budget', 'routing', 'registry', 'root'])
def test_final_owner_source_checks_uncommitted_effective_authority(running, monkeypatch, change):
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    _, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    original = current.recheck_finish_external_observation
    def changed(store, observation):
        original(store, observation)
        conn = finish._ACTIVE.get().connection
        if change == 'owner':
            conn.execute("UPDATE organization_metadata SET owner_id='00000000-0000-4000-8000-000000000000'")
        elif change == 'settings':
            conn.execute("UPDATE organization_policy SET settings='{}' WHERE id=1")
        elif change == 'configuration':
            conn.execute("INSERT OR REPLACE INTO organization_configuration VALUES (1,'{}',0)")
        elif change == 'budget':
            conn.execute('INSERT OR REPLACE INTO organization_owner_budget_policy VALUES (1,?)',
                         (json.dumps({'max_total_tokens': 1234}),))
        elif change == 'routing':
            conn.execute("INSERT OR REPLACE INTO organization_model_routing_config VALUES (1,'{}')")
        elif change == 'registry':
            conn.execute("INSERT INTO organization_project_registry VALUES ('foreign','{}',?,0)", ('0' * 64,))
        else:
            conn.execute("INSERT INTO organization_root_grants VALUES ('root7','/unapproved','[]',1,1)")
    monkeypatch.setattr(current, 'recheck_finish_external_observation', changed)
    with pytest.raises((ValueError, OwnerPolicyError)):
        running.store.finish(review, result)
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (review['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


@pytest.mark.parametrize('field', ['owner_id', 'settings', 'configuration', 'registry_created', 'registry_extra', 'root_extra', 'foreign_revision'])
@pytest.mark.parametrize('blob', [False, True])
def test_final_owner_admission_rejects_raw_oversize_before_helper_or_materialization(running, monkeypatch, field, blob):
    from eidolon_cli import organization_prepared_validation_setup as setup
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        observation = token.targets['notes']['observation']
        with running.store._write() as conn:
            raw = b'x' * (2 * 1024**2 + 1)
            value = raw if blob else raw.decode()
            if field == 'owner_id':
                conn.execute('UPDATE organization_metadata SET owner_id=?', (value,))
            elif field == 'settings':
                conn.execute('UPDATE organization_policy SET settings=?', (value,))
            elif field == 'configuration':
                conn.execute('INSERT OR REPLACE INTO organization_configuration VALUES (1,?,0)', (value,))
            elif field == 'registry_created':
                conn.execute("INSERT INTO organization_project_registry VALUES ('foreign','{}',?,?)", ('0' * 64, value))
            elif field in {'registry_extra', 'root_extra'}:
                table = 'organization_project_registry' if field == 'registry_extra' else 'organization_root_grants'
                conn.execute(f'ALTER TABLE {table} ADD COLUMN extra BLOB')
                if field == 'registry_extra':
                    conn.execute("INSERT INTO organization_project_registry VALUES ('foreign','{}',?,0,?)", ('0' * 64, value))
                else:
                    conn.execute("INSERT INTO organization_root_grants VALUES ('root7','/unapproved','[]',1,1,?)", (value,))
            else:
                conn.execute('INSERT INTO organization_prepared_validation_registrations '
                    '(sequence,project_id,revision,organization_id,owner_id,enabled,policy_generation,source_revision,'
                    'candidate,candidate_hash,receipt,receipt_hash) '
                    "SELECT sequence+1,'foreign',?,organization_id,owner_id,enabled,policy_generation,source_revision,"
                    "'{}',candidate_hash,'{}',receipt_hash FROM organization_prepared_validation_registrations LIMIT 1", (value,))
            statements = []
            conn.set_trace_callback(statements.append)
            monkeypatch.setattr(setup, 'check_current_prepared_registration',
                                lambda *a, **k: pytest.fail('Owner checkpoint ran before SQL admission'))
            with pytest.raises(ValueError, match='owner source'):
                current.recheck_finish_owner_source(running.store, conn, observation)
            conn.set_trace_callback(None)
            # Every body-table SELECT before rejection is a scalar SQL guard.
            # No raw TEXT/BLOB projection or normal owner helper can run first.
            body_reads = [sql for sql in statements if 'FROM main.organization_' in sql]
            assert body_reads and all(sql.startswith(('SELECT 1 ', 'SELECT count(*) ',
                                                      'SELECT coalesce(sum(')) for sql in body_reads)
            conn.rollback()


def test_owner_source_admission_allows_absent_optional_tables_and_excludes_history_bodies(running):
    with running.store._write() as conn:
        # The ready-owner checkpoint inspects these schemas but never reads old
        # audit/receipt bodies. Do not make their historical payloads a hot read.
        conn.execute("INSERT INTO organization_metadata_audit VALUES (?,999,'history',?,'now')",
                     (conn.execute('SELECT organization_id FROM organization_metadata').fetchone()[0], 'x' * (2 * 1024**2 + 1)))
        for table in ('organization_root_revocation_reviews', 'organization_root_revocations',
                      'organization_root_grants', 'organization_project_registry', 'organization_owner_budget_policy'):
            conn.execute(f'DROP TABLE {table}')
        current._finish_owner_source_bounds(conn)
        conn.rollback()


def test_owner_source_schema_is_bounded_before_pragma_or_body_reads(running):
    with running.store._write() as conn:
        conn.execute("CREATE TABLE oversized_owner_schema (value TEXT DEFAULT '" + 'x' * 65537 + "')")
        statements = []
        conn.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match='owner source schema exceeds capacity'):
            current._finish_owner_source_bounds(conn)
        conn.set_trace_callback(None)
        assert not any('table_xinfo' in sql or 'FROM main.organization_' in sql for sql in statements)
        conn.rollback()


def test_owner_source_metadata_capacity_is_checked_before_owner_reader(running, monkeypatch):
    with running.store._write() as conn:
        monkeypatch.setattr(current, '_MAX_OWNER_SOURCE_BYTES', 1)
        with pytest.raises(ValueError, match='metadata capacity'):
            current._finish_owner_source_bounds(conn)


@pytest.mark.parametrize('table', ['organization_root_grants', 'organization_root_revocations',
                                  'organization_root_revocation_reviews', 'organization_project_registry',
                                  'organization_owner_budget_policy'])
def test_optional_owner_table_view_is_not_treated_as_absent(running, table):
    with running.store._write() as conn:
        conn.execute(f'DROP TABLE {table}')
        conn.execute(f'CREATE VIEW {table} AS SELECT zeroblob(3000000) AS alias')
        statements = []
        conn.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match='ordinary table'):
            current._finish_owner_source_bounds(conn)
        conn.set_trace_callback(None)
        assert not any(f'FROM main.{table}' in sql for sql in statements)
        conn.rollback()

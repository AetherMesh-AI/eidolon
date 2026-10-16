"""One-operation static JSON reuse never reuses authority or proof conclusions."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from eidolon_cli import organization_prepared_validation_finish as finish
from eidolon_cli import organization_prepared_validation_flow as flow
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli.organization_project_validation import canonical, digest
from tests.eidolon_cli.test_organization_prepared_validation_finish import (
    running, owner, prepared, folder_profile, confirmed, ready, after_preflight,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import scalar

pytestmark = pytest.mark.linux_only


def drop_guards(conn, table):
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')


def test_static_grant_and_candidate_bounded_parsers_are_preflight_only(running, monkeypatch):
    ready(running, monkeypatch)
    checked, parse = ledger._checked_json, setup._json
    grants, candidates = [], []
    def checked_grant(record, sha256):
        if type(record) is str and '"contract":"prepared-validation-v1"' in record:
            grants.append(finish._ACTIVE.get() is not None)
        return checked(record, sha256)
    def parsed_candidate(value, maximum, label):
        if label == 'Retained prepared candidate':
            candidates.append(finish._ACTIVE.get() is not None)
        return parse(value, maximum, label)
    monkeypatch.setattr(ledger, '_checked_json', checked_grant)
    monkeypatch.setattr(setup, '_json', parsed_candidate)
    assert running.store.finish(running.claim, {})
    assert grants == [False] and candidates == [False]


@pytest.mark.parametrize('kind', ['grant', 'candidate'])
@pytest.mark.parametrize('change', ['raw', 'hash', 'rehashed', 'binary', 'identity'])
def test_exact_static_record_cas_rejects_every_changed_representation(running, monkeypatch, kind, change):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        identity, raw, sha256 = token.static_records[(kind, 'notes')]
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                if change == 'raw':
                    raw += ' '
                elif change == 'hash':
                    sha256 = 'f' * 64
                elif change == 'rehashed':
                    value = json.loads(raw)
                    value['injected'] = True
                    raw, sha256 = canonical(value), digest(value)
                elif change == 'binary':
                    raw = raw.encode()
                else:
                    identity = (*identity[:2], identity[2] + 1) if kind == 'candidate' else (kind, 'another-objective', 'notes')
                with pytest.raises(ValueError, match='static'):
                    finish.observed_finish_static_record(conn, identity, raw, sha256)
    assert token.static_records == {}


def test_static_records_are_detached_connection_thread_scoped_and_cleared(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        identity, raw, sha256 = token.static_records[('grant', 'notes')]
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                first = finish.observed_finish_static_record(conn, identity, raw, sha256)
                first['profile']['bundleSha256'] = 'f' * 64
                assert finish.observed_finish_static_record(conn, identity, raw, sha256) == json.loads(raw)
                with running.store._connect() as other:
                    with pytest.raises(ValueError, match='foreign'):
                        finish.observed_finish_static_record(other, identity, raw, sha256)
                with ThreadPoolExecutor(max_workers=1) as worker:
                    result = worker.submit(copy_context().run,
                        lambda: finish.observed_finish_static_record(conn, identity, raw, sha256))
                    with pytest.raises(ValueError, match='foreign'):
                        result.result()
                del token.static_records[('grant', 'notes')]
                with pytest.raises(ValueError, match='not observed'):
                    finish.observed_finish_static_record(conn, identity, raw, sha256)
    assert token.static_records == {} and not token.alive
    with running.store._connect() as other:
        assert finish.observed_finish_static_record(other, identity, raw, sha256) is None


@pytest.mark.parametrize('kind', ['grant', 'candidate'])
@pytest.mark.parametrize('change', ['whitespace', 'rehashed', 'hash', 'binary'])
def test_actual_retained_static_mutation_aborts_first_finish(running, monkeypatch, kind, change):
    ready(running, monkeypatch)
    def mutate(_token):
        table, body, hash_column = ('prepared_validation_bindings', 'grant_record', 'grant_sha256') if kind == 'grant' else (setup._TABLE, 'candidate', 'candidate_hash')
        with running.store._write() as conn:
            drop_guards(conn, table)
            raw, sha256 = conn.execute(f'SELECT {body},{hash_column} FROM {table}').fetchone()
            if change == 'whitespace':
                raw += ' '
            elif change == 'rehashed':
                value = json.loads(raw)
                value['injected'] = True
                raw, sha256 = canonical(value), digest(value)
            elif change == 'binary':
                raw = raw.encode()
            else:
                sha256 = 'f' * 64
            conn.execute(f'UPDATE {table} SET {body}=?,{hash_column}=?', (raw, sha256))
    after_preflight(monkeypatch, mutate)
    with pytest.raises((ValueError, RuntimeError)):
        running.store.finish(running.claim, {})
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


@pytest.mark.parametrize('change', ['missing', 'foreign', 'cleanup', 'staging', 'unknown', 'malformed', 'binary'])
def test_failure_routing_hint_never_promotes_missing_or_ambiguous_claims(running, monkeypatch, change):
    ready(running, monkeypatch)
    with running.store._write() as conn:
        set_id = conn.execute('SELECT id FROM prepared_validation_sets').fetchone()[0]
        runs = conn.execute('SELECT run_id FROM prepared_validation_steps ORDER BY step_id').fetchall()
        assert flow._claims_complete_pair(conn, set_id, runs)
        drop_guards(conn, 'prepared_validation_results')
        if change == 'missing':
            conn.execute('DELETE FROM prepared_validation_results WHERE run_id=?', (runs[0][0],))
        else:
            row = conn.execute('SELECT record,status FROM prepared_validation_results WHERE run_id=?', (runs[0][0],)).fetchone()
            value = json.loads(row[0])
            status = row[1]
            if change == 'foreign':
                value['runId'] = 'foreign-run'
            elif change in {'cleanup', 'staging'}:
                value['cleanupConfirmed' if change == 'cleanup' else 'stagingCleanupConfirmed'] = False
            elif change == 'unknown':
                status = 'unknown'
            raw = '{' if change == 'malformed' else canonical(value)
            if change == 'binary':
                raw = raw.encode()
            conn.execute('UPDATE prepared_validation_results SET record=?,sha256=?,status=? WHERE run_id=?',
                         (raw, digest(value), status, runs[0][0]))
        assert flow._claims_complete_pair(conn, set_id, runs) is False


@pytest.mark.parametrize('remaining', [0, -1])
def test_aggregate_raw_capacity_boundary_precedes_write_and_preserves_retry(running, monkeypatch, remaining):
    ready(running, monkeypatch)
    with running.store._connect() as conn:
        size = conn.execute('SELECT length(CAST(grant_record AS BLOB)) FROM prepared_validation_bindings').fetchone()[0]
        size += conn.execute(f'SELECT length(CAST(candidate AS BLOB)) FROM {setup._TABLE}').fetchone()[0]
    assert finish._MAX_STATIC_RECORD_BYTES == 128 * 1024**2
    monkeypatch.setattr(finish, '_MAX_STATIC_RECORD_BYTES', size + remaining)
    writes, original = [], running.store._write
    from contextlib import contextmanager
    @contextmanager
    def write():
        writes.append(True)
        with original() as conn:
            yield conn
    monkeypatch.setattr(running.store, '_write', write)
    if remaining == 0:
        assert running.store.finish(running.claim, {})
        assert len(writes) == 1
    else:
        with pytest.raises(ValueError, match='aggregate raw-byte capacity'):
            running.store.finish(running.claim, {})
        assert writes == []
        assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'running'
        assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
        assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
        assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
        monkeypatch.setattr(finish, '_MAX_STATIC_RECORD_BYTES', size)
        assert running.store.finish(running.claim, {})


def test_static_capacity_counts_utf8_once_per_exact_retained_record(monkeypatch):
    connection = object()
    token = finish._FinishToken(object(), '', '', {}, objective_id='objective',
        projects=('first', 'second'), static_projects=('first', 'second'))
    first = canonical({'value': '\u00e9'})
    second = canonical({'value': '\u03bb'})
    limit = len(first.encode()) + len(second.encode())
    monkeypatch.setattr(finish, '_MAX_STATIC_RECORD_BYTES', limit)
    with finish._static_preflight_scope(token, connection):
        for _ in range(2):
            finish.retain_finish_static_record(connection, ('grant', 'objective', 'first'), first, digest(json.loads(first)))
        assert token.static_bytes == len(first.encode())
        finish.retain_finish_static_record(connection, ('grant', 'objective', 'second'), second, digest(json.loads(second)))
        assert token.static_bytes == limit
        with pytest.raises(ValueError, match='capacity'):
            finish.retain_finish_static_record(connection, ('candidate', 'first', 1), '{}', digest({}))
        assert len(token.static_records) == 2 and token.static_bytes == limit


@pytest.mark.parametrize('other_state', ['unreviewed', 'failed'])
def test_rejected_review_reads_other_selected_static_grants_without_proof_promotion(owner, monkeypatch, other_state):
    from tests.eidolon_cli.test_organization_prepared_validation_finish import two_projects, review_result
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import synthetic_receipt
    from tests.eidolon_cli.test_organization_project_workspace_receipt import events_for
    from eidolon_cli import organization_project_workspace_node as runtime
    state = two_projects(owner)
    roots = []
    def run(source, directory, manifest, toolchain, profile, step_id, **options):
        if source.root_alias not in roots:
            roots.append(source.root_alias)
        receipt = synthetic_receipt(source, profile, step_id, options)
        if len(roots) == 2 and other_state == 'failed':
            receipt['status'] = 'failed'
        for event in events_for(receipt):
            assert options['record_event'](event)
        return receipt
    monkeypatch.setattr(runtime, 'run_prepared_workspace_step', run)
    monkeypatch.setattr(runtime, '_docker', lambda *a, **k: pytest.fail('No Docker in synthetic lifecycle'))
    assert state.store.run_project_stage(state.claim, state.cancel) == {}
    assert state.store.finish(state.claim, {})
    claims = [state.store.claim_next(recoverable=True), state.store.claim_next(recoverable=True)]
    review = next(item for item in claims if item['type'] == 'request.test_review')
    second = next(item for item in claims if item['type'] == 'request.project_test')
    assert state.store.run_project_stage(second, state.cancel) == {}
    result = review_result(state, review)
    result['approved'] = False
    def check_scope(token):
        project = json.loads(review['payload'])['projectId']
        assert token.projects == (project,)
        assert set(token.static_projects) == {'notes', 'other'}
        assert set(token.static_records) == {('grant', 'notes'), ('grant', 'other'), ('candidate', project)}
        assert token.static_bytes == sum(len(row[1].encode()) for row in token.static_records.values())
    after_preflight(monkeypatch, check_scope)
    assert state.store.finish(review, result)
    assert scalar(state, 'SELECT round FROM objective_control WHERE objective_id=?', (state.objective,)) == 1
    assert scalar(state, 'SELECT count(*) FROM prepared_validation_pair_reviews WHERE approved=0') == 1
    assert scalar(state, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(state, 'SELECT sum(units) FROM prepared_validation_steps') == 4


def test_connection_context_is_exact_readonly_and_sees_uncommitted_changes(running, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import finish_host
    from eidolon_cli import organization_selective_repair as repair
    import time
    _, review = finish_host(running, monkeypatch)
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    context = running.store.context(review, mark_read=False)
    monkeypatch.setattr(running.store, '_read_internal_replies', lambda *a: pytest.fail('Read-only context acknowledged replies'))
    monkeypatch.setattr(repair, 'capture_inputs', lambda *a: pytest.fail('Read-only context captured inputs'))
    with running.store._write() as conn:
        before = conn.total_changes
        assert running.store._context_with_connection(conn, review) == context
        assert conn.total_changes == before
        conn.execute('UPDATE objectives SET title=? WHERE id=?', ('Uncommitted title', running.objective))
        before = conn.total_changes
        assert running.store._context_with_connection(conn, review)['objective']['title'] == 'Uncommitted title'
        assert conn.total_changes == before
        assert running.store.context(review, mark_read=False)['objective']['title'] == context['objective']['title']
        conn.rollback()
    assert running.store.context(review, mark_read=False) == context


def test_public_context_keeps_reply_and_input_capture_effects(running, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import finish_host
    from eidolon_cli import organization_selective_repair as repair
    _, review = finish_host(running, monkeypatch)
    effects = []
    read, capture = running.store._read_internal_replies, repair.capture_inputs
    def read_replies(conn, request):
        effects.append('read')
        return read(conn, request)
    def capture_inputs(store, conn, request, context):
        effects.append('capture')
        return capture(store, conn, request, context)
    monkeypatch.setattr(running.store, '_read_internal_replies', read_replies)
    monkeypatch.setattr(repair, 'capture_inputs', capture_inputs)
    running.store.context(review, mark_read=False)
    assert effects == []
    running.store.context(review)
    assert effects == ['read', 'capture']


def test_finish_context_receipt_uses_exact_caller_transaction(running, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import finish_host, review_result
    _, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    original = running.store._context_with_connection
    connections = []
    def context(conn, claim):
        assert conn is finish._ACTIVE.get().connection
        assert conn.in_transaction
        connections.append(conn)
        return original(conn, claim)
    monkeypatch.setattr(running.store, '_context_with_connection', context)
    monkeypatch.setattr(running.store, 'context', lambda *a, **k: pytest.fail('Finish must not open a second context connection'))
    assert running.store.finish(review, result)
    assert len(connections) == 1


def test_static_records_reject_a_separate_profile_and_retry_revalidates(running, monkeypatch, tmp_path):
    from eidolon_cli.organization_store import OrganizationStore
    ready(running, monkeypatch)
    other = OrganizationStore(tmp_path / 'foreign-profile' / 'state.db')
    first = None
    for _ in range(2):
        with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
            assert token is not first
            assert token.static_bytes > 0
            identity, raw, sha256 = token.static_records[('grant', 'notes')]
            with running.store._write() as conn:
                request = running.store._owned(conn, running.claim)
                with finish.finish_verification_scope(token, running.store, conn, request):
                    with other._connect() as foreign, pytest.raises(ValueError, match='foreign'):
                        finish.observed_finish_static_record(foreign, identity, raw, sha256)
                    assert finish.observed_finish_static_record(conn, identity, raw, sha256) == json.loads(raw)
                conn.rollback()
        assert not token.alive and token.static_bytes == 0 and token.static_records == {}
        first = token


def test_context_completion_rejects_evidence_changed_inside_its_transaction(running, monkeypatch):
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import finish_host, review_result
    from eidolon_cli.organization_executor import OrganizationExecutionError
    import hashlib
    _, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    with running.store._write() as conn:
        request = running.store._owned(conn, review)
        row = conn.execute('SELECT id,content FROM evidence WHERE objective_id=? LIMIT 1', (running.objective,)).fetchone()
        changed = row['content'] + '\nUnreviewed in-transaction addition.'
        conn.execute('UPDATE evidence SET content=?,sha256=? WHERE id=?',
                     (changed, hashlib.sha256(changed.encode()).hexdigest(), row['id']))
        with pytest.raises((ValueError, OrganizationExecutionError)):
            running.store._verify_context_completion(conn, request, result)
        conn.rollback()
    assert running.store.finish(review, result)


@pytest.mark.parametrize('size,blob', [(65536, False), (65537, False), (65536, True), (65537, True)])
def test_static_preload_bounds_legacy_binding_before_python_materialization(running, size, blob):
    import sqlite3
    with running.store._write() as conn:
        original = conn.execute('SELECT binding FROM objective_projects WHERE objective_id=? AND project_id=?',
                                (running.objective, 'notes')).fetchone()[0]
        value = original + ' ' * (size - len(original.encode()))
        encoded = value.encode()
        drop_guards(conn, 'objective_projects')
        conn.execute('UPDATE objective_projects SET binding=? WHERE objective_id=? AND project_id=?',
                     (encoded if blob else value, running.objective, 'notes'))
        if size == 65536 and not blob:
            # Exact legacy byte limit remains inclusive; padding changes no JSON identity.
            assert ledger._binding(conn, running.objective, 'notes')[0]['projectId'] == 'notes'
            return
        observed = []
        def text_factory(raw):
            assert raw != encoded, 'The rejected legacy TEXT reached Python decoding'
            return raw.decode()
        def row_factory(cursor, row):
            if len(cursor.description) == 1 and cursor.description[0][0] == 'binding':
                assert row[0] is None, 'The rejected legacy TEXT/BLOB reached Python materialization'
                observed.append(True)
            return sqlite3.Row(cursor, row)
        conn.text_factory, conn.row_factory = text_factory, row_factory
        with pytest.raises(ValueError, match='legacy project binding changed'):
            ledger._binding(conn, running.objective, 'notes')
        assert observed == [True]

"""Real source reads and SQLite review/application lineage, without execution."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import threading
import time

import pytest

from eidolon_cli import organization_project_effective_snapshot as effective
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotError, ProjectSnapshotLimits
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_edits import _propose, _read, _setup, PATH, SOURCE
from tests.eidolon_cli.test_organization_file_creation import _create
from tests.eidolon_cli.test_organization_project_workflow import apply_validate, project_proposal


pytestmark = pytest.mark.linux_only


def capture(store, objective, source, paths=('example.txt',), **kwargs):
    root = source.parent
    info = root.stat()
    options = dict(root_alias='root0', expected_root_identity=(info.st_dev, info.st_ino),
                   check_authorized=lambda: True, deadline_monotonic=time.monotonic() + 60,
                   limits=ProjectSnapshotLimits(100, 1024 * 1024, 2 * 1024 * 1024))
    options.update(kwargs)
    with store._connect() as conn:
        result = effective.capture_effective_project_snapshot(conn, objective['id'], str(root), paths, **options)
        assert conn.total_changes == 0
        return result


def applied(tmp_path):
    store, source, objective = _setup(tmp_path)
    _propose(store)
    apply_validate(store)
    return store, source, objective


def test_exact_original_coverage_and_full_reviewed_provenance_are_retained(tmp_path):
    store, source, objective = _setup(tmp_path)
    binary = b'\x89PNG\x00\xff\x80ordinary asset'
    (source.parent / 'icon.png').write_bytes(binary)
    source.chmod(0o755)
    evidence = project_proposal(store, source)
    apply_validate(store)
    paths = ['example.txt', 'icon.png', 'config.json']
    result = capture(store, objective, source, paths)
    reopened = OrganizationStore(store.path, store.settings)
    assert result == capture(reopened, objective, source, list(reversed(paths)))
    before = {item.path: item for item in result.original.files}
    after = {item.path: item for item in result.effective.files}
    assert set(before) == set(after) == set(paths)
    assert before['example.txt'].content == source.read_bytes() == SOURCE.encode('utf-8')
    assert after['example.txt'].content == SOURCE.replace('old value', 'new value').encode('utf-8')
    assert before['example.txt'].mode == after['example.txt'].mode == 0o555
    assert before['icon.png'] == after['icon.png'] and after['icon.png'].content == binary
    assert before['config.json'].content != after['config.json'].content
    assert result.snapshot_sha256 != result.original.snapshot_sha256
    assert result.preimage_sha256 == digest(result.preimage_manifest())
    assert result.managed_revisions_sha256 == digest(result.managed_manifest())
    assert {row.path for row in result.managed_revisions} == {'example.txt', 'config.json'}
    proof = store.evidence(evidence)['editProposal']
    for row in result.managed_revisions:
        assert row.proposal_id == proof['id'] and row.proposal_sha256 == proof['proposalSha256']
        assert row.evidence_id == evidence and row.revision == 1 and row.base_revision == 0
        assert row.base_sha256 == before[row.path].sha256 and row.sha256 == after[row.path].sha256
        assert row.review_request_id and row.reviewer_id and row.application_request_id and row.source_receipt_id
    with store._write() as conn:
        assert effective.verify_effective_snapshot_lineage(conn, result, str(source.parent), check_authorized=lambda: True)
    metadata = result.source_evidence()
    assert 'content' not in json.dumps(metadata)
    assert SOURCE not in repr(result) and 'new value' not in repr(result)
    metadata['original']['files'][0]['sha256'] = 'forged'
    assert result.source_evidence()['original']['files'][0]['sha256'] != 'forged'
    assert result.execution_authorized is False and result.effective.execution_authorized is False
    with pytest.raises(FrozenInstanceError):
        result.managed_revisions[0].revision = 42


@pytest.mark.parametrize('mutation', [
    'omit_applied', 'preimage', 'root_path', 'root_inode', 'self_review', 'rejected_review',
    'review_evidence', 'review_status', 'applied_sha', 'source_receipt', 'head', 'rogue_head',
    'declared_check', 'application_status', 'application_payload', 'application_foreign_proposal',
])
def test_unselected_stale_or_unverified_managed_revisions_cannot_be_captured(tmp_path, monkeypatch, mutation):
    store, source, objective = _setup(tmp_path)
    if mutation == 'declared_check':
        project_proposal(store, source, second=False,
                         checks=[{'kind': 'text_contains', 'path': PATH, 'text': 'never present'}])
    else:
        _propose(store)
    apply_validate(store)
    kwargs, paths = {}, ['example.txt']
    if mutation == 'omit_applied':
        (source.parent / 'other.txt').write_bytes(b'ordinary source')
        paths = ['other.txt']
    elif mutation == 'preimage':
        source.write_text('changed source', encoding='utf-8')
    elif mutation == 'root_inode':
        info = source.parent.stat()
        kwargs['expected_root_identity'] = (info.st_dev, info.st_ino + 1)
    else:
        with store._write() as conn:
            if mutation == 'root_path':
                conn.execute('DROP TRIGGER immutable_workspace_roots_update')
                conn.execute("UPDATE workspace_roots SET root_identity='changed'")
            elif mutation in {'self_review', 'rejected_review'}:
                conn.execute('DROP TRIGGER immutable_edit_reviews_update')
                if mutation == 'self_review':
                    author = conn.execute('SELECT author_id FROM edit_proposals').fetchone()[0]
                    conn.execute('UPDATE edit_reviews SET reviewer_id=?', (author,))
                    conn.execute('UPDATE reviews SET agent_id=?', (author,))
                else:
                    conn.execute('UPDATE edit_reviews SET approved=0')
                    conn.execute('UPDATE reviews SET approved=0')
            elif mutation == 'review_evidence':
                conn.execute("UPDATE reviews SET evidence_ids='[]'")
            elif mutation == 'review_status':
                conn.execute("UPDATE requests SET status='failed' WHERE type='request.review'")
            elif mutation == 'application_status':
                conn.execute("UPDATE requests SET status='failed' WHERE type='request.apply'")
            elif mutation == 'application_payload':
                conn.execute("UPDATE requests SET payload='{}' WHERE type='request.apply'")
            elif mutation == 'application_foreign_proposal':
                row = conn.execute("SELECT id,payload FROM requests WHERE type='request.apply'").fetchone()
                payload = {**json.loads(row['payload']), 'proposalId': 'different-proposal'}
                conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
            elif mutation == 'applied_sha':
                conn.execute('DROP TRIGGER immutable_edit_applications_update')
                conn.execute("UPDATE edit_applications SET applied_sha256=?", ('0' * 64,))
            elif mutation == 'source_receipt':
                conn.execute("UPDATE tool_receipts SET result_sha256=?", ('0' * 64,))
            elif mutation == 'head':
                conn.execute('UPDATE workspace_heads SET revision=0')
            elif mutation == 'rogue_head':
                conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                             (objective['id'], 'root0/orphan.txt', 1, objective['id'], 'rogue',
                              hashlib.sha256(b'rogue').hexdigest(), time.time()))
                conn.execute('INSERT INTO workspace_heads VALUES (?,?,1)', (objective['id'], 'root0/orphan.txt'))
    if mutation == 'application_foreign_proposal':
        monkeypatch.setattr(effective.OrganizationEditStore, '_request_proposal',
                            lambda *_: pytest.fail('A misbound application cannot materialize a different proposal'))
    with pytest.raises(ValueError):
        capture(store, objective, source, paths, **kwargs)


def test_unapplied_proposals_do_not_replace_source_and_creations_fail_explicitly(tmp_path):
    store, source, objective = _setup(tmp_path)
    _propose(store)
    result = capture(store, objective, source)
    assert result.original == result.effective and result.managed_revisions == ()
    other = tmp_path / 'creation'
    other.mkdir()
    store, source, objective = _setup(other)
    claim = store.claim_next()
    read, _ = _read(store, claim, path='root0/new.txt')
    assert read['sourceExists'] is False
    store.finish(claim, {'summary': 'Create exact new source',
                         'edits': [_create('root0/new.txt', read)], 'validations': []})
    apply_validate(store)
    with pytest.raises(ProjectSnapshotError, match='creation is unsupported'):
        capture(store, objective, source, ['example.txt', 'new.txt'])
    assert not (source.parent / 'new.txt').exists()


def test_execution_only_policy_matches_effective_bytes_and_never_leaks_bodies(tmp_path):
    store, source, objective = applied(tmp_path)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    final_bytes = SOURCE.replace('old value', 'new value').encode('utf-8')
    final_hash = hashlib.sha256(final_bytes).hexdigest()
    with pytest.raises(ProjectSnapshotError, match='effective source bytes'):
        capture(store, objective, source, reviewed_execution_only_hashes={'example.txt': original_hash})
    retained = capture(store, objective, source, reviewed_execution_only_hashes={'example.txt': final_hash})
    assert retained.effective.files[0].execution_only is True
    assert retained.effective.files[0].content == final_bytes
    assert 'old value' not in json.dumps(retained.source_evidence())
    assert 'new value' not in json.dumps(retained.source_evidence())
    fixture = b'const fixture = "ghp_' + b'a' * 36 + b'";'
    (source.parent / 'fixture.js').write_bytes(fixture)
    with pytest.raises(ProjectSnapshotError, match='secret-bearing'):
        capture(store, objective, source, ['example.txt', 'fixture.js'])
    result = capture(store, objective, source, ['example.txt', 'fixture.js'],
                     reviewed_execution_only_hashes={'fixture.js': hashlib.sha256(fixture).hexdigest()})
    assert result.original.files[1].execution_only and result.effective.files[1].execution_only
    assert fixture.decode() not in json.dumps(result.source_evidence())
    (source.parent / 'fixture.js').write_bytes(fixture + b'changed')
    with pytest.raises(ProjectSnapshotError, match='hash changed'):
        capture(store, objective, source, ['example.txt', 'fixture.js'],
                reviewed_execution_only_hashes={'fixture.js': hashlib.sha256(fixture).hexdigest()})
    (source.parent / '.env').write_bytes(fixture)
    with pytest.raises(ValueError):
        capture(store, objective, source, ['example.txt', '.env'],
                reviewed_execution_only_hashes={'.env': hashlib.sha256(fixture).hexdigest()})


@pytest.mark.parametrize('scenario', ['revoked', 'cancelled', 'deadline', 'limits', 'transaction'])
def test_capture_cannot_outlive_its_authority_or_bounds(tmp_path, scenario):
    store, source, objective = applied(tmp_path)
    options = {}
    if scenario == 'revoked':
        options['check_authorized'] = lambda: False
    elif scenario == 'cancelled':
        event = threading.Event()
        event.set()
        options['cancel'] = event
    elif scenario == 'deadline':
        options['deadline_monotonic'] = time.monotonic() - 1
    elif scenario == 'limits':
        options['limits'] = ProjectSnapshotLimits(1, 1, 1)
    else:
        with store._write() as conn, pytest.raises(ProjectSnapshotError, match='autocommit'):
            info = source.parent.stat()
            effective.capture_effective_project_snapshot(conn, objective['id'], str(source.parent), ['example.txt'],
                root_alias='root0', expected_root_identity=(info.st_dev, info.st_ino),
                check_authorized=lambda: True, limits=ProjectSnapshotLimits(10, 1000, 2000),
                deadline_monotonic=time.monotonic() + 60)
        return
    with pytest.raises(ProjectSnapshotError):
        capture(store, objective, source, **options)


def test_changed_review_during_capture_and_later_lineage_recheck_fail(tmp_path, monkeypatch):
    store, source, objective = applied(tmp_path)
    retained = capture(store, objective, source)
    original_capture = effective.capture_project_snapshot
    def changed(*args, **kwargs):
        result = original_capture(*args, **kwargs)
        with store._write() as conn:
            conn.execute("UPDATE reviews SET evidence_ids='[]'")
        return result
    monkeypatch.setattr(effective, 'capture_project_snapshot', changed)
    with pytest.raises(ValueError, match='review'):
        capture(store, objective, source)
    with store._write() as conn, pytest.raises(ValueError, match='review'):
        effective.verify_effective_snapshot_lineage(conn, retained, str(source.parent), check_authorized=lambda: True)


@pytest.mark.parametrize('scenario', ['sequential_revisions', 'separate_projects'])
def test_provenance_retains_all_applied_revisions_with_root_scoped_coverage(tmp_path, scenario):
    store, source, objective = _setup(tmp_path, tasks=2)
    if scenario == 'separate_projects':
        other = tmp_path / 'other'
        other.mkdir()
        (other / 'second.txt').write_text('old value', encoding='utf-8')
        store = OrganizationStore(store.path, replace(store.settings, read_roots=(*store.settings.read_roots, str(other))))
    _propose(store)
    apply_validate(store)
    initial = capture(store, objective, source)
    if scenario == 'sequential_revisions':
        _propose(store, old='new value', new='latest value')
    else:
        claim = store.claim_next()
        read, _ = _read(store, claim, path='root1/second.txt')
        store.finish(claim, {'summary': 'Edit a separate project', 'edit': {
            'path': 'root1/second.txt', 'baseRevision': read['workspaceRevision'],
            'baseSha256': read['sourceSha256'], 'oldText': 'old value', 'newText': 'other value'}})
    apply_validate(store)
    current = capture(store, objective, source)
    if scenario == 'sequential_revisions':
        assert current.original == initial.original
        assert [row.revision for row in current.managed_revisions] == [1, 2]
        assert current.managed_revisions[1].base_sha256 == current.managed_revisions[0].sha256
        assert current.effective.files[0].content == SOURCE.replace('old value', 'latest value').encode('utf-8')
        assert current.preimage_sha256 == initial.preimage_sha256
        assert current.managed_revisions_sha256 != initial.managed_revisions_sha256
        with store._write() as conn, pytest.raises(ProjectSnapshotError, match='provenance'):
            effective.verify_effective_snapshot_lineage(conn, initial, str(source.parent), check_authorized=lambda: True)
    else:
        assert initial == current
        second = capture(store, objective, other / 'second.txt', ['second.txt'], root_alias='root1')
        assert [row.path for row in second.managed_revisions] == ['second.txt']
        assert second.effective.files[0].content == b'other value'
        assert second.original.files[0].content == b'old value'


@pytest.mark.parametrize('table,column,trigger', [
    ('edit_proposals', 'new_content', 'immutable_edit_proposals_update'),
    ('workspace_revisions', 'content', 'immutable_workspace_revisions_update'),
    ('evidence', 'content', None),
    ('edit_project_specs', 'files', 'immutable_edit_project_specs_update'),
    ('project_validation_receipts', 'result', 'immutable_project_validation_receipts_update'),
    ('reviews', 'summary', None),
    ('requests', 'payload', None),
    ('tool_receipts', 'result', None),
])
def test_historical_sql_byte_bounds_precede_any_body_materialization(tmp_path, monkeypatch, table, column, trigger):
    store, source, objective = _setup(tmp_path)
    project_proposal(store, source)
    apply_validate(store)
    # A small policy ceiling avoids allocating a production-sized hostile blob,
    # while the actual SQLite body is retained and measured in SQL, not mocked.
    monkeypatch.setattr(effective, '_MAX_MANAGED_HISTORY_BYTES', 128 * 1024)
    with store._write() as conn:
        if trigger:
            conn.execute('DROP TRIGGER ' + trigger)
        conn.execute(f'UPDATE {table} SET {column}=CAST(zeroblob(?) AS TEXT)', (256 * 1024,))
    monkeypatch.setattr(effective, 'final_source_manifest',
                        lambda *_: pytest.fail('Oversized history must fail before fetching source/evidence bodies'))
    with pytest.raises(ProjectSnapshotError, match='retained row or byte bound'):
        capture(store, objective, source, ['example.txt', 'config.json'])


def test_sql_history_row_bound_precedes_existing_lineage_verifier(tmp_path, monkeypatch):
    store, source, objective = applied(tmp_path)
    monkeypatch.setattr(effective, '_MAX_MANAGED_HISTORY_ROWS', 1)
    monkeypatch.setattr(effective, 'final_source_manifest',
                        lambda *_: pytest.fail('Over-budget rows must fail before materialization'))
    with pytest.raises(ProjectSnapshotError, match='retained row or byte bound'):
        capture(store, objective, source)
    with store._connect() as conn:
        assert not conn.in_transaction


def test_bounded_lineage_read_closes_its_transaction_and_preserves_caller_work(tmp_path):
    store, source, objective = applied(tmp_path)
    retained = capture(store, objective, source)
    with store._connect() as conn:
        with pytest.raises(ProjectSnapshotError, match='captured alias'):
            effective.verify_effective_snapshot_lineage(conn, retained, str(source.parent) + '-other',
                                                        check_authorized=lambda: True)
        assert not conn.in_transaction
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT INTO events(objective_id,text,kind,created) VALUES (?,?,?,?)',
                     (objective['id'], 'uncommitted caller work', 'test', time.time()))
        with pytest.raises(ProjectSnapshotError, match='captured alias'):
            effective.verify_effective_snapshot_lineage(conn, retained, str(source.parent) + '-other',
                                                        check_authorized=lambda: True)
        assert conn.in_transaction
        assert conn.execute("SELECT count(*) FROM events WHERE text='uncommitted caller work'").fetchone()[0] == 1
        with store._connect() as other:
            assert other.execute("SELECT count(*) FROM events WHERE text='uncommitted caller work'").fetchone()[0] == 0
        conn.rollback()


def test_misbound_referenced_evidence_is_budgeted_before_verification(tmp_path, monkeypatch):
    store, source, objective = applied(tmp_path)
    monkeypatch.setattr(effective, '_MAX_MANAGED_HISTORY_BYTES', 128 * 1024)
    with store._write() as conn:
        conn.execute("UPDATE evidence SET objective_id='different-objective', content=CAST(zeroblob(?) AS TEXT)",
                     (256 * 1024,))
    monkeypatch.setattr(effective, 'final_source_manifest',
                        lambda *_: pytest.fail('Referenced foreign evidence must be bounded before allocation'))
    with pytest.raises(ProjectSnapshotError, match='retained row or byte bound'):
        capture(store, objective, source)

"""Adversarial exact-grant and evidence boundaries with real SQLite/filesystem I/O."""
from dataclasses import replace
import hashlib
import json
import os
import sqlite3
import threading
import time

import pytest

from eidolon_cli.organization_project_config import parse_project_grants
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_edits import _setup, _propose, _review, PATH


def _project(tmp_path):
    store, source, objective = _setup(tmp_path)
    grants = parse_project_grants([{'id': 'sample', 'files': [PATH], 'execution': {}}], 1)
    staff = tuple(replace(member, tool_grants=tuple(dict.fromkeys((*member.tool_grants, 'run_tests'))))
                  for member in store.settings.roster)
    store = OrganizationStore(store.path, replace(store.settings, roster=staff,
        tool_grants=(*store.settings.tool_grants, 'run_tests'), project_grants=grants))
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks=? WHERE objective_id=?",
                     (json.dumps(['project_tests']), objective['id']))
    return store, source, objective, grants[0]


def _reviewed_project(tmp_path):
    store, source, objective, grant = _project(tmp_path)
    _propose(store)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    return store, source, objective, grant


@pytest.mark.parametrize('value', [
    {'id': 'sample', 'files': ['root0/test_a.py'], 'execution': {'command': 'echo injected'}},
    {'id': 'sample', 'files': ['root0/test_a.py'], 'execution': {'recipe': 'shell'}},
    {'id': 'sample', 'files': ['root0/test_a.py'], 'execution': {'timeout_seconds': True}},
    {'id': 'sample', 'files': ['root1/test_a.py'], 'execution': {}},
    {'id': 'sample', 'files': ['root0/../test_a.py'], 'execution': {}},
    {'id': 'sample', 'files': ['root0/.env'], 'execution': {}},
    {'id': 'sample', 'files': ['root0/test_a.py', 'root0/test_a.py'], 'execution': {}},
    {'id': 'sample', 'files': ['root0/test_a.py'], 'execution': {}, 'shell': True},
])
def test_project_recipes_cannot_expand_exact_owner_authority(value):
    with pytest.raises(ValueError):
        parse_project_grants([value], 1)


@pytest.mark.linux_only
@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'fifo', 'credential'])
def test_project_snapshot_refuses_unsafe_real_source_bytes(tmp_path, attack):
    store, source, objective, grant = _project(tmp_path)
    source.unlink()
    if attack == 'symlink':
        target = tmp_path / 'outside.txt'
        target.write_text('outside private data')
        source.symlink_to(target)
    elif attack == 'hardlink':
        target = tmp_path / 'outside.txt'
        target.write_text('outside private data')
        os.link(target, source)
    elif attack == 'fifo':
        os.mkfifo(source)
    else:
        source.write_text('AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n')
    with store._connect() as conn:
        with pytest.raises(ValueError):
            store._project_snapshot(conn, objective['id'], grant)
    assert not list(tmp_path.glob('eidolon-project-*'))


@pytest.mark.linux_only
def test_project_snapshot_requires_reviewed_overlay_and_unchanged_original_preimage(tmp_path):
    store, source, objective, grant = _reviewed_project(tmp_path)
    original = source.read_bytes()
    with store._connect() as conn:
        snapshot = store._project_snapshot(conn, objective['id'], grant)
    assert snapshot[0]['content'] == original.decode().replace('old value', 'new value')
    assert snapshot[0]['revision'] == 1
    assert snapshot[0]['base_sha256'] == hashlib.sha256(original).hexdigest()
    assert snapshot[0]['sha256'] == hashlib.sha256(snapshot[0]['content'].encode()).hexdigest()
    source.write_text('source changed independently')
    with store._connect() as conn:
        with pytest.raises(ValueError, match='preimage changed'):
            store._project_snapshot(conn, objective['id'], grant)


@pytest.mark.linux_only
@pytest.mark.parametrize('scope', ['organization', 'author'])
def test_project_execution_rechecks_current_explicit_run_grants(tmp_path, scope):
    store, _, objective, _ = _reviewed_project(tmp_path)
    settings = store.settings
    if scope == 'organization':
        settings = replace(settings, tool_grants=tuple(g for g in settings.tool_grants if g != 'run_tests'))
    else:
        with store._connect() as conn:
            author = conn.execute('SELECT author_id FROM tasks WHERE objective_id=?', (objective['id'],)).fetchone()[0]
        settings = replace(settings, roster=tuple(replace(member,
            tool_grants=tuple(g for g in member.tool_grants if g != 'run_tests')) if member.id == author else member
            for member in settings.roster))
    revoked = OrganizationStore(store.path, settings)
    with revoked._connect() as conn:
        with pytest.raises(ValueError, match='explicit'):
            revoked._project_grant(conn, objective['id'])


def _simulated_terminal_result(files):
    # Ledger tests deliberately inject a terminal backend result. Native runner
    # isolation is exercised separately; this helper makes no execution claim.
    from eidolon_cli.organization_project_runner import snapshot_digest
    return {'runner': 'eidolon.isolated-python-unittest', 'runnerVersion': 1,
            'status': 'passed', 'exitCode': 0, 'testCount': 1,
            'snapshotSha256': snapshot_digest(files),
            'runtime': {'stdlibOnly': True, 'executableSha256': 'a' * 64, 'runtimeSha256': 'b' * 64},
            'isolation': {'established': True, 'backend': 'linux-bubblewrap-seccomp',
                          'sourceReadOnly': True, 'runtimeReadOnly': True, 'network': 'none', 'processLimit': 1,
                          'seccompInstalled': True, 'namespaceCreationDenied': True}}


@pytest.mark.linux_only
def test_model_supplied_project_success_cannot_create_backend_evidence(tmp_path):
    store, _, objective, _ = _reviewed_project(tmp_path)
    claim = store.claim_next()
    assert claim['type'] == 'request.project_test'
    with pytest.raises(ValueError):
        store.finish(claim, {'status': 'passed', 'exitCode': 0, 'testCount': 1})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 0
        with pytest.raises(ValueError, match='not been executed'):
            store.verify_project_tests(conn, objective['id'])


@pytest.mark.linux_only
@pytest.mark.parametrize('stop', ['cancel', 'expire', 'revoke'])
def test_retained_backend_outcome_cannot_bypass_lost_execution_authority(tmp_path, monkeypatch, stop):
    from eidolon_cli import organization_project_runner as runner
    store, _, objective, _ = _reviewed_project(tmp_path)
    claim = store.claim_next()
    assert claim['type'] == 'request.project_test'
    def stopped_after_dispatch(files, grant, cancel):
        # The start must be visible from another connection before dispatch.
        with store._connect() as conn:
            assert conn.execute('SELECT count(*) FROM project_run_starts WHERE request_id=?', (claim['id'],)).fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 0
        if stop == 'cancel':
            store.cancel(objective['id'])
        elif stop == 'expire':
            with store._write() as conn:
                conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
        else:
            OrganizationStore(store.path, replace(store.settings,
                tool_grants=tuple(g for g in store.settings.tool_grants if g != 'run_tests')))
        return _simulated_terminal_result(files)
    monkeypatch.setattr(runner, 'run_project_tests', stopped_after_dispatch)
    store.run_project_stage(claim, threading.Event())
    assert not store.finish(claim, {})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM project_run_reviews').fetchone()[0] == 0
        for table in ('project_run_starts', 'project_run_results'):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                conn.execute(f'DELETE FROM {table}')
        assert conn.execute("SELECT count(*) FROM requests WHERE type='request.source_integrate'").fetchone()[0] == 0


@pytest.mark.linux_only
def test_unknown_execution_start_survives_restart_and_cannot_replay(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, _, _, _ = _reviewed_project(tmp_path)
    claim = store.claim_next()
    dispatched = []
    def interrupted(files, grant, cancel):
        dispatched.append(files)
        raise RuntimeError('Simulated process termination after durable admission')
    monkeypatch.setattr(runner, 'run_project_tests', interrupted)
    with pytest.raises(RuntimeError):
        store.run_project_stage(claim, threading.Event())
    reopened = OrganizationStore(store.path)
    with reopened._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
    assert reopened.recover_expired() == 1
    assert reopened.retry(claim['id'])
    assert reopened.claim_next() is None
    with reopened._connect() as conn:
        request = conn.execute('SELECT * FROM requests WHERE id=?', (claim['id'],)).fetchone()
        assert request['status'] == 'pending_intervention'
        assert 'unknown outcome' in request['reason']
        assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 0
    assert len(dispatched) == 1


@pytest.mark.linux_only
def test_review_must_bind_exact_current_snapshot_then_reject_source_drift(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, source, objective, _ = _reviewed_project(tmp_path)
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    claim = store.claim_next()
    store.run_project_stage(claim, threading.Event())
    assert store.finish(claim, {})
    review = store.claim_next()
    assert review['type'] == 'request.test_review'
    payload = json.loads(review['payload'])
    with pytest.raises(ValueError, match='exact evidence'):
        store.finish(review, {'approved': True, 'summary': 'Incorrect receipt', 'evidenceIds': ['fabricated']})
    assert store.finish(review, {'approved': True, 'summary': 'Independent exact snapshot review',
                                 'evidenceIds': payload['evidenceIds']})
    with store._connect() as conn:
        assert store.verify_project_tests(conn, objective['id'])[0]['id'] == payload['runId']
    source.write_text('Changed after independent review')
    with store._connect() as conn:
        with pytest.raises(ValueError, match='preimage changed'):
            store.verify_project_tests(conn, objective['id'])


@pytest.mark.linux_only
def test_same_file_prior_author_remains_in_current_revision_authority(tmp_path):
    store, _, objective, _ = _reviewed_project(tmp_path)
    with store._write() as conn:
        old_author = conn.execute('SELECT author_id FROM tasks WHERE objective_id=?', (objective['id'],)).fetchone()[0]
        store._replan(conn, objective['id'], 'Refine current reviewed content with a different author')
    store = OrganizationStore(store.path, replace(store.settings, roster=tuple(
        replace(member, enabled=False) if member.id == old_author else member for member in store.settings.roster)))
    plan = store.claim_next()
    assert plan['type'] == 'request.plan'
    assert store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Refine the reviewed source',
        'description': 'Refine a different line while retaining the previous reviewed change', 'type': 'work.edit', 'team': 'engineering'}]})
    claim = store.claim_next()
    if claim['type'] == 'request.hire':
        assert store.finish(claim, {})
        claim = store.claim_next()
    assert claim['type'] == 'work.edit' and claim['agent_id'] != old_author
    _propose(store, claim=claim, old='café', new='cafeteria')
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    # Revocation of a contributing revision author cannot be hidden by a later
    # author's independent proposal of a replacement on the same source path.
    with store._connect() as conn:
        with pytest.raises(ValueError, match='explicit current'):
            store._project_grant(conn, objective['id'])


@pytest.mark.linux_only
@pytest.mark.parametrize('field,value', [
    ('runner', 'model-produced'), ('runnerVersion', 0),
    ('isolation.established', False), ('isolation.backend', 'host'),
    ('isolation.sourceReadOnly', False), ('isolation.runtimeReadOnly', False),
    ('isolation.network', 'host'), ('isolation.processLimit', 100),
    ('isolation.seccompInstalled', False), ('isolation.namespaceCreationDenied', False),
    ('runtime.stdlibOnly', False), ('runtime.executableSha256', 'unverified'),
    ('runtime.runtimeSha256', 'unverified'), ('testCount', 0), ('exitCode', 1),
    ('snapshotSha256', '0' * 64),
])
def test_test_gate_rejects_nonisolated_or_unbound_backend_results(tmp_path, monkeypatch, field, value):
    from eidolon_cli import organization_project_runner as runner
    store, _, _, _ = _reviewed_project(tmp_path)
    def untrusted_result(files, grant, cancel):
        result = _simulated_terminal_result(files)
        parts = field.split('.')
        destination = result if len(parts) == 1 else result[parts[0]]
        destination[parts[-1]] = value
        return result
    monkeypatch.setattr(runner, 'run_project_tests', untrusted_result)
    claim = store.claim_next()
    store.run_project_stage(claim, threading.Event())
    with pytest.raises(ValueError):
        store.finish(claim, {})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_reviews').fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM requests WHERE type='request.test_review'").fetchone()[0] == 0


def _source_setup(tmp_path):
    from tests.eidolon_cli.test_organization_source_integration import git
    store, source, objective, _ = _project(tmp_path)
    git(source.parent, 'init', '-b', 'main')
    git(source.parent, 'add', '.')
    git(source.parent, 'commit', '-m', 'Original reviewed fixture')
    settings = replace(store.settings, tool_grants=(*store.settings.tool_grants, 'integrate_source'),
                       roster=tuple(replace(member, tool_grants=(*member.tool_grants, 'integrate_source'))
                                    for member in store.settings.roster))
    store = OrganizationStore(store.path, settings)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='source_project' WHERE objective_id=?", (objective['id'],))
    _propose(store)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    return store, source, objective


def _source_ready(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, source, objective = _source_setup(tmp_path)
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    claim = store.claim_next()
    store.run_project_stage(claim, threading.Event())
    assert store.finish(claim, {})
    review = store.claim_next()
    assert review['type'] == 'request.test_review'
    assert store.finish(review, {'approved': True, 'summary': 'Exact snapshot independently reviewed',
                                 'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    claim = store.claim_next()
    assert claim['type'] == 'request.source_integrate'
    return store, source, objective, claim


@pytest.mark.linux_only
@pytest.mark.parametrize('stop', ['cancel', 'expire'])
def test_source_publication_rechecks_cancellation_and_elapsed_lease(tmp_path, monkeypatch, stop):
    from eidolon_cli import organization_source_integration as integration
    from tests.eidolon_cli.test_organization_source_integration import git
    store, source, _, claim = _source_ready(tmp_path, monkeypatch)
    original = integration.integrate_source
    cancel = threading.Event()
    def interrupted_before_publication(*args, before_publish, **kwargs):
        def guard():
            if stop == 'cancel':
                cancel.set()
                before_publish()
            else:
                now = time.time()
                with monkeypatch.context() as clock:
                    clock.setattr(time, 'time', lambda: now + store.settings.lease_seconds + 1)
                    before_publish()
        return original(*args, before_publish=guard, **kwargs)
    monkeypatch.setattr(integration, 'integrate_source', interrupted_before_publication)
    before = source.read_bytes()
    with pytest.raises(ValueError, match='cancelled|lease'):
        store.run_project_stage(claim, cancel)
    assert source.read_bytes() == before
    assert git(source.parent, 'for-each-ref', '--format=%(refname)', 'refs/heads/eidolon/') == b''
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 0


@pytest.mark.linux_only
def test_source_acceptance_rechecks_current_delivery_branch(tmp_path, monkeypatch):
    from tests.eidolon_cli.test_organization_source_integration import git
    store, source, objective, claim = _source_ready(tmp_path, monkeypatch)
    store.run_project_stage(claim, threading.Event())
    assert store.finish(claim, {})
    with store._connect() as conn:
        assert store.verified_source_integration(conn, objective['id'])
        receipt = json.loads(conn.execute('SELECT record FROM project_source_receipts').fetchone()[0])
    git(source.parent, 'update-ref', '-d', receipt['ref'])
    with store._connect() as conn:
        with pytest.raises(ValueError, match='removed or changed'):
            store.verified_source_integration(conn, objective['id'])


@pytest.mark.linux_only
def test_committed_source_receipt_recovers_after_lost_completion_without_republication(tmp_path, monkeypatch):
    from eidolon_cli import organization_source_integration as integration
    store, _, objective, claim = _source_ready(tmp_path, monkeypatch)
    assert store.run_project_stage(claim, threading.Event()) == {}
    with store._connect() as conn:
        persisted = dict(conn.execute('SELECT * FROM project_source_receipts WHERE request_id=?', (claim['id'],)).fetchone())
        assert conn.execute('SELECT status FROM requests WHERE id=?', (claim['id'],)).fetchone()[0] == 'running'
    # Receipt commit survived, but request completion/acknowledgement did not.
    reopened = OrganizationStore(store.path)
    with reopened._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
    assert reopened.recover_expired() == 1
    assert reopened.retry(claim['id'])
    retried = reopened.claim_next()
    assert retried['id'] == claim['id'] and retried['token'] != claim['token']
    monkeypatch.setattr(integration, 'integrate_source', lambda *args, **kwargs:
                        pytest.fail('A persisted exact branch receipt must recover read-only'))
    assert reopened.run_project_stage(retried, threading.Event()) == {}
    assert reopened.finish(retried, {})
    with reopened._connect() as conn:
        assert dict(conn.execute('SELECT * FROM project_source_receipts').fetchone()) == persisted
        assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 1
        assert reopened.verified_source_integration(conn, objective['id'])
    artifact = reopened.evidence('project_source_' + claim['id'])
    assert artifact['kind'] == 'source_integration'
    assert artifact['sha256'] == persisted['sha256']
    next_stage = reopened.claim_next()
    assert next_stage['type'] == 'request.integrate'
    assert artifact['id'] in json.loads(next_stage['payload'])['evidenceIds']
    assert any(row['id'] == artifact['id'] for row in reopened.context(next_stage)['evidence'])


@pytest.mark.linux_only
@pytest.mark.parametrize('stop', ['cancel', 'lease', 'deadline'])
def test_test_dispatch_rechecks_authority_after_source_base_preparation(tmp_path, monkeypatch, stop):
    from eidolon_cli import organization_project_runner as runner
    from eidolon_cli import organization_source_integration as integration
    store, _, objective = _source_setup(tmp_path)
    if stop == 'deadline':
        store = OrganizationStore(store.path, replace(store.settings, lease_seconds=300))
        with store._write() as conn:
            conn.execute('UPDATE objective_budgets SET deadline=? WHERE objective_id=?',
                         (time.time() + 100, objective['id']))
    claim = store.claim_next()
    assert claim['type'] == 'request.project_test'
    original = integration.prepare_source_integration
    cancel = threading.Event()
    def expires_during_preparation(*args, **kwargs):
        base = original(*args, **kwargs)
        if stop == 'cancel':
            cancel.set()
        else:
            now = time.time()
            seconds = store.settings.lease_seconds if stop == 'lease' else 100
            monkeypatch.setattr(time, 'time', lambda: now + seconds + 1)
        return base
    monkeypatch.setattr(integration, 'prepare_source_integration', expires_during_preparation)
    monkeypatch.setattr(runner, 'run_project_tests', lambda *args, **kwargs:
                        pytest.fail('Expired or cancelled admission must never dispatch the runner'))
    with pytest.raises(ValueError, match='cancelled|lease|deadline'):
        store.run_project_stage(claim, cancel)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 0


@pytest.mark.linux_only
@pytest.mark.parametrize('outcome', ['passed', 'failed'])
def test_original_run_budget_survives_restart_and_only_exact_success_reuses(tmp_path, monkeypatch, outcome):
    from eidolon_cli import organization_project_runner as runner
    store, _, earlier, _ = _project(tmp_path)
    assert store.cancel(earlier['id'])
    store = OrganizationStore(store.path, replace(store.settings, max_project_runs=1))
    objective = store.create_objective('Bounded original project budget', idempotency_key='limited-project',
                                       delivery_mode='managed_artifact', required_checks=['project_tests'])
    plan = store.claim_next()
    assert store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Make one reviewed source improvement',
        'description': 'Replace old value', 'type': 'work.edit', 'team': 'engineering'}]})
    claim = store.claim_next()
    if claim['type'] == 'request.hire':
        assert store.finish(claim, {})
        claim = store.claim_next()
    _propose(store, claim=claim)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    dispatched = []
    def result(files, grant, cancel):
        dispatched.append(files)
        return {**_simulated_terminal_result(files), 'status': outcome,
                'exitCode': 0 if outcome == 'passed' else 1}
    monkeypatch.setattr(runner, 'run_project_tests', result)
    claim = store.claim_next()
    assert store.run_project_stage(claim, threading.Event()) == {}
    # Increase startup configuration after a committed outcome/lost finish.
    reopened = OrganizationStore(store.path, replace(store.settings, max_project_runs=12))
    with reopened._write() as conn:
        assert conn.execute('SELECT max_runs FROM project_execution_budgets WHERE objective_id=?',
                            (objective['id'],)).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            conn.execute('UPDATE project_execution_budgets SET max_runs=12 WHERE objective_id=?', (objective['id'],))
    assert reopened.retry(claim['id'])
    retried = reopened.claim_next()
    if outcome == 'passed':
        assert retried['id'] == claim['id']
        assert reopened.run_project_stage(retried, threading.Event()) == {}
        assert reopened.finish(retried, {})
        assert reopened.claim_next()['type'] == 'request.test_review'
    else:
        assert retried is None
        with reopened._connect() as conn:
            blocked = conn.execute('SELECT status,reason FROM requests WHERE id=?', (claim['id'],)).fetchone()
            assert blocked['status'] == 'pending_intervention' and 'run budget exhausted' in blocked['reason']
    with reopened._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_starts WHERE objective_id=?', (objective['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 1
    assert len(dispatched) == 1


@pytest.mark.linux_only
def test_manual_source_handoff_after_test_review_preserves_exact_test_snapshot(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, source, objective, _ = _project(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='source_project' WHERE objective_id=?", (objective['id'],))
    _, proposal, _, _ = _propose(store)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    assert not any(row['type'] == 'request.merge' for row in store.snapshot()['requests'])
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    claim = store.claim_next()
    assert claim['type'] == 'request.project_test'
    store.run_project_stage(claim, threading.Event())
    assert store.finish(claim, {})
    review = store.claim_next()
    assert review['type'] == 'request.test_review'
    assert store.finish(review, {'approved': True, 'summary': 'Reviewed exact selected tests and code',
                                 'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    handoff = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    assert handoff['status'] == 'pending_intervention'
    with store._connect() as conn:
        before, _ = store.verify_project_tests(conn, objective['id'])
        original_snapshot = before['snapshot_sha256']
    source.write_bytes(proposal['editProposal']['newContent'].encode('utf-8'))
    # Matching managed bytes alone cannot stand in for owner-verified handoff.
    with store._connect() as conn:
        with pytest.raises(ValueError, match='preimage changed'):
            store.verify_project_tests(conn, objective['id'])
    assert store.resolve(handoff['id'], 'record_handoff', 'Applied the exact reviewed output',
                         [proposal['id']], idempotency_key='manual-after-tests')
    with store._connect() as conn:
        after, _ = store.verify_project_tests(conn, objective['id'])
        assert after['snapshot_sha256'] == original_snapshot
    # A later rollback to the original bytes cannot reuse the old handoff as
    # current source delivery merely because the test snapshot used that base.
    source.write_bytes(proposal['editProposal']['baseContent'].encode('utf-8'))
    with store._connect() as conn:
        with pytest.raises(ValueError, match='preimage changed'):
            store.verify_project_tests(conn, objective['id'])
    source.write_bytes(proposal['editProposal']['newContent'].encode('utf-8'))
    integrate = store.claim_next()
    assert integrate['type'] == 'request.integrate'
    assert store.finish(integrate, {'summary': 'Reviewed delivery', 'deliverable': 'Reviewed exact tests and verified manual source delivery'})
    accept = store.claim_next()
    assert accept['type'] == 'request.accept'
    ids = json.loads(accept['payload'])['evidenceIds']
    criteria = store.context(accept)['objective']['acceptanceCriteria']
    assert store.finish(accept, {'approved': True, 'summary': 'All exact evidence checked', 'evidenceIds': ids,
        'criteriaResults': [{'criterion': item, 'satisfied': True, 'reason': 'Exact independent test and handoff receipts',
                             'evidenceIds': ids} for item in criteria], 'conflicts': []})
    assert store.snapshot()['objectives'][0]['status'] == 'completed'
    assert 'integrate_source' not in store.settings.tool_grants


@pytest.mark.linux_only
def test_failed_project_gate_requires_bounded_replan_instead_of_replay(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, _, objective, _ = _reviewed_project(tmp_path)
    dispatched = []
    def failed(files, grant, cancel):
        dispatched.append(files)
        return {**_simulated_terminal_result(files), 'status': 'failed', 'exitCode': 1}
    monkeypatch.setattr(runner, 'run_project_tests', failed)
    claim = store.claim_next()
    store.run_project_stage(claim, threading.Event())
    assert store.finish(claim, {})
    with store._connect() as conn:
        gate = conn.execute("SELECT * FROM requests WHERE objective_id=? AND type='request.project_failed'",
                            (objective['id'],)).fetchone()
        assert gate['status'] == 'pending_intervention'
        actions = {item['action'] for item in store.allowed_owner_resolutions(conn, gate)}
        assert 'retry_configuration' not in actions
        assert {'request_replan', 'amend_scope'}.issubset(actions)
        identifier = gate['id']
    with pytest.raises(ValueError):
        store.resolve(identifier, 'retry_configuration', idempotency_key='invalid-failed-gate-retry')
    with pytest.raises(ValueError):
        store.retry(identifier, idempotency_key='invalid-legacy-retry')
    assert store.resolve(identifier, 'request_replan', 'Revise the reviewed implementation and test coverage',
                         idempotency_key='reviewed-project-replan')
    plan = store.claim_next()
    assert plan['type'] == 'request.plan' and json.loads(plan['payload'])['round'] == 1
    assert len(dispatched) == 1
    with store._connect() as conn:
        assert conn.execute('SELECT status FROM requests WHERE id=?', (identifier,)).fetchone()[0] == 'cancelled'
        assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 1

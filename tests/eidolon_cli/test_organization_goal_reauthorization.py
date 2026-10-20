"""Synthetic owner refresh keeps original finite scope and lifetime accounting."""
import json
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_goal_continuation import setup,approve,enable,template


def revise_budget(store):
    with store._connect() as conn:
        from eidolon_cli.organization_goal_allowances import goal_projection
        old=goal_projection(conn,'goal')
    store.configure_goal_allowance('goal',objective_ids=old['objectiveIds'],limits=old['limits'],paused=False,expected_revision=old['revision'],idempotency_key='budget',confirmed=True)


def refresh(store,key='refresh'):
    preview=store.preview_goal_reauthorization('goal')
    args=dict(preview_sha256=preview['sha256'],expected_revision=preview['authorization']['controlRevision'],idempotency_key=key,confirmed=True)
    return store.reauthorize_goal_continuation('goal',**args),args


def test_budget_refresh_stays_disabled_preserves_launches_and_survives_restart(tmp_path):
    store,_=setup(tmp_path);approve(store,[template(),template(id='two')]);enable(store)
    first=store.advance_goal_continuation()['launched']
    with store._connect() as conn: original=conn.execute('SELECT record FROM organization_continuation_plans').fetchone()[0]
    revise_budget(store)
    assert store.advance_goal_continuation()['launched'] is None
    receipt,args=refresh(store)
    assert receipt['enabled'] is False
    reopened=OrganizationStore(store.path,store.settings)
    assert reopened.reauthorize_goal_continuation('goal',**args)==receipt
    assert reopened.advance_goal_continuation()['launched'] is None
    enable(reopened,rev=receipt['revision'],key='enable-refreshed')
    second=reopened.advance_goal_continuation()['launched']
    assert second['templateId']=='two' and second['objectiveId']!=first['objectiveId']
    assert reopened.advance_goal_continuation()['launched'] is None
    with reopened._connect() as conn:
        assert conn.execute('SELECT record FROM organization_continuation_plans').fetchone()[0]==original
        assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==2


def test_stale_preview_and_edited_retry_reject(tmp_path):
    store,_=setup(tmp_path);approve(store)
    preview=store.preview_goal_reauthorization('goal')
    enable(store)
    with pytest.raises(ValueError,match='stale'):
        store.reauthorize_goal_continuation('goal',preview_sha256=preview['sha256'],expected_revision=1,idempotency_key='stale',confirmed=True)
    receipt,args=refresh(store)
    with pytest.raises(ValueError,match='different input'):
        store.reauthorize_goal_continuation('goal',**{**args,'preview_sha256':'f'*64})


def test_changed_launched_scope_cannot_be_refreshed(tmp_path):
    store,_=setup(tmp_path);approve(store);enable(store)
    launched=store.advance_goal_continuation()['launched']
    with store._write() as conn: conn.execute('UPDATE objective_control SET amended_scope=? WHERE objective_id=?',('Different',launched['objectiveId']))
    with pytest.raises(ValueError,match='Changed or cancelled'):
        refresh(store)


def test_authorization_and_disable_rollback_if_receipt_write_fails(tmp_path):
    store,_=setup(tmp_path);approve(store);enable(store)
    with store._connect(write=True) as conn: conn.execute("CREATE TRIGGER fail_refresh BEFORE INSERT ON organization_continuation_receipts WHEN NEW.idempotency_key='refresh' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'): refresh(store)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_continuation_authorizations').fetchone()[0]==0
        assert conn.execute('SELECT enabled FROM organization_continuation_controls ORDER BY revision DESC').fetchone()[0]==1


def test_workspace_replacement_cannot_be_refreshed(tmp_path):
    from tests.eidolon_cli.test_organization_projects import configured
    store,_=setup(tmp_path,configured(tmp_path));approve(store,[template(projectIds=['alpha'])])
    root=tmp_path/'alpha';root.rename(tmp_path/'old-alpha');root.mkdir()
    with pytest.raises(ValueError,match='scope or workspace'): refresh(store)


def test_original_deadline_cannot_be_extended_by_budget_refresh(tmp_path,monkeypatch):
    import eidolon_cli.organization_goal_continuation as continuation
    store,_=setup(tmp_path);approve(store)
    with store._connect() as conn: deadline=json.loads(conn.execute('SELECT record FROM organization_continuation_plans').fetchone()[0])['deadlineTimestamp']
    monkeypatch.setattr(continuation.time,'time',lambda:deadline+1)
    with pytest.raises(ValueError,match='deadline'): refresh(store)


def test_legacy_passive_authorization_read_does_not_migrate(tmp_path):
    from eidolon_cli.organization_goal_continuation import _records
    from eidolon_cli.organization_goal_reauthorization import effective_authority
    store,_=setup(tmp_path);approve(store)
    with store._connect(write=True) as conn:
        conn.execute('DROP TABLE organization_continuation_authorizations')
        before='\n'.join(conn.iterdump());plan=_records(conn)[0]
        assert effective_authority(conn,plan)==(plan['goalRevision'],plan['authoritySha256'])
        assert '\n'.join(conn.iterdump())==before


def test_concurrent_identical_refresh_is_exactly_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store,_=setup(tmp_path);approve(store)
    preview=store.preview_goal_reauthorization('goal')
    args=dict(preview_sha256=preview['sha256'],expected_revision=1,idempotency_key='refresh',confirmed=True)
    peers=[OrganizationStore(store.path,store.settings) for _ in range(3)]
    with ThreadPoolExecutor(3) as pool:
        results=list(pool.map(lambda peer:peer.reauthorize_goal_continuation('goal',**args),peers))
    assert results[0]==results[1]==results[2]
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_continuation_authorizations').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM organization_continuation_controls').fetchone()[0]==2


def test_launch_after_preview_invalidates_refresh_without_reset(tmp_path):
    store,_=setup(tmp_path);approve(store);enable(store)
    preview=store.preview_goal_reauthorization('goal')
    assert store.advance_goal_continuation()['launched']
    with pytest.raises(ValueError,match='stale'):
        store.reauthorize_goal_continuation('goal',preview_sha256=preview['sha256'],expected_revision=2,idempotency_key='refresh',confirmed=True)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_continuation_authorizations').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==1


def test_refresh_exact_receipt_replays_after_budget_drift(tmp_path):
    store,_=setup(tmp_path);approve(store)
    receipt,args=refresh(store)
    revise_budget(store)
    assert store.reauthorize_goal_continuation('goal',**args)==receipt
    with pytest.raises(ValueError,match='changed'):
        enable(store,rev=receipt['revision'])

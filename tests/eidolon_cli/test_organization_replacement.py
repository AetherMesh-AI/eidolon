"""Expired work replacement is a single owner-authorized transaction, never renewal."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_request_recovery import pending_question


def expired(tmp_path, monkeypatch):
    store, objective, work, question = pending_question(tmp_path, max_open_objectives=1)
    instant = time.time() + store.settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: instant)
    return store, objective, work, question


def submit(store, draft, **overrides):
    return store.replace_objective(draft['sourceId'], draft['sourceVersion'],
        overrides.get('title', draft['title']), overrides.get('description', draft['description']),
        overrides.get('criteria', draft['acceptanceCriteria']), confirmed=overrides.get('confirmed', True))


def test_replacement_race_replay_and_restart_preserve_history_and_finite_authority(tmp_path, monkeypatch):
    store, original, work, question = expired(tmp_path, monkeypatch)
    before = store.snapshot()
    draft = store.preview_replacement(original['id'])
    settings = store.settings
    # A read/cancelled review cannot consume a slot or alter the old objective.
    with pytest.raises(ValueError, match='confirmation'):
        submit(store, draft, confirmed=False)
    assert store.snapshot() == before
    with pytest.raises(ValueError, match='open-objective limit'):
        store.create_objective('Unrelated objective', idempotency_key='blocked')
    peers = [OrganizationStore(store.path), OrganizationStore(store.path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda peer: submit(peer, draft), peers))
    assert results[0]['id'] == results[1]['id']
    reopened = OrganizationStore(store.path)
    replacement = submit(reopened, draft)
    assert replacement['id'] == results[0]['id']
    assert replacement['replacesObjectiveId'] == original['id']
    after = reopened.snapshot()
    assert len(after['objectives']) == 2
    old = next(row for row in after['objectives'] if row['id'] == original['id'])
    assert old['status'] == 'cancelled'
    assert old['replacementObjectiveId'] == replacement['id']
    assert old['usage'] == before['objectives'][0]['usage']
    assert replacement['usage']['modelCalls'] == replacement['usage']['reservedTokens'] == 0
    assert replacement['usage']['modelCallLimit'] == draft['allowance']['modelCalls']
    assert replacement['usage']['deadlineTimestamp'] == time.time() + draft['allowance']['durationSeconds']
    assert replacement['executiveId'] == old['executiveId'] and replacement['managerId'] == old['managerId']
    assert [(a['id'], a['identityId']) for a in after['agents']] == [(a['id'], a['identityId']) for a in before['agents']]
    assert reopened.settings == settings
    assert not reopened.finish(work, {'summary': 'Late result'})
    with pytest.raises(ValueError, match='not awaiting|cancelled|deadline'):
        reopened.respond(question['id'], 'Late answer', idempotency_key='late')
    with pytest.raises(ValueError, match='different input'):
        submit(reopened, draft, title='Changed intent')
    with reopened._write() as conn:
        assert conn.execute('SELECT count(*) FROM objective_replacements').fetchone()[0] == 1
    claim = reopened.claim_next()
    assert claim['objective_id'] == replacement['id'] and claim['type'] == 'request.decompose'


@pytest.mark.parametrize('change', ['unconfirmed', 'scope', 'policy', 'cancel', 'history_capacity', 'invalid_criteria'])
def test_replacement_rejects_stale_or_unauthorized_drafts_without_partial_cancellation(tmp_path, monkeypatch, change):
    store, original, _, _ = expired(tmp_path, monkeypatch)
    draft = store.preview_replacement(original['id'])
    kwargs = {}
    if change == 'unconfirmed':
        kwargs['confirmed'] = 'true'
    elif change == 'scope':
        with store._write() as conn:
            conn.execute('UPDATE objective_control SET amended_scope=? WHERE objective_id=?', ('Changed elsewhere', original['id']))
    elif change == 'policy':
        store.reload_configuration(replace(store.settings, max_model_calls=store.settings.max_model_calls + 1))
    elif change == 'cancel':
        store.cancel(original['id'])
    elif change == 'history_capacity':
        monkeypatch.setattr('eidolon_cli.organization_history.MAX_CURRENT_OBJECTIVES', 1)
    else:
        kwargs['criteria'] = ['']
    before = store.snapshot()
    with pytest.raises(ValueError):
        submit(store, draft, **kwargs)
    assert store.snapshot() == before
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objective_replacements').fetchone()[0] == 0


def test_replacement_waits_for_cross_process_execution(tmp_path, monkeypatch):
    import multiprocessing
    from eidolon_cli.organization_service import OrganizationService
    from tests.eidolon_cli.test_organization_request_recovery import _hold_peer_recovery_lock

    store, original, work, _ = expired(tmp_path, monkeypatch)
    draft = store.preview_replacement(original['id'])
    service = OrganizationService(store, home=tmp_path)
    starts = []
    monkeypatch.setattr(service, 'start', lambda: starts.append(True))
    context = multiprocessing.get_context('spawn')
    ready, release = context.Queue(), context.Event()
    process = context.Process(target=_hold_peer_recovery_lock, args=(str(tmp_path / 'organization' / 'execution-locks'), work['id'], ready, release))
    process.start()
    try:
        assert ready.get(timeout=20)
        with pytest.raises(ValueError, match='another runtime'):
            service.replace_objective(draft['sourceId'], draft['sourceVersion'], draft['title'], draft['description'], draft['acceptanceCriteria'], confirmed=True)
        assert starts == []
        assert len(store.snapshot()['objectives']) == 1
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        ready.close()
    assert process.exitcode == 0
    result = service.replace_objective(draft['sourceId'], draft['sourceVersion'], draft['title'], draft['description'], draft['acceptanceCriteria'], confirmed=True)
    assert result['replacesObjectiveId'] == original['id']
    assert starts == [True]


@pytest.mark.parametrize('changed_grant', [False, True])
def test_replacement_preserves_selected_projects_checks_and_original_budget_ceiling(tmp_path, monkeypatch, changed_grant):
    from tests.eidolon_cli.test_organization_multi_project_execution import configured
    store, original = configured(tmp_path, selected=('alpha',))
    settings = store.settings
    now = time.time() + settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: now)
    grants = settings.project_grants
    if changed_grant:
        grants = (replace(grants[0], files=('root0/different.py',)), *grants[1:])
    store.reload_configuration(replace(settings, max_model_calls=settings.max_model_calls + 1, project_grants=grants))
    before = store.snapshot()
    if changed_grant:
        with pytest.raises(ValueError, match='Project authority changed'):
            store.preview_replacement(original['id'])
        assert store.snapshot() == before
        return
    draft = store.preview_replacement(original['id'])
    result = submit(store, draft)
    assert result['projects'] == original['projects']
    assert result['requiredChecks'] == ['project_tests']
    assert result['usage']['modelCallLimit'] == settings.max_model_calls
    assert draft['projectIds'] == ['alpha']
    assert store.settings.project_grants == settings.project_grants
    assert store.settings.tool_grants == settings.tool_grants

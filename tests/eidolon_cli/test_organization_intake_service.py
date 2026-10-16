"""Real store/service lifecycle with an injected offline provider boundary."""
import json
import threading
import time

from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


def wait(check):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.01)
    raise AssertionError('Intake did not settle')


def output(prompt, **changes):
    value = dict(snapshotId=prompt['organization']['snapshotId'], routeId='manager', projectIds=[],
                 deliveryMode='managed_artifact', priority='P3', rationale='Routine report',
                 evidenceIds=['owner-request'], acceptanceCriteria=['Complete report'], assumptions=[],
                 duplicateCandidateIds=[], dependencyCandidateIds=[], question=None)
    return {'raw': json.dumps({**value, **changes}), 'usage': {'inputTokens': 42, 'outputTokens': 12}}


def test_submission_runs_once_and_admits_through_real_store(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    service = OrganizationService(store, home=tmp_path)
    starts = []
    monkeypatch.setattr(service, 'start', lambda: starts.append(True))
    calls = []
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='test', model='small', input_limit=1000, output_limit=1000)
        calls.append(prompt)
        return output(prompt)
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    first = service.intake.submit('Write a report', idempotency_key='same')
    wait(lambda: service.intake.active_count == 0)
    second = service.intake.submit('Write a report', idempotency_key='same')
    assert first['id'] == second['id'] and second['status'] == 'admitted'
    assert len(calls) == len(starts) == len(store.snapshot()['objectives']) == 1
    assert second['usage']['inputTokens'] == 42
    service.stop()


def test_stop_keeps_uncertain_debit_and_running_slot_until_exit(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    service = OrganizationService(store, home=tmp_path)
    entered, release = threading.Event(), threading.Event()
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='test', model='small', input_limit=1000, output_limit=1000)
        entered.set()
        assert release.wait(5)
        return output(prompt)
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    first = service.intake.submit('Write report', idempotency_key='stop')
    assert entered.wait(5)
    try:
        assert not service.stop(timeout=0)
        assert service.active_execution_count == 1
        assert store.intake_read(first['id'])['status'] == 'uncertain'
        assert not store.snapshot()['objectives']
    finally:
        release.set()
        wait(lambda: service.intake.active_count == 0)
        service.stop()
    assert store.intake_read(first['id'])['usage']['reservedTokens'] == 2000


def test_question_does_not_start_objective_scheduler(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    service = OrganizationService(store, home=tmp_path)
    starts = []
    monkeypatch.setattr(service, 'start', lambda: starts.append(True))
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='test', model='small', input_limit=1000, output_limit=1000)
        return output(prompt, routeId=None, question={'text': 'Which project?', 'neededFor': 'Routing is ambiguous',
                                                     'checkedEvidenceIds': ['owner-request']})
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    first = service.intake.submit('Fix it', idempotency_key='question')
    wait(lambda: service.intake.active_count == 0)
    assert store.intake_read(first['id'])['status'] == 'needs_answer'
    assert not starts and not store.snapshot()['objectives']
    service.stop()


def test_essential_answer_creates_one_linked_charged_continuation(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    service = OrganizationService(store, home=tmp_path)
    monkeypatch.setattr(service, 'start', lambda: None)
    calls = []
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='test', model='small', input_limit=1000, output_limit=1000)
        calls.append(prompt)
        if len(calls) == 1:
            return output(prompt, routeId=None, question={'text': 'Which report?', 'neededFor': 'Required deliverable',
                                                         'checkedEvidenceIds': ['owner-request']})
        return output(prompt)
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    first = service.intake.submit('Write report', idempotency_key='original', owner_priority='P4')
    wait(lambda: service.intake.active_count == 0)
    second = service.intake.submit('Quarterly report', idempotency_key='answer', parent_intake_id=first['id'])
    wait(lambda: service.intake.active_count == 0)
    replay = service.intake.submit('Quarterly report', idempotency_key='answer', parent_intake_id=first['id'])
    assert second['id'] == replay['id'] and replay['status'] == 'admitted'
    assert replay['parentIntakeId'] == first['id'] and replay['proposal']['effectivePriority'] == 'P4'
    assert store.intake_read(first['id'])['status'] == 'answered'
    assert 'Quarterly report' in calls[1]['request'] and len(calls) == 2
    import pytest
    with pytest.raises(ValueError, match='not waiting'):
        service.intake.submit('Other answer', idempotency_key='other', parent_intake_id=first['id'])
    assert len(store.snapshot()['objectives']) == 1
    service.stop()


def test_explicit_recovery_reconciles_abandoned_pending_without_replaying(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    first, _ = store.begin_intake('Unsent request', idempotency_key='before')
    second, _ = store.begin_intake('Sent request', idempotency_key='after')
    store.reserve_intake_call(second['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    service = OrganizationService(store, home=tmp_path)
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', lambda **_: __import__('pytest').fail('replayed'))
    assert service.intake.read(first['id'])['status'] == 'pending'
    service.intake.recover()
    assert service.intake.read(first['id'])['status'] == 'failed'
    view = service.intake.read(second['id'])
    assert view['status'] == 'uncertain' and view['usage']['reservedTokens'] == 2000
    assert not store.snapshot()['objectives']
    service.stop()


def test_dispatch_callback_does_not_hold_intake_running_lock(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    entered, release, counted = threading.Event(), threading.Event(), threading.Event()
    def can_dispatch():
        entered.set()
        assert release.wait(3)
        return False
    service = OrganizationService(store, home=tmp_path, can_dispatch=can_dispatch)
    errors = []
    def submit():
        try:
            service.intake.submit('Request', idempotency_key='race')
        except ValueError as exc:
            errors.append(str(exc))
    sender = threading.Thread(target=submit, daemon=True)
    sender.start()
    assert entered.wait(2)
    reader = threading.Thread(target=lambda: (service.active_execution_count, counted.set()), daemon=True)
    reader.start()
    try:
        assert counted.wait(1), 'Dispatch callback blocked the running-record count'
    finally:
        release.set()
        sender.join(2)
        reader.join(2)
    assert errors and not sender.is_alive() and not reader.is_alive()
    service.stop()


def test_explicit_recovery_reconciles_crash_between_proposal_and_admission(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    first, _ = store.begin_intake('Report', idempotency_key='crash')
    store.reserve_intake_call(first['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    store.finish_intake(first['id'], output(store.intake_prompt(first['id']))['raw'])
    service = OrganizationService(store, home=tmp_path)
    assert service.intake.read(first['id'])['status'] == 'proposed'
    service.intake.recover()
    assert service.intake.read(first['id'])['status'] == 'needs_review'
    assert not store.snapshot()['objectives']
    service.stop()


def test_owner_rotation_does_not_reconcile_or_expose_prior_owner_intakes(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    previous = []
    for index in range(2):
        row, _ = store.begin_intake('Old owner request', idempotency_key=f'old-{index}')
        previous.append(row)
    store.reserve_intake_call(previous[0]['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    with store._write() as conn:
        conn.execute("UPDATE agent_identity SET identity_id='new-owner' WHERE agent_id='owner'")
    service = OrganizationService(store, home=tmp_path)
    monkeypatch.setattr(service, 'start', lambda: None)
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='test', model='small', input_limit=1000, output_limit=1000)
        return output(prompt)
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    assert service.intake.recent() == []
    current = service.intake.submit('New owner report', idempotency_key='new')
    wait(lambda: service.intake.active_count == 0)
    assert service.intake.read(current['id'])['status'] == 'admitted'
    assert [row['id'] for row in service.intake.recent()] == [current['id']]
    with store._connect() as conn:
        retained = conn.execute("SELECT status,dispatched,input_limit FROM organization_request_intakes WHERE id=?", (previous[0]['id'],)).fetchone()
        assert tuple(retained) == ('pending', 1, 1000)
        assert conn.execute("SELECT status FROM organization_request_intakes WHERE id=?", (previous[1]['id'],)).fetchone()[0] == 'pending'
    service.stop()

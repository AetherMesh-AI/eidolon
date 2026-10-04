"""Owner resolutions cannot race an occupied local or peer execution fence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from eidolon_cli.organization_service import OrganizationService, _ExecutionLock
from eidolon_cli.organization_store import OrganizationStore


def pending_plan(tmp_path):
    store = OrganizationStore(tmp_path / "organization" / "state.db")
    objective = store.create_objective("Draft an evidence-based brief", idempotency_key="objective")
    claim = store.claim_next()
    assert claim["type"] == "request.plan"
    assert store.fail(claim, "The requested source facts are missing")
    service = OrganizationService(store, home=tmp_path)
    return service, objective, claim


def test_resolution_replays_once_and_retains_changed_payload_guard(tmp_path, monkeypatch):
    service, _, claim = pending_plan(tmp_path)
    starts = []
    monkeypatch.setattr(service, "start", lambda: starts.append(True))
    payload = dict(action="provide_input", text="Option A costs 40 dollars.", idempotency_key="answer-1")
    assert service.resolve(claim["id"], **payload)
    assert starts == [True]
    assert not service.resolve(claim["id"], **payload)
    assert starts == [True]
    with pytest.raises(ValueError, match="different input"):
        service.resolve(claim["id"], **{**payload, "text": "Option A costs 70 dollars."})
    request = next(row for row in service.store.snapshot()["requests"] if row["id"] == claim["id"])
    assert request["status"] == "queued"
    assert request["attempts"] == 1


def test_resolution_waits_for_another_request_of_same_objective_to_exit(tmp_path):
    service, objective, claim = pending_plan(tmp_path)
    service._running["another-request"] = SimpleNamespace(claim={"objective_id": objective["id"]})
    with pytest.raises(ValueError, match="stopping or finishing"):
        service.resolve(claim["id"], action="provide_input", text="Supplied facts", idempotency_key="answer")
    assert service.store.snapshot()["requests"][0]["status"] == "pending_intervention"


def test_resolution_cannot_evade_peer_provider_lock_after_lease_was_fenced(tmp_path, monkeypatch):
    service, _, claim = pending_plan(tmp_path)
    starts = []
    monkeypatch.setattr(service, "start", lambda: starts.append(True))
    payload = dict(action="provide_input", text="Supplied facts", idempotency_key="peer-answer")
    with _ExecutionLock(tmp_path / "organization" / "execution-locks", claim["id"]) as held:
        assert held
        with pytest.raises(ValueError, match="another runtime"):
            service.resolve(claim["id"], **payload)
        assert starts == []
        assert not service.store.resolution_recorded(claim["id"], **payload)
    assert service.resolve(claim["id"], **payload)
    assert starts == [True]


def test_stopping_service_refuses_new_owner_mutations(tmp_path):
    service, _, claim = pending_plan(tmp_path)
    service._stop.set()
    with pytest.raises(RuntimeError, match="stopping"):
        service.resolve(claim["id"], action="provide_input", text="Supplied facts", idempotency_key="stop-answer")
    assert service.store.snapshot()["requests"][0]["status"] == "pending_intervention"

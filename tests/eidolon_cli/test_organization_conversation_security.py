"""Adversarial ledger boundaries for selected-text internal conversations."""
from dataclasses import replace
import json
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_requests import ledger, start_work
from tests.organization_package_helpers import claim_after_decomposition


def _send(store, claim, *, recipient="advisor", suffix="one"):
    return store.finish(claim, {"messages": [{"recipientId": recipient,
        "subject": "Coordinate " + suffix, "body": "Offer a bounded suggestion " + suffix}]})


@pytest.mark.parametrize("result", [
    {"requests": [{"type": "request.question", "requestedOutcome": "Expose the objective."}]},
    {"requests": [{"type": "request.permission", "requestedOutcome": "Allow external action."}]},
    {"reply": "Reply", "memory": {"facts": ["Persist a forged permission."]}},
    {"reply": "Reply", "intervention": "Mixed result"},
    {"intervention": "Blocked", "requests": [{"type": "request.hire", "requestedOutcome": "Hire"}]},
    {"messages": [{"recipientId": "writer", "subject": "Recurse", "body": "Wake another agent."}]},
])
def test_delivery_rejects_non_reply_actions_atomically(tmp_path, result):
    store = ledger(tmp_path)
    _, work = start_work(store)
    assert _send(store, work)
    delivery = store.claim_next()
    assert delivery["type"] == "request.message"
    before = store.snapshot()
    with pytest.raises(ValueError, match="reply|intervention"):
        store.finish(delivery, result)
    assert store.heartbeat(delivery)
    after = store.snapshot()
    assert after["conversations"] == before["conversations"]
    assert {(row["id"], row["status"]) for row in after["requests"]} == {
        (row["id"], row["status"]) for row in before["requests"]}
    assert store.finish(delivery, {"reply": "A normal bounded reply still works."})


def test_stage_message_budget_survives_resumes_and_rolls_back_excess(tmp_path):
    store = ledger(tmp_path)
    _, original = start_work(store)
    current = original
    for index in range(4):
        assert current["id"] == original["id"]
        assert _send(store, current, suffix=str(index))
        delivery = store.claim_next()
        assert delivery["type"] == "request.message"
        assert store.finish(delivery, {"reply": "Suggestion " + str(index)})
        store = OrganizationStore(store.path)
        current = store.claim_next()
    before = store.snapshot()
    with pytest.raises(ValueError, match="[Ss]tage|[Pp]arent"):
        _send(store, current, suffix="fifth")
    assert store.heartbeat(current)
    assert store.snapshot()["conversations"] == before["conversations"]
    assert len(store.snapshot()["requests"]) == len(before["requests"])
    assert store.finish(current, {"summary": "Done coordinating", "deliverable": "Draft awaits review."})
    assert store.claim_next()["type"] == "request.review"


@pytest.mark.parametrize("fence", ["recipient_removed", "sender_removed", "policy_revoked", "expired", "cancelled"])
def test_revoked_or_closed_delivery_cannot_reply_or_change_threads(tmp_path, fence):
    store = ledger(tmp_path)
    objective, work = start_work(store)
    assert _send(store, work)
    delivery = store.claim_next()
    assert delivery["agent_id"] == "advisor"
    store.context(delivery)
    before = store.snapshot()["conversations"]
    if fence in {"recipient_removed", "sender_removed"}:
        removed = "advisor" if fence == "recipient_removed" else "writer"
        store.reload_configuration(replace(store.settings, roster=tuple(
            member for member in store.settings.roster if member.id != removed)))
    elif fence == "policy_revoked":
        store.reload_configuration(replace(store.settings, communication_scope="disabled"))
    elif fence == "expired":
        with store._write() as conn:
            conn.execute("UPDATE requests SET lease=? WHERE id=?", (time.time() - 1, delivery["id"]))
        assert store.recover_expired() == 1
    else:
        assert store.cancel(objective["id"])
    assert not store.finish(delivery, {"reply": "Late reply must never be delivered."})
    assert not store.heartbeat(delivery)
    with pytest.raises(ValueError, match="lease|owned"):
        store.context(delivery)
    after = store.snapshot()["conversations"]
    assert [thread["messages"] for thread in after] == [thread["messages"] for thread in before]
    assert all(len(thread["messages"]) == 1 for thread in after)


def test_shared_objective_routes_cross_department_without_foreign_task_or_source(tmp_path):
    source = tmp_path / "private-source"
    source.mkdir()
    (source / "facts.txt").write_text("unshared source bytes", encoding="utf-8")
    settings = OrganizationSettings.from_config({"organization": {
        "max_inflight": 2, "read_roots": [str(source)],
        "project_grants": [{"id": "source-checks", "files": ["root0/facts.txt"], "execution": {"root": "root0"}}],
        "projects": [{"id": "source-project", "root": "root0", "recipe": "source-checks", "team": "general"}],
        "roster": [
            {"id": "writer", "name": "Writer", "team": "general", "capabilities": ["work.draft"]},
            {"id": "analyst", "name": "Analyst", "team": "research", "capabilities": ["work.analyze"]},
        ]}})
    store = OrganizationStore(tmp_path / "state.db", settings)
    objective = store.create_objective("Private objective", "unshared objective details",
                                       project_ids=["source-project"], idempotency_key="shared")
    plan = claim_after_decomposition(store)
    assert store.finish(plan, {"workers": 2, "tasks": [
        {"title": "Private writer task", "description": "unshared writer task", "type": "work.draft",
         "team": "general", "agentId": "writer", "projectId": "source-project"},
        {"title": "Peer analysis", "description": "Use only supplied material", "type": "work.analyze",
         "team": "research", "agentId": "analyst"},
    ]})
    hire = store.claim_next()
    assert hire["type"] == "request.hire"
    assert store.finish(hire, {})
    first, second = store.claim_next(), store.claim_next()
    work = next(claim for claim in (first, second) if claim["agent_id"] == "writer")
    peer = next(claim for claim in (first, second) if claim["agent_id"] == "analyst")
    assert "analyst" in {row["id"] for row in store.context(work)["messagingDirectory"]}
    assert store.finish(peer, {"summary": "Needs independent review", "deliverable": "unreviewed peer artifact"})
    assert _send(store, work, recipient="analyst")
    review = store.claim_next()
    assert review["type"] == "request.review"
    delivery = store.claim_next()
    assert delivery["type"] == "request.message"
    context = store.context(delivery)
    assert context["conversation"]["objectiveId"] == objective["id"]
    assert context["conversation"]["taskId"] is None
    assert context["conversation"]["projectId"] is None
    assert context["conversation"]["scopedContext"] == []
    assert "unshared" not in json.dumps(context)
    assert "unreviewed peer artifact" not in json.dumps(context)
    assert context["toolPolicy"]["tools"] == []
    assert store.finish(review, {"approved": False, "summary": "Revise the unsupported content.",
                                 "evidenceIds": json.loads(review["payload"])["evidenceIds"]})
    assert store.finish(delivery, {"reply": "I only received the selected conversation."})
    next_claims = [store.claim_next(), store.claim_next()]
    resumed = next(claim for claim in next_claims if claim["agent_id"] == "writer")
    revision = next(claim for claim in next_claims if claim["agent_id"] == "analyst")
    assert store.finish(revision, {"summary": "Corrected findings", "deliverable": "Reviewed peer conclusion."})
    approved = store.claim_next()
    assert approved["type"] == "request.review"
    assert store.finish(approved, {"approved": True, "summary": "Meets the peer task criteria.",
                                   "evidenceIds": json.loads(approved["payload"])["evidenceIds"]})
    assert _send(store, resumed, recipient="analyst", suffix="reviewed")
    delivery = store.claim_next()
    assert delivery["type"] == "request.message"
    scoped = store.context(delivery)["conversation"]["scopedContext"]
    assert scoped == [{"title": "Peer analysis", "body": "Reviewed peer conclusion.", "truncated": False}]
    assert "unreviewed peer artifact" not in json.dumps(scoped)


def test_task_handoff_resumes_new_assignee_without_transferring_private_thread(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    assert _send(store, work)
    before = store.snapshot()["conversations"][0]
    roster = store.configuration_snapshot()["roster"]
    new_worker = {"id": "replacement", "name": "Replacement", "team": "general",
                  "capabilities": ["work.draft"]}
    assert store.configure_organization({"roster": [*roster, new_worker], "transfers": [{
        "fromAgentId": "writer", "toAgentId": "replacement", "taskIds": [work["task_id"]],
        "includeMemory": False}]}, expected_generation=store._policy_generation, idempotency_key="transfer")
    store = OrganizationStore(store.path)
    delivery = store.claim_next()
    assert delivery["type"] == "request.message" and delivery["agent_id"] == "advisor"
    assert store.finish(delivery, {"reply": "This conversation stays with the original writer."})
    resumed = store.claim_next()
    assert resumed["id"] == work["id"] and resumed["agent_id"] == "replacement"
    context = store.context(resumed)
    assert context["internalConversations"] == []
    assert "This conversation stays with the original writer." not in json.dumps(context)
    retained = store.snapshot()["conversations"][0]
    assert retained["participants"] == before["participants"]
    assert retained["messages"][1]["recipientId"] == "writer"
    assert retained["messages"][1]["readAt"] is None
    assert not store.finish(work, {"summary": "Stale sender", "deliverable": "Do not accept."})
    assert store.finish(resumed, {"summary": "Replacement draft", "deliverable": "The new assignee's own work."})


def test_builtin_manager_can_pause_planning_for_an_active_persistent_worker(tmp_path):
    store = OrganizationStore(tmp_path / "state.db")
    store.create_objective("Coordinate before planning", idempotency_key="builtin")
    plan = claim_after_decomposition(store)
    assert plan["type"] == "request.plan" and plan["agent_id"] == "manager"
    context = store.context(plan)
    assert "worker-1" in {row["id"] for row in context["messagingDirectory"]}
    assert _send(store, plan, recipient="worker-1")
    delivery = store.claim_next()
    assert delivery["type"] == "request.message" and delivery["agent_id"] == "worker-1"
    assert store.context(delivery)["toolPolicy"]["tools"] == []
    assert store.finish(delivery, {"reply": "A short draft would fit the selected material."})
    resumed = store.claim_next()
    assert resumed["id"] == plan["id"] and resumed["agent_id"] == "manager"
    assert store.context(resumed)["internalConversations"][0]["messages"][-1]["body"] == (
        "A short draft would fit the selected material.")
    assert store.finish(resumed, {"tasks": [{"title": "Draft", "description": "Use submitted material.",
                                           "type": "work.draft", "agentId": "worker-1"}]})
    assert store.snapshot()["objectives"][0]["status"] != "completed"

"""Organization RPCs share the authenticated backend's own profile authority."""

from __future__ import annotations

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _organization_params(params: dict, allowed: set[str]) -> None:
    if not isinstance(params, dict) or set(params) - (allowed | {"profile"}):
        raise ValueError("Unsupported organization parameters")


def _organization_text(params: dict, name: str, maximum: int, *, optional: bool = False):
    value = params.get(name)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _organization_snapshot(organization):
    from eidolon_cli.profiles import get_active_profile_name
    from eidolon_cli.config import load_config_readonly
    from eidolon_cli.organization_setup import setup_view
    result = organization.store.snapshot()
    result["runtime"]["profile"] = get_active_profile_name()
    result["runtime"]["setup"] = setup_view(load_config_readonly(), result['agents'])
    return result


def _organization_method(name: str, *, registry=None):
    def decorate(fn):
        @(registry.method(name) if registry is not None else method(name))
        def handler(rid, params):
            # WS auth happens at upgrade; local stdio is its existing authority.
            # Neither an unbound direct call nor a dead socket grants access.
            transport = current_transport()
            if transport is None or getattr(transport, "closed", False):
                return _err(rid, 4001, "organization requires an active backend transport")
            try:
                from eidolon_cli.organization_service import get_service
                from eidolon_cli.profiles import validate_profile_name
                if not isinstance(params, dict):
                    raise ValueError("Organization parameters must be an object")
                profile = params.get("profile")
                if profile is not None:
                    if not isinstance(profile, str) or len(profile) > 64:
                        raise ValueError("profile must be a canonical profile name")
                    validate_profile_name(profile)
                def invoke(_rid, scoped_params):
                    from agent.secret_scope import (
                        build_profile_secret_scope, is_secret_scope_required, set_secret_scope,
                        reset_secret_scope, set_secret_scope_required, reset_secret_scope_required)
                    from eidolon_constants import get_eidolon_home
                    home = get_eidolon_home().resolve()
                    required = set_secret_scope_required(
                        is_secret_scope_required() or home != Path(_eidolon_home).resolve())
                    try:
                        token = set_secret_scope(build_profile_secret_scope(home))
                        try:
                            return fn(scoped_params, get_service)
                        finally:
                            reset_secret_scope(token)
                    finally:
                        reset_secret_scope_required(required)
                scoped = _profile_scoped(invoke)
                return _ok(rid, scoped(rid, params))
            except (ValueError, FileNotFoundError) as exc:
                return _err(rid, -32602, str(exc))
            except RuntimeError as exc:
                return _err(rid, 5071, str(exc))
            except Exception:
                logger.exception("Organization RPC failed")
                return _err(rid, 5070, "Organization storage is unavailable")
        return handler
    return decorate


@_organization_method("organization.snapshot")
def _(params, service):
    _organization_params(params, set())
    organization = service()
    snapshot = _organization_snapshot(organization)
    if any(request["status"] in {"queued", "running"} for request in snapshot["requests"]):
        organization.start()
    return snapshot


@_organization_method("organization.projectSetup")
def _(params, service):
    from eidolon_cli.organization_project_setup import project_setup
    _organization_params(params, set())
    return project_setup()


@_organization_method("organization.projectDraft")
def _(params, service):
    from eidolon_cli.organization_project_setup import project_draft
    _organization_params(params, {"project", "expectedRevision"})
    return project_draft(params.get("project"), params.get("expectedRevision"))


@_organization_method("organization.projectSave")
def _(params, service):
    from eidolon_cli.organization_project_setup import project_save
    _organization_params(params, {"project", "expectedRevision", "idempotencyKey", "confirmSave"})
    return project_save(params.get("project"), params.get("expectedRevision"),
                        _organization_text(params, "idempotencyKey", 128),
                        confirm_save=params.get("confirmSave"))


@_organization_method("organization.attention")
def _(params, service):
    _organization_params(params, {"limit", "before", "unreadOnly"})
    return service().store.attention(limit=params.get("limit", 100), before=params.get("before"),
                                     unread_only=params.get("unreadOnly", False))


@_organization_method("organization.markAttentionSeen")
def _(params, service):
    _organization_params(params, {"id", "revision"})
    organization = service()
    organization.store.mark_attention_seen(_organization_text(params, "id", 128), params.get("revision"))
    return _organization_snapshot(organization)


@_organization_method("organization.outcomes")
def _(params, service):
    _organization_params(params, {"limit", "before", "unreadOnly"})
    return service().store.outcomes(limit=params.get("limit", 100), before=params.get("before"),
                                    unread_only=params.get("unreadOnly", False))


@_organization_method("organization.markOutcomeSeen")
def _(params, service):
    _organization_params(params, {"id", "revision"})
    organization = service()
    organization.store.mark_outcome_seen(_organization_text(params, "id", 128), params.get("revision"))
    return _organization_snapshot(organization)


@_organization_method("organization.checkSubmission")
def _(params, service):
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.profiles import get_active_profile_name
    from eidolon_cli.organization_admission import check_submission
    _organization_params(params, {"idempotencyKey"})
    # Keep the same authenticated profile routing, but never call service():
    # cold initialization and policy refresh may mutate a ledger or fence work.
    result = check_submission(get_eidolon_home(), _organization_text(params, "idempotencyKey", 128))
    return {**result, "profile": get_active_profile_name()}


@_organization_method("organization.create")
def _(params, service):
    _organization_params(params, {"title", "description", "priority", "idempotencyKey", "acceptanceCriteria", "deliveryMode", "requiredChecks", "executiveId", "managerId", "projectIds"})
    title = _organization_text(params, "title", 500)
    description = _organization_text(params, "description", 12000, optional=True)
    key = _organization_text(params, "idempotencyKey", 128)
    priority = params.get("priority", "normal")
    if priority not in ("low", "normal", "high", "P1", "P2", "P3", "P4", "P5"):
        raise ValueError("priority must be low, normal, high, or P1–P5")
    organization = service()
    objective = organization.create_objective(title, description, priority,
                                                     idempotency_key=key, acceptance_criteria=params.get("acceptanceCriteria"),
                                                     delivery_mode=params.get("deliveryMode", "source_project"), required_checks=params.get("requiredChecks"),
                                                     executive_id=_organization_text(params, "executiveId", 64, optional=True),
                                                     manager_id=_organization_text(params, "managerId", 64, optional=True),
                                                     project_ids=params.get("projectIds"))
    return {"objective": objective, "snapshot": _organization_snapshot(organization)}


@_organization_method("organization.previewReplacement")
def _(params, service):
    _organization_params(params, {"id"})
    return service().store.preview_replacement(_organization_text(params, "id", 128))


@_organization_method("organization.replaceObjective")
def _(params, service):
    _organization_params(params, {"sourceId", "sourceVersion", "title", "description", "acceptanceCriteria", "confirmed"})
    organization = service()
    objective = organization.replace_objective(
        _organization_text(params, "sourceId", 128), _organization_text(params, "sourceVersion", 64),
        _organization_text(params, "title", 500), _organization_text(params, "description", 30000),
        params.get("acceptanceCriteria"), confirmed=params.get("confirmed"))
    return {"objective": objective, "snapshot": _organization_snapshot(organization)}


@_organization_method("organization.cancel")
def _(params, service):
    _organization_params(params, {"id"})
    objective_id = _organization_text(params, "id", 128)
    organization = service()
    organization.cancel(objective_id)
    return _organization_snapshot(organization)


@_organization_method("organization.history")
def _(params, service):
    _organization_params(params, {"query", "state", "before", "limit"})
    return service().store.history(query=params.get("query", ""), state=params.get("state", "archived"),
                                   before=params.get("before"), limit=params.get("limit", 25))


@_organization_method("organization.historyObjective")
def _(params, service):
    _organization_params(params, {"id"})
    return service().store.history_objective(_organization_text(params, "id", 128))


@_organization_method("organization.archive")
def _(params, service):
    _organization_params(params, {"id", "archived", "expectedRevision", "idempotencyKey"})
    organization = service()
    organization.set_objective_archived(_organization_text(params, "id", 128), params.get("archived"),
        expected_revision=params.get("expectedRevision"),
        idempotency_key=_organization_text(params, "idempotencyKey", 128))
    return _organization_snapshot(organization)


@_organization_method("organization.retry")
def _(params, service):
    _organization_params(params, {"id", "idempotencyKey"})
    request_id = _organization_text(params, "id", 128)
    key = _organization_text(params, "idempotencyKey", 128)
    organization = service()
    organization.retry(request_id, idempotency_key=key)
    return _organization_snapshot(organization)


@_organization_method("organization.setPaused")
def _(params, service):
    from eidolon_cli.profiles import get_active_profile_name
    _organization_params(params, {"id", "paused", "expectedRevision", "idempotencyKey"})
    organization = service()
    receipt = organization.store.set_objective_paused(
        _organization_text(params, "id", 128), params.get("paused"),
        expected_revision=params.get("expectedRevision"),
        idempotency_key=_organization_text(params, "idempotencyKey", 128))
    return {"receipt": receipt, "profile": get_active_profile_name(),
            "objective": organization.store._objective_view(receipt["objectiveId"]),
            "snapshot": _organization_snapshot(organization)}


@_organization_method("organization.changePriority")
def _(params, service):
    _organization_params(params, {"id", "priority", "expectedRevision", "idempotencyKey"})
    organization = service()
    receipt = organization.store.change_objective_priority(
        _organization_text(params, "id", 128), params.get("priority"),
        expected_revision=params.get("expectedRevision"),
        idempotency_key=_organization_text(params, "idempotencyKey", 128))
    # Scheduling preference only: do not start, cancel or wake provider work.
    from eidolon_cli.profiles import get_active_profile_name
    return {"receipt": receipt, "profile": get_active_profile_name(),
            "objective": organization.store._objective_view(receipt["objectiveId"]),
            "snapshot": _organization_snapshot(organization)}


@_organization_method("organization.resolve")
def _(params, service):
    _organization_params(params, {"id", "action", "text", "evidenceIds", "requiredChecks", "acceptanceCriteria", "idempotencyKey"})
    organization = service()
    organization.resolve(_organization_text(params, "id", 128),
                         action=_organization_text(params, "action", 64),
                         text=_organization_text(params, "text", 12000, optional=True),
                         evidence_ids=params.get("evidenceIds"),
                         required_checks=params.get("requiredChecks"),
                         acceptance_criteria=params.get("acceptanceCriteria"),
                         idempotency_key=_organization_text(params, "idempotencyKey", 128))
    return _organization_snapshot(organization)


@_organization_method("organization.respond")
def _(params, service):
    _organization_params(params, {"id", "text", "decision", "idempotencyKey"})
    organization = service()
    organization.respond(_organization_text(params, "id", 128),
                         text=_organization_text(params, "text", 12000),
                         decision=_organization_text(params, "decision", 64),
                         idempotency_key=_organization_text(params, "idempotencyKey", 128))
    return _organization_snapshot(organization)


@_organization_method("organization.configure")
def _(params, service):
    _organization_params(params, {"configuration", "expectedGeneration", "idempotencyKey"})
    organization = service()
    organization.configure(params.get("configuration"), expected_generation=params.get("expectedGeneration"),
                           idempotency_key=_organization_text(params, "idempotencyKey", 128))
    return _organization_snapshot(organization)


@_organization_method("organization.evidence")
def _(params, service):
    _organization_params(params, {"id"})
    evidence_id = _organization_text(params, "id", 128)
    artifact = service().store.evidence(evidence_id)
    if artifact is None:
        raise ValueError("Evidence does not exist in this profile")
    return artifact


@_organization_method("organization.toolReceipts")
def _(params, service):
    _organization_params(params, {"id"})
    return service().store.tool_receipts(_organization_text(params, "id", 128))


@_organization_method("organization.executionAudit")
def _(params, service):
    _organization_params(params, {"id"})
    return service().store.execution_audit(_organization_text(params, "id", 128))


def register(server):
    bind_module(globals(), server, skip=("_",))

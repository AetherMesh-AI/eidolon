"""Explicit same-profile staffing policy; selection never provisions an identity."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from eidolon_cli.organization_config import OrganizationSettings


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESERVED_IDS = frozenset({"owner", "executive", "director", "manager", "reviewer"})
TYPED_REQUESTS = ("request.question", "request.decision", "request.permission")
SUPPORTED_AUTHORITY = ("answer.question", "answer.decision", "staff.manage")

_FIELDS = frozenset({"id", "name", "team", "capabilities", "enabled", "provider", "model", "tool_grants", "role", "manager_id", "responsibilities", "purpose", "scope", "authority", "managed_teams"})


@dataclass(frozen=True)
class OrganizationStaff:
    id: str
    name: str
    team: str
    capabilities: tuple[str, ...]
    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    tool_grants: tuple[str, ...] = ()
    role: str = "Worker"
    manager_id: str = "manager"
    responsibilities: tuple[str, ...] = ()
    purpose: str = ""
    scope: str = ""
    authority: tuple[str, ...] = ()
    managed_teams: tuple[str, ...] = ()


def _text(value, field, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{field} must be nonempty text of at most {maximum} characters")
    return value.strip()


def parse_roster(raw, settings: OrganizationSettings) -> tuple[OrganizationStaff, ...]:
    """Parse definitions only; absent resources stay unavailable without being installed."""
    from eidolon_cli.organization_config import SUPPORTED_WORK_CAPABILITIES, SUPPORTED_TOOL_GRANTS

    if not isinstance(raw, list) or len(raw) > settings.max_members:
        raise ValueError(f"organization.roster must be a list of at most {settings.max_members} persistent staff entries (max_members)")
    result = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - _FIELDS:
            raise ValueError("organization.roster entries contain unsupported fields")
        ident = entry.get("id")
        if (not isinstance(ident, str) or not _ID.fullmatch(ident)
                or ident in _RESERVED_IDS or ident.startswith("reviewer-") or ident in seen):
            raise ValueError("organization.roster ids must be unique canonical staff ids, not reserved authority ids")
        name = _text(entry.get("name"), f"organization.roster.{ident}.name", 100)
        team = _text(entry.get("team", settings.team), f"organization.roster.{ident}.team", 64)
        role = entry.get("role", "Worker")
        defaults = {"Worker": (), "Manager": ("request.plan", "request.integrate", "request.hire"),
                    "Executive": ("request.decompose", "request.accept")}
        routes = {"Worker": (*SUPPORTED_WORK_CAPABILITIES, *TYPED_REQUESTS),
                  "Manager": (*defaults["Manager"], *TYPED_REQUESTS),
                  "Executive": (*defaults["Executive"], "request.hire", *TYPED_REQUESTS)}
        if not isinstance(role, str) or role not in routes:
            raise ValueError(f"organization.roster.{ident}.role must be Executive, Manager or Worker")
        manager_id = entry.get("manager_id", {"Worker": "manager", "Manager": "executive", "Executive": "owner"}[role])
        if not isinstance(manager_id, str) or not _ID.fullmatch(manager_id) or manager_id == ident:
            raise ValueError(f"organization.roster.{ident}.manager_id must identify its persistent leader")
        responsibilities = entry.get("responsibilities", [])
        if not isinstance(responsibilities, list) or len(responsibilities) > 12:
            raise ValueError(f"organization.roster.{ident}.responsibilities must be a list of at most 12 scoped duties")
        responsibilities = tuple(_text(value, "Responsibility", 500) for value in responsibilities)
        purpose = entry.get("purpose", "")
        if purpose:
            purpose = _text(purpose, "Agent purpose", 3000)
        elif purpose != "":
            raise ValueError("Agent purpose must be text")
        scope = entry.get("scope", "")
        if scope:
            scope = _text(scope, "Agent scope", 3000)
        elif scope != "":
            raise ValueError("Agent scope must be text")
        authority = entry.get("authority", [])
        if (not isinstance(authority, list)
                or any(value not in SUPPORTED_AUTHORITY for value in authority)):
            raise ValueError("Agent authority must select answer.question, answer.decision or staff.manage")
        if "staff.manage" in authority and role == "Worker":
            raise ValueError("Staff management authority requires a Manager or Executive")
        managed_teams = entry.get("managed_teams", [])
        if not isinstance(managed_teams, list) or len(managed_teams) > 64:
            raise ValueError("Agent managed_teams must be a list of at most 64 teams")
        managed_teams = tuple(dict.fromkeys(_text(value, "Managed team", 64) for value in managed_teams))
        if managed_teams and "staff.manage" not in authority:
            raise ValueError("Agent managed_teams requires explicit staff.manage authority")
        capabilities = entry.get("capabilities", list(defaults[role]))
        if (not isinstance(capabilities, list)
                or any(value not in routes[role] for value in capabilities)):
            raise ValueError(f"organization.roster.{ident}.capabilities must select supported capabilities for its role")
        grants = entry.get("tool_grants", [])
        if (not isinstance(grants, list) or any(value not in SUPPORTED_TOOL_GRANTS for value in grants)
                or not set(grants).issubset(settings.tool_grants)):
            raise ValueError(f"organization.roster.{ident}.tool_grants must be a subset of organization.tool_grants")
        if role != "Worker" and grants:
            raise ValueError("Organization leaders remain tool-free; grants belong to scoped workers")
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError(f"organization.roster.{ident}.enabled must be a boolean")
        provider, model = entry.get("provider"), entry.get("model")
        if (provider is None) != (model is None):
            raise ValueError(f"organization.roster.{ident} provider and model overrides must be configured together")
        if provider is not None:
            provider = _text(provider, f"organization.roster.{ident}.provider", 100)
            model = _text(model, f"organization.roster.{ident}.model", 300)
        result.append(OrganizationStaff(ident, name, team, tuple(dict.fromkeys(capabilities)),
                                        enabled, provider, model, tuple(dict.fromkeys(grants)),
                                        role, manager_id, responsibilities, purpose, scope,
                                        tuple(dict.fromkeys(authority)), managed_teams))
        seen.add(ident)
    roles = {"owner": "Owner", "executive": "Executive", "director": "Manager", "manager": "Manager",
             **{staff.id: staff.role for staff in result}}
    for staff in result:
        expected = {"Executive": "Owner", "Manager": "Executive", "Worker": "Manager"}[staff.role]
        if roles.get(staff.manager_id) != expected:
            raise ValueError(f"organization.roster.{staff.id}.manager_id must identify a {expected}")
    return tuple(result)


def configured_staff(settings: OrganizationSettings) -> tuple[OrganizationStaff, ...]:
    """Persistent definitions are independent of concurrent execution capacity."""
    if settings.roster is not None:
        return settings.roster
    return tuple(OrganizationStaff(f"worker-{index}", f"Worker {index}", settings.team,
                                   settings.capabilities, tool_grants=tuple(tool for tool in settings.tool_grants if tool == 'read_file'))
                 for index in range(1, min(settings.max_workers, settings.max_members) + 1))


def configured_workers(settings: OrganizationSettings) -> tuple[OrganizationStaff, ...]:
    """Existing worker identities, excluding their persistent organizational leaders."""
    return tuple(staff for staff in configured_staff(settings) if staff.role == "Worker")


def staff_unavailability(staff: OrganizationStaff | None, settings: OrganizationSettings,
                        request_type: str | None = None) -> str | None:
    """Explain policy ineligibility; provider/tool reachability is checked at execution."""
    if staff is None:
        return "The configured staff entry is missing; restore or replace its explicit roster definition."
    if not staff.enabled:
        return f"Configured staff {staff.name} is disabled."
    if not set(staff.tool_grants).issubset(settings.tool_grants):
        return f"Configured staff {staff.name} requests tools outside the current organization grant."
    if request_type is None:
        reasons = [staff_unavailability(staff, settings, kind) for kind in staff.capabilities]
        if any(reason is None for reason in reasons):
            return None
        return next(iter(reasons), f"Configured staff {staff.name} has no enabled work capabilities.")
    if request_type not in staff.capabilities:
        return f"Configured staff {staff.name} does not accept {request_type}."
    if staff.role == "Worker" and request_type.startswith("work.") and request_type not in settings.capabilities:
        return f"The organization has not enabled {request_type}."
    if request_type in {"work.inspect", "work.edit"}:
        if "read_file" not in settings.tool_grants or "read_file" not in staff.tool_grants:
            return f"Configured staff {staff.name} needs an explicit read_file grant for {request_type}."
        if not settings.read_roots:
            return f"{request_type} needs an explicit organization.read_roots configuration."
    return None


def select_staff_activation(settings: OrganizationSettings, active_ids: Iterable[str],
                            desired_total: int,
                            required_routes: list[tuple[str, str]] | None = None) -> tuple[OrganizationStaff, ...]:
    """Activate existing identities to cover routes; never allocate disposable slots.

    The explicit roster's headcount does not constrain execution concurrency.
    A bounded greedy cover over at most 64 identities avoids exponential subset
    search while retaining exact capability/team and permission checks.
    """
    roster = configured_workers(settings)
    capacity = len(roster)
    if type(desired_total) is not int or not 1 <= desired_total <= capacity:
        raise ValueError(f"Requested staffing exceeds configured worker capacity ({capacity}); no generic worker was created")
    active = set(active_ids)
    routes = tuple(dict.fromkeys(required_routes or ()))
    available = tuple(staff for staff in roster if staff_unavailability(staff, settings) is None)

    def covers(staff, route):
        team, request_type = route
        return staff.team == team and staff_unavailability(staff, settings, request_type) is None

    for route in routes:
        if any(covers(staff, route) for staff in available):
            continue
        team, request_type = route
        reasons = [staff_unavailability(staff, settings, request_type) for staff in roster
                   if staff.team == team and request_type in staff.capabilities]
        reason = next((reason for reason in reasons if reason), "No configured staff accepts this team and request type.")
        raise ValueError(f"No eligible configured staff for {request_type} in team {team}. {reason}")
    current = tuple(staff for staff in available if staff.id in active)
    uncovered = {route for route in routes if not any(covers(staff, route) for staff in current)}
    inactive = [staff for staff in available if staff.id not in active]
    chosen = []
    while uncovered:
        best = max(inactive, key=lambda staff: sum(covers(staff, route) for route in uncovered))
        chosen.append(best)
        inactive.remove(best)
        uncovered = {route for route in uncovered if not covers(best, route)}
    slots = max(len(chosen), desired_total - len(current))
    additions = tuple((*chosen, *inactive)[:slots])
    if len(current) + len(additions) < desired_total:
        raise ValueError(f"Requested {desired_total} workers, but only {len(current) + len(additions)} configured enabled staff are available; no generic worker was created.")
    return additions

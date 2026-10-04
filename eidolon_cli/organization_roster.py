"""Explicit same-profile staffing policy; selection never provisions an identity."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from eidolon_cli.organization_config import OrganizationSettings


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESERVED_IDS = frozenset({"owner", "executive", "director", "manager", "reviewer"})
_FIELDS = frozenset({"id", "name", "team", "capabilities", "enabled", "provider", "model", "tool_grants"})


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


def _text(value, field, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{field} must be nonempty text of at most {maximum} characters")
    return value.strip()


def parse_roster(raw, settings: OrganizationSettings) -> tuple[OrganizationStaff, ...]:
    """Parse definitions only; absent resources stay unavailable without being installed."""
    from eidolon_cli.organization_config import SUPPORTED_WORK_CAPABILITIES, SUPPORTED_TOOL_GRANTS

    if not isinstance(raw, list) or len(raw) > 8:
        raise ValueError("organization.roster must be a list of at most 8 configured staff entries")
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
        capabilities = entry.get("capabilities", [])
        if (not isinstance(capabilities, list)
                or any(value not in SUPPORTED_WORK_CAPABILITIES for value in capabilities)):
            raise ValueError(f"organization.roster.{ident}.capabilities must select supported work capabilities")
        grants = entry.get("tool_grants", [])
        if (not isinstance(grants, list) or any(value not in SUPPORTED_TOOL_GRANTS for value in grants)
                or not set(grants).issubset(settings.tool_grants)):
            raise ValueError(f"organization.roster.{ident}.tool_grants must be a subset of organization.tool_grants")
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
                                        enabled, provider, model, tuple(dict.fromkeys(grants))))
        seen.add(ident)
    return tuple(result)


def configured_workers(settings: OrganizationSettings) -> tuple[OrganizationStaff, ...]:
    """Explicit empty staffing stays empty; only absent configuration uses logical slots."""
    if settings.roster is not None:
        return settings.roster
    return tuple(OrganizationStaff(f"worker-{index}", f"Logical worker {index}", settings.team,
                                   settings.capabilities, tool_grants=tuple(tool for tool in settings.tool_grants if tool == 'read_file'))
                 for index in range(1, settings.max_workers + 1))


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
    if request_type not in settings.capabilities:
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
    """Return newly activated definitions, covering exact plan routes within the total cap.

    The roster has at most eight entries, so bounded subset search avoids greedy
    selection taking two specialists when one configured generalist covers both routes.
    Existing active identities keep occupying capacity until the store retires them.
    """
    if type(desired_total) is not int or not 1 <= desired_total <= settings.max_workers:
        raise ValueError("Staffing request exceeds configured worker capacity; owner intervention is required")
    active = set(active_ids)
    routes = tuple(dict.fromkeys(required_routes or ()))
    roster = configured_workers(settings)
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
    uncovered = tuple(route for route in routes if not any(covers(staff, route) for staff in current))
    inactive = tuple(staff for staff in available if staff.id not in active)
    if len(active) > settings.max_workers:
        raise ValueError("Active staffing exceeds current worker capacity; retire excess staff before hiring")
    capacity = settings.max_workers - len(active)
    chosen = None
    for count in range(min(capacity, len(inactive)) + 1):
        chosen = next((group for group in combinations(inactive, count)
                       if all(any(covers(staff, route) for staff in group) for route in uncovered)), None)
        if chosen is not None:
            break
    if chosen is None:
        demand = ", ".join(f"{kind} in team {team}" for team, kind in uncovered)
        raise ValueError(f"Configured staffing capacity cannot cover {demand}; owner intervention is required.")
    # A later objective may need a new team while existing staff stay active.
    # The manager's count is a minimum, never permission to exceed max_workers.
    slots = max(len(chosen), desired_total - len(active))
    selected = {staff.id for staff in chosen}
    additions = (*chosen, *(staff for staff in inactive if staff.id not in selected))[:slots]
    if len(active) + len(additions) < desired_total:
        raise ValueError(f"Requested {desired_total} workers, but only {len(active) + len(additions)} configured enabled staff are available; no generic worker was created.")
    return tuple(additions)

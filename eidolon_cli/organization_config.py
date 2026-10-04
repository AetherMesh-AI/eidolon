"""Bounded policy for the organization control plane, independent of providers."""

from dataclasses import dataclass
from pathlib import Path

from eidolon_cli.organization_roster import OrganizationStaff, parse_roster


WORK_CAPABILITIES = ("work.draft", "work.analyze")
SUPPORTED_WORK_CAPABILITIES = (*WORK_CAPABILITIES, "work.inspect", "work.edit")
SUPPORTED_TOOL_GRANTS = ("read_file", "patch")


@dataclass(frozen=True)
class OrganizationSettings:
    max_workers: int = 2
    max_inflight: int = 2
    max_tasks: int = 12
    max_open_objectives: int = 20
    max_attempts: int = 2
    max_revisions: int = 2
    lease_seconds: int = 45
    timeout_seconds: int = 180
    team: str = "general"
    capabilities: tuple[str, ...] = WORK_CAPABILITIES
    roster: tuple[OrganizationStaff, ...] | None = None
    tool_grants: tuple[str, ...] = ()
    read_roots: tuple[str, ...] = ()
    max_tool_calls: int = 8
    max_tool_result_chars: int = 12000

    @classmethod
    def from_config(cls, config: dict):
        raw = config.get("organization", {})
        if not isinstance(raw, dict):
            raise ValueError("organization must be a configuration object")
        limits = {"max_workers": (1, 8), "max_inflight": (1, 4), "max_tasks": (1, 24),
                  "max_open_objectives": (1, 100), "max_attempts": (1, 3),
                  "max_revisions": (0, 3), "lease_seconds": (15, 300),
                  "timeout_seconds": (30, 600), "max_tool_calls": (1, 20),
                  "max_tool_result_chars": (1000, 20000)}
        values = {}
        for key, (minimum, maximum) in limits.items():
            value = raw.get(key, getattr(cls, key))
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"organization.{key} must be between {minimum} and {maximum}")
            values[key] = value
        team = raw.get("team", "general")
        if not isinstance(team, str) or not team.strip() or len(team) > 64:
            raise ValueError("organization.team must be a nonempty name of at most 64 characters")
        capabilities = raw.get("capabilities", list(WORK_CAPABILITIES))
        if not isinstance(capabilities, list) or any(v not in SUPPORTED_WORK_CAPABILITIES for v in capabilities):
            raise ValueError("organization.capabilities must select supported work capabilities")
        grants = raw.get("tool_grants", [])
        if not isinstance(grants, list) or any(v not in SUPPORTED_TOOL_GRANTS for v in grants):
            raise ValueError("organization.tool_grants must select supported tools: read_file or managed-workspace patch")
        roots = raw.get("read_roots", [])
        if (not isinstance(roots, list) or len(roots) > 8 or any(
            not isinstance(root, str) or not root.strip() or len(root) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in root)
            or not Path(root).is_absolute() for root in roots
        )):
            raise ValueError("organization.read_roots must contain at most 8 bounded absolute filesystem paths")
        settings = cls(**values, team=team.strip(), capabilities=tuple(dict.fromkeys(capabilities)),
                       tool_grants=tuple(dict.fromkeys(grants)), read_roots=tuple(dict.fromkeys(roots)))
        if "roster" in raw:
            from dataclasses import replace
            settings = replace(settings, roster=parse_roster(raw["roster"], settings))
        return settings


def from_config(config: dict) -> OrganizationSettings:
    return OrganizationSettings.from_config(config)

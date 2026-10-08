"""Bounded policy for the organization control plane, independent of providers."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from eidolon_cli.organization_roster import OrganizationStaff, parse_roster
from eidolon_cli.organization_budget import OrganizationModelCost, money, parse_model_costs


WORK_CAPABILITIES = ("work.draft", "work.analyze")
SUPPORTED_WORK_CAPABILITIES = (*WORK_CAPABILITIES, "work.inspect", "work.edit")
SUPPORTED_TOOL_GRANTS = ("read_file", "list_files", "search_files", "patch", "run_tests", "integrate_source")


@dataclass(frozen=True)
class OrganizationSettings:
    max_workers: int = 2
    max_inflight: int = 2
    max_members: int = 16
    max_request_depth: int = 4
    max_requests_per_stage: int = 4
    max_tasks: int = 12
    max_open_objectives: int = 20
    max_attempts: int = 2
    max_revisions: int = 2
    max_replans: int = 2
    max_stages: int = 120
    max_owner_resolutions: int = 12
    max_output_tokens: int = 8000
    max_context_tokens: int = 128000
    max_model_calls: int = 120
    max_total_tokens: int = 8000000
    objective_timeout_seconds: int = 86400
    max_cost_usd: str | None = None
    model_costs: tuple[OrganizationModelCost, ...] = ()
    lease_seconds: int = 45
    timeout_seconds: int = 180
    team: str = "general"
    communication_scope: str = "collaborators"
    capabilities: tuple[str, ...] = WORK_CAPABILITIES
    roster: tuple[OrganizationStaff, ...] | None = None
    tool_grants: tuple[str, ...] = ()
    read_roots: tuple[str, ...] = ()
    project_grants: tuple = ()
    projects: tuple = ()
    # Internal provenance, never read from YAML or accepted as owner grants.
    ledger_project_ids: tuple[str, ...] = ()
    project_registry_conflicts: tuple[str, ...] = ()
    max_project_runs: int = 4
    max_tool_calls: int = 8
    max_tool_result_chars: int = 12000

    @classmethod
    def from_config(cls, config: dict):
        raw = config.get("organization", {})
        if not isinstance(raw, dict):
            raise ValueError("organization must be a configuration object")
        limits = {"max_workers": (1, 8), "max_inflight": (1, 4), "max_members": (1, 64),
                  "max_request_depth": (1, 8), "max_requests_per_stage": (1, 8), "max_tasks": (1, 24),
                  "max_open_objectives": (1, 100), "max_attempts": (1, 3),
                  "max_revisions": (0, 3), "max_replans": (0, 3), "max_stages": (4, 300),
                  "max_owner_resolutions": (1, 24), "max_output_tokens": (256, 16000), "lease_seconds": (15, 300),
                  "timeout_seconds": (30, 600), "max_tool_calls": (1, 20),
                  "max_context_tokens": (4096, 2000000), "max_model_calls": (1, 1000),
                  "max_total_tokens": (4096, 1000000000), "objective_timeout_seconds": (60, 604800),
                  "max_tool_result_chars": (1000, 20000), "max_project_runs": (1, 12)}
        values = {}
        for key, (minimum, maximum) in limits.items():
            default = getattr(cls, key)
            if key == "max_members" and isinstance(raw.get("roster"), list):
                default = max(default, len(raw["roster"]))
            value = raw.get(key, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"organization.{key} must be between {minimum} and {maximum}")
            values[key] = value
        values["max_cost_usd"] = money(raw["max_cost_usd"], "organization.max_cost_usd", allow_zero=False) if raw.get("max_cost_usd") is not None else None
        values["model_costs"] = parse_model_costs(raw.get("model_costs", []))
        if values["max_context_tokens"] <= values["max_output_tokens"] + 2048:
            raise ValueError("organization.max_context_tokens must leave input space beyond output and protocol reserves")
        communication = raw.get("communication_scope", "collaborators")
        if not isinstance(communication, str) or communication not in {"disabled", "same_team", "collaborators"}:
            raise ValueError("organization.communication_scope must be disabled, same_team or collaborators")
        values["communication_scope"] = communication
        team = raw.get("team", "general")
        if not isinstance(team, str) or not team.strip() or len(team) > 64:
            raise ValueError("organization.team must be a nonempty name of at most 64 characters")
        capabilities = raw.get("capabilities", list(WORK_CAPABILITIES))
        if not isinstance(capabilities, list) or any(v not in SUPPORTED_WORK_CAPABILITIES for v in capabilities):
            raise ValueError("organization.capabilities must select supported work capabilities")
        grants = raw.get("tool_grants", [])
        if not isinstance(grants, list) or any(v not in SUPPORTED_TOOL_GRANTS for v in grants):
            raise ValueError("organization.tool_grants must select supported tools: read_file, list_files, search_files, patch, run_tests or integrate_source")
        roots = raw.get("read_roots", [])
        if (not isinstance(roots, list) or len(roots) > 8 or any(
            not isinstance(root, str) or not root.strip() or len(root) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in root)
            or not Path(root).is_absolute() for root in roots
        )):
            raise ValueError("organization.read_roots must contain at most 8 bounded absolute filesystem paths")
        from eidolon_cli.organization_project_config import parse_project_grants
        values["project_grants"] = parse_project_grants(raw.get("project_grants", []), len(roots))
        from eidolon_cli.organization_projects import parse_projects
        values["projects"] = parse_projects(raw.get("projects", []), roots, values["project_grants"])
        settings = cls(**values, team=team.strip(), capabilities=tuple(dict.fromkeys(capabilities)),
                       tool_grants=tuple(dict.fromkeys(grants)), read_roots=tuple(roots))
        if "roster" in raw:
            from dataclasses import replace
            settings = replace(settings, roster=parse_roster(raw["roster"], settings))
        return settings


def from_config(config: dict) -> OrganizationSettings:
    return OrganizationSettings.from_config(config)



def profile_organization(home):
    """One strict, uncached read with profile expansion and managed precedence."""
    import yaml
    from eidolon_cli.config_primitives import (
        InvalidUserConfigError, _CONFIG_LOCK, _expand_env_vars, _merge_managed_overlay,
    )
    with _CONFIG_LOCK:
        try:
            try:
                content = (Path(home) / 'config.yaml').read_bytes()
            except FileNotFoundError:
                content = b''
            raw = yaml.safe_load(content)
            if raw is not None and not isinstance(raw, dict):
                raise ValueError('Profile configuration must be a mapping')
            expanded, _ = _merge_managed_overlay(_expand_env_vars(
                {'organization': (raw or {}).get('organization', {})}))
            if not isinstance(expanded.get('organization'), dict):
                raise ValueError('Organization configuration must be a mapping')
            return expanded
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
            raise InvalidUserConfigError(
                'Organization configuration is invalid; repair the profile configuration before resuming work') from None


class ProfileSourcesRequired(RuntimeError):
    """A cold enabled gateway must hydrate sources before policy validation."""


def load_profile_configuration(home, *, require_sources=False):
    """File-backed adopters call this while holding the ledger write lock.

    Bypass the general loader's cache, fallback and config-repair writes.
    """
    config = profile_organization(home)
    enabled = config['organization'].get('gateway_enabled') is True
    if enabled and require_sources:
        raise ProfileSourcesRequired()
    return OrganizationSettings.from_config(config), enabled


@contextmanager
def profile_configuration_scope(home):
    """Refresh profile-local expansion inputs after acquiring the ledger writer.

    Ambient process configuration retains its existing semantics. Scoped hosts
    must replace their old snapshot, including keys removed from the local file.
    Existing external-source values are reused; hydration never occurs here.
    """
    from agent.secret_scope import (build_profile_secret_scope, current_secret_scope,
                                    is_secret_scope_required, reset_secret_scope, set_secret_scope)
    token = None
    if current_secret_scope() is not None or is_secret_scope_required():
        token = set_secret_scope(build_profile_secret_scope(home))
    try:
        yield
    finally:
        if token is not None:
            reset_secret_scope(token)

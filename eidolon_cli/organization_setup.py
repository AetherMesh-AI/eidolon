"""Configuration-only first-use guidance, never a provider readiness probe.

Only bounded counts, booleans and known reason codes cross the UI boundary.
Provider resolution may refresh credentials or discover external identities, so
it must remain in execution, not in a quiet organization snapshot poll.
"""
from __future__ import annotations

from eidolon_cli.organization_executor import _REQUEST_TYPES


def setup_view(config: dict, agents: list[dict]) -> dict:
    model = config.get('model')
    model = model if isinstance(model, dict) else {}
    organization = config.get('organization')
    organization = organization if isinstance(organization, dict) else {}
    # Include built-in planning/review identities, not only configurable workers.
    # Control executors and the human owner never resolve a model provider.
    staff = [member for member in agents if member.get('lifecycle') in {'active', 'available'}
             and _REQUEST_TYPES.intersection(member.get('capabilities', []))]
    inherited = sum(not member.get('provider') for member in staff)
    # Warn about the explicit opt-in to the unsupported PR15 transport. A
    # resolver fallback may select another transport, so this is configuration
    # guidance, never a verdict about the effective route or account.
    # Aliases may name custom providers, so only the canonical built-in is
    # definite without credential-aware resolution. Execution still enforces
    # _guard_route against the actual resolved route.
    app_server = str(model.get('openai_runtime') or '').strip().lower() == 'codex_app_server'
    blocked = app_server and any(
        str(member.get('provider') or model.get('provider') or '').strip().lower()
        == 'openai-codex' for member in staff)
    return {
        'version': 1,
        'provider': {
            'status': 'warning' if blocked else 'unchecked',
            'blockers': ['codex_app_server'] if blocked else [],
            'inheritedMembers': inherited,
            'overriddenMembers': len(staff) - inherited,
        },
        'backgroundOptIn': organization.get('gateway_enabled') is True,
    }

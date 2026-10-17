"""Narrow ledger overrides below administrator policy, without file writes."""
from dataclasses import asdict, replace
import json

from eidolon_cli.organization_completion_reserve import COMPLETION_RESERVE_FIELDS
from eidolon_cli.organization_pacing import PACING_FIELDS
from eidolon_cli.organization_model_routing import ROUTING_FIELDS

BUDGET_FIELDS = ('max_model_calls', 'max_total_tokens', 'max_cost_usd',
                 'objective_timeout_seconds', 'max_context_tokens', 'max_output_tokens',
                 *COMPLETION_RESERVE_FIELDS, 'model_costs', *PACING_FIELDS)
EDITABLE_FIELDS = (*ROUTING_FIELDS, *BUDGET_FIELDS)
OWNER_POLICY_TABLES = {'organization_owner_budget_policy', 'organization_owner_policy_receipts'}
OWNER_POLICY_DDL = (
    '''CREATE TABLE IF NOT EXISTS organization_owner_budget_policy (
       id INTEGER PRIMARY KEY CHECK(id=1), configuration TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS organization_owner_policy_receipts (
       idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
       owner_id TEXT NOT NULL, input_hash TEXT NOT NULL, result_json TEXT NOT NULL)''',
    '''CREATE TRIGGER IF NOT EXISTS organization_owner_policy_receipts_immutable_update
       BEFORE UPDATE ON organization_owner_policy_receipts
       BEGIN SELECT RAISE(ABORT,'Owner policy receipt is immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS organization_owner_policy_receipts_immutable_delete
       BEFORE DELETE ON organization_owner_policy_receipts
       BEGIN SELECT RAISE(ABORT,'Owner policy receipt is immutable'); END''',
)
OWNER_POLICY_SCHEMA = ';\n'.join(OWNER_POLICY_DDL) + ';\n'


def managed_fields(*, write_locks=True):
    from eidolon_cli import managed_scope
    from eidolon_cli.config_primitives import managed_install_system
    from eidolon_constants import get_eidolon_home
    # Authority must observe administrator edits even if stat metadata was reused.
    managed_scope.invalidate_managed_cache()
    if write_locks and managed_install_system(get_eidolon_home()) is not None:
        return set(EDITABLE_FIELDS)
    keys = managed_scope.managed_config_keys()
    return {field for field in EDITABLE_FIELDS
            if any(key == 'organization' or key == f'organization.{field}'
                   or key.startswith(f'organization.{field}.') for key in keys)}


def validated_patch(settings, patch):
    """Reuse settings validation without restoring revoked roster tool grants."""
    from eidolon_cli.organization_config import OrganizationSettings
    raw = json.loads(json.dumps(asdict(settings)))
    raw.update(patch)
    raw['tool_grants'] = list(dict.fromkeys([*raw['tool_grants'],
        *(grant for staff in raw.get('roster') or () for grant in staff.get('tool_grants', []))]))
    if raw.get('roster') is None:
        raw.pop('roster', None)
    parsed = OrganizationSettings.from_config({'organization': raw})
    return replace(settings, **{key: getattr(parsed, key) for key in patch})


def resolve_owner_overrides(conn, base):
    locked = managed_fields(write_locks=False)
    from eidolon_cli import managed_scope
    from eidolon_cli.config_primitives import _expand_env_vars
    managed = _expand_env_vars(managed_scope.load_managed_config()).get('organization', {})
    enforced = {key: value for key, value in managed.items() if key in locked} if isinstance(managed, dict) else {}
    routing = conn.execute('SELECT configuration FROM organization_model_routing_config WHERE id=1').fetchone()
    patch = {}
    if routing is not None:
        saved = json.loads(routing[0])
        if not isinstance(saved, dict) or set(saved) != set(ROUTING_FIELDS):
            raise ValueError('Persisted model routing contains unsupported settings')
        patch.update({key: value for key, value in saved.items() if key not in locked})
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_owner_budget_policy'").fetchone():
        row = conn.execute('SELECT configuration FROM organization_owner_budget_policy WHERE id=1').fetchone()
        if row is not None:
            saved = json.loads(row[0])
            if not isinstance(saved, dict) or set(saved) - set(BUDGET_FIELDS):
                raise ValueError('Persisted budget policy contains unsupported settings')
            patch.update({key: value for key, value in saved.items() if key not in locked})
    patch.update(enforced)
    return validated_patch(base, patch) if patch else base

"""Reviewed starter membership for an unused, served organization.

No model output is authority. Reads never initialize execution, and creation only
changes persistent names/membership inside the existing owner transaction.
"""
from dataclasses import asdict
import json
import time

from eidolon_cli.organization_metadata import _uuid
from eidolon_cli.organization_owner_policy import (
    OwnerPolicyError, _connection, _hash, _identity, _schema, _source, _writer,
)
from eidolon_cli.organization_roster import _text, parse_roster


_MEMBER_FIELDS = {'id', 'name', 'role', 'manager_id', 'team', 'capabilities', 'purpose', 'responsibilities'}
_EXECUTIVE_FIELDS = {'name', 'purpose', 'responsibilities'}
_WARNINGS = [
    'Creates persistent identities only; no objective or background execution is started.',
    'Model routing, budgets, credentials and tool grants remain unchanged.',
    'Built-in staffing, planning, review and execution support identities are retained.',
    'Unused default worker identities are retained as retired history, not deleted.',
]


def validate_team(team, settings):
    if not isinstance(team, dict) or set(team) != {'executive', 'members'}:
        raise ValueError('Team requires exactly executive and members')
    executive = team['executive']
    if not isinstance(executive, dict) or set(executive) != _EXECUTIVE_FIELDS:
        raise ValueError('Executive requires exactly name, purpose and responsibilities; its identity and Fixed routing cannot change')
    executive = {**executive, 'name': _text(executive['name'], 'Executive name', 100),
                 'purpose': _text(executive['purpose'], 'Executive purpose', 3000)}
    duties = executive['responsibilities']
    if not isinstance(duties, list) or not 1 <= len(duties) <= 12:
        raise ValueError('Executive responsibilities requires 1–12 duties')
    executive['responsibilities'] = [_text(value, 'Responsibility', 500) for value in duties]
    members = team['members']
    if not isinstance(members, list) or not 2 <= len(members) <= min(12, settings.max_members):
        raise ValueError('Starter team requires 2–12 members within the configured member limit')
    for member in members:
        if not isinstance(member, dict) or set(member) != _MEMBER_FIELDS:
            raise ValueError('Team members require only id, name, role, manager_id, team, capabilities, purpose and responsibilities')
        _text(member['purpose'], 'Member purpose', 3000)
        if not isinstance(member['responsibilities'], list) or not 1 <= len(member['responsibilities']) <= 12:
            raise ValueError('Member responsibilities requires 1–12 duties')
        if member['role'] not in ('Manager', 'Worker'):
            raise ValueError('Starter members must be Managers or Workers; the root executive is fixed')
    roster = parse_roster(members, settings)
    roles = {staff.id: staff.role for staff in roster}
    names = [executive['name'].casefold(), *(staff.name.casefold() for staff in roster)]
    if len(set(names)) != len(names):
        raise ValueError('Team names must be unique, ignoring case')
    managers = [staff for staff in roster if staff.role == 'Manager']
    workers = [staff for staff in roster if staff.role == 'Worker']
    if not managers or not workers:
        raise ValueError('Starter team requires a manager and a worker')
    for staff in roster:
        if staff.role == 'Manager':
            if staff.manager_id != 'executive' or set(staff.capabilities) != {'request.plan', 'request.integrate'}:
                raise ValueError('Managers must report to executive and accept request.plan and request.integrate')
            if not any(worker.manager_id == staff.id for worker in workers):
                raise ValueError('Each starter manager must have at least one worker')
        elif roles.get(staff.manager_id) != 'Manager':
            raise ValueError('Workers must report to a manager in this team')
        elif not staff.capabilities or not set(staff.capabilities) <= {'work.draft', 'work.analyze'}:
            raise ValueError('Starter workers accept work.draft or work.analyze; tools require separate owner configuration')
        elif next(manager.team for manager in managers if manager.id == staff.manager_id) != staff.team:
            raise ValueError('Workers and their manager must share a routing team')
    canonical = {'executive': executive, 'members': [
        {key: value for key, value in json.loads(json.dumps(asdict(staff))).items() if key in _MEMBER_FIELDS}
        for staff in roster]}
    return canonical


def presets():
    def member(ident, name, role, manager, duties, capabilities):
        return dict(id=ident, name=name, role=role, manager_id=manager, team='general',
                    capabilities=capabilities, purpose=duties, responsibilities=[duties])
    return [{'id': 'research-writing', 'name': 'Research and writing', 'team': {
        'executive': {'name': 'Executive', 'purpose': 'Set direction and assess completed outcomes.',
                      'responsibilities': ['Decompose goals and independently accept outcomes.']},
        'members': [member('team-lead', 'Team lead', 'Manager', 'executive',
                           'Plan and integrate evidence-backed work.', ['request.plan', 'request.integrate']),
                    member('analyst', 'Analyst', 'Worker', 'team-lead',
                           'Analyze information supplied by the owner.', ['work.analyze']),
                    member('writer', 'Writer', 'Worker', 'team-lead',
                           'Draft clear deliverables from supplied context.', ['work.draft'])]}}]


def _fresh(conn, settings):
    if conn.execute('SELECT 1 FROM organization_team_setup').fetchone():
        return False
    if settings.roster is not None:
        return False
    # Even settled work or a conversation makes these identities non-disposable.
    for table in ('objectives', 'requests', 'agent_history', 'owner_chat_threads',
                  'organization_management_receipts', 'organization_configuration'):
        if conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
            return False
    if conn.execute('SELECT 1 FROM agent_context WHERE revision>0 LIMIT 1').fetchone():
        return False
    return True


def _readiness(home):
    from eidolon_cli.organization_config import profile_configuration_scope
    from eidolon_cli.config import load_config_readonly
    from eidolon_cli.organization_setup import setup_view
    from eidolon_cli.organization_owner_chat_executor import readiness_reason
    from eidolon_cli.providers import normalize_provider, custom_provider_aliases
    from eidolon_cli.config_providers import get_compatible_custom_providers
    with profile_configuration_scope(home):
        config = load_config_readonly(initialize_home=False)
        view = setup_view(config, [{'lifecycle': 'active', 'capabilities': ['request.decompose']}])
        generation_blocker = readiness_reason({}, config=config)
    model = config.get('model') or {}
    model = model if isinstance(model, dict) else {'default': model}
    blockers = list(view['provider']['blockers'])
    # Configuration-only capability diagnostics: these do not inspect login
    # state or imply that credentials are missing, valid, or authorized.
    raw_provider = str(model.get('provider') or '').strip().lower()
    custom_route = raw_provider == 'openai' and any(raw_provider in custom_provider_aliases(
        entry.get('name', ''), entry.get('provider_key', ''))
        for entry in get_compatible_custom_providers(config))
    # User-defined aliases can shadow built-ins; canonical built-ins still win.
    app_server = (not custom_route and raw_provider in {'openai', 'openai-api', 'openai-codex'}
                  and str(model.get('openai_runtime') or '').strip().lower() == 'codex_app_server')
    if generation_blocker or app_server:
        if normalize_provider(raw_provider) == 'openai-codex':
            blockers.append('subscription_generation_unsupported')
        if app_server and 'codex_app_server' not in blockers:
            blockers.append('codex_app_server')
        # Retain the broad code for older clients while newer clients explain
        # the specific route limitation instead of asking users to log in again.
        blockers.append('generation_route_unsupported')
    if not model.get('default'):
        blockers.append('model_not_configured')
    return {'status': 'blocked' if blockers else 'unchecked', 'blockers': blockers,
            'modelRouting': 'unchanged', 'backgroundEnabled': view['backgroundOptIn']}


def _generation_allowance(conn, settings, *, completing_key=None):
    """Passive local setup allowance; exact route/shared admission still wins."""
    from eidolon_cli.organization_team_budget import budget_view
    view = budget_view(conn, settings, completing_key=completing_key)
    result = {'status': view['status'], 'remainingCalls': view['remaining']['totalCalls']}
    if view['policyVersion'] in (2, 3):
        result.update(policyVersion=view['policyVersion'], remainingByOperation={
            'improveTeamInput': view['remaining']['improveCalls'],
            'generateTeam': view['remaining']['generationCalls']})
    if view['policyVersion'] == 3:
        result.update(mode='subscription_calls', setupUsage=view['setupUsage'],
                      requestTimeoutSeconds=view['requestTimeoutSeconds'],
                      hardTokenGuarantee=False, hardUsdGuarantee=False)
    return result


def _support(conn):
    return [dict(row) for row in conn.execute("SELECT id,name,role FROM agents WHERE id IN "
        "('director','manager','reviewer','control:project','control:apply') ORDER BY id")]


def _current(conn, home, expected_id):
    identity = _identity(conn, expected_id)
    policy = _schema(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'organization_team_setup', 'organization_team_generations'} <= tables:
        raise OwnerPolicyError('execution_not_initialized')
    settings, revision, _ = _source(home, conn)
    # Bind previews to existing identities, not just the configuration bytes.
    agents = [dict(row) for row in conn.execute('SELECT * FROM agents ORDER BY id')]
    revision = _hash([revision, identity, policy['generation'], agents])
    from eidolon_cli.organization_team_budget import read_policy
    setup_policy = read_policy(conn)
    if setup_policy is not None:
        revision = _hash([revision, setup_policy])
    return identity, policy, settings, revision


def _subscription_capability(home, transport):
    """Passive availability only; does not open a vault or discover models."""
    import sys
    from pathlib import Path
    from tui_gateway.organization_credential_transport import owner_home, owner_generation
    if sys.platform != 'darwin':
        return 'unsupported_platform'
    if owner_home(transport) != Path(home).resolve() or owner_generation(transport) is None:
        return 'local_owner_required'
    return 'available'


def team_setup(home, expected_id, *, subscription_transport=None):
    _uuid(expected_id, 'expectedOrganizationId')
    base = {'capabilityVersion': 1, 'inputImprovementVersion': 1, 'setupBudgetVersion': 2,
            'setupSubscriptionVersion': 1,
            'setupSubscriptionState': _subscription_capability(home, subscription_transport), 'presets': presets(), 'readiness': _readiness(home), 'supportAgents': []}
    try:
        with _connection(home) as conn:
            identity, policy, settings, revision = _current(conn, home, expected_id)
            result = {**base, 'state': 'available' if _fresh(conn, settings) else 'configured',
                      'organizationId': identity['organizationId'], 'sourceRevision': revision,
                      'supportAgents': _support(conn), 'generationAllowance': _generation_allowance(conn, settings)}
        if result['state'] == 'available' and result['generationAllowance'].get('policyVersion') == 3:
            from tui_gateway.methods_organization_setup_subscription import subscription_setup_readiness
            result['readiness'] = {**base['readiness'], **subscription_setup_readiness(
                subscription_transport, home, expected_id, revision)}
        return result
    except OwnerPolicyError as exc:
        if exc.reason not in {'unconfigured', 'identity_required', 'execution_not_initialized', 'unsupported'}:
            raise
        return {**base, 'state': 'unsupported' if exc.reason == 'unsupported' else 'unconfigured', 'reason': exc.reason}


def _preview(conn, home, params):
    identity, policy, settings, revision = _current(conn, home, params['expectedOrganizationId'])
    if params.get('sourceRevision') != revision:
        raise OwnerPolicyError('source_conflict')
    if not _fresh(conn, settings):
        raise OwnerPolicyError('organization_not_empty')
    from eidolon_cli import managed_scope
    from eidolon_cli.config_primitives import managed_install_system
    if managed_install_system(home) is not None or any(
            key == 'organization' or key == 'organization.roster' or key.startswith('organization.roster.')
            for key in managed_scope.managed_config_keys()):
        raise OwnerPolicyError('managed_configuration')
    team = validate_team(params.get('team'), settings)
    # Reuse the owner configuration parser, including project and model constraints.
    from eidolon_cli.organization_management import _validate_owner_config
    _validate_owner_config({'roster': team['members']}, settings)
    support = _support(conn)
    if {member['name'].casefold() for member in [team['executive'], *team['members']]} & {
            member['name'].casefold() for member in support}:
        raise ValueError('Team names must not duplicate retained support identity names')
    core = {'organizationId': identity['organizationId'], 'sourceRevision': revision,
            'team': team, 'executiveModelMode': 'execFixed', 'supportAgents': support}
    readiness = _readiness(home)
    assigned = {assignment.agent_id for assignment in settings.model_assignments}
    if assigned and any(member['id'] not in assigned for member in team['members']):
        # Existing routing deliberately permits legacy inheritance for unassigned
        # identities. Disclose that contract without granting catalogue routes.
        readiness['warnings'] = ['new_member_routes_unassigned']
    return ({'capabilityVersion': 1, **core, 'previewVersion': _hash(core),
             'readiness': readiness, 'generationAllowance': _generation_allowance(conn, settings),
             'warnings': list(_WARNINGS)}, policy, settings)


def preview_team(home, params):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    with _connection(home) as conn:
        return _preview(conn, home, params)[0]


def create_team(home, params):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    if params.get('confirmed') is not True:
        raise ValueError('Creating the reviewed team requires explicit confirmation')
    key = _text(params.get('idempotencyKey'), 'Idempotency key', 128)
    digest = _hash(params)
    with _connection(home, write=True) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        _schema(conn)
        prior = conn.execute('SELECT * FROM organization_team_setup WHERE id=1').fetchone()
        if prior:
            if prior['organization_id'] != identity['organizationId']:
                raise OwnerPolicyError('identity_conflict')
            if prior['idempotency_key'] != key or prior['input_hash'] != digest:
                raise OwnerPolicyError('idempotency_conflict')
            return json.loads(prior['result_json'])
        preview, policy, settings = _preview(conn, home, params)
        if params.get('previewVersion') != preview['previewVersion']:
            raise OwnerPolicyError('preview_conflict')
        store = _writer(conn, home, settings, policy)
        store.configure_organization({'roster': preview['team']['members']},
            expected_generation=policy['generation'], idempotency_key='team-setup:' + key)
        executive = preview['team']['executive']
        conn.execute("UPDATE agents SET name=? WHERE id='executive'", (executive['name'],))
        conn.execute("UPDATE agent_identity SET purpose=?,responsibilities=? WHERE agent_id='executive'",
                     (executive['purpose'], json.dumps(executive['responsibilities'])))
        result = {'capabilityVersion': 1, 'state': 'created', 'organizationId': identity['organizationId'],
                  'team': preview['team'], 'readiness': preview['readiness'],
                  'receipt': {'idempotencyKey': key, 'previewVersion': preview['previewVersion']}}
        conn.execute('INSERT INTO organization_team_setup VALUES (1,?,?,?,?,?)',
                     (identity['organizationId'], key, digest, json.dumps(result), time.time()))
        return result


def generate_team(home, params, *, cancel=None, subscription_transport=None):
    from eidolon_cli.organization_team_admission import generate_team as run
    return run(home, params, cancel=cancel, subscription_transport=subscription_transport)


def improve_team_input(home, params, *, cancel=None, subscription_transport=None):
    from eidolon_cli.organization_team_admission import improve_team_input as run
    return run(home, params, cancel=cancel, subscription_transport=subscription_transport)


def setup_budget_policy(home, expected_id):
    from eidolon_cli.organization_team_budget import budget_policy
    return budget_policy(home, expected_id)


def preview_setup_budget(home, params):
    from eidolon_cli.organization_team_budget import preview_budget
    return preview_budget(home, params)


def apply_setup_budget(home, params):
    from eidolon_cli.organization_team_budget import apply_budget
    return apply_budget(home, params)


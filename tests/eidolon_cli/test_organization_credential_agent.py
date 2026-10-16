"""Real scheduler leases and durable grants, with no OS credentials or provider."""
import json
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_credential_agent import scheduler_capsule, compose_credential_execution
from eidolon_cli.organization_credential_security import CredentialError
from eidolon_cli.organization_credential_service import owner_catalog, credential_agents


def fields(**extra):
    return {'service': 'github', 'label': 'eidolonReleases', 'type': 'token',
        'description': 'Synthetic metadata only', 'kind': 'secret', 'expiresAt': None, **extra}


@pytest.fixture
def leased(tmp_path):
    metadata = save_metadata(tmp_path / 'organization' / 'state.db', display_name='Synthetic organization',
        owner_display_name='Synthetic owner', expected_organization_id=None, expected_revision=None,
        idempotency_key='identity')['metadata']
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', OrganizationSettings())
    store.create_objective('Synthetic catalog work', idempotency_key='objective')
    from tests.organization_package_helpers import claim_after_decomposition
    planner = claim_after_decomposition(store)
    store.finish(planner, {'tasks': [{'title': 'Manage metadata', 'description': 'Synthetic metadata only',
        'type': 'work.draft', 'team': 'general', 'dependsOn': []}]})
    claim = store.claim_next()
    assert claim['type'] == 'work.draft'
    catalog, owner = owner_catalog(tmp_path, metadata['organizationId'], metadata['owner']['ownerId'])
    agent = next(row for row in credential_agents(tmp_path) if row['id'] == claim['agent_id'])
    entry = catalog.create(owner, fields(), operation_id='create')
    return tmp_path, store, claim, catalog, owner, agent, entry


def grant(leased, permissions, revision=0, operation='grant', scope=None):
    home, store, claim, catalog, owner, agent, entry = leased
    return catalog.configure_grant(owner, actor_id=agent['identityId'], scope=scope or entry['entryId'],
        permissions=permissions, bindings=[], expected_revision=revision, operation_id=operation)


def test_scheduler_issues_only_for_exact_identity_and_rechecks_current_grants(leased):
    home, store, claim, catalog, owner, agent, entry = leased
    assert scheduler_capsule(home, store, claim, lambda: False) is None
    # A display/roster ID never substitutes for durable identity authority.
    catalog.configure_grant(owner, actor_id=agent['id'], scope=entry['entryId'], permissions=['discover'],
        bindings=[], expected_revision=0, operation_id='wrong-actor')
    assert scheduler_capsule(home, store, claim, lambda: False) is None
    grant(leased, ['discover', 'edit_metadata'])
    capsule = scheduler_capsule(home, store, claim, lambda: False)
    assert capsule.principal.actor_id == agent['identityId'] and not capsule.principal.owner
    assert capsule.invoke('organization_credential_catalog', {})['entries'][0]['id'] == entry['entryId']
    edit = {'action': 'edit_metadata', 'entryId': entry['entryId'], 'expectedRevision': 1,
        'operationId': 'agent-edit', 'metadata': fields(description='Renamed context')}
    assert capsule.invoke('organization_credential_manage', edit)['revision'] == 2
    assert capsule.invoke('organization_credential_manage', edit)['revision'] == 2
    grant(leased, [], revision=1, operation='withdraw')
    assert capsule.invoke('organization_credential_catalog', {})['entries'] == []
    with pytest.raises(CredentialError, match='permission_denied'):
        capsule.invoke('organization_credential_manage', {**edit, 'expectedRevision': 2, 'operationId': 'after-withdraw'})
    assert catalog.discover(owner)['entries'][0]['revision'] == 2


def test_lease_cancellation_policy_and_identity_fences(leased):
    home, store, claim, catalog, owner, agent, entry = leased
    grant(leased, ['discover'])
    stopped = [False]
    capsule = scheduler_capsule(home, store, claim, lambda: stopped[0])
    stopped[0] = True
    with pytest.raises(CredentialError, match='lease_invalid'):
        capsule.invoke('organization_credential_catalog', {})
    stopped[0] = False
    with store._write() as conn:
        conn.execute('UPDATE requests SET token=? WHERE id=?', ('replacement-token', claim['id']))
    with pytest.raises(CredentialError, match='lease_invalid'):
        capsule.invoke('organization_credential_catalog', {})
    assert scheduler_capsule(home, store, {**claim, 'type': 'request.project_test'}, lambda: False) is None
    assert scheduler_capsule(home, store, {**claim, 'type': 'request.source_integrate'}, lambda: False) is None


def test_create_grants_exact_scope_no_self_grant_or_value_text(leased):
    home, store, claim, catalog, owner, agent, entry = leased
    scope = 'create:ssh:dl380:password:secret'
    grant(leased, ['create'], scope=scope)
    capsule = scheduler_capsule(home, store, claim, lambda: False)
    create = {'action': 'create', 'entryId': None, 'expectedRevision': None, 'operationId': 'agent-create',
        'metadata': fields(service='ssh', label='dl380', type='password')}
    result = capsule.invoke('organization_credential_manage', create)
    assert result['version'] == 0
    assert capsule.invoke('organization_credential_catalog', {})['entries'] == []
    with pytest.raises(CredentialError, match='permission_denied'):
        capsule.invoke('organization_credential_manage', {**create, 'operationId': 'broaden',
            'metadata': fields(service='ssh', label='other', type='password')})
    for extra in ({'value': 'SYNTHETIC_NEVER_ACCEPTED'}, {'permissions': ['use']}, {'actorId': 'owner'}):
        with pytest.raises(CredentialError, match='invalid_parameters'):
            capsule.invoke('organization_credential_manage', {**create, **extra})
    assert 'SYNTHETIC_NEVER_ACCEPTED' not in catalog.path.read_bytes().decode('latin1')


def test_tool_capsule_installs_exact_schemas_bounds_calls_and_sanitizes_errors(leased):
    home, store, claim, catalog, owner, agent, entry = leased
    grant(leased, ['discover', 'revoke'])
    capsule = scheduler_capsule(home, store, claim, lambda: False)
    execution = compose_credential_execution(None, {'_credentialCapsule': capsule})
    assert compose_credential_execution(None, {'_credentialCapsule': {'owner': True}}) is None
    checks = []
    runtime = SimpleNamespace(_organization_check=lambda: checks.append(1), api_mode='chat_completions')
    execution.install(runtime)
    execution.check_wire(runtime, {'tools': execution.schemas})
    call = SimpleNamespace(id='call', function=SimpleNamespace(name='organization_credential_catalog', arguments='{}'))
    output = execution._run_one(runtime, call, 'task')
    assert json.loads(output)['result']['entries'][0]['id'] == entry['entryId'] and len(checks) == 2
    call.function.arguments = '{SYNTHETIC_SECRET_BAD_JSON'
    assert 'SYNTHETIC' not in execution._run_one(runtime, call, 'task')
    call.function.arguments = '{}'
    grant(leased, [], revision=1, operation='withdraw')
    assert json.loads(execution._run_one(runtime, call, 'task'))['result']['entries'] == []
    execution.verify_completion()


@pytest.mark.linux_only
def test_real_agent_catalog_and_management_rounds_keep_value_out_of_model(leased, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import threading
    from eidolon_cli import config, organization_executor as executor
    from tests.eidolon_cli.test_organization_credentials import MemoryBackend, put
    home, store, claim, catalog, owner, agent, entry = leased
    backend = MemoryBackend()
    catalog.backend = backend
    secret = 'SYNTHETIC_REAL_AGENT_PRIVATE_e012'
    put(catalog, owner, entry, value=secret)
    monkeypatch.setattr('eidolon_cli.organization_credential_service.native_backend', lambda _: backend)
    grant(leased, ['discover', 'edit_metadata'])
    capsule = scheduler_capsule(home, store, claim, lambda: False)
    received = []
    final = {'summary': 'Updated catalog metadata', 'deliverable': 'The approved entry description was updated. Value contents were unavailable.'}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({'data': [{'id': 'organization-test'}]})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            if len(received) <= 2:
                name = 'organization_credential_catalog' if len(received) == 1 else 'organization_credential_manage'
                arguments = {} if len(received) == 1 else {'action': 'edit_metadata', 'entryId': entry['entryId'],
                    'expectedRevision': 2, 'operationId': 'real-agent-edit', 'metadata': fields(description='Reviewed synthetic description')}
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'credential-' + str(len(received)),
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                message, finish = {'role': 'assistant', 'content': json.dumps(final)}, 'stop'
            self.reply({'id': 'local-catalog', 'object': 'chat.completion', 'created': 1, 'model': 'organization-test',
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})
        def reply(self, value):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'local-test-only')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'organization-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
            'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}, 'approvals': {'mode': 'off'}}))
    config._LOAD_CONFIG_CACHE.clear()
    try:
        result = executor.execute({'type': 'work.draft'}, {'_credentialCapsule': capsule, 'timeoutSeconds': 20,
            'objective': {'title': 'Review metadata', 'description': 'Update the approved catalog description.'}}, threading.Event())
        assert {key: value for key, value in result.items() if key != 'usage'} == final
        assert len(received) == 3
        assert catalog.discover(owner)['entries'][0]['revision'] == 3
        wire = json.dumps(received)
        assert secret not in wire and secret[:20] not in wire and 'opaque-value' not in wire
        assert {tool['function']['name'] for tool in received[0]['tools']} == {
            'organization_credential_catalog', 'organization_credential_manage', 'organization_credential_replace'}
        assert any(message['role'] == 'tool' for message in received[-1]['messages'])
        assert result['usage'] == {'inputTokens': 90, 'outputTokens': 60}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        config._LOAD_CONFIG_CACHE.clear()

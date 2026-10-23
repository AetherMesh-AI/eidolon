"""Fail-closed native fixture transport; no provider or physical socket is used."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import httpx
import psutil
import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'apps/desktop/e2e/fresh-team-provider-guard.py'


def run_synchronous_mock_coroutine(coroutine):
    # The canonical synthetic branch performs no asynchronous I/O. Running it
    # directly avoids Windows event-loop socketpair fallback under the separate
    # physical-network backstop, without allowing any physical connection.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError('Synthetic transport unexpectedly attempted asynchronous I/O')


def real_sender_reservation(body):
    from eidolon_cli.organization_executor import _ToolFreeBoundary
    reservations = []
    sender = SimpleNamespace(provider='openai-api', model=body['model'], api_mode='chat_completions',
        _organization_prompt=body['messages'][-1]['content'], _organization_input_limit=13952,
        _organization_output_limit=8000, _organization_reserve=lambda **values: reservations.append(values))
    _ToolFreeBoundary._organization_reserve_call(sender, body)
    assert len(reservations) == 1
    return reservations[0]


def test_identity_guard_matches_reviewed_creation_guard():
    source = (HELPER.parent / 'organization-creation.spec.ts').read_text()
    original = source.split('const providerSendGuard = String.raw`', 1)[1].split('\n`', 1)[0]
    names = {'_fixture_identity_matches', '_fixture_live_identity'}
    functions = lambda text: {node.name: ast.dump(node, include_attributes=False)
        for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name in names}
    assert functions(HELPER.read_text()) == functions(original)
    assert set(functions(original)) == names


def test_electron_bootstrap_guards_first_renderer_and_only_fulfills_exact_font():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to validate the native Electron fixture bootstrap')
    source = (HELPER.parent / 'organization-fresh-team-setup.spec.ts').read_text()
    bootstrap = source.split('const electronNetworkGuard = String.raw`', 1)[1].split('\n`', 1)[0]
    font = 'https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap'
    assert font in (ROOT / 'apps/desktop/src/themes/presets.ts').read_text()
    # No Electron or network modules are available in this independent VM.
    # Every request must terminate in a local Response or the counted loopback
    # stub; any undeclared import or external forwarding fails the regression.
    script = r'''
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const events = {}, records = [], forwarded = []
let handler, before
const session = { protocol: { handle(scheme, fn) { assert.equal(scheme, 'https'); handler = fn } },
  webRequest: { onBeforeRequest(filter, fn) { assert.deepEqual(Array.from(filter.urls), ['http://*/*','https://*/*','ws://*/*','wss://*/*']); before = fn } } }
const electron = { app: { on(name, fn) { events[name] = fn } },
  net: { fetch(request, options) {
    assert.ok(['127.0.0.1','[::1]'].includes(new URL(request.url).hostname))
    assert.equal(options.bypassCustomProtocolHandlers, true)
    assert.equal(options.redirect, 'error')
    forwarded.push(request.url); return new Response('loopback stub')
  } } }
vm.runInNewContext(fs.readFileSync(0, 'utf8'), { Response, URL, WeakSet,
  process: { env: { EIDOLON_FRESH_TEAM_GUARD_DIR: '/synthetic' } },
  require(name) {
    if (name === 'electron') return electron
    if (name === 'node:fs') return { appendFileSync(path, value) { records.push({path, value: JSON.parse(value)}) } }
    if (name === 'node:path') return require('node:path')
    throw Error('Undeclared bootstrap import: ' + name)
  } })
assert.throws(() => events['web-contents-created']({}, { id: 0, session }), /before external-request guard/)
events['session-created'](session)
events['web-contents-created']({}, { id: 1, session })
const font = process.argv[1]
function permitted(url, method = 'GET') {
  let answer; before({url, method}, value => { answer = value }); return !answer.cancel
}
for (const url of ['https://example.invalid/', font + '&unexpected=1', 'http://localhost/', 'ws://api.openai.com/']) {
  assert.equal(permitted(url), false)
  assert.equal(handler({url, method:'GET'}).status, 403)
}
assert.equal(permitted(font, 'POST'), false)
assert.equal(handler({url:font, method:'POST'}).status, 403)
assert.equal(permitted(font), true)
assert.equal(handler({url:font, method:'GET'}).status, 200)
assert.equal(permitted('http://127.0.0.1:1234/'), true)
assert.equal(handler({url:'https://127.0.0.1:1234/', method:'GET'}).status, 200)
assert.equal(forwarded.length, 1)
assert.equal(records.filter(row => row.path.includes('browser-attempts')).length, 10)
assert.equal(records.filter(row => row.path.includes('browser-fonts')).length, 1)
assert.equal(records.filter(row => row.path.includes('browser-windows')).at(-1).value.guardedBeforeCreation, true)
'''
    subprocess.run([node, '-e', script, font], input=bootstrap, text=True, check=True, timeout=10)


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    home = root / 'profiles' / 'fresh'
    (home / 'organization').mkdir(parents=True)
    evidence = tmp_path / 'guard'
    evidence.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('EIDOLON_FRESH_TEAM_ROOT_HOME', str(root))
    monkeypatch.setenv('EIDOLON_FRESH_TEAM_GUARD_DIR', str(evidence))
    # Independent backstop under every function the helper wraps. A broken
    # fixture cannot turn a regression into a real external request.
    physical = []
    def no_physical(*args, **kwargs):
        physical.append(True)
        raise RuntimeError('unexpected physical transport')
    for name in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
        if not hasattr(socket.socket, name):
            continue
        monkeypatch.setattr(socket.socket, name, no_physical)
    for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'gethostbyaddr', 'getnameinfo'):
        monkeypatch.setattr(socket, name, no_physical)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_physical)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_physical)
    import eidolon_cli.banner as banner
    import eidolon_cli.inventory as inventory
    monkeypatch.setattr(banner, 'check_for_updates', banner.check_for_updates)
    monkeypatch.setattr(inventory, '_prewarm_pricing_async', inventory._prewarm_pricing_async)
    spec = importlib.util.spec_from_file_location('fresh_team_guard', HELPER)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    identity = {'organizationId': 'org-one', 'ownerId': 'owner-one', 'profile': 'fresh'}
    isolation = {**identity, 'isolationVersion': 1, 'credentialMode': 'profile-local-openai-api',
        'executionMode': 'tool-free-organization', 'supportedProviders': ['openai-api']}
    inputs = {'jobs': 'Review café drafts', 'goals': 'Keep original text', 'namingStyle': 'Ash names'}
    request = {'expectedOrganizationId': identity['organizationId'], 'sourceRevision': 'revision-one',
        'idempotencyKey': 'generation-one', **inputs}
    expected = {**identity, 'open': True, 'host': '127.0.0.1', 'port': 12345, 'home': str(home),
        'pid': os.getpid(), 'create_time': psutil.Process().create_time(),
        'descriptor': {'capabilityVersion': 1, 'state': 'ready',
            'scope': {'kind': 'served-profile', 'profile': 'fresh'}, 'isolation': isolation,
            'metadata': {'organizationId': 'org-one', 'owner': {'ownerId': 'owner-one'}}},
        'model': 'synthetic-model', 'key': 'synthetic-fresh-team-key-one-never-real',
        'inputs': inputs, 'request': request, 'team': {'executive': {'name': 'Ash'}, 'members': []}}
    entry = {'pid': expected['pid'], 'create_time': expected['create_time'],
        'purpose': 'serve', 'home': str(home), 'profile': '', 'host': expected['host'], 'port': expected['port']}
    (root / 'spawn-ledger.json').write_text(json.dumps([entry]))
    (home / '.organization-isolation.json').write_text(json.dumps(isolation))
    (home / '.env').write_text('OPENAI_API_KEY=' + expected['key'] + '\n')
    (home / 'config.yaml').write_text('model:\n  provider: openai-api\n  default: synthetic-model\n')
    permit = evidence / 'permit.json'
    permit.write_text(json.dumps(expected))
    path = home / 'organization' / 'state.db'
    body = {'model': expected['model'], 'stream': False, 'max_tokens': 8000, 'messages': [
        {'role': 'system', 'content': 'EIDOLON_STARTER_TEAM_V1'},
        {'role': 'user', 'content': json.dumps(inputs)}]}
    reserved = real_sender_reservation(body)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE organization_team_generations (idempotency_key TEXT, organization_id TEXT, '
            'input_hash TEXT, source_revision TEXT, status TEXT, dispatched INTEGER, reserved_tokens INTEGER, reserved_cost TEXT)')
        digest = hashlib.sha256(json.dumps({'operation': 'generateTeam', 'input': request},
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        conn.execute('INSERT INTO organization_team_generations VALUES (?,?,?,?,?,?,?,?)',
            ('generation-one', 'org-one', digest, 'revision-one', 'running', 1,
             reserved['input_limit'] + reserved['output_limit'], None))
        for table in ('organization_team_setup', 'objectives', 'requests', 'organization_model_calls'):
            conn.execute('CREATE TABLE ' + table + '(id TEXT)')
    # Live listener verification is exercised by the native proof; this unit
    # seam avoids binding a socket in restricted local executors.
    monkeypatch.setattr(guard, '_fixture_live_identity', lambda pid: {'pid': pid,
        'create_time': expected['create_time'], 'alive': True, 'listeners': [['127.0.0.1', 12345]]})
    def make_request(**changes):
        return httpx.Request(changes.pop('method', 'POST'), changes.pop('url', guard.CANONICAL_URL),
            headers={'Authorization': changes.pop('authorization', 'Bearer ' + expected['key'])},
            json={**body, **changes})
    yield guard, expected, permit, path, make_request, physical
    assert physical == []


@pytest.mark.parametrize('asynchronous', [False, True])
def test_only_reserved_synthetic_completion_and_no_repeat(guarded, asynchronous):
    guard, expected, permit, path, make_request, physical = guarded
    request = make_request()
    if asynchronous:
        response = run_synchronous_mock_coroutine(guard._guarded_async(None, request))
    else:
        response = guard._guarded_sync(None, request)
    assert response.status_code == 200
    assert json.loads(response.json()['choices'][0]['message']['content']) == expected['team']
    sent = [json.loads(line) for line in guard.SENDS.read_text().splitlines()]
    assert len(sent) == 1
    assert sent[0]['reservation']['dispatched'] == 1
    reserved = real_sender_reservation(json.loads(request.content))
    tokens = reserved['input_limit'] + reserved['output_limit']
    assert tokens < 24000
    assert sent[0]['reservation']['reserved_tokens'] == tokens
    assert sent[0]['wireAccounting']['reservedTokens'] == tokens
    assert sent[0]['wireAccounting']['outputLimit'] == reserved['output_limit'] == 8000
    assert sent[0]['keySha256'] == hashlib.sha256(expected['key'].encode()).hexdigest()
    assert sent[0]['physicalNetworkSend'] is False
    with pytest.raises(AssertionError, match='denied'):
        guard._guarded_sync(None, request)
    assert len(guard.SENDS.read_text().splitlines()) == 1
    assert len(guard.ATTEMPTS.read_text().splitlines()) == 1


def test_sdk_serialization_preserves_real_sender_wire_reservation(guarded):
    from openai import OpenAI
    guard, expected, _, _, _, physical = guarded
    # Match production's Unicode owner JSON before the SDK serializes its body.
    options = {'model': expected['model'], 'stream': False, 'max_tokens': 8000,
        'response_format': {'type': 'json_object'}, 'messages': [
            {'role': 'system', 'content': 'EIDOLON_STARTER_TEAM_V1'},
            {'role': 'user', 'content': json.dumps(expected['inputs'], ensure_ascii=False)}]}
    admitted = real_sender_reservation(options)
    captured = []
    def capture(request):
        assert str(request.url) == guard.CANONICAL_URL
        assert request.method == 'POST'
        body = json.loads(request.content)
        assert '\\u00e9' not in body['messages'][-1]['content']
        assert 'café' in body['messages'][-1]['content']
        captured.append(guard._request_reservation(body))
        return httpx.Response(200, request=request, json={
            'id': 'synthetic-sdk', 'object': 'chat.completion', 'created': 1,
            'model': expected['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': '{}'}}]})
    with httpx.Client(transport=httpx.MockTransport(capture), trust_env=False) as client:
        with OpenAI(api_key=expected['key'], base_url='https://api.openai.com/v1',
                    http_client=client, max_retries=0) as sdk:
            sdk.chat.completions.create(**options)
    assert len(captured) == 1
    assert captured[0]['reservedTokens'] == admitted['input_limit'] + admitted['output_limit']
    assert captured[0]['outputLimit'] == admitted['output_limit'] == 8000
    assert captured[0]['reservedTokens'] < 24000
    assert physical == []


def test_update_availability_is_locally_scripted_before_cli_boot(guarded):
    import eidolon_cli.banner as banner
    guard, *_ = guarded
    assert banner.check_for_updates is guard._isolated_update_check
    assert banner.check_for_updates(passive=True) == 0
    assert banner.check_for_updates() == 0
    rows = [json.loads(line) for line in (guard.GUARD_ROOT / ('background-' + str(guard.PID) + '.jsonl')).read_text().splitlines()]
    assert [row['passive'] for row in rows] == [True, False]
    assert all(row['physicalNetworkSend'] is False for row in rows)


def test_metadata_seed_only_populates_current_unknown_caches(guarded):
    guard, expected, *_ = guarded
    home = Path(expected['home'])
    before = {str(path.relative_to(home)): path.read_bytes() for path in home.rglob('*') if path.is_file()}
    started = time.time()
    receipt = guard.seed_offline_metadata(home)
    assert started <= receipt['seededAt'] <= time.time()
    assert receipt['home'] == str(home.resolve())
    assert receipt['prices'] == receipt['realModelCapabilities'] == 'unknown'
    for relative, value in before.items():
        assert (home / relative).read_bytes() == value
    caps = json.loads((home / 'cache/reasoning_caps.json').read_text())
    assert set(caps) == {'https://openrouter.ai/api/v1/models', 'https://inference-api.nousresearch.com/v1/models'}
    for entry in caps.values():
        assert entry == {'ts': receipt['seededAt'], 'caps': {'synthetic-offline-metadata-sentinel-never-selected': None}}
    assert json.loads((home / 'cache/endpoint_model_metadata.json').read_text()) == {
        'https://api.openai.com/v1': {'at': receipt['seededAt'], 'models': {}}}
    assert set(str(path.relative_to(home)) for path in home.rglob('*') if path.is_file()) == set(before) | {
        'cache/reasoning_caps.json', 'cache/endpoint_model_metadata.json'}
    for row in receipt['caches']:
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256']
    with pytest.raises(AssertionError, match='before first activation'):
        guard.seed_offline_metadata(home)


def test_cold_metadata_inventory_and_real_readiness_before_and_after_setup(guarded, monkeypatch):
    from agent import credential_pool, model_metadata, models_dev, usage_pricing
    from eidolon_cli import auth, config, inventory, model_catalog, models, models_pricing
    from eidolon_cli import organization_workspaces, profile_isolation
    from eidolon_cli.organization_metadata import save_metadata
    from eidolon_cli.organization_owner_chat_executor import readiness_reason
    from eidolon_cli.runtime_provider import resolve_runtime_provider
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override

    guard, _, _, _, _, physical = guarded
    root = guard.GUARD_ROOT.parent / 'cold-metadata-origin'
    root.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setenv('HOME', str(root))
    monkeypatch.setattr(Path, 'home', lambda: root)
    # Match the native sandbox: no ambient provider keys or alternative endpoints.
    for provider in auth.PROVIDER_REGISTRY.values():
        for name in (*getattr(provider, 'api_key_env_vars', ()), getattr(provider, 'base_url_env_var', '')):
            if name:
                monkeypatch.delenv(name, raising=False)
    # Exercise real cold-cache warm-up decisions, not the production pytest skip.
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    fixture = json.loads((HELPER.parent / 'isolated-model-catalog.json').read_text())

    def seed(home):
        (home / 'cache').mkdir(exist_ok=True)
        (home / 'models_dev_cache.json').write_text(json.dumps(fixture['registry']))
        (home / 'cache/model_catalog.json').write_text(json.dumps(fixture['manifest']))
        return guard.seed_offline_metadata(home)

    def cold():
        for module, fields in (
            (models_dev, {'_models_dev_cache': {}, '_models_dev_cache_time': 0, '_models_dev_retry_after': 0}),
            (model_catalog, {'_catalog_cache': None, '_catalog_cache_source_mtime': 0}),
            (model_metadata, {'_endpoint_model_metadata_cache': {}, '_endpoint_model_metadata_cache_time': {}}),
            (models_pricing, {'_pricing_cache': {}, '_pricing_cache_retry_after': {}, '_pricing_provider_cache_keys': {}}),
        ):
            for name, value in fields.items():
                monkeypatch.setattr(module, name, value)
        for prefix in ('openrouter', 'nous'):
            for suffix, value in (('reasoning_caps_cache', None), ('reasoning_caps_failed_at', None),
                                  ('caps_disk_checked', False), ('caps_warm_started', False)):
                monkeypatch.setattr(models, '_' + prefix + '_' + suffix, value)

    (root / 'config.yaml').write_text(json.dumps({'model': {'provider': '', 'default': ''}, **fixture['config']}))
    (root / '.env').write_text('')
    root_seed = seed(root)
    root_before = {name: (root / name).read_bytes() for name in ('config.yaml', '.env')}
    token = set_eidolon_home_override(root)
    try:
        metadata = save_metadata(root / 'organization/state.db', display_name='Synthetic origin',
            owner_display_name='Synthetic owner', expected_organization_id=None, expected_revision=None,
            idempotency_key='metadata-origin')['metadata']
        receipt = organization_workspaces.create_workspace(root, {
            'capabilityVersion': 1, 'expectedOrganizationId': metadata['organizationId'],
            'expectedOwnerId': metadata['owner']['ownerId'], 'idempotencyKey': 'metadata-fresh',
            'displayName': 'Fresh metadata workspace', 'ownerDisplayName': 'Fresh owner'})
        cold()
        inventory.build_model_options_payload(inventory.load_picker_context(), include_unconfigured=True)
    finally:
        reset_eidolon_home_override(token)
    home = root / 'profiles' / receipt['profile']
    before = json.loads((home / 'config.yaml').read_text())
    assert before['model'] == {'provider': '', 'default': ''}
    assert (home / '.env').read_text() == ''
    (home / 'config.yaml').write_text(json.dumps({**before, **fixture['config']}))
    owned_seed = seed(home)
    assert root_seed['home'] != owned_seed['home']
    marker = (home / '.organization-isolation.json').read_bytes()
    token = set_eidolon_home_override(home)
    try:
        for stage in ('blank', 'key-only', 'configured'):
            if stage == 'key-only':
                from eidolon_cli.credential_lifecycle import save_provider_env_credential
                save_provider_env_credential('OPENAI_API_KEY', 'synthetic-own-metadata-key-never-real')
            if stage == 'configured':
                configured = config.load_config()
                configured['model'] = {'provider': 'openai-api', 'default': 'synthetic-studio-one'}
                config.save_config(configured)
            cold()
            payload = inventory.build_model_options_payload(inventory.load_picker_context(), include_unconfigured=True)
            # The built-in MoA preset is a virtual configuration row, not an
            # authenticated physical provider or an allowed isolated route.
            physical_rows = [row for row in payload['providers'] if row['slug'] != 'moa']
            assert not any(row.get('authenticated') for row in physical_rows)
            assert not any(row.get('pricing') for row in payload['providers'])
            assert all(row.get('models') == (['synthetic-studio-one'] if stage == 'configured'
                and row['slug'] == 'openai-api' else []) for row in physical_rows)
            assert not os.environ.get('OPENAI_API_KEY')
            assert not credential_pool.load_pool('openai-api').has_credentials()
            if stage != 'configured':
                with pytest.raises(profile_isolation.ProfileIsolationError, match='Select a supported provider and a model'):
                    resolve_runtime_provider(requested='openai-api')
                assert config.load_config_readonly()['model']['default'] == ''
            else:
                runtime = resolve_runtime_provider(requested='openai-api')
                assert runtime['api_key'] == 'synthetic-own-metadata-key-never-real'
                assert runtime['base_url'] == 'https://api.openai.com/v1'
                assert runtime['source'] == 'profile-local-env'
                assert readiness_reason({}) is None
            assert model_metadata.get_model_context_length('synthetic-studio-one', provider='openai-api',
                base_url='https://api.openai.com/v1') == model_metadata.DEFAULT_FALLBACK_CONTEXT
            assert usage_pricing.get_pricing_entry('synthetic-studio-one', provider='openai-api',
                base_url='https://api.openai.com/v1', api_key='synthetic-own-metadata-key-never-real') is None
            assert models_pricing._pricing_cache == {}
            assert not guard.ATTEMPTS.exists()
            assert not guard.SENDS.exists()
            assert physical == []
            assert (home / '.organization-isolation.json').read_bytes() == marker
        rows = [json.loads(line) for line in (guard.GUARD_ROOT / ('metadata-' + str(guard.PID) + '.jsonl')).read_text().splitlines()]
        assert [row['home'] for row in rows] == [str(root.resolve()), *([str(home.resolve())] * 3)]
        assert all(row['providerSlugs'] and 'openai-api' in row['providerSlugs'] for row in rows)
        assert all(row['outcome'] == 'kept-unknown' and row['physicalNetworkSend'] is False for row in rows)
        assert all((root / name).read_bytes() == value for name, value in root_before.items())
    finally:
        reset_eidolon_home_override(token)
        import eidolon_logging
        eidolon_logging._reset_queued_handlers()
        shutil.rmtree(root)


@pytest.mark.parametrize('change', ['missing-permit', 'foreign-home', 'foreign-owner', 'closed', 'pid',
    'restart', 'ledger-home', 'unconfigured-model', 'foreign-key', 'wrong-model', 'tools', 'wrong-inputs',
    'missing-reservation', 'unreserved', 'wrong-key', 'wrong-revision', 'wrong-hash', 'premature-team', 'hidden-work',
    'ceiling-not-charge', 'wrong-output-policy', 'incomplete-installation'])
def test_invalid_send_fails_before_any_response_or_network(guarded, change):
    guard, expected, permit, path, make_request, physical = guarded
    request = make_request()
    if change == 'incomplete-installation':
        marker_path = guard.GUARD_ROOT / ('installed-' + str(guard.PID) + '.json')
        marker = json.loads(marker_path.read_text())
        marker['displayPricingPrewarm'] = False
        marker_path.write_text(json.dumps(marker))
    elif change == 'missing-permit':
        permit.unlink()
    elif change in {'foreign-home', 'foreign-owner', 'closed', 'pid', 'restart'}:
        key, value = {'foreign-home': ('home', str(path.parent)), 'foreign-owner': ('ownerId', 'other'),
            'closed': ('open', False), 'pid': ('pid', os.getpid() + 1),
            'restart': ('create_time', expected['create_time'] + 1)}[change]
        expected[key] = value
        permit.write_text(json.dumps(expected))
    elif change == 'ledger-home':
        (guard.ROOT_HOME / 'spawn-ledger.json').write_text('[]')
    elif change == 'unconfigured-model':
        (path.parent.parent / 'config.yaml').write_text('model: {}')
    elif change == 'foreign-key':
        request = make_request(authorization='Bearer synthetic-fresh-team-key-other-never-real')
    elif change == 'wrong-model':
        request = make_request(model='other-model')
    elif change == 'wrong-output-policy':
        request = make_request(max_tokens=7999)
    elif change == 'tools':
        request = make_request(tools=[{'type': 'function'}])
    elif change == 'wrong-inputs':
        request = make_request(messages=[{'role': 'system', 'content': 'EIDOLON_STARTER_TEAM_V1'},
            {'role': 'user', 'content': '{}'}])
    else:
        sql = {'missing-reservation': 'DELETE FROM organization_team_generations',
            'unreserved': 'UPDATE organization_team_generations SET dispatched=0',
            'wrong-key': "UPDATE organization_team_generations SET idempotency_key='other'",
            'wrong-revision': "UPDATE organization_team_generations SET source_revision='other'",
            'wrong-hash': "UPDATE organization_team_generations SET input_hash='other'",
            'ceiling-not-charge': 'UPDATE organization_team_generations SET reserved_tokens=24000',
            'premature-team': "INSERT INTO organization_team_setup VALUES ('other')",
            'hidden-work': "INSERT INTO requests VALUES ('other')"}[change]
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(sql)
    with pytest.raises(AssertionError, match='denied'):
        guard._guarded_sync(None, request)
    assert not guard.SENDS.exists()
    assert len(guard.ATTEMPTS.read_text().splitlines()) == 1


@pytest.mark.parametrize('url', ['https://example.invalid/', 'http://localhost/',
    'https://api.openai.com/v1/models', 'http://api.openai.com/v1/chat/completions',
    'https://api.openai.com/v1/chat/completions?unexpected=1'])
def test_external_httpx_sync_and_async_fail_closed(guarded, url):
    guard, _, _, _, make_request, _ = guarded
    with pytest.raises(AssertionError, match='denied'):
        guard._guarded_sync(None, make_request(url=url))
    with pytest.raises(AssertionError, match='denied'):
        run_synchronous_mock_coroutine(guard._guarded_async(None, make_request(url=url)))
    assert len(guard.ATTEMPTS.read_text().splitlines()) == 2


def test_socket_dns_backstop_rejects_external_and_hostname_loopback(guarded):
    guard, *_ = guarded
    checks = 0
    for host in ('api.openai.com', 'example.invalid', 'localhost', '198.51.100.1'):
        with socket.socket() as sock:
            with pytest.raises(AssertionError, match='denied'):
                sock.connect((host, 443))
            with pytest.raises(AssertionError, match='denied'):
                sock.connect_ex((host, 443))
        checks += 2
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with pytest.raises(AssertionError, match='denied'):
                sock.sendto(b'synthetic', (host, 443))
            checks += 1
            if hasattr(sock, 'sendmsg'):
                with pytest.raises(AssertionError, match='denied'):
                    sock.sendmsg([b'synthetic'], [], 0, (host, 443))
                checks += 1
        with pytest.raises(AssertionError, match='denied'):
            socket.getaddrinfo(host, 443)
        checks += 1
        with pytest.raises(AssertionError, match='denied'):
            socket.getnameinfo((host, 443), 0)
        checks += 1
        for lookup in (socket.gethostbyname, socket.gethostbyname_ex, socket.gethostbyaddr):
            with pytest.raises(AssertionError, match='denied'):
                lookup(host)
            checks += 1
    assert len(guard.ATTEMPTS.read_text().splitlines()) == checks
    assert guard._loopback('127.0.0.1') and guard._loopback('::1')
    assert guard._loopback(b'127.0.0.1')
    assert socket.gethostbyname('127.0.0.1') == '127.0.0.1'
    assert socket.gethostbyname_ex('127.0.0.1') == ('127.0.0.1', [], ['127.0.0.1'])
    assert socket.gethostbyaddr('::1') == ('::1', [], ['::1'])
    assert socket.getnameinfo(('127.0.0.1', 443), 0) == ('127.0.0.1', '443')


def test_effective_home_override_cannot_borrow_environment_identity(guarded):
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    guard, _, _, path, make_request, _ = guarded
    token = set_eidolon_home_override(path.parent)
    try:
        with pytest.raises(AssertionError, match='denied'):
            guard._guarded_sync(None, make_request())
    finally:
        reset_eidolon_home_override(token)
    assert not guard.SENDS.exists()


def test_concurrent_repeated_calls_claim_exactly_once(guarded):
    guard, _, _, _, make_request, _ = guarded
    def send(_):
        try:
            return guard._guarded_sync(None, make_request()).status_code
        except AssertionError:
            return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert results.count(200) == 1
    assert results.count('denied') == 1
    assert len(guard.SENDS.read_text().splitlines()) == 1
    assert len(guard.ATTEMPTS.read_text().splitlines()) == 1

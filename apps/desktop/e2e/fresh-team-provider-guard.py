"""Native acceptance transport only. Installed as sitecustomize in synthetic homes.

Production route, key resolution, reservation, parsing and creation remain real.
No canonical OpenAI request ever reaches a socket. Unexpected traffic is denied.
"""
import hashlib
from contextlib import closing
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import sqlite3

import httpx
import psutil
import yaml

GUARD_ROOT = Path(os.environ['EIDOLON_FRESH_TEAM_GUARD_DIR']).resolve()
ROOT_HOME = Path(os.environ['EIDOLON_FRESH_TEAM_ROOT_HOME']).resolve()
PID = os.getpid()
STARTED = psutil.Process().create_time()
CANONICAL_URL = 'https://api.openai.com/v1/chat/completions'
ATTEMPTS = GUARD_ROOT / ('attempts-' + str(PID) + '.jsonl')
SENDS = GUARD_ROOT / ('sends-' + str(PID) + '.jsonl')


def _record(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value, sort_keys=True) + '\n')


def _deny(kind, target):
    _record(ATTEMPTS, {'pid': PID, 'kind': kind, 'target': str(target)})
    raise AssertionError('Fresh-team fixture denied ' + kind)


def _loopback(host):
    try:
        if isinstance(host, bytes):
            host = host.decode('ascii')
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError, UnicodeError):
        return False


# Reused verbatim from organization-creation.spec.ts, blob
# 0ae18b0ed0336a4db2e7b5f6cc301e8b6ea96f53, _fixture_identity_matches through
# _fixture_live_identity. The regression test compares their Python ASTs.
def _fixture_identity_matches(marker,entry,live,expected):
 try:
  host=expected['host'];port=expected['port'];profile=expected['profile']
  if expected['open'] is not True or not ipaddress.ip_address(host).is_loopback:
   return False
  if type(port) is not int or not 0 < port < 65536:
   return False
  descriptor=expected['descriptor']
  if descriptor.get('capabilityVersion') != 1 or descriptor.get('scope') != {'kind':'served-profile','profile':profile}:
   return False
  if expected.get('organizationId'):
   identity={'organizationId':expected['organizationId'],'ownerId':expected['ownerId']}
   isolation=descriptor.get('isolation',{})
   if descriptor.get('state') != 'ready' or isolation.get('profile') != profile:
    return False
   if any(isolation.get(key) != value for key,value in identity.items()):
    return False
   if isolation.get('isolationVersion') != 1 or isolation.get('credentialMode') != 'profile-local-openai-api' or isolation.get('executionMode') != 'tool-free-organization' or isolation.get('supportedProviders') != ['openai-api']:
    return False
   metadata=descriptor.get('metadata',{})
   if metadata.get('organizationId') != identity['organizationId'] or metadata.get('owner',{}).get('ownerId') != identity['ownerId']:
    return False
  pid=marker['pid'];started=marker['create_time']
  times=[started,entry.get('create_time'),live.get('create_time')]
  if type(pid) is not int or pid <= 0 or any(type(value) not in (int,float) or not math.isfinite(value) or value <= 0 for value in times):
   return False
  if marker.get('sync') is not True or marker.get('async') is not True:
   return False
  if entry.get('pid') != pid or live.get('pid') != pid or entry.get('create_time') != started or live.get('create_time') != started or live.get('alive') is not True:
   return False
  # ledger.profile is optional browser-route metadata, not --profile identity.
  if entry.get('purpose') != 'serve' or entry.get('home') != expected['home'] or entry.get('profile') not in ('',profile):
   return False
  return entry.get('host') == host and entry.get('port') == port and [host,port] in live.get('listeners',[])
 except (KeyError,TypeError,ValueError,AttributeError):
  return False

def _fixture_live_identity(pid):
 try:
  process=psutil.Process(pid)
  started=process.create_time()
  listeners=[[item.laddr.ip,item.laddr.port] for item in process.net_connections(kind='tcp') if item.status == psutil.CONN_LISTEN]
  alive=process.is_running() and psutil.Process(pid).create_time() == started
  return {'pid':pid,'create_time':started,'alive':alive,'listeners':listeners}
 except (psutil.Error,OSError,ValueError):
  return {}


def _request_reservation(body):
    from eidolon_cli.organization_evidence import CONTEXT_RESERVE_TOKENS, wire_input_bound
    caps = [body[key] for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if key in body]
    # This real setup path explicitly supplies OUTPUT_TOKENS=8000. The input
    # charge is exact conservative wire framing plus context reserve, rather
    # than the 24000-token admission ceiling.
    if not caps or any(type(cap) is not int or cap != 8000 for cap in caps):
        raise ValueError('setup-output-policy')
    bound = wire_input_bound(body)
    total = bound + CONTEXT_RESERVE_TOKENS + max(caps)
    if total > 24000:
        raise ValueError('setup-token-ceiling')
    return {'wireInputBound': bound, 'contextReserve': CONTEXT_RESERVE_TOKENS,
            'outputLimit': max(caps), 'reservedTokens': total}


def _synthetic_response(request):
    """Only a verified, explicitly armed, already-reserved setup call is mocked."""
    if str(request.url) != CANONICAL_URL or request.method != 'POST':
        _deny('unexpected-http', request.url)
    try:
        from eidolon_constants import get_eidolon_home
        home = Path(get_eidolon_home()).resolve()
        expected = json.loads((GUARD_ROOT / 'permit.json').read_text())
        if expected['home'] != str(home) or home.parent != ROOT_HOME / 'profiles':
            raise ValueError('home')
        marker = json.loads((GUARD_ROOT / ('installed-' + str(PID) + '.json')).read_text())
        entries = json.loads((ROOT_HOME / 'spawn-ledger.json').read_text())
        matches = [entry for entry in entries if _fixture_identity_matches(
            marker, entry, _fixture_live_identity(PID), expected)]
        if len(matches) != 1 or expected['pid'] != PID or expected['create_time'] != STARTED:
            raise ValueError('live-owner')
        isolation = json.loads((home / '.organization-isolation.json').read_text())
        if any(isolation.get(key) != expected[key] for key in ('profile', 'organizationId', 'ownerId')):
            raise ValueError('isolation')
        model = yaml.safe_load((home / 'config.yaml').read_text())['model']
        if model.get('provider') != 'openai-api' or model.get('default') != expected['model']:
            raise ValueError('configured-model')
        key = expected['key']
        if not key.startswith('synthetic-fresh-team-key-') or not key.endswith('-never-real'):
            raise ValueError('synthetic-key')
        if request.headers.get('authorization') != 'Bearer ' + key:
            raise ValueError('request-key')
        # This is synthetic test data. Do not record the raw key in evidence.
        if ('OPENAI_API_KEY=' + key) not in (home / '.env').read_text().splitlines():
            raise ValueError('configured-key')
        body = json.loads(request.content)
        messages = body['messages']
        if (body.get('model') != expected['model'] or body.get('stream') is True
                or body.get('tools') or body.get('functions')):
            raise ValueError('request-model-or-tools')
        if not any(item.get('role') == 'system' and 'EIDOLON_STARTER_TEAM_V1' in item.get('content', '')
                   for item in messages):
            raise ValueError('setup-system')
        owner_text = [item['content'] for item in messages if item.get('role') == 'user']
        if len(owner_text) != 1 or json.loads(owner_text[0]) != expected['inputs']:
            raise ValueError('original-inputs')
        accounting = _request_reservation(body)
        with closing(sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute('SELECT * FROM organization_team_generations')]
            if len(rows) != 1:
                raise ValueError('reservation-count')
            row = rows[0]
            if (row['organization_id'] != expected['organizationId'] or row['status'] != 'running'
                    or row['dispatched'] != 1 or row['reserved_tokens'] != accounting['reservedTokens']
                    or row['idempotency_key'] != expected['request']['idempotencyKey']
                    or row['source_revision'] != expected['request']['sourceRevision']):
                raise ValueError('durable-reservation')
            digest = hashlib.sha256(json.dumps({'operation': 'generateTeam', 'input': expected['request']},
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if row['input_hash'] != digest:
                raise ValueError('reservation-input-hash')
            if conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] != 0:
                raise ValueError('premature-team')
            if any(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                   for table in ('objectives', 'requests', 'organization_model_calls')):
                raise ValueError('hidden-dispatch')
        claim = GUARD_ROOT / ('claimed-' + hashlib.sha256(str(home).encode()).hexdigest())
        # Exclusive creation binds the limit across processes and restarts.
        with claim.open('x') as stream:
            stream.write(row['idempotency_key'])
    except (OSError, KeyError, ValueError, TypeError, sqlite3.Error) as error:
        _deny('invalid-synthetic-send', type(error).__name__ + ':' + str(error))
    _record(SENDS, {
        'pid': PID, 'create_time': STARTED, 'home': str(home),
        'profile': expected['profile'], 'organizationId': expected['organizationId'],
        'ownerId': expected['ownerId'], 'url': str(request.url), 'model': body['model'],
        'keySha256': hashlib.sha256(key.encode()).hexdigest(), 'inputs': json.loads(owner_text[0]),
        'reservation': {key: row[key] for key in ('idempotency_key', 'organization_id', 'status',
                                                'dispatched', 'reserved_tokens', 'reserved_cost')},
        'wireAccounting': accounting,
        'physicalNetworkSend': False,
    })
    return httpx.Response(200, request=request, json={
        'id': 'synthetic-' + row['idempotency_key'], 'object': 'chat.completion', 'created': 1,
        'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': json.dumps(expected['team'])}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
    })


_sync = httpx.HTTPTransport.handle_request
_async = httpx.AsyncHTTPTransport.handle_async_request


def _guarded_sync(self, request):
    if _loopback(request.url.host):
        return _sync(self, request)
    return _synthetic_response(request)


async def _guarded_async(self, request):
    if _loopback(request.url.host):
        return await _async(self, request)
    return _synthetic_response(request)


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
_sendto = socket.socket.sendto
_sendmsg = getattr(socket.socket, 'sendmsg', None)


def _check_socket(sock, address):
    if sock.family == socket.AF_UNIX:
        return
    if not isinstance(address, tuple) or not _loopback(address[0]):
        _deny('socket', address)


def _guarded_connect(self, address):
    _check_socket(self, address)
    return _connect(self, address)


def _guarded_connect_ex(self, address):
    _check_socket(self, address)
    return _connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host is not None and not _loopback(host):
        _deny('dns', host)
    return _getaddrinfo(host, *args, **kwargs)


def _guarded_sendto(self, data, *args):
    _check_socket(self, args[-1])
    return _sendto(self, data, *args)


def _guarded_sendmsg(self, buffers, ancdata=(), flags=0, address=None):
    if address is not None:
        _check_socket(self, address)
    return _sendmsg(self, buffers, ancdata, flags, address)


def _guarded_gethostbyname(host):
    if not _loopback(host):
        _deny('dns', host)
    # Numeric loopback needs no resolver, including no reverse DNS lookup.
    return host.decode('ascii') if isinstance(host, bytes) else host


def _guarded_gethostbyname_ex(host):
    address = _guarded_gethostbyname(host)
    return address, [], [address]


def _guarded_getnameinfo(address, flags):
    if not isinstance(address, tuple) or not _loopback(address[0]):
        _deny('dns', address)
    return str(address[0]), str(address[1])


httpx.HTTPTransport.handle_request = _guarded_sync
httpx.AsyncHTTPTransport.handle_async_request = _guarded_async
socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.socket.sendto = _guarded_sendto
if _sendmsg is not None:
    socket.socket.sendmsg = _guarded_sendmsg
socket.getaddrinfo = _guarded_getaddrinfo
socket.gethostbyname = _guarded_gethostbyname
socket.gethostbyname_ex = _guarded_gethostbyname_ex
socket.gethostbyaddr = _guarded_gethostbyname_ex
socket.getnameinfo = _guarded_getnameinfo
# Same read-only availability seam as organization-switch-fixture.ts. It must
# precede CLI boot: the real checker can spawn git as well as use urllib, so a
# Python socket guard alone cannot isolate that unrelated background service.
import eidolon_cli.banner as _banner


def _isolated_update_check(*args, **kwargs):
    _record(GUARD_ROOT / ('background-' + str(PID) + '.jsonl'), {
        'pid': PID, 'kind': 'update-availability', 'physicalNetworkSend': False,
        'passive': kwargs.get('passive', False), 'behind': 0,
    })
    return 0


_banner.check_for_updates = _isolated_update_check
_marker = GUARD_ROOT / ('installed-' + str(PID) + '.json')
_pending = _marker.with_suffix('.pending')
_pending.write_text(json.dumps({'bootstrapHome': str(Path(os.environ['HERMES_HOME']).resolve()),
    'pid': PID, 'create_time': STARTED, 'sync': True, 'async': True, 'socket': True, 'dns': True,
    'updateAvailability': True}))
_pending.replace(_marker)

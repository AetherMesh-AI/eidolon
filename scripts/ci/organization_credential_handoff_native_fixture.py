"""One synthetic real-service handoff, gated to a fresh isolated test process."""
from pathlib import Path
import json
import os
import sqlite3
import uuid
from urllib.parse import urlsplit

from scripts.ci.organization_credentials_native_fixture import SyntheticBackend, _home as _catalog_home

_MARKER = 'eidolon-synthetic-credential-handoff-v1'


def _home(home):
    home = _catalog_home(home)
    if (home / '.credential-handoff-fixture').read_text() != _MARKER:
        raise RuntimeError('Synthetic handoff fixture home required')
    rendezvous = (home / '.credential-handoff-rendezvous').read_text()
    if str(uuid.UUID(rendezvous)) != rendezvous:
        raise RuntimeError('Fresh handoff rendezvous required')
    return home, rendezvous


def seed(home):
    import yaml
    from scripts.ci.organization_credentials_native_fixture import seed as seed_catalog
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_credentials import CredentialCatalog
    from eidolon_cli.organization_credential_security import Principal
    from eidolon_cli.organization_credential_service import credential_agents

    home = Path(home).resolve()
    rendezvous_path = home / '.credential-handoff-rendezvous'
    rendezvous = rendezvous_path.read_text()
    if (str(uuid.UUID(rendezvous)) != rendezvous or (home / 'organization').exists()
            or rendezvous_path.is_symlink() or (home / 'config.yaml').is_symlink()):
        raise RuntimeError('Fresh isolated handoff sandbox required')
    config = yaml.safe_load((home / 'config.yaml').read_text()) or {}
    config['organization'] = {**config.get('organization', {}), 'max_workers': 1, 'max_inflight': 1,
                              'timeout_seconds': 180, 'max_attempts': 1}
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    seeded = seed_catalog(home)
    (home / '.credential-handoff-fixture').write_text(_MARKER)
    _home(home)
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(config))
    store.create_objective('Synthetic owner-prepared replacement',
        description='Complete only the listed owner-prepared local replacement.', idempotency_key='handoff-objective')
    executive = store.claim_next()
    if executive is None or executive['type'] != 'request.decompose':
        raise RuntimeError('Expected isolated executive stage')
    context = store.context(executive)
    objective = context['objective']
    if not store.finish(executive, {'workPackages': [{
            'title': objective['title'], 'description': objective['description'],
            'managerId': objective['managerId'], 'criterionIndexes': list(range(len(objective['acceptanceCriteria']))),
            'projectIds': [], 'dependsOn': [], 'maxTasks': 1}]}):
        raise RuntimeError('Synthetic decomposition failed')
    planner = store.claim_next()
    if planner is None or planner['type'] != 'request.plan' or not store.finish(planner, {'tasks': [{
            'title': 'Replace the prepared local value', 'description': 'Use the exact owner handoff only.',
            'type': 'work.draft', 'team': 'general', 'dependsOn': []}]}):
        raise RuntimeError('Synthetic planning failed')
    agents = credential_agents(home)
    if len(agents) != 1:
        raise RuntimeError('Expected one isolated synthetic Worker')
    with store._connect() as conn:
        queued = conn.execute("SELECT id FROM requests WHERE status='queued'").fetchall()
    if len(queued) != 1:
        raise RuntimeError('Expected one isolated synthetic work request')
    catalog = CredentialCatalog(home / 'organization' / 'credentials.db', seeded['organizationId'], backend=SyntheticBackend())
    owner = Principal(seeded['organizationId'], seeded['ownerId'], owner=True)
    entry = catalog.create(owner, {'service': 'synthetic', 'label': 'workerHandoff', 'type': 'token',
        'description': 'Synthetic native handoff acceptance only', 'kind': 'secret', 'expiresAt': None},
        operation_id='synthetic-handoff-entry')
    catalog.configure_grant(owner, actor_id=agents[0]['identityId'], scope=entry['entryId'],
        permissions=['replace_value'], bindings=[], expected_revision=0, operation_id='synthetic-handoff-grant')
    result = {**seeded, 'agentId': agents[0]['id'], 'identityId': agents[0]['identityId'],
              'entryId': entry['entryId'], 'requestId': queued[0]['id']}
    (home / '.credential-handoff-seed.json').write_text(json.dumps(result))
    return result


def dispatch_allowed(home, seed_data, rendezvous):
    try:
        if (home / '.credential-handoff-start').read_text() != rendezvous:
            return False
        with sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True) as conn:
            row = conn.execute('SELECT type,status FROM requests WHERE id=?', (seed_data['requestId'],)).fetchone()
            unrelated = conn.execute("SELECT count(*) FROM requests WHERE status IN ('queued','running') AND id!=?",
                                     (seed_data['requestId'],)).fetchone()[0]
        return row in [('work.draft', 'queued'), ('work.draft', 'running')] and unrelated == 0
    except (OSError, sqlite3.Error):
        return False


def install():
    import sys
    import yaml
    if os.environ.get('EIDOLON_NATIVE_CREDENTIAL_HANDOFF_FIXTURE') != '1' or 'serve' not in sys.argv:
        raise RuntimeError('Explicit synthetic handoff server process required')
    home, rendezvous = _home(os.environ.get('HERMES_HOME', ''))
    config = yaml.safe_load((home / 'config.yaml').read_text()) or {}
    model = config.get('model', {})
    provider = model.get('provider')
    named_endpoint = config.get('providers', {}).get(provider, {}).get('api', '')
    endpoint = urlsplit(model.get('base_url') or named_endpoint)
    if (provider not in {'mock', 'custom'} or endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.port is None
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path != '/' + rendezvous + '/v1'
            or (named_endpoint and urlsplit(named_endpoint) != endpoint)):
        raise RuntimeError('Exact loopback synthetic model rendezvous required')
    seed_data = json.loads((home / '.credential-handoff-seed.json').read_text())
    from eidolon_cli import organization_credential_service, organization_service
    backend = SyntheticBackend()
    organization_credential_service.native_backend = lambda directory: backend
    original = organization_service.OrganizationService.__init__

    def bounded_dispatch(self, *args, **kwargs):
        previous = kwargs.get('can_dispatch')
        kwargs['can_dispatch'] = lambda: (self.home == home and (previous is None or previous())
                                         and dispatch_allowed(home, seed_data, rendezvous))
        original(self, *args, **kwargs)

    organization_service.OrganizationService.__init__ = bounded_dispatch


def inspect(home):
    from eidolon_cli.organization_credentials import CredentialCatalog
    from eidolon_cli.organization_credential_security import Principal
    home, _ = _home(home)
    seeded = json.loads((home / '.credential-handoff-seed.json').read_text())
    catalog = CredentialCatalog(home / 'organization' / 'credentials.db', seeded['organizationId'], backend=SyntheticBackend())
    details = catalog.owner_details(Principal(seeded['organizationId'], seeded['ownerId'], owner=True))
    with sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        request = dict(conn.execute('SELECT id,type,status,execution_count FROM requests WHERE id=?',
                                   (seeded['requestId'],)).fetchone())
        calls = conn.execute('SELECT count(*) FROM organization_model_calls WHERE request_id=?',
                             (seeded['requestId'],)).fetchone()[0]
    with catalog._connection() as conn:
        receipts = [json.loads(row[0]) for row in conn.execute('SELECT result FROM credential_receipts')]
    return {'entries': details['entries'], 'grants': details['grants'], 'request': request,
            'modelCalls': calls, 'receipts': receipts, 'nativeStorageExercised': False}


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in {'seed', 'inspect'}:
        raise SystemExit('Use seed|inspect SYNTHETIC_HOME')
    print(json.dumps((seed if sys.argv[1] == 'seed' else inspect)(sys.argv[2])))

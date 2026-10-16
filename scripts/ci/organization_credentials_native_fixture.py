"""Synthetic desktop acceptance fixture; never imported by production startup.

The explicit, marker-bound install hook belongs only to an isolated test process.
It changes no native vault, host secrets, grants, or production storage policy.
"""
from pathlib import Path
import json
import os
import sqlite3

_MARKER = 'eidolon-synthetic-credential-fixture-v1'


def _home(home):
    path = Path(home).resolve()
    if not path.is_dir() or (path / '.credential-fixture').read_text() != _MARKER:
        raise RuntimeError('Synthetic credential fixture home required')
    return path


class SyntheticBackend:
    def __init__(self):
        self._values = {}

    def status(self):
        from eidolon_cli.organization_credential_security import BackendState
        return BackendState('synthetic-in-memory-test', 'available')

    def put(self, binding, value):
        from eidolon_cli.organization_credential_security import CredentialError
        from eidolon_cli.organization_credential_native import binding_reference
        if not isinstance(value, bytes) or not value.startswith(b'SYNTHETIC_'):
            raise CredentialError('synthetic_value_required')
        reference = binding_reference(binding)
        if reference in self._values:
            raise CredentialError('backend_version_exists')
        self._values[reference] = (binding, value)
        return reference

    def consume(self, reference, binding, consumer):
        from eidolon_cli.organization_credential_security import CredentialError
        item = self._values.get(reference)
        if not item or item[0] != binding:
            raise CredentialError('value_missing')
        consumer(item[1])


def seed(home):
    import yaml
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_metadata import save_metadata
    from eidolon_cli.organization_credential_service import credential_agents
    home = Path(home).resolve()
    config = yaml.safe_load((home / 'config.yaml').read_text()) or {}
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(config))
    metadata = save_metadata(store.path, display_name='Credential owner proof', owner_display_name='Synthetic owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='credential-fixture-metadata')['metadata']
    (home / '.credential-fixture').write_text(_MARKER)
    agent = credential_agents(home)[0]
    return {'organizationId': metadata['organizationId'], 'ownerId': metadata['owner']['ownerId'],
            'agentId': agent['id'], 'identityId': agent['identityId']}


def install():
    import sys
    if os.environ.get('EIDOLON_NATIVE_CREDENTIAL_FIXTURE') != '1' or 'serve' not in sys.argv:
        raise RuntimeError('Explicit synthetic server process required')
    home = _home(os.environ.get('HERMES_HOME', ''))
    from eidolon_cli import organization_credential_service, organization_service
    backend = SyntheticBackend()
    organization_credential_service.native_backend = lambda directory: backend
    original = organization_service.OrganizationService.__init__
    def stopped_dispatch(self, *args, **kwargs):
        kwargs['can_dispatch'] = lambda: False
        original(self, *args, **kwargs)
    organization_service.OrganizationService.__init__ = stopped_dispatch
    original_recovery = organization_service.start_existing_services
    def recovered():
        result = original_recovery()
        (home / '.credential-fixture-backend-pid').write_text(str(os.getpid()))
        return result
    organization_service.start_existing_services = recovered


def inspect(home):
    from eidolon_cli.organization_metadata import read_descriptor
    from eidolon_cli.organization_credential_service import owner_catalog, credential_agents
    home = _home(home)
    metadata = read_descriptor(home / 'organization' / 'state.db')['metadata']
    catalog, principal = owner_catalog(home, metadata['organizationId'], metadata['owner']['ownerId'])
    # Observation is metadata only, with an honestly labelled test backend.
    catalog.backend = SyntheticBackend()
    result = catalog.owner_details(principal)
    result['agents'] = credential_agents(home)
    with sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True) as conn:
        attempts = conn.execute('SELECT COALESCE(SUM(execution_count),0) FROM requests').fetchone()[0]
        calls = conn.execute('SELECT COUNT(*) FROM organization_model_calls').fetchone()[0]
    with catalog._connection() as conn:
        receipts = conn.execute('SELECT COUNT(*) FROM credential_receipts').fetchone()[0] if conn else 0
        audit = conn.execute('SELECT COUNT(*) FROM credential_audit').fetchone()[0] if conn else 0
    return {'catalog': result, 'receiptCount': receipts, 'auditCount': audit, 'attempts': attempts, 'modelCalls': calls}


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in {'seed', 'inspect'}:
        raise SystemExit('Use seed|inspect SYNTHETIC_HOME')
    print(json.dumps((seed if sys.argv[1] == 'seed' else inspect)(sys.argv[2])))

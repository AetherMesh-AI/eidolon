"""One served organization, independent of execution state and profile secrets."""
from pathlib import Path

from eidolon_cli.organization_credential_security import CredentialError, Principal, SecureInputs
from eidolon_cli.organization_credential_native import native_backend
from eidolon_cli.organization_credentials import CredentialCatalog
from eidolon_cli.organization_metadata import read_descriptor

secure_inputs = SecureInputs()


def owner_catalog(home, organization_id, owner_id):
    home = Path(home).resolve()
    ledger = home / 'organization' / 'state.db'
    path = home / 'organization' / 'credentials.db'
    vault = home / 'organization' / 'credential-vault'
    if any(item.resolve() != item for item in (ledger, path, vault)):
        raise CredentialError('scope_denied')
    metadata = read_descriptor(ledger).get('metadata')
    if (not metadata or metadata['organizationId'] != organization_id
            or metadata['owner']['ownerId'] != owner_id):
        raise CredentialError('identity_changed')
    catalog = CredentialCatalog(path, organization_id, backend=native_backend(vault), inputs=secure_inputs)
    return catalog, Principal(organization_id, owner_id, owner=True)


def credential_agents(home):
    """Read existing active persistent identities without initializing runtime state."""
    import sqlite3
    ledger = Path(home).resolve() / 'organization' / 'state.db'
    if ledger.resolve() != ledger or not ledger.is_file():
        raise CredentialError('identity_changed')
    with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'agents', 'agent_identity', 'staff_state'} <= tables:
            return []
        return [dict(row) for row in conn.execute(
            "SELECT a.id,i.identity_id AS identityId,a.name FROM agents a "
            "JOIN agent_identity i ON i.agent_id=a.id JOIN staff_state s ON s.agent_id=a.id "
            "WHERE s.active=1 AND s.source!='retired' AND a.role='Worker' ORDER BY a.id")]

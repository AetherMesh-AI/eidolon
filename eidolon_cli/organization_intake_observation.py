"""Non-provisioning intake history. Never recover, migrate, or acquire a service."""
from contextlib import closing
from pathlib import Path
import sqlite3

from eidolon_cli.organization_intake_contract import _text
from eidolon_cli.organization_request_intake import INTAKE_SCHEMA, _view


def read_intakes(home, *, intake_id=None):
    """Read persisted owner-scoped state, including unresolved interrupted rows.

    SQLite may take transient WAL read locks. No ledger rows, schema, journal
    mode, service state, or execution lock files are changed by this operation.
    Recovery is a separate authenticated mutation; a pending row is not proof
    that its original process is still running.
    """
    if intake_id is not None:
        intake_id = _text(intake_id, 'Intake ID', 128)
    path = Path(home) / 'organization' / 'state.db'
    if not path.exists():
        if intake_id is not None:
            raise ValueError('Intake is not available to the current organization owner')
        return []
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'organization_request_intakes' not in tables:
                if intake_id is not None:
                    raise ValueError('Intake is not available to the current organization owner')
                return []
            with closing(sqlite3.connect(':memory:')) as schema:
                schema.executescript(INTAKE_SCHEMA)
                required = {row[1] for row in schema.execute('PRAGMA table_info(organization_request_intakes)')}
            columns = {row[1] for row in conn.execute('PRAGMA table_info(organization_request_intakes)')}
            if not required <= columns:
                raise RuntimeError('Intake history schema is unsupported')
            owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
            if owner is None:
                raise RuntimeError('Organization owner identity is missing')
            select = 'SELECT i.* FROM organization_request_intakes i'
            if 'organization_intake_duplicate_links' in tables:
                select = ('SELECT i.*,l.objective_id linked_objective_id FROM organization_request_intakes i '
                          'LEFT JOIN organization_intake_duplicate_links l ON l.intake_id=i.id')
            query = select + ' WHERE i.owner_identity_id=?'
            params = [owner[0]]
            if intake_id is not None:
                query += ' AND i.id=?'
                params.append(intake_id)
            rows = conn.execute(query + ' ORDER BY i.created DESC,i.id DESC LIMIT 20', params).fetchall()
            if intake_id is not None:
                if not rows:
                    raise ValueError('Intake is not available to the current organization owner')
                return _view(rows[0])
            return [_view(row) for row in rows]
    except sqlite3.Error:
        raise RuntimeError('Intake history storage is unavailable') from None

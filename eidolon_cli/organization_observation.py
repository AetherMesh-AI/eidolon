"""Non-provisioning organization projection. Never acquire or start a service."""
from contextlib import closing
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
import sqlite3

from eidolon_cli.organization_config import load_profile_configuration, profile_configuration_scope
from eidolon_cli.organization_policy import _fingerprint, resolve_settings
from eidolon_cli.organization_store import ORGANIZATION_SCHEMA, OrganizationStore


class ObservationError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f'Organization observation: {reason}')


class _ObservationView(OrganizationStore):
    """Private projection-only context, never returned to a caller.

    The normal constructor provisions/migrates/adopts policy. This explicit
    alternative initializes only the fields used by the shared projection.
    Every read is confined to the supplied read-only transaction; inherited
    methods cannot open a second connection or enter a writer.
    """
    def __init__(self, path, conn, settings):
        self.path = path
        self.settings = settings
        policy = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()
        if policy is None:
            raise ObservationError('corrupt')
        self._policy_generation = policy['generation']
        self._policy_fingerprint = _fingerprint(asdict(settings))

    def _connect(self):
        raise RuntimeError('Observation cannot open a mutable store connection')

    def _write(self):
        raise RuntimeError('Observation cannot enter a store writer')


@lru_cache(maxsize=1)
def _required_schema():
    # Compile the same DDL as initialization only in memory. Never execute it
    # against the observed ledger, even when that ledger is empty or older.
    with closing(sqlite3.connect(':memory:')) as schema:
        schema.executescript(ORGANIZATION_SCHEMA)
        names = [row[0] for row in schema.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: frozenset(row[1] for row in schema.execute(
            'PRAGMA table_info("' + name.replace('"', '""') + '")')) for name in names}


def _compatible_schema(conn, tables):
    from eidolon_cli.organization_owner_policy_config import OWNER_POLICY_TABLES
    from eidolon_cli.organization_outcome_contracts import CONTRACT_TABLES
    if tables & CONTRACT_TABLES and not CONTRACT_TABLES <= tables:
        return False
    for name, columns in _required_schema().items():
        if name in CONTRACT_TABLES and not tables & CONTRACT_TABLES:
            continue  # Fully absent typed contracts are a supported legacy ledger.
        if name in OWNER_POLICY_TABLES:
            continue  # Optional owner-editor data does not invalidate legacy observation.
        if name in {'organization_root_grants', 'organization_folder_receipts', 'organization_root_revocations', 'organization_root_revocation_reviews', 'organization_shared_call_receipts', 'organization_shared_scope_pin',
                    'organization_team_setup', 'organization_team_generations', 'organization_route_observations'} and name not in tables:
            continue  # New opt-in audit data must not provision or invalidate an older ledger.
        if name not in tables:
            return False
        present = {row[1] for row in conn.execute('PRAGMA table_info("' + name.replace('"', '""') + '")')}
        if not columns <= present:
            return False
    return True


def _result(state, reason=None, snapshot=None):
    return {'capabilityVersion': 1, 'state': state, 'snapshot': snapshot,
            **({'reason': reason} if reason else {})}


def _storage_failure(exc):
    """Keep snapshot and direct passive reads on the same sanitized contract."""
    code = getattr(exc, 'sqlite_errorcode', 0) & 255
    if code == sqlite3.SQLITE_ERROR and str(exc).startswith(('no such table:', 'no such column:')):
        return _result('unsupported', 'schema')
    reason = {sqlite3.SQLITE_BUSY: 'locked', sqlite3.SQLITE_LOCKED: 'locked',
              sqlite3.SQLITE_CORRUPT: 'corrupt', sqlite3.SQLITE_NOTADB: 'corrupt'}.get(code, 'unreadable')
    raise ObservationError(reason) from None


def read_observation(home: Path, *, projection=None, archival=False):
    """Observe committed records and compare policy without adopting it.

    Use a WAL-aware SQLite read-only connection, not immutable mode. Transient
    WAL read-lock bookkeeping is possible; no row/schema/journal-mode writes
    occur. Missing and older ledgers are not initialized or migrated. File
    configuration can change independently; mutations must still revalidate it.
    """
    home = Path(home)
    path = home / 'organization' / 'state.db'
    try:
        path.stat()
    except FileNotFoundError:
        return _result('unconfigured', 'database_missing')
    except OSError:
        raise ObservationError('unreadable') from None
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=0.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables or tables <= {'organization_metadata', 'organization_metadata_audit',
                                        'organization_metadata_receipts'}:
                return _result('unconfigured', 'execution_not_initialized')
            if not _compatible_schema(conn, tables):
                return _result('unsupported', 'schema')
            if archival:
                from eidolon_cli.organization_policy import persisted_settings
                settings = persisted_settings(conn)
                if settings is None:
                    raise ObservationError('corrupt')
            else:
                try:
                    with profile_configuration_scope(home):
                        settings, _ = load_profile_configuration(home)
                except Exception:
                    raise ObservationError('configuration_invalid') from None
                settings = resolve_settings(conn, settings)
            view = _ObservationView(path, conn, settings)
            if projection is not None:
                return {**_result('available'), 'data': projection(view)}
            return _result('available', snapshot=view._snapshot(conn))
    except sqlite3.Error as exc:
        return _storage_failure(exc)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ObservationError('corrupt') from None

"""Inspect an existing admission without provisioning or waking its organization."""
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def check_submission(home: Path, key: str) -> dict:
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError('Idempotency key must be nonempty text of at most 128 characters')
    key = key.strip()
    result = {'version': 1, 'idempotencyKey': key, 'objective': None}
    path = home / 'organization' / 'state.db'
    if not path.exists():
        # Absence is a point-in-time observation, never proof that an in-flight
        # creation will not commit later. Do not initialize a store here.
        return result
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        row = conn.execute('SELECT o.id,o.title,o.created,coalesce(h.archived,0) AS archived '
                           'FROM objectives o LEFT JOIN objective_history h ON h.objective_id=o.id '
                           'WHERE o.idempotency_key=?', (key,)).fetchone()
        if row is not None:
            result['objective'] = {'id': row['id'], 'title': row['title'],
                                   'createdAt': datetime.fromtimestamp(row['created'], timezone.utc).isoformat(),
                                   'archived': bool(row['archived'])}
        return result
    finally:
        conn.close()

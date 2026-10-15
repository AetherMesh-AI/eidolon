"""Starter setup ledger contracts, shared by initialization and observation."""
TEAM_SETUP_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_team_setup (
 id INTEGER PRIMARY KEY CHECK(id=1), organization_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 result_json TEXT NOT NULL, created REAL NOT NULL);
'''

TEAM_SETUP_SCHEMA += """
CREATE TABLE IF NOT EXISTS organization_team_generations (
 idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL, input_hash TEXT NOT NULL,
 source_revision TEXT NOT NULL, status TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
 reserved_tokens INTEGER NOT NULL DEFAULT 0, reserved_cost TEXT, result_json TEXT,
 shared_receipt TEXT, created REAL NOT NULL);
"""

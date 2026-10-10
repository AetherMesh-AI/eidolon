"""Owner-confirmed, single-successor recovery; original evidence and grants stay intact."""
from __future__ import annotations

import hashlib
import json
import time

from eidolon_cli.organization_budget import budget_view
from eidolon_cli.organization_identity import objective_assignment_view
from eidolon_cli.organization_projects import objective_projects, selected_projects

REPLACEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_replacements (
 source_id TEXT PRIMARY KEY REFERENCES objectives(id),
 replacement_id TEXT NOT NULL UNIQUE REFERENCES objectives(id),
 input_hash TEXT NOT NULL, source_version TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS replacement_immutable_update
 BEFORE UPDATE ON objective_replacements BEGIN SELECT RAISE(ABORT,'Replacement audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS replacement_immutable_delete
 BEFORE DELETE ON objective_replacements BEGIN SELECT RAISE(ABORT,'Replacement audit is immutable'); END;
"""


def replacement_links(conn, objective_id):
    source = conn.execute('SELECT source_id FROM objective_replacements WHERE replacement_id=?', (objective_id,)).fetchone()
    successor = conn.execute('SELECT replacement_id FROM objective_replacements WHERE source_id=?', (objective_id,)).fetchone()
    return {'replacesObjectiveId': source[0] if source else None,
            'replacementObjectiveId': successor[0] if successor else None}


def replacement_preview(conn, settings, objective_id):
    row = conn.execute('SELECT * FROM objectives WHERE id=?', (objective_id,)).fetchone()
    if row is None:
        raise ValueError('Objective not found in this profile')
    control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
    budget = budget_view(conn, objective_id, settings)
    if row['cancelled'] or control['status'] in {'accepted', 'legacy_completed'} or not budget or time.time() < budget['deadlineTimestamp']:
        raise ValueError('Only an expired, unfinished objective can be replaced')
    if replacement_links(conn, objective_id)['replacementObjectiveId']:
        raise ValueError('This objective already has a replacement')
    requests = [dict(item) for item in conn.execute('SELECT * FROM requests WHERE objective_id=? ORDER BY id', (objective_id,))]
    if any(item['status'] == 'running' for item in requests):
        raise ValueError('Execution is still stopping; review replacement after it exits')
    projects = objective_projects(conn, objective_id)
    if projects != selected_projects([project['id'] for project in projects], settings):
        raise ValueError('Project authority changed; review configuration before replacing this objective')
    assignment = objective_assignment_view(conn, objective_id)
    policy = dict(conn.execute('SELECT * FROM organization_policy WHERE id=1').fetchone())
    identities = [dict(item) for item in conn.execute('SELECT * FROM agent_identity WHERE agent_id IN (?,?) ORDER BY agent_id', tuple(assignment.values()))]
    leaders = [dict(item) for item in conn.execute('SELECT * FROM agents WHERE id IN (?,?) ORDER BY id', tuple(assignment.values()))]
    staff = [dict(item) for item in conn.execute('SELECT * FROM staff_state WHERE agent_id IN (?,?) ORDER BY agent_id', tuple(assignment.values()))]
    history = [dict(item) for item in conn.execute('SELECT * FROM objective_history WHERE objective_id=?', (objective_id,))]
    version = hashlib.sha256(json.dumps([dict(row), dict(control), budget, assignment, projects, policy, identities, leaders, staff, requests, history], sort_keys=True).encode()).hexdigest()
    return {'sourceId': objective_id, 'sourceVersion': version,
            'title': row['title'], 'description': control['amended_scope'] or row['description'],
            'acceptanceCriteria': json.loads(control['criteria']), 'priority': f"P{6-row['priority']}",
            'deliveryMode': control['delivery_mode'], 'requiredChecks': json.loads(control['required_checks']),
            **assignment, 'projectIds': [project['id'] for project in projects],
            'allowance': {'modelCalls': budget['modelCallLimit'], 'tokens': budget['tokenLimit'],
                          'costUsd': budget['configuredCostLimitUsd'], 'durationSeconds': settings.objective_timeout_seconds,
                          'projectRuns': min(conn.execute('SELECT max_runs FROM project_execution_budgets WHERE objective_id=?', (objective_id,)).fetchone()[0], settings.max_project_runs),
                          'maxReplans': min(control['max_replans'], settings.max_replans),
                          'maxStages': min(control['max_stages'], settings.max_stages)}}


class OrganizationReplacementStore:
    def preview_replacement(self, objective_id):
        from eidolon_cli.organization_store import _text
        ident = _text(objective_id, 'Objective ID', 128)
        with self._connect() as conn:
            self._require_current_policy(conn)
            return replacement_preview(conn, self.settings, ident)

    def replace_objective(self, source_id, source_version, title, description, acceptance_criteria, *, confirmed):
        from eidolon_cli.organization_store import _id, _text
        from eidolon_cli.organization_acceptance import acceptance_criteria as normalize_criteria
        source_id = _text(source_id, 'Source objective', 128)
        source_version = _text(source_version, 'Reviewed source version', 64)
        if confirmed is not True:
            raise ValueError('Explicit owner confirmation of the fresh allowance and deadline is required')
        title = _text(title, 'Title', 500)
        description = _text(description, 'Description', 30000)
        criteria = normalize_criteria(acceptance_criteria, description)
        digest = hashlib.sha256(json.dumps([source_id, source_version, title, description, criteria]).encode()).hexdigest()
        with self._write() as conn:
            self._require_current_policy(conn)
            previous = conn.execute('SELECT * FROM objective_replacements WHERE source_id=?', (source_id,)).fetchone()
            if previous:
                if previous['input_hash'] != digest:
                    raise ValueError('This objective already has a replacement with different input; open its linked replacement')
                ident = previous['replacement_id']
            else:
                draft = replacement_preview(conn, self.settings, source_id)
                if draft['sourceVersion'] != source_version:
                    raise ValueError('Source or authority changed; review a fresh replacement draft')
                self._cancel_objective(conn, source_id)
                # Cancellation frees the open-work slot in this same transaction.
                # Current/history storage ceilings still apply; failure rolls it all back.
                ident = self._create_objective(conn, title, description, draft['priority'],
                    idempotency_key=_id('replacement'), acceptance_criteria=criteria,
                    delivery_mode=draft['deliveryMode'], required_checks=draft['requiredChecks'],
                    executive_id=draft['executiveId'], manager_id=draft['managerId'], project_ids=draft['projectIds'], project_run_limit=draft['allowance']['projectRuns'])
                allowance = draft['allowance']
                conn.execute('UPDATE objective_budgets SET max_model_calls=?,max_total_tokens=?,max_cost_usd=? WHERE objective_id=?',
                             (allowance['modelCalls'], allowance['tokens'], allowance['costUsd'], ident))
                conn.execute('UPDATE objective_control SET max_replans=?,max_stages=? WHERE objective_id=?',
                             (allowance['maxReplans'], allowance['maxStages'], ident))
                conn.execute('INSERT INTO objective_replacements VALUES (?,?,?,?,?)',
                             (source_id, ident, digest, source_version, time.time()))
                self._event(conn, source_id, f'Owner confirmed replacement {ident}; original evidence and usage are retained.')
                self._event(conn, ident, f'Owner confirmed a fresh finite allowance and deadline, replacing {source_id}.')
        return self._objective_view(ident)

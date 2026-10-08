"""Reject no-progress request loops without inventing semantic equivalence."""
from __future__ import annotations

import json


def normalized_text(text):
    # Paths, code and quoted facts may be case/whitespace-sensitive. Only the
    # canonical request boundary's surrounding-whitespace normalization is safe.
    return text.strip()


def request_signature(kind, outcome, team, dependencies, evidence, proposal=None):
    return json.dumps([kind, normalized_text(outcome), team, sorted(dependencies), sorted(evidence), proposal], sort_keys=True, separators=(',', ':'))


def reject_repeated_requests(conn, parent, proposals, ancestors):
    seen = set()
    for item in proposals:
        key = request_signature(item['type'], item['requestedOutcome'], item.get('team', parent['team']),
                                item['dependencyIds'], item['evidenceIds'], item.get('managementProposal'))
        if key in seen:
            raise ValueError('Duplicate typed requests in one stage would repeat the same work; provide distinct outcomes instead')
        seen.add(key)
    for row in conn.execute('SELECT r.*,c.requested_outcome,c.parent_request_id,c.dependencies,c.evidence_ids FROM requests r JOIN request_contracts c ON c.request_id=r.id WHERE r.objective_id=? AND c.parent_request_id IS NOT NULL', (parent['objective_id'],)):
        for item in proposals:
            if (row['id'] in ancestors and row['type'] == item['type']
                    and row['team'] == item.get('team', parent['team'])
                    and normalized_text(row['requested_outcome']) == normalized_text(item['requestedOutcome'])
                    and set(json.loads(row['evidence_ids'])) == set(item['evidenceIds'])):
                raise ValueError('Repeated ancestor request would create agent ping-pong without progress; owner input is required')
            if row['parent_request_id'] != parent['id']:
                continue
            payload = json.loads(row['payload'])
            previous = request_signature(row['type'], row['requested_outcome'], row['team'],
                                         json.loads(row['dependencies']), json.loads(row['evidence_ids']), payload.get('managementProposal'))
            if previous in seen:
                raise ValueError('This assignment already raised the same request; use its retained response instead of repeating it')


def reject_duplicate_tasks(tasks):
    seen = set()
    for item in tasks:
        if not isinstance(item, dict):
            continue  # The canonical plan validator reports malformed fields.
        values = {name: normalized_text(item[name]) if isinstance(item.get(name), str) else item.get(name)
                  for name in ('title', 'description', 'type', 'team', 'agentId', 'managerId', 'projectId')}
        values['dependsOn'] = item.get('dependsOn', [])
        values['writePaths'] = item.get('writePaths')
        key = json.dumps(values, sort_keys=True)
        if key in seen:
            raise ValueError('Plan contains duplicate tasks with the same outcome, route and dependencies')
        seen.add(key)

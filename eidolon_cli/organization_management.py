"""Transactional, bounded organization membership and explicit context transfers.

Membership changes never provision credentials or expand organization tool policy.
The same persistent identity retains its completed history after a reorganization.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import time
import uuid

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_identity import normalize_agent_memory
from eidolon_cli.organization_roster import configured_staff, parse_roster


MANAGEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_management_receipts (
 idempotency_key TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
 actor_id TEXT NOT NULL REFERENCES agents(id), input_hash TEXT NOT NULL,
 result TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_management_audit (
 id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
 actor_id TEXT NOT NULL REFERENCES agents(id), kind TEXT NOT NULL,
 subject_id TEXT NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
 created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS organization_management_request ON organization_management_audit(request_id,created);
"""

_CONFIG_FIELDS = frozenset({'roster', 'max_inflight', 'max_members', 'transfers'})


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _plain(value):
    return json.loads(json.dumps(value))


def _configuration(settings):
    return {'roster': [_plain(asdict(staff)) for staff in configured_staff(settings)],
            'max_inflight': settings.max_inflight, 'max_members': settings.max_members}


def _audit(conn, actor, request_id, kind, subject, before, after):
    conn.execute('INSERT INTO organization_management_audit VALUES (?,?,?,?,?,?,?,?)',
                 ('change_' + uuid.uuid4().hex, request_id, actor, kind, subject,
                  _json(before), _json(after), time.time()))


def _team_allowed(actor, team):
    return team == actor.team or team in actor.managed_teams or '*' in actor.managed_teams


def _configuration_request(config, expected_generation, idempotency_key):
    if type(expected_generation) is not int or expected_generation < 1:
        raise ValueError('expected_generation must identify the current organization policy')
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError('Configuration idempotency_key must be nonempty text of at most 128 characters')
    digest = hashlib.sha256(_json({'configuration': config, 'generation': expected_generation}).encode()).hexdigest()
    return 'owner:' + idempotency_key, digest


def _validate_owner_config(config, settings):
    if not isinstance(config, dict) or not config or set(config) - _CONFIG_FIELDS:
        raise ValueError('Organization configuration may change only roster, max_inflight, max_members and explicit transfers')
    raw = _plain(asdict(settings))
    raw.update({key: value for key, value in config.items() if key != 'transfers'})
    if raw['roster'] is None:
        raw['roster'] = _configuration(settings)['roster']
    return OrganizationSettings.from_config({'organization': raw})


def _validate_agent_members(proposal, settings, actor):
    if not isinstance(proposal, dict) or set(proposal) - {'members', 'transfers'}:
        raise ValueError('A staffing proposal may contain only members and explicit transfers')
    members = proposal.get('members', [])
    if not isinstance(members, list) or len(members) > settings.max_members:
        raise ValueError('Staffing members must be a bounded list of full roster entries')
    roster = {staff.id: _plain(asdict(staff)) for staff in configured_staff(settings)}
    seen = set()
    for entry in members:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ValueError('Staffing members must be full roster entries with an id')
        if entry['id'] in seen:
            raise ValueError('Staffing members must have unique ids')
        seen.add(entry['id'])
        roster[entry['id']] = entry
    parsed = parse_roster(list(roster.values()), settings)
    current = {staff.id: staff for staff in configured_staff(settings)}
    parents = {staff.id: staff for staff in parsed}
    allowed_models = {(staff.provider, staff.model) for staff in current.values()} | {(None, None)}
    for staff in parsed:
        if staff.id not in seen:
            continue
        old = current.get(staff.id)
        if not _team_allowed(actor, staff.team) or (old and not _team_allowed(actor, old.team)):
            raise ValueError('Staff management is outside the actor managed teams')
        parent = parents.get(staff.manager_id)
        if parent and (old is None or old.manager_id != staff.manager_id) and not _team_allowed(actor, parent.team):
            raise ValueError('Staff management cannot change reporting lines outside its managed teams')
        if not set(staff.authority).issubset(actor.authority):
            raise ValueError('Staff management cannot grant authority the actor does not hold')
        if any((team == "*" and "*" not in actor.managed_teams) or not _team_allowed(actor, team)
               for team in staff.managed_teams):
            raise ValueError('Staff management cannot expand managed teams; wildcard authority must be explicit')
        if not set(staff.tool_grants).issubset(old.tool_grants if old else ()):
            raise ValueError('Staff management cannot add tool grants; new members start tool-free')
        if (staff.provider, staff.model) not in allowed_models:
            raise ValueError('Staff management may select only an already configured provider and model pair')
        work = {kind for kind in staff.capabilities if kind.startswith('work.')}
        if not work.issubset(settings.capabilities):
            raise ValueError('Staff management cannot expand organization work capabilities')
    return replace(settings, roster=parsed)


def _transfer_rows(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('Transfers must be a list of at most 64 explicit source and destination entries')
    seen_tasks, seen_pairs = set(), set()
    for transfer in value:
        if not isinstance(transfer, dict) or set(transfer) - {'fromAgentId', 'toAgentId', 'taskIds', 'includeMemory'}:
            raise ValueError('Transfers must contain fromAgentId, toAgentId, taskIds and includeMemory only')
        source, target = transfer.get('fromAgentId'), transfer.get('toAgentId')
        tasks = transfer.get('taskIds', [])
        if (not isinstance(source, str) or not isinstance(target, str) or not source or not target
                or len(source) > 128 or len(target) > 128 or source == target or (source, target) in seen_pairs):
            raise ValueError('Each transfer needs distinct, explicit source and destination agents')
        if (not isinstance(tasks, list) or len(tasks) > 100
                or any(not isinstance(task, str) or not 1 <= len(task) <= 128 for task in tasks)
                or len(set(tasks)) != len(tasks) or seen_tasks.intersection(tasks)):
            raise ValueError('Transfer taskIds must identify unique bounded open work, without overlapping transfers')
        include_memory = transfer.get('includeMemory', False)
        if type(include_memory) is not bool:
            raise ValueError('Transfer includeMemory must be a boolean')
        if not tasks and not include_memory:
            raise ValueError('A transfer must explicitly select open tasks or bounded memory')
        seen_tasks.update(tasks)
        seen_pairs.add((source, target))
        yield source, target, tasks, include_memory


def _copy_memory(conn, source, target):
    old = conn.execute('SELECT * FROM agent_context WHERE agent_id=?', (source,)).fetchone()
    new = conn.execute('SELECT * FROM agent_context WHERE agent_id=?', (target,)).fetchone()
    source_memory = normalize_agent_memory(json.loads(old['memory']))
    merged = normalize_agent_memory(json.loads(new['memory']))
    for key, items in source_memory.items():
        merged[key] = list(dict.fromkeys([*merged.get(key, []), *items]))[-24:]
        while sum(map(len, merged[key])) > 4000:
            merged[key].pop(0)
    conn.execute('UPDATE agent_context SET memory=?,revision=revision+1,updated=? WHERE agent_id=?',
                 (_json(merged), time.time(), target))
    return {'sourceRevision': old['revision'], 'destinationRevision': new['revision'] + 1,
            'retainedItems': sum(len(values) for values in merged.values())}


def _transfer_tasks(conn, source, target, task_ids, actor_id, actor=None):
    moved_objectives = set()
    for task_id in task_ids:
        task = conn.execute('SELECT t.*,a.agent_id AS assigned_agent,a.manager_id AS assigned_manager '
                            'FROM tasks t LEFT JOIN task_assignments a ON a.task_id=t.id WHERE t.id=?', (task_id,)).fetchone()
        if task is None or task['status'] in {'completed', 'cancelled'}:
            raise ValueError('Transfers must identify existing open tasks')
        if actor and not _team_allowed(actor, task['team']):
            raise ValueError('Transferred work is outside the actor managed teams')
        if source['role'] == 'Worker':
            # Unpinned plans acquire an exact owner when a stage pauses. Only
            # that durable continuation, never a past claim or artifact author,
            # can authorize an explicit transfer of otherwise unassigned work.
            continuation_owners = {row[0] for row in conn.execute(
                'SELECT DISTINCT c.agent_id FROM request_continuations c JOIN requests r ON r.id=c.request_id '
                "WHERE r.task_id=? AND r.type=? AND r.status IN ('waiting_response','queued','pending_intervention')",
                (task_id, task['type']))} if task['assigned_agent'] is None else set()
            if task['assigned_agent'] != source['id'] and continuation_owners != {source['id']}:
                raise ValueError('Transfer source does not own the exact task assignment or paused continuation')
            if task['type'] not in json.loads(target['accepts']):
                raise ValueError('Transfer destination does not accept the task capability')
            conn.execute('UPDATE task_assignments SET agent_id=?,manager_id=?,assigned_by_id=? WHERE task_id=?',
                         (target['id'], target['manager_id'], actor_id, task_id))
            conn.execute('UPDATE tasks SET team=? WHERE id=?', (target['team'], task_id))
            conn.execute("UPDATE requests SET team=?,agent_id=NULL WHERE task_id=? AND status NOT IN ('completed','cancelled')",
                         (target['team'], task_id))
            conn.execute('UPDATE request_continuations SET agent_id=? WHERE agent_id=? AND request_id IN '
                         "(SELECT id FROM requests WHERE task_id=? AND status NOT IN ('completed','cancelled'))",
                         (target['id'], source['id'], task_id))
        elif source['role'] == 'Manager':
            if task['assigned_manager'] != source['id']:
                raise ValueError('Transfer source does not manage the exact task')
            if task['team'] != target['team'] and target['id'] != 'manager':
                raise ValueError('Manager task transfers must preserve the task team; transfer its worker explicitly to change team')
            worker = conn.execute('SELECT manager_id FROM agents WHERE id=?', (task['assigned_agent'],)).fetchone()
            assignee = task['assigned_agent'] if worker and worker['manager_id'] == target['id'] else None
            conn.execute('UPDATE task_assignments SET agent_id=?,manager_id=?,assigned_by_id=? WHERE task_id=?',
                         (assignee, target['id'], actor_id, task_id))
        else:
            assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (task['objective_id'],)).fetchone()
            if assignment is None or (assignment['executive_id'] != source['id'] and task['objective_id'] not in moved_objectives):
                raise ValueError('Transfer source does not oversee the exact task objective')
        if source['role'] != 'Worker':
            field = 'manager_id' if source['role'] == 'Manager' else 'executive_id'
            assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (task['objective_id'],)).fetchone()
            if assignment and assignment[field] == source['id']:
                open_ids = {row[0] for row in conn.execute(
                    "SELECT id FROM tasks WHERE objective_id=? AND status NOT IN ('completed','cancelled')", (task['objective_id'],))}
                if not open_ids.issubset(task_ids):
                    if source['role'] == 'Executive':
                        raise ValueError('An executive handoff must explicitly select every open task in its objective')
                    continue
                if source['role'] == 'Manager':
                    conn.execute('UPDATE objective_assignments SET manager_id=?,executive_id=? WHERE objective_id=?',
                                 (target['id'], target['manager_id'], task['objective_id']))
                    kinds = ('request.plan', 'request.integrate')
                else:
                    manager = conn.execute('SELECT manager_id FROM agents WHERE id=?', (assignment['manager_id'],)).fetchone()
                    if manager is None or manager['manager_id'] != target['id']:
                        raise ValueError('Executive handoff requires the objective manager to report to its destination')
                    conn.execute('UPDATE objective_assignments SET executive_id=? WHERE objective_id=?',
                                 (target['id'], task['objective_id']))
                    kinds = ('request.accept',)
                moved_objectives.add(task['objective_id'])
                for kind in kinds:
                    conn.execute("UPDATE requests SET team=?,agent_id=NULL WHERE objective_id=? AND type=? AND status NOT IN ('completed','cancelled')",
                                 (target['team'], task['objective_id'], kind))
                conn.execute('UPDATE request_continuations SET agent_id=? WHERE agent_id=? AND request_id IN '
                             "(SELECT id FROM requests WHERE objective_id=? AND status NOT IN ('completed','cancelled'))",
                             (target['id'], source['id'], task['objective_id']))


def _validate_open_assignments(conn):
    rows = conn.execute("SELECT t.id,t.team,t.type,a.agent_id,a.manager_id FROM tasks t "
                        "JOIN task_assignments a ON a.task_id=t.id WHERE t.status NOT IN ('completed','cancelled')")
    for row in rows:
        manager = conn.execute('SELECT * FROM agents WHERE id=?', (row['manager_id'],)).fetchone()
        retired = conn.execute("SELECT 1 FROM staff_state WHERE agent_id=? AND active=0",
                               (row['manager_id'],)).fetchone()
        if (manager is None or manager['role'] != 'Manager' or retired
                or (manager['id'] != 'manager' and manager['team'] != row['team'])):
            raise ValueError('Reorganization must explicitly transfer open tasks before retiring or replacing their manager')
        if row['agent_id'] is None:
            continue
        worker = conn.execute('SELECT * FROM agents WHERE id=?', (row['agent_id'],)).fetchone()
        state = conn.execute('SELECT source FROM staff_state WHERE agent_id=?', (row['agent_id'],)).fetchone()
        if (worker is None or worker['role'] != 'Worker' or worker['manager_id'] != row['manager_id']
                or worker['team'] != row['team'] or row['type'] not in json.loads(worker['accepts'])
                or (state and state['source'] == 'retired')):
            raise ValueError('Reorganization requires an explicit transfer for each affected open task')


    for row in conn.execute("SELECT a.* FROM objective_assignments a JOIN objectives o ON o.id=a.objective_id "
                            "JOIN objective_control c ON c.objective_id=o.id WHERE o.cancelled=0 AND c.status!='accepted'"):
        manager = conn.execute('SELECT * FROM agents WHERE id=?', (row['manager_id'],)).fetchone()
        executive = conn.execute('SELECT * FROM agents WHERE id=?', (row['executive_id'],)).fetchone()
        retired = conn.execute("SELECT 1 FROM staff_state WHERE agent_id IN (?,?) AND active=0",
                               (row['manager_id'], row['executive_id'])).fetchone()
        if (not manager or not executive or manager['role'] != 'Manager' or executive['role'] != 'Executive'
                or manager['manager_id'] != executive['id'] or retired):
            raise ValueError('Reorganization requires an explicit full open-objective handoff before changing its leadership')


class OrganizationManagementStore:
    def configuration_snapshot(self, conn=None):
        if conn is None:
            with self._connect() as connection:
                return self.configuration_snapshot(connection)
        from eidolon_cli.organization_policy import persisted_settings
        return _configuration(persisted_settings(conn) or self.settings)

    def management_view(self, conn):
        from eidolon_cli.organization_store import _iso
        from eidolon_cli.organization_policy import persisted_settings
        settings = persisted_settings(conn) or self.settings
        changes = [{'id': item['id'], 'requestId': item['request_id'], 'actorId': item['actor_id'],
                    'kind': item['kind'], 'subjectId': item['subject_id'],
                    'before': json.loads(item['before_state']), 'after': json.loads(item['after_state']),
                    'createdAt': _iso(item['created'])}
                   for item in conn.execute('SELECT * FROM organization_management_audit ORDER BY created DESC,id DESC LIMIT 50')]
        row = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()
        return {'generation': row['generation'], 'configuration': self.configuration_snapshot(conn),
                'recentChanges': changes, 'allowedTools': list(settings.tool_grants),
                'allowedCapabilities': list(settings.capabilities),
                'maxRequestDepth': settings.max_request_depth,
                'maxRequestsPerStage': settings.max_requests_per_stage}

    @staticmethod
    def _management_receipt(conn, key, input_hash):
        row = conn.execute('SELECT * FROM organization_management_receipts WHERE idempotency_key=?', (key,)).fetchone()
        if row and row['input_hash'] != input_hash:
            raise ValueError('Management idempotency key belongs to a different configuration or request')
        return json.loads(row['result']) if row else None

    def configuration_recorded(self, config, *, expected_generation, idempotency_key):
        key, input_hash = _configuration_request(config, expected_generation, idempotency_key)
        with self._connect() as conn:
            return self._management_receipt(conn, key, input_hash) is not None

    def configure_organization(self, config, *, expected_generation, idempotency_key):
        key, input_hash = _configuration_request(config, expected_generation, idempotency_key)
        with self._write() as conn:
            receipt = self._management_receipt(conn, key, input_hash)
            if receipt is not None:
                return receipt
            self._require_current_policy(conn)
            if expected_generation != self._policy_generation:
                raise ValueError('Organization configuration changed; reload its current generation before saving')
            if conn.execute("SELECT 1 FROM requests WHERE status='running' LIMIT 1").fetchone():
                raise ValueError('Wait for running organization requests to finish before configuring members')
            updated = _validate_owner_config(config, self.settings)
            result = self._apply_configuration(conn, updated, config.get('transfers', []), 'owner', None)
            conn.execute('INSERT INTO organization_management_receipts VALUES (?,?,?,?,?,?)',
                         (key, None, 'owner', input_hash, _json(result), time.time()))
            return result

    def apply_management(self, conn, request, proposal, actor_id):
        """Apply an owned staffing request within its finish transaction only."""
        self._require_current_policy(conn)
        current = conn.execute('SELECT * FROM requests WHERE id=?', (request['id'],)).fetchone()
        owner = actor_id == 'owner'
        if current is None or current['type'] != 'request.hire':
            raise ValueError('Staff management requires an existing hire request')
        payload = json.loads(current['payload'])
        if payload.get('managementProposal') != proposal:
            raise ValueError('Staff management must apply exactly the proposal retained on its request')
        actor = None if owner else self._staff(actor_id)
        if owner:
            if current['status'] != 'pending_intervention':
                raise ValueError('Owner staffing approval requires the exact pending hire request')
        else:
            if (current['status'] != 'running' or current['agent_id'] != actor_id
                    or current['token'] != request['token'] or not current['lease'] or current['lease'] <= time.time()):
                raise ValueError('Staff management requires the exact currently owned hire request')
            if actor is None or not actor.enabled or 'staff.manage' not in actor.authority:
                raise ValueError('Staff management requires explicit staff.manage authority')
            if 'request.hire' not in actor.capabilities or not _team_allowed(actor, current['team']):
                raise ValueError('Staff management request is outside the actor accepted route and managed teams')
        key = 'request:' + current['id']
        input_hash = hashlib.sha256(_json({'actor': actor_id, 'proposal': proposal}).encode()).hexdigest()
        receipt = self._management_receipt(conn, key, input_hash)
        if receipt is not None:
            return receipt
        if conn.execute("SELECT 1 FROM requests WHERE status='running' AND id<>? LIMIT 1", (current['id'],)).fetchone():
            raise ValueError('Wait for other running requests before applying a staffing reorganization')
        if owner:
            if not isinstance(proposal, dict) or set(proposal) - {'members', 'transfers'}:
                raise ValueError('A staffing proposal may contain only members and explicit transfers')
            members = proposal.get('members', [])
            if not isinstance(members, list) or any(not isinstance(entry, dict) or not isinstance(entry.get('id'), str) for entry in members):
                raise ValueError('Staffing members must be full roster entries with an id')
            if len({entry['id'] for entry in members}) != len(members):
                raise ValueError('Staffing members must have unique ids')
            roster = {staff.id: _plain(asdict(staff)) for staff in configured_staff(self.settings)}
            roster.update({entry['id']: entry for entry in members})
            updated = _validate_owner_config({'roster': list(roster.values())}, self.settings)
        else:
            updated = _validate_agent_members(proposal, self.settings, actor)
        if not proposal.get('members') and not proposal.get('transfers'):
            raise ValueError('Staffing proposal must contain at least one member or explicit transfer')
        result = self._apply_configuration(conn, updated, proposal.get('transfers', []), actor_id, current['id'], actor)
        conn.execute('INSERT INTO organization_management_receipts VALUES (?,?,?,?,?,?)',
                     (key, current['id'], actor_id, input_hash, _json(result), time.time()))
        return result

    def _apply_configuration(self, conn, updated, transfers, actor_id, request_id, actor=None):
        before = _configuration(self.settings)
        transfer_rows = list(_transfer_rows(transfers))
        before_members = {staff['id']: staff for staff in before['roster']}
        if actor:
            for staff in configured_staff(updated):
                if before_members.get(staff.id) == _plain(asdict(staff)):
                    continue
                historical = conn.execute('SELECT team FROM agents WHERE id=?', (staff.id,)).fetchone()
                if historical and not _team_allowed(actor, historical['team']):
                    raise ValueError('Staff management cannot adopt a retained identity outside its managed teams')
        sources = {}
        for source, target, tasks, include_memory in transfer_rows:
            row = conn.execute('SELECT * FROM agents WHERE id=?', (source,)).fetchone()
            if row is None:
                raise ValueError('Transfer source must identify an existing persistent agent')
            sources[source] = dict(row)
        if request_id and actor_id != 'owner':
            acting = next((staff for staff in configured_staff(updated) if staff.id == actor_id), None)
            if acting is None or not acting.enabled or "request.hire" not in acting.capabilities:
                raise ValueError("Staff management cannot disable its own active request route")
        self.settings = updated
        self._sync_staff(conn)
        self._migrate_identities(conn)
        current = {staff.id: staff for staff in configured_staff(updated)}
        previous_members = {staff["id"]: staff for staff in before["roster"]}
        for staff in current.values():
            if staff.enabled and previous_members.get(staff.id) != _plain(asdict(staff)):
                conn.execute("UPDATE staff_state SET active=1 WHERE agent_id=?", (staff.id,))
        for source, target, tasks, include_memory in transfer_rows:
            destination = conn.execute('SELECT * FROM agents WHERE id=?', (target,)).fetchone()
            source_row = sources[source]
            if destination is None or target not in current or not current[target].enabled:
                raise ValueError('Transfer destination must identify an enabled configured member')
            if source_row['role'] != destination['role']:
                raise ValueError('Transfers require source and destination members with the same role')
            if actor and (not _team_allowed(actor, source_row['team']) or not _team_allowed(actor, destination['team'])):
                raise ValueError('Context and work transfers must remain within the actor managed teams')
            _transfer_tasks(conn, source_row, destination, tasks, actor_id, actor)
            memory = _copy_memory(conn, source, target) if include_memory else None
            _audit(conn, actor_id, request_id, 'transfer', source,
                   {'fromAgentId': source, 'team': source_row['team']},
                   {'toAgentId': target, 'team': destination['team'], 'taskIds': tasks,
                    'includeMemory': include_memory, 'memory': memory})
        _validate_open_assignments(conn)
        after = _configuration(updated)
        conn.execute('INSERT INTO organization_configuration VALUES (1,?,?) ON CONFLICT(id) '
                     'DO UPDATE SET configuration=excluded.configuration,updated=excluded.updated', (_json(after), time.time()))
        self._adopt_policy(conn, exclude_request_id=request_id, force=True)
        previous = {staff['id']: staff for staff in before['roster']}
        members = {staff['id']: staff for staff in after['roster']}
        for ident in sorted(previous.keys() | members.keys()):
            if previous.get(ident) != members.get(ident):
                _audit(conn, actor_id, request_id, 'member', ident, previous.get(ident), members.get(ident))
        _audit(conn, actor_id, request_id, 'configuration', 'organization',
               {key: value for key, value in before.items() if key != 'roster'},
               {key: value for key, value in after.items() if key != 'roster'})
        objective = conn.execute('SELECT objective_id FROM requests WHERE id=?', (request_id,)).fetchone() if request_id else None
        self._event(conn, objective['objective_id'] if objective else None,
                    'Persistent organization configuration updated; identity and completed history retained.',
                    'delegation', actor_id)
        return self.management_view(conn)

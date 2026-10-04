"""Durable organization identities, scoped working memory and assignment history.

Identity is independent of an execution lease. Memory is a bounded, structured
working set; the append-only history retains provenance even after that working
set rolls forward. Neither is evidence or an authorization to use a tool.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time
import uuid


IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_identity (
 agent_id TEXT PRIMARY KEY REFERENCES agents(id), identity_id TEXT NOT NULL UNIQUE,
 created REAL NOT NULL, responsibilities TEXT NOT NULL, purpose TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_context (
 agent_id TEXT PRIMARY KEY REFERENCES agents(id), memory TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, updated REAL);
CREATE TABLE IF NOT EXISTS agent_history (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), agent_id TEXT NOT NULL REFERENCES agents(id),
 objective_id TEXT NOT NULL REFERENCES objectives(id), task_id TEXT REFERENCES tasks(id),
 request_type TEXT NOT NULL, summary TEXT NOT NULL, evidence_ids TEXT NOT NULL,
 created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS agent_history_recent ON agent_history(agent_id,created DESC,request_id);
CREATE TABLE IF NOT EXISTS task_assignments (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), agent_id TEXT REFERENCES agents(id),
 manager_id TEXT NOT NULL REFERENCES agents(id), assigned_by_id TEXT NOT NULL REFERENCES agents(id));
CREATE TABLE IF NOT EXISTS objective_assignments (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id),
 executive_id TEXT NOT NULL REFERENCES agents(id), manager_id TEXT NOT NULL REFERENCES agents(id));
"""

_MEMORY_FIELDS = ('facts', 'decisions', 'lessons', 'openQuestions')
_MAX_MEMORY_ITEMS = 24
_MAX_MEMORY_TEXT = 1000
_MAX_MEMORY_FIELD_CHARS = 4000
_PURPOSES = {
    'owner': 'Set objectives and resolve decisions requiring human authority.',
    'executive': 'Independently assess whether integrated outcomes meet the owner’s objective.',
    'director': 'Coordinate staffing and activate explicitly configured agents.',
    'manager': 'Plan scoped work, coordinate workers and integrate reviewed outcomes.',
}


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def normalize_agent_memory(value):
    """Validate an incremental memory proposal, never arbitrary transcripts/data."""
    if not isinstance(value, dict) or set(value) - set(_MEMORY_FIELDS):
        raise ValueError('Agent memory must contain only facts, decisions, lessons and openQuestions')
    normalized = {}
    for key, items in value.items():
        if (not isinstance(items, list) or len(items) > _MAX_MEMORY_ITEMS
                or any(not isinstance(item, str) or not item.strip()
                       or len(item) > _MAX_MEMORY_TEXT for item in items)):
            raise ValueError(f'Agent memory {key} must contain at most {_MAX_MEMORY_ITEMS} nonempty '
                             f'text items of at most {_MAX_MEMORY_TEXT} characters')
        normalized[key] = list(dict.fromkeys(item.strip() for item in items))
    return normalized


def agent_identity_view(conn, agent_id):
    row = conn.execute('SELECT * FROM agent_identity WHERE agent_id=?', (agent_id,)).fetchone()
    if row is None:
        raise ValueError('Agent identity not found')
    return {'identityId': row['identity_id'], 'createdAt': _iso(row['created']),
            'responsibilities': json.loads(row['responsibilities']), 'purpose': row['purpose']}


def agent_context_view(conn, agent_id):
    row = conn.execute('SELECT * FROM agent_context WHERE agent_id=?', (agent_id,)).fetchone()
    if row is None:
        raise ValueError('Agent context not found')
    history = [{'requestId': item['request_id'], 'objectiveId': item['objective_id'],
                'objectiveTitle': item['objective_title'], 'taskId': item['task_id'],
                'requestType': item['request_type'], 'summary': item['summary'],
                'evidenceIds': json.loads(item['evidence_ids']), 'createdAt': _iso(item['created'])}
               for item in conn.execute('SELECT h.*,o.title AS objective_title FROM agent_history h '
                                        'JOIN objectives o ON o.id=h.objective_id WHERE h.agent_id=? '
                                        'ORDER BY h.created DESC,h.request_id DESC LIMIT 12', (agent_id,))]
    return {'memory': json.loads(row['memory']), 'revision': row['revision'],
            'updatedAt': _iso(row['updated']), 'recentHistory': history,
            'contextSummary': history[0]['summary'] if history else 'No completed work retained yet.'}


def task_assignment_view(conn, task_id):
    row = conn.execute('SELECT * FROM task_assignments WHERE task_id=?', (task_id,)).fetchone()
    return ({'agentId': row['agent_id'], 'managingAgentId': row['manager_id'],
             'assignedById': row['assigned_by_id']} if row else
            {'agentId': None, 'managingAgentId': 'manager', 'assignedById': 'manager'})


def objective_assignment_view(conn, objective_id):
    row = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (objective_id,)).fetchone()
    return {'executiveId': row['executive_id'], 'managerId': row['manager_id']} if row else {
        'executiveId': 'executive', 'managerId': 'manager'}


def _leader_enabled(conn, agent):
    state = conn.execute('SELECT active FROM staff_state WHERE agent_id=?', (agent['id'],)).fetchone()
    return state is None or bool(state['active'])


def _stage_record(conn, request, result, *, historical=False):
    """Use authoritative artifacts for provenance, including pre-upgrade history."""
    artifacts = conn.execute('SELECT id,summary,created FROM evidence WHERE request_id=? '
                             'UNION ALL SELECT id,summary,created FROM objective_deliverables '
                             'WHERE request_id=?', (request['id'], request['id'])).fetchall()
    review = conn.execute('SELECT summary,evidence_ids,created FROM reviews WHERE request_id=? '
                          'UNION ALL SELECT summary,evidence_ids,created FROM objective_acceptances '
                          'WHERE request_id=?', (request['id'], request['id'])).fetchone()
    summary = (artifacts[0]['summary'] if artifacts else review['summary'] if review else
               result.get('summary') or f"Completed {request['type']}.")
    evidence = [item['id'] for item in artifacts] if artifacts else json.loads(review['evidence_ids']) if review else []
    created = (artifacts[0]['created'] if artifacts else review['created'] if review else
               request['created'] if historical else time.time())
    return str(summary)[:2000], evidence, created


class OrganizationIdentityStore:
    def _migrate_identities(self, conn):
        # Update labels/reporting lines only. Historical request, event and
        # artifact foreign keys retain their exact authors and authority IDs.
        conn.execute("UPDATE agents SET role='Manager' WHERE role='Director'")
        conn.execute("UPDATE agents SET role='Worker' WHERE role='Employee'")
        conn.execute("UPDATE agents SET manager_id='executive' WHERE role='Manager' AND "
                     "manager_id IN (SELECT id FROM agents WHERE role='Manager')")
        conn.execute("UPDATE agents SET name='Staffing manager' WHERE id='director' AND name='Director'")
        configured = {staff.id: staff for staff in self.settings.roster or ()}
        for agent in conn.execute('SELECT * FROM agents').fetchall():
            staff = configured.get(agent['id'])
            responsibilities = list(getattr(staff, 'responsibilities', ())) or json.loads(agent['accepts'])
            purpose = getattr(staff, 'purpose', '') or _PURPOSES.get(agent['id']) or (
                'Independently review evidence against scoped acceptance criteria.'
                if 'request.review' in json.loads(agent['accepts']) else
                f"Carry out the configured {agent['team']} responsibilities as a {agent['role'].lower()}.")
            earliest = conn.execute('SELECT min(created) FROM requests WHERE agent_id=?', (agent['id'],)).fetchone()[0]
            conn.execute('INSERT OR IGNORE INTO agent_identity VALUES (?,?,?,?,?)',
                         (agent['id'], 'identity_' + uuid.uuid4().hex, earliest or time.time(),
                          json.dumps(responsibilities), purpose))
            # Role configuration can evolve without replacing identity or memory.
            state = conn.execute('SELECT source FROM staff_state WHERE agent_id=?', (agent['id'],)).fetchone()
            if staff is not None and (state is None or state['source'] != 'retired'):
                conn.execute('UPDATE agent_identity SET responsibilities=?,purpose=? WHERE agent_id=?',
                             (json.dumps(responsibilities), purpose, agent['id']))
            conn.execute('INSERT OR IGNORE INTO agent_context(agent_id,memory) VALUES (?,?)',
                         (agent['id'], json.dumps({key: [] for key in _MEMORY_FIELDS})))
        conn.execute("INSERT OR IGNORE INTO objective_assignments SELECT id,'executive','manager' FROM objectives")
        # Backfill old completed stages from retained ledger artifacts, not an
        # invented conversational transcript. Request IDs make reopen idempotent.
        for request in conn.execute("SELECT r.* FROM requests r LEFT JOIN agent_history h ON h.request_id=r.id "
                                    "WHERE r.status='completed' AND r.agent_id IS NOT NULL AND h.request_id IS NULL").fetchall():
            summary, evidence, created = _stage_record(conn, request, {}, historical=True)
            conn.execute('INSERT INTO agent_history VALUES (?,?,?,?,?,?,?,?)',
                         (request['id'], request['agent_id'], request['objective_id'], request['task_id'],
                          request['type'], summary, json.dumps(evidence), created))

    def _remember_agent_finish(self, conn, request, result):
        """Only called after a valid owned finish, inside that same transaction."""
        proposal = normalize_agent_memory(result['memory']) if 'memory' in result else {}
        row = conn.execute('SELECT * FROM agent_context WHERE agent_id=?', (request['agent_id'],)).fetchone()
        memory = json.loads(row['memory'])
        for key, items in proposal.items():
            # Repeated information is refreshed to the end; recent context has
            # bounded prompt cost while immutable stage history keeps provenance.
            memory[key] = [item for item in memory[key] if item not in items] + items
            memory[key] = memory[key][-_MAX_MEMORY_ITEMS:]
            while sum(map(len, memory[key])) > _MAX_MEMORY_FIELD_CHARS:
                memory[key].pop(0)
        summary, evidence, created = _stage_record(conn, request, result)
        conn.execute('INSERT INTO agent_history VALUES (?,?,?,?,?,?,?,?)',
                     (request['id'], request['agent_id'], request['objective_id'], request['task_id'],
                      request['type'], summary, json.dumps(evidence), created))
        conn.execute('UPDATE agent_context SET memory=?,revision=revision+1,updated=? WHERE agent_id=?',
                     (json.dumps(memory), created, request['agent_id']))

    def _assign_task(self, conn, task_id, specification, planner_id):
        task = conn.execute('SELECT team,type FROM tasks WHERE id=?', (task_id,)).fetchone()
        agent_id, manager_id = specification.get('agentId'), specification.get('managerId')
        if any(value is not None and (not isinstance(value, str) or not value.strip())
               for value in (agent_id, manager_id)):
            raise ValueError('Task agentId and managerId must identify configured agents')
        worker = conn.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone() if agent_id else None
        if agent_id and (worker is None or worker['role'] != 'Worker' or worker['team'] != task['team']
                         or task['type'] not in json.loads(worker['accepts'])):
            raise ValueError('Task agentId must identify a Worker accepting the exact team and capability')
        managers = conn.execute("SELECT * FROM agents WHERE role='Manager' AND team=? ORDER BY id", (task['team'],)).fetchall()
        managers = [manager for manager in managers if _leader_enabled(conn, manager)]
        if manager_id is None:
            from eidolon_cli.organization_roster import staff_unavailability
            matching_managers = set()
            for candidate in conn.execute("SELECT * FROM agents WHERE role='Worker' AND team=?", (task['team'],)):
                state = conn.execute('SELECT source FROM staff_state WHERE agent_id=?', (candidate['id'],)).fetchone()
                configured = self._staff(candidate['id'])
                if (task['type'] in json.loads(candidate['accepts'])
                        and not (state and state['source'] == 'retired')
                        and (configured is None or not staff_unavailability(configured, self.settings, task['type']))):
                    matching_managers.add(candidate['manager_id'])
            scoped = [row['id'] for row in managers if row['id'] in matching_managers]
            manager_id = worker['manager_id'] if worker else (
                planner_id if planner_id in scoped else next(iter(scoped), None)
                or ('manager' if 'manager' in matching_managers else None)
                # Impossible routes still belong to a manager and must reach
                # visible intervention rather than being silently substituted.
                or next((row['id'] for row in managers if row['id'] == planner_id), None) or 'manager')
        manager = conn.execute('SELECT * FROM agents WHERE id=?', (manager_id,)).fetchone()
        if (manager is None or manager['role'] != 'Manager' or not _leader_enabled(conn, manager)
                or (manager['team'] != task['team'] and manager_id != 'manager')):
            raise ValueError('Task managerId must identify an enabled Manager for the task team')
        if worker is not None and worker['manager_id'] != manager_id:
            raise ValueError('Task agentId must report to its managing agent')
        conn.execute('INSERT INTO task_assignments VALUES (?,?,?,?)',
                     (task_id, agent_id, manager_id, planner_id))

    @staticmethod
    def _assign_objective(conn, objective_id, executive_id=None, manager_id=None):
        manager_id = 'manager' if manager_id is None else manager_id
        if not isinstance(manager_id, str) or not manager_id:
            raise ValueError('Objective manager_id must identify a configured Manager')
        manager = conn.execute('SELECT * FROM agents WHERE id=?', (manager_id,)).fetchone()
        if manager is None or manager['role'] != 'Manager' or not _leader_enabled(conn, manager):
            raise ValueError('Objective manager_id must identify an enabled Manager')
        if not {'request.plan', 'request.integrate'}.issubset(json.loads(manager['accepts'])):
            raise ValueError('Objective Manager must accept both request.plan and request.integrate')
        executive_id = manager['manager_id'] if executive_id is None else executive_id
        if not isinstance(executive_id, str) or not executive_id:
            raise ValueError('Objective executive_id must identify a configured Executive')
        executive = conn.execute('SELECT * FROM agents WHERE id=?', (executive_id,)).fetchone()
        if executive is None or executive['role'] != 'Executive' or not _leader_enabled(conn, executive):
            raise ValueError('Objective executive_id must identify an enabled Executive')
        if 'request.accept' not in json.loads(executive['accepts']):
            raise ValueError('Objective Executive must accept request.accept')
        if manager['manager_id'] != executive_id:
            raise ValueError('Objective Manager must report to the selected Executive')
        conn.execute('INSERT INTO objective_assignments VALUES (?,?,?)', (objective_id, executive_id, manager_id))
        return manager

    @staticmethod
    def _objective_agent(conn, objective_id, role):
        field = {'Manager': 'managerId', 'Executive': 'executiveId'}[role]
        return conn.execute('SELECT * FROM agents WHERE id=?',
                            (objective_assignment_view(conn, objective_id)[field],)).fetchone()

    @staticmethod
    def _assignment_allows(conn, request, agent):
        role = {'request.plan': 'Manager', 'request.integrate': 'Manager',
                'request.accept': 'Executive'}.get(request['type'])
        if role:
            return OrganizationIdentityStore._objective_agent(conn, request['objective_id'], role)['id'] == agent['id']
        if not request['task_id'] or request['type'].startswith('request.'):
            return True
        assignment = conn.execute('SELECT * FROM task_assignments WHERE task_id=?', (request['task_id'],)).fetchone()
        if assignment is None:
            return True
        return (agent['manager_id'] == assignment['manager_id']
                and (assignment['agent_id'] is None or assignment['agent_id'] == agent['id']))

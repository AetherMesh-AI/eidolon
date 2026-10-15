"""Shared goal ceilings projected from canonical reservations, never a second wallet."""
from decimal import Decimal
import hashlib
import json
import re
import time

GOAL_TABLES = frozenset({'organization_goal_revisions', 'organization_goal_members', 'organization_goal_receipts'})
GOAL_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_goal_revisions (
 id TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL, sha256 TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS organization_goal_members (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), goal_id TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS goal_members_goal ON organization_goal_members(goal_id);
CREATE TABLE IF NOT EXISTS organization_goal_receipts (
 idempotency_key TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, result TEXT NOT NULL);
'''
for _table in GOAL_TABLES:
    for _op in ('UPDATE', 'DELETE'):
        GOAL_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                        "BEGIN SELECT RAISE(ABORT,'Goal allowance history is immutable'); END;\n")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('Goal and objective IDs must be canonical')
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Goal allowance requires bounded integer limits')
    return value


def normalize_limits(value):
    from eidolon_cli.organization_budget import money
    if not isinstance(value, dict) or set(value) != {'modelCalls','tokens','costUsd','stages','projectRuns','deadlineTimestamp','completionReserve'}:
        raise ValueError('Goal allowance requires exact finite limit fields')
    result = {key: _integer(value[key], 1, maximum) for key, maximum in
              [('modelCalls',100000), ('tokens',2000000000), ('stages',100000), ('deadlineTimestamp',4102444800)]}
    result['projectRuns'] = _integer(value['projectRuns'], 0, 100000)
    result['costUsd'] = None if value['costUsd'] is None else money(value['costUsd'], 'Goal configured cost', allow_zero=False)
    reserve = value['completionReserve']
    if not isinstance(reserve, dict) or set(reserve) != {'modelCalls','tokens','costUsd'}:
        raise ValueError('Goal completion reserve requires exact fields')
    reserve = {**reserve, 'modelCalls': _integer(reserve['modelCalls'],0,result['modelCalls']),
               'tokens': _integer(reserve['tokens'],0,result['tokens'])}
    if result['costUsd'] is None:
        if reserve['costUsd'] is not None:
            raise ValueError('A money reserve requires a configured money ceiling')
    else:
        reserve['costUsd'] = money(reserve['costUsd'], 'Goal completion cost reserve')
        if Decimal(reserve['costUsd']) > Decimal(result['costUsd']):
            raise ValueError('Completion reserve exceeds the goal ceiling')
    result['completionReserve'] = reserve
    return result


def _present(conn):
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & GOAL_TABLES
    if present and present != GOAL_TABLES:
        raise ValueError('Goal allowance storage is incomplete')
    return bool(present)


def _latest(conn, goal_id):
    row = conn.execute('SELECT * FROM organization_goal_revisions WHERE id=? ORDER BY revision DESC LIMIT 1', (goal_id,)).fetchone()
    if row is None:
        raise ValueError('Goal allowance membership has no configuration')
    value = json.loads(row['record'])
    if _hash(value) != row['sha256'] or value['id'] != goal_id or value['revision'] != row['revision']:
        raise ValueError('Goal allowance configuration is corrupt')
    return value


def goal_projection(conn, goal_id):
    if not _present(conn):
        return None
    goal = _latest(conn, goal_id)
    members = [row[0] for row in conn.execute('SELECT objective_id FROM organization_goal_members WHERE goal_id=? ORDER BY objective_id', (goal_id,))]
    calls = conn.execute('SELECT c.*,k.classification FROM organization_model_calls c JOIN organization_goal_members m '
        'ON m.objective_id=c.objective_id LEFT JOIN organization_model_call_classes k ON k.model_call_id=c.id WHERE m.goal_id=?', (goal_id,)).fetchall()
    unknown = bool(conn.execute('SELECT 1 FROM organization_goal_members m JOIN objective_budgets b ON b.objective_id=m.objective_id '
        'WHERE m.goal_id=? AND b.legacy_usage_unknown=1 LIMIT 1', (goal_id,)).fetchone())
    complete_cost = not unknown and all(row['reserved_cost_usd'] is not None for row in calls)
    cost = sum((Decimal(row['reserved_cost_usd']) for row in calls if row['reserved_cost_usd'] is not None), Decimal(0))
    completion = [row for row in calls if row['classification'] == 'completion']
    limits = goal['limits']
    used = {'modelCalls': len(calls), 'tokens': sum(row['input_limit'] + row['output_limit'] for row in calls),
            'costUsd': format(cost, 'f') if complete_cost else None}
    for key, table in [('stages','objective_usage'), ('projectRuns','project_run_starts')]:
        used[key] = conn.execute(f'SELECT count(*) FROM {table} c JOIN organization_goal_members m ON m.objective_id=c.objective_id WHERE m.goal_id=?', (goal_id,)).fetchone()[0]
    completion_used = {'modelCalls': len(completion), 'tokens': sum(row['input_limit']+row['output_limit'] for row in completion),
                       'costUsd': sum((Decimal(row['reserved_cost_usd']) for row in completion if row['reserved_cost_usd'] is not None), Decimal(0))}
    ordinary = {}
    for key in ('modelCalls','tokens','costUsd'):
        if unknown or limits[key] is None or used[key] is None:
            ordinary[key] = None
        else:
            remaining = max(Decimal(limits[key]) - Decimal(used[key]), 0)
            protected = max(Decimal(limits['completionReserve'][key]) - Decimal(completion_used[key]), 0)
            ordinary[key] = str(max(remaining - protected, 0))
    return {**goal, 'objectiveIds': members, 'reserved': used, 'ordinaryRemaining': ordinary,
            'legacyUsageUnknown': unknown, 'accounting': 'canonical cumulative reservation ceilings; configured USD is not billed cost'}


def goal_for_objective(conn, objective_id):
    if not _present(conn):
        return None
    row = conn.execute('SELECT goal_id FROM organization_goal_members WHERE objective_id=?', (objective_id,)).fetchone()
    return goal_projection(conn, row['goal_id']) if row else None


def require_goal_admission(conn, objective_id, *, stage=False, model_tokens=None, configured_cost=None,
                           completion=False, project_run=False):
    goal = goal_for_objective(conn, objective_id)
    if goal is None:
        return
    limits, used = goal['limits'], goal['reserved']
    if goal['paused'] or time.time() >= limits['deadlineTimestamp']:
        raise ValueError('Shared goal allowance is paused or its deadline expired')
    if goal['legacyUsageUnknown']:
        raise ValueError('Shared goal contains unknown historical usage; no new work was admitted')
    increments = {'stages': int(stage), 'projectRuns': int(project_run)}
    if model_tokens is not None:
        increments.update(modelCalls=1, tokens=model_tokens)
        if limits['costUsd'] is not None:
            if configured_cost is None or used['costUsd'] is None:
                raise ValueError('Shared goal configured-money ceiling requires known conservative reservations')
            if Decimal(used['costUsd']) + Decimal(configured_cost) > Decimal(limits['costUsd']):
                raise ValueError('Shared goal configured-money reservation ceiling exhausted')
        if not completion:
            for key, amount in [('modelCalls',1), ('tokens',model_tokens), ('costUsd',configured_cost)]:
                if key == 'costUsd' and limits[key] is None:
                    continue
                if amount is None or goal['ordinaryRemaining'][key] is None or Decimal(goal['ordinaryRemaining'][key]) < Decimal(amount):
                    raise ValueError('Shared goal protected completion reserve would be consumed')
    for key, amount in increments.items():
        if amount and used[key] + amount > limits[key]:
            raise ValueError(f'Shared goal {key} reservation ceiling exhausted')


def inherit_goal_membership(conn, source_id, replacement_id):
    if not _present(conn):
        return
    row = conn.execute('SELECT goal_id FROM organization_goal_members WHERE objective_id=?', (source_id,)).fetchone()
    if row:
        if conn.execute('SELECT count(*) FROM organization_goal_members WHERE goal_id=?', (row['goal_id'],)).fetchone()[0] >= 1024:
            raise ValueError('Goal membership capacity reached; replacement cannot discard historical spend')
        conn.execute('INSERT INTO organization_goal_members VALUES (?,?,?)', (replacement_id,row['goal_id'],time.time()))


class OrganizationGoalAllowanceStore:
    def configure_goal_allowance(self, goal_id, *, objective_ids, limits, paused, expected_revision, idempotency_key, confirmed):
        from eidolon_cli.organization_store import _text
        from eidolon_cli.organization_projects import validate_objective_projects
        if confirmed is not True or type(paused) is not bool:
            raise ValueError('Explicit owner confirmation and pause state are required')
        goal_id = _id(goal_id)
        if not isinstance(objective_ids,list) or not 1 <= len(objective_ids) <= 1024 :
            raise ValueError('Goal membership requires one to 1024 unique objective IDs')
        objective_ids = sorted(_id(value) for value in objective_ids)
        if len(set(objective_ids)) != len(objective_ids):
            raise ValueError('Goal membership requires unique objective IDs')
        limits = normalize_limits(limits)
        revision = _integer(expected_revision,0,2**31-1)
        key = _text(idempotency_key,'Goal allowance retry key',128)
        digest = _hash([goal_id,objective_ids,limits,paused,revision])
        with self._write() as conn:
            self._require_current_policy(conn)
            old = conn.execute('SELECT * FROM organization_goal_receipts WHERE idempotency_key=?',(key,)).fetchone()
            if old:
                if old['input_sha256'] != digest:
                    raise ValueError('Goal allowance retry key belongs to different input')
                return json.loads(old['result'])
            existing = conn.execute('SELECT MAX(revision) FROM organization_goal_revisions WHERE id=?',(goal_id,)).fetchone()[0] or 0
            if existing != revision:
                raise ValueError('Goal allowance revision changed')
            if not existing and conn.execute('SELECT count(DISTINCT id) FROM organization_goal_revisions').fetchone()[0] >= 128:
                raise ValueError('Goal allowance capacity reached')
            members = {row[0] for row in conn.execute('SELECT objective_id FROM organization_goal_members WHERE goal_id=?',(goal_id,))}
            if not members <= set(objective_ids):
                raise ValueError('Historical goal membership cannot be removed or moved')
            # Replacement history is one accounting lineage, including replacements
            # created before any goal existed. Never admit only its fresh tail.
            selected = set(objective_ids)
            for ident in objective_ids:
                links = conn.execute('SELECT source_id,replacement_id FROM objective_replacements '
                                     'WHERE source_id=? OR replacement_id=?', (ident,ident)).fetchall()
                if any(row['source_id'] not in selected or row['replacement_id'] not in selected for row in links):
                    raise ValueError('Goal membership must include the complete replacement lineage')
            for ident in objective_ids:
                if conn.execute('SELECT 1 FROM objectives WHERE id=?',(ident,)).fetchone() is None:
                    raise ValueError('Goal objective does not exist')
                validate_objective_projects(conn,ident,self.settings)
                owner = conn.execute('SELECT goal_id FROM organization_goal_members WHERE objective_id=?',(ident,)).fetchone()
                if owner and owner['goal_id'] != goal_id:
                    raise ValueError('An objective cannot move between goal allowances')
            record = {'id':goal_id,'revision':revision+1,'limits':limits,'paused':paused}
            conn.execute('INSERT INTO organization_goal_revisions VALUES (?,?,?,?,?)',(goal_id,revision+1,json.dumps(record,sort_keys=True),_hash(record),time.time()))
            for ident in set(objective_ids)-members:
                conn.execute('INSERT INTO organization_goal_members VALUES (?,?,?)',(ident,goal_id,time.time()))
            result = {'goalId':goal_id,'revision':revision+1,'sha256':_hash(record)}
            conn.execute('INSERT INTO organization_goal_receipts VALUES (?,?,?)',(key,digest,json.dumps(result,sort_keys=True)))
            return result

"""Finite owner-approved continuation; every launch is one durable transaction."""
from decimal import Decimal
import json
import os
import stat
import time

from eidolon_cli.organization_goal_allowances import _hash, _id, _integer, goal_projection, require_goal_admission

CONTINUATION_TABLES = frozenset({'organization_continuation_plans','organization_continuation_controls',
                                'organization_continuation_launches','organization_continuation_receipts'})
CONTINUATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_continuation_plans (
 goal_id TEXT PRIMARY KEY, record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_continuation_controls (
 goal_id TEXT NOT NULL REFERENCES organization_continuation_plans(goal_id), revision INTEGER NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), PRIMARY KEY(goal_id,revision));
CREATE TABLE IF NOT EXISTS organization_continuation_launches (
 goal_id TEXT NOT NULL REFERENCES organization_continuation_plans(goal_id), template_id TEXT NOT NULL,
 objective_id TEXT NOT NULL UNIQUE REFERENCES objectives(id), scope_sha256 TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(goal_id,template_id));
CREATE TABLE IF NOT EXISTS organization_continuation_receipts (
 idempotency_key TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, result TEXT NOT NULL);
'''
for _table in CONTINUATION_TABLES:
    for _op in ('UPDATE','DELETE'):
        CONTINUATION_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                               "BEGIN SELECT RAISE(ABORT,'Continuation history is immutable'); END;\n")


def _authority(conn):
    policy = dict(conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone())
    # Bind existing leadership/staff identities as well as configuration authority.
    policy['routing'] = [dict(r) for r in conn.execute('SELECT * FROM agents ORDER BY id')]
    policy['agents'] = [dict(r) for r in conn.execute('SELECT * FROM agent_identity ORDER BY agent_id')]
    policy['staff'] = [dict(r) for r in conn.execute('SELECT * FROM staff_state ORDER BY agent_id')]
    return _hash(policy)


def _roots(projects):
    pins = []
    for project in projects:
        path = project['readRoot']
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError('Continuation workspace must remain an existing directory')
        pins.append({'path':path,'realpath':os.path.realpath(path),'device':info.st_dev,'inode':info.st_ino})
    return pins


def _records(conn):
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & CONTINUATION_TABLES
    if not present:
        return []
    if present != CONTINUATION_TABLES:
        raise ValueError('Continuation storage is incomplete')
    records = []
    for row in conn.execute('SELECT * FROM organization_continuation_plans ORDER BY goal_id LIMIT 129'):
        value = json.loads(row['record'])
        if _hash(value) != row['sha256'] or value['goalId'] != row['goal_id']:
            raise ValueError('Continuation plan is corrupt')
        records.append(value)
    if len(records)>128:
        raise ValueError('Continuation capacity exceeded')
    return records


class OrganizationGoalContinuationStore:
    def preview_goal_continuation(self, goal_id, *, templates, deadline_timestamp):
        """Read/validate only. Explicit finite owner template, never model-expanded."""
        with self._connect() as conn:
            return self._continuation_preview(conn,goal_id,templates,deadline_timestamp)

    def _continuation_preview(self,conn,goal_id,templates,deadline):
        from eidolon_cli.organization_store import _text, _PRIORITY
        from eidolon_cli.organization_outcome_contracts import definitions, required_by
        from eidolon_cli.organization_acceptance import required_checks
        from eidolon_cli.organization_projects import selected_projects
        from eidolon_cli.organization_milestones import milestone_projection, _ids
        self._require_current_policy(conn)
        goal_id = _id(goal_id)
        goal = goal_projection(conn,goal_id)
        if goal is None:
            raise ValueError('An existing owner-configured goal is required')
        deadline = _integer(deadline,1,4102444800)
        if deadline>goal['limits']['deadlineTimestamp'] or deadline<=time.time():
            raise ValueError('Continuation deadline must be future and within its goal deadline')
        if not isinstance(templates,list) or not 1<=len(templates)<=32:
            raise ValueError('Continuation requires one to 32 finite templates')
        milestones = {v['id']:v for v in milestone_projection(conn)}
        result=[]
        for item in templates:
            if not isinstance(item,dict) or set(item)!={'id','title','description','priority','acceptanceCriteria','deliveryMode','requiredChecks','projectIds','executiveId','managerId','dependencyIds','templateDependencyIds'}:
                raise ValueError('Continuation template requires exact fields')
            title=_text(item['title'],'Title',500)
            description=_text(item['description'],'Description',30000)
            defs=definitions(item['acceptanceCriteria'],description)
            criteria=[d['text'] if d['legacy'] else {k:v for k,v in d.items() if k!='legacy'} for d in defs]
            checks=list(dict.fromkeys([*required_checks(item['requiredChecks']),*required_by(defs)]))
            if item['priority'] not in _PRIORITY or item['deliveryMode'] not in ('managed_artifact','source_project'):
                raise ValueError('Invalid continuation priority or delivery mode')
            projects=selected_projects(_ids(item['projectIds'],'projectIds',8),self.settings)
            if self.settings.projects and item['deliveryMode']=='source_project' and not projects:
                raise ValueError('Source-project continuation requires explicit projects')
            if any(d.get('projectId') and d['projectId'] not in {p['id'] for p in projects} for d in defs):
                raise ValueError('Criterion project must belong to the continuation workspace')
            from eidolon_cli.organization_identity import _leader_enabled
            leaders={}
            for field,role,accepts in [('executiveId','Executive',{'request.accept'}),('managerId','Manager',{'request.plan','request.integrate'})]:
                ident=_id(item[field])
                agent=conn.execute('SELECT * FROM agents WHERE id=?',(ident,)).fetchone()
                if agent is None or agent['role']!=role or not _leader_enabled(conn,agent) or not accepts<=set(json.loads(agent['accepts'])):
                    raise ValueError('Continuation leadership must be configured, enabled and accept the required stages')
                leaders[field]=agent
            if leaders['managerId']['manager_id']!=leaders['executiveId']['id']:
                raise ValueError('Continuation Manager must report to its Executive')
            dependencies=[]
            for ident in _ids(item['dependencyIds'],'dependencyIds',32):
                if ident not in milestones:
                    raise ValueError('Continuation dependency must already exist')
                value=milestones[ident]
                dependencies.append({k:value[k] for k in ('id','revision','sha256')})
            template_dependencies=_ids(item['templateDependencyIds'],'templateDependencyIds',31)
            if not set(template_dependencies)<={t['id'] for t in result}:
                raise ValueError('Template dependencies must select earlier finite templates')
            result.append({**item,'id':_id(item['id']),'title':title,'description':description,
                           'acceptanceCriteria':criteria,'requiredChecks':checks,'projects':projects,
                           'rootPins':_roots(projects),'dependencies':dependencies})
        if len({t['id'] for t in result})!=len(result):
            raise ValueError('Continuation template IDs must be unique')
        record={'goalId':goal_id,'goalRevision':goal['revision'],'deadlineTimestamp':deadline,
                'authoritySha256':_authority(conn),'templates':result}
        return {'plan':record,'sha256':_hash(record)}

    def approve_goal_continuation(self,goal_id,*,templates,deadline_timestamp,preview_sha256,idempotency_key,confirmed):
        if confirmed is not True:
            raise ValueError('Explicit owner approval is required')
        key=_id(idempotency_key)
        digest=_hash([goal_id,templates,deadline_timestamp,preview_sha256])
        with self._write() as conn:
            old=conn.execute('SELECT * FROM organization_continuation_receipts WHERE idempotency_key=?',(key,)).fetchone()
            if old:
                if old['input_sha256']!=digest:
                    raise ValueError('Continuation retry key belongs to different input')
                return json.loads(old['result'])
            preview=self._continuation_preview(conn,goal_id,templates,deadline_timestamp)
            if preview['sha256']!=preview_sha256:
                raise ValueError('Continuation preview is stale')
            if conn.execute('SELECT 1 FROM organization_continuation_plans WHERE goal_id=?',(goal_id,)).fetchone():
                raise ValueError('A goal has one immutable finite continuation plan')
            conn.execute('INSERT INTO organization_continuation_plans VALUES (?,?,?,?)',(goal_id,json.dumps(preview['plan'],sort_keys=True),preview['sha256'],time.time()))
            conn.execute('INSERT INTO organization_continuation_controls VALUES (?,1,0)',(goal_id,))
            result={'goalId':goal_id,'sha256':preview['sha256'],'revision':1,'enabled':False}
            conn.execute('INSERT INTO organization_continuation_receipts VALUES (?,?,?)',(key,digest,json.dumps(result,sort_keys=True)))
            return result

    def set_goal_continuation_enabled(self,goal_id,*,enabled,expected_revision,idempotency_key,confirmed):
        if confirmed is not True or type(enabled) is not bool:
            raise ValueError('Explicit owner enable or disable confirmation is required')
        key=_id(idempotency_key);goal_id=_id(goal_id);_integer(expected_revision,1,2**31-1)
        digest=_hash([goal_id,enabled,expected_revision])
        with self._write() as conn:
            old=conn.execute('SELECT * FROM organization_continuation_receipts WHERE idempotency_key=?',(key,)).fetchone()
            if old:
                if old['input_sha256']!=digest: raise ValueError('Continuation retry key belongs to different input')
                return json.loads(old['result'])
            self._require_current_policy(conn)
            plan=next((p for p in _records(conn) if p['goalId']==goal_id),None)
            if plan is None: raise ValueError('Continuation plan does not exist')
            control=conn.execute('SELECT * FROM organization_continuation_controls WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(goal_id,)).fetchone()
            if control['revision']!=expected_revision: raise ValueError('Continuation control revision changed')
            if enabled: self._continuation_current(conn,plan)
            result={'goalId':goal_id,'revision':expected_revision+1,'enabled':enabled}
            conn.execute('INSERT INTO organization_continuation_controls VALUES (?,?,?)',(goal_id,expected_revision+1,int(enabled)))
            conn.execute('INSERT INTO organization_continuation_receipts VALUES (?,?,?)',(key,digest,json.dumps(result,sort_keys=True)))
            return result

    def _continuation_current(self,conn,plan):
        self._require_current_policy(conn)
        goal=goal_projection(conn,plan['goalId'])
        if goal['revision']!=plan['goalRevision'] or goal['paused'] or time.time()>=plan['deadlineTimestamp']:
            raise ValueError('Continuation goal changed, paused or expired')
        if _authority(conn)!=plan['authoritySha256']:
            raise ValueError('Continuation authority changed')
        if not goal['objectiveIds'] or len(goal['objectiveIds'])>=1024:
            raise ValueError('Continuation goal membership is unavailable or full')
        if conn.execute('SELECT 1 FROM objectives o JOIN organization_goal_members m ON m.objective_id=o.id WHERE m.goal_id=? AND o.cancelled=1 LIMIT 1',(plan['goalId'],)).fetchone():
            raise ValueError('Continuation goal contains cancelled work')
        require_goal_admission(conn,goal['objectiveIds'][0],stage=True,model_tokens=1,configured_cost='0')
        if goal['limits']['costUsd'] is not None and Decimal(goal['ordinaryRemaining']['costUsd'])<=0:
            raise ValueError('Continuation money reserve exhausted')
        return goal

    def advance_goal_continuation(self):
        """Launch at most one previously approved template; caller owns dispatch gate."""
        from eidolon_cli.organization_projects import selected_projects
        from eidolon_cli.organization_milestones import milestone_projection, _objective
        result={'launched':None,'blocked':[]}
        with self._write() as conn:
            for plan in _records(conn):
                control=conn.execute('SELECT enabled FROM organization_continuation_controls WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(plan['goalId'],)).fetchone()
                if not control or not control['enabled']: continue
                conn.execute('SAVEPOINT continuation_launch')
                try:
                    self._continuation_current(conn,plan)
                    milestones={m['id']:m for m in milestone_projection(conn)}
                    for item in plan['templates']:
                        if conn.execute('SELECT 1 FROM organization_continuation_launches WHERE goal_id=? AND template_id=?',(plan['goalId'],item['id'])).fetchone(): continue
                        if any(d['id'] not in milestones or any(milestones[d['id']][k]!=d[k] for k in ('revision','sha256')) or milestones[d['id']]['status']!='complete' for d in item['dependencies']): continue
                        ready=True
                        for dependency in item['templateDependencyIds']:
                            prior=conn.execute('SELECT objective_id,scope_sha256 FROM organization_continuation_launches WHERE goal_id=? AND template_id=?',(plan['goalId'],dependency)).fetchone()
                            if prior is None:
                                ready=False;break
                            objective,_,scope=_objective(conn,prior['objective_id'])
                            if objective['status']!='accepted' or objective['cancelled'] or scope!=prior['scope_sha256']:
                                ready=False;break
                        if not ready: continue
                        projects=selected_projects(item['projectIds'],self.settings)
                        if projects!=item['projects'] or _roots(projects)!=item['rootPins']:
                            raise ValueError('Continuation workspace identity changed')
                        # Rollback all creation if later membership/receipt insertion fails.
                        ident=self._create_objective(conn,item['title'],item['description'],item['priority'],
                            idempotency_key='continuation:'+_hash([plan['goalId'],item['id']]),
                            acceptance_criteria=item['acceptanceCriteria'],delivery_mode=item['deliveryMode'],
                            required_checks=item['requiredChecks'],executive_id=item['executiveId'],manager_id=item['managerId'],project_ids=item['projectIds'])
                        conn.execute('INSERT INTO organization_goal_members VALUES (?,?,?)',(ident,plan['goalId'],time.time()))
                        scope=_objective(conn,ident)[2]
                        conn.execute('INSERT INTO organization_continuation_launches VALUES (?,?,?,?,?)',(plan['goalId'],item['id'],ident,scope,time.time()))
                        result['launched']={'goalId':plan['goalId'],'templateId':item['id'],'objectiveId':ident}
                        conn.execute('RELEASE continuation_launch')
                        return result
                    conn.execute('RELEASE continuation_launch')
                except (ValueError,OSError) as exc:
                    conn.execute('ROLLBACK TO continuation_launch')
                    conn.execute('RELEASE continuation_launch')
                    result['blocked'].append({'goalId':plan['goalId'],'reason':str(exc)})
        return result

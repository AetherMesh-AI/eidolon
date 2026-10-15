"""Owner-only budget/policy refresh for an unchanged finite continuation plan."""
import json
import time

from eidolon_cli.organization_goal_allowances import _hash, _id, _integer, goal_projection

AUTH_TABLE = 'organization_continuation_authorizations'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_continuation_authorizations (
 goal_id TEXT NOT NULL REFERENCES organization_continuation_plans(goal_id),
 revision INTEGER NOT NULL, plan_sha256 TEXT NOT NULL, record TEXT NOT NULL,
 sha256 TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(goal_id,revision));
CREATE TRIGGER IF NOT EXISTS immutable_continuation_authorization_update
 BEFORE UPDATE ON organization_continuation_authorizations BEGIN SELECT RAISE(ABORT,'Continuation authorization is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_continuation_authorization_delete
 BEFORE DELETE ON organization_continuation_authorizations BEGIN SELECT RAISE(ABORT,'Continuation authorization is immutable'); END;
'''


def effective_authority(conn,plan):
    """Legacy absence is passive; a receipt cannot rewrite the original plan."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(AUTH_TABLE,)).fetchone():
        return plan['goalRevision'],plan['authoritySha256']
    row=conn.execute(f'SELECT * FROM {AUTH_TABLE} WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(plan['goalId'],)).fetchone()
    if row is None: return plan['goalRevision'],plan['authoritySha256']
    value=json.loads(row['record'])
    if row['plan_sha256']!=_hash(plan) or _hash(value)!=row['sha256'] or value['planSha256']!=row['plan_sha256'] or value['goalId']!=plan['goalId']:
        raise ValueError('Continuation authorization identity is corrupt')
    return value['goalRevision'],value['authoritySha256']


class GoalReauthorizationStore:
    def preview_goal_reauthorization(self,goal_id):
        with self._connect() as conn: return self._reauthorization_preview(conn,goal_id)

    def _reauthorization_preview(self,conn,goal_id):
        from eidolon_cli.organization_goal_continuation import _records
        from eidolon_cli.organization_milestones import _objective
        self._require_current_policy(conn)
        goal_id=_id(goal_id)
        plan=next((p for p in _records(conn) if p['goalId']==goal_id),None)
        if plan is None: raise ValueError('Continuation plan does not exist')
        # Re-normalize the original finite inputs. Scope, roots, checks, dependency
        # revisions, deadline and identities must be byte-for-byte equivalent.
        enriched={'projects','rootPins','dependencies'}
        templates=[{k:v for k,v in item.items() if k not in enriched} for item in plan['templates']]
        fresh=self._continuation_preview(conn,goal_id,templates,plan['deadlineTimestamp'])['plan']
        for field in ('goalId','deadlineTimestamp','templates'):
            if fresh[field]!=plan[field]:
                raise ValueError('Changed continuation scope or workspace cannot be reauthorized')
        launches=[dict(r) for r in conn.execute('SELECT template_id,objective_id,scope_sha256 FROM organization_continuation_launches WHERE goal_id=? ORDER BY template_id',(goal_id,))]
        for launch in launches:
            objective,_,scope=_objective(conn,launch['objective_id'])
            if objective['cancelled'] or scope!=launch['scope_sha256']:
                raise ValueError('Changed or cancelled launched objective cannot be reauthorized')
        control=conn.execute('SELECT revision FROM organization_continuation_controls WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(goal_id,)).fetchone()
        previous=conn.execute(f'SELECT MAX(revision) FROM {AUTH_TABLE} WHERE goal_id=?',(goal_id,)).fetchone()[0] if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(AUTH_TABLE,)).fetchone() else None
        goal=goal_projection(conn,goal_id)
        policy=dict(conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone())
        record={'goalLimits':goal['limits'],'policy':{**policy,'toolGrants':list(self.settings.tool_grants)},'goalId':goal_id,'planSha256':_hash(plan),'goalRevision':fresh['goalRevision'],
                'authoritySha256':fresh['authoritySha256'],'controlRevision':control['revision'],
                'authorizationRevision':previous or 0,'launches':launches}
        return {'authorization':record,'sha256':_hash(record)}

    def reauthorize_goal_continuation(self,goal_id,*,preview_sha256,expected_revision,idempotency_key,confirmed):
        if confirmed is not True: raise ValueError('Explicit owner reauthorization is required')
        key=_id(idempotency_key);goal_id=_id(goal_id);_integer(expected_revision,1,2**31-1)
        digest=_hash(['reauthorize',goal_id,preview_sha256,expected_revision])
        with self._write() as conn:
            old=conn.execute('SELECT * FROM organization_continuation_receipts WHERE idempotency_key=?',(key,)).fetchone()
            if old:
                if old['input_sha256']!=digest: raise ValueError('Continuation retry key belongs to different input')
                return json.loads(old['result'])
            preview=self._reauthorization_preview(conn,goal_id)
            value=preview['authorization']
            if preview['sha256']!=preview_sha256 or value['controlRevision']!=expected_revision:
                raise ValueError('Continuation reauthorization preview is stale')
            revision=value['authorizationRevision']+1
            conn.execute(f'INSERT INTO {AUTH_TABLE} VALUES (?,?,?,?,?,?)',(goal_id,revision,value['planSha256'],json.dumps(value,sort_keys=True),preview['sha256'],time.time()))
            conn.execute('INSERT INTO organization_continuation_controls VALUES (?,?,0)',(goal_id,expected_revision+1))
            result={'goalId':goal_id,'revision':expected_revision+1,'authorizationRevision':revision,'sha256':preview['sha256'],'enabled':False}
            conn.execute('INSERT INTO organization_continuation_receipts VALUES (?,?,?)',(key,digest,json.dumps(result,sort_keys=True)))
            return result

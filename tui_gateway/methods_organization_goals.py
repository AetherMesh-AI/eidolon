"""Served-owner goal review and controls; reads never provision a runtime."""
from .method_ctx import HandlerRegistry

_registry=HandlerRegistry()
_FIELDS={
 'organization.goals':set(),
 'organization.goalReceipt':{'idempotencyKey','receiptKind'},
 'organization.configureGoal':{'goalId','objectiveIds','limits','paused','expectedRevision','idempotencyKey','confirmed'},
 'organization.previewGoalContinuation':{'goalId','templates','deadlineTimestamp'},
 'organization.approveGoalContinuation':{'goalId','templates','deadlineTimestamp','previewSha256','idempotencyKey','confirmed'},
 'organization.setGoalContinuationEnabled':{'goalId','enabled','expectedRevision','idempotencyKey','confirmed'},
}


def _plan_view(conn,view,plan):
    from eidolon_cli.organization_goal_continuation import _roots
    from eidolon_cli.organization_projects import selected_projects
    from eidolon_cli.organization_milestones import milestone_projection,_objective
    control=conn.execute('SELECT revision,enabled FROM organization_continuation_controls WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(plan['goalId'],)).fetchone()
    reason=None
    try: view._continuation_current(conn,plan)
    except (ValueError,OSError) as exc: reason=str(exc)
    launches=[dict(r) for r in conn.execute('SELECT template_id,objective_id,scope_sha256 FROM organization_continuation_launches WHERE goal_id=? ORDER BY created',(plan['goalId'],))]
    by_id={r['template_id']:r for r in launches}
    milestones={r['id']:r for r in milestone_projection(conn)}
    states=[]
    for item in plan['templates']:
        blockers=[]
        if item['id'] not in by_id:
            for dependency in item['dependencies']:
                current=milestones.get(dependency['id'])
                if current is None or current['status']!='complete' or any(current[k]!=dependency[k] for k in ('revision','sha256')):
                    blockers.append('milestone:'+dependency['id'])
            for dependency in item['templateDependencyIds']:
                prior=by_id.get(dependency)
                if prior is None: blockers.append('template:'+dependency);continue
                objective,_,fingerprint=_objective(conn,prior['objective_id'])
                if objective['status']!='accepted' or objective['cancelled'] or fingerprint!=prior['scope_sha256']:
                    blockers.append('template:'+dependency)
            try:
                projects=selected_projects(item['projectIds'],view.settings)
                if projects!=item['projects'] or _roots(projects)!=item['rootPins']: blockers.append('workspace_identity_changed')
            except (ValueError,OSError): blockers.append('workspace_unavailable')
        states.append({'id':item['id'],'launched':item['id'] in by_id,'blockers':blockers})
    can_enable=reason is None and not any(any(b.startswith('workspace_') for b in state['blockers']) for state in states)
    if reason is None and any(state['blockers'] for state in states):
        reason='Waiting for exact approved prerequisites or workspace identity; review the listed step blockers.'
    return {**plan,'controlRevision':control['revision'],'enabled':bool(control['enabled']),
            'waitingReason':reason,'canEnable':can_enable,'launches':launches,'templateStates':states}


def _method(name):
    fields=_FIELDS[name]
    @_registry.method(name)
    def handler(rid,params):
        from contextlib import closing
        from pathlib import Path
        import json
        import sqlite3
        from eidolon_cli.organization_metadata import read_descriptor
        from eidolon_constants import set_eidolon_home_override,reset_eidolon_home_override
        transport=current_transport()
        if transport is None or getattr(transport,'closed',False):
            return _err(rid,4001,'Goal controls require an active owner connection')
        if not isinstance(params,dict) or set(params)!=fields|{'expectedOrganizationId','expectedOwnerId'}:
            return _err(rid,-32602,'Unsupported goal parameters; served profile only')
        try:
            home=Path(_eidolon_home).resolve();path=home/'organization'/'state.db'
            if path.resolve()!=path: raise ValueError('Goal storage is outside served profile')
            identity=read_descriptor(path).get('metadata') or {}
            if (not identity or identity['organizationId']!=params['expectedOrganizationId'] or identity['owner']['ownerId']!=params['expectedOwnerId']):
                raise ValueError('Goal organization or owner changed')
            token=set_eidolon_home_override(home)
            try:
                from eidolon_cli.profiles import get_active_profile_name
                result={'version':1,'organizationId':identity['organizationId'],'ownerId':identity['owner']['ownerId'],'profile':get_active_profile_name()}
                if name in ('organization.goals','organization.goalReceipt','organization.previewGoalContinuation'):
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as conn:
                        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
                        from eidolon_cli.organization_goal_allowances import GOAL_TABLES,_present,goal_projection
                        from eidolon_cli.organization_goal_continuation import CONTINUATION_TABLES,_records
                        if name=='organization.goalReceipt':
                            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                            choices={'allowance':(GOAL_TABLES,'organization_goal_receipts'),'continuation':(CONTINUATION_TABLES,'organization_continuation_receipts')}
                            if params['receiptKind'] not in choices: raise ValueError('Unknown goal receipt kind')
                            required,table=choices[params['receiptKind']]
                            if tables&required and not required<=tables: raise ValueError('Goal receipt storage is incomplete')
                            key=params['idempotencyKey']
                            if not isinstance(key,str) or not key or len(key)>128: raise ValueError('Invalid goal receipt key')
                            row=conn.execute(f'SELECT result FROM {table} WHERE idempotency_key=?',(key,)).fetchone() if table in tables else None
                            result['receipt']=json.loads(row[0]) if row else None
                        else:
                            from eidolon_cli.organization_config import load_profile_configuration,profile_configuration_scope
                            from eidolon_cli.organization_policy import resolve_settings
                            from eidolon_cli.organization_observation import _ObservationView
                            with profile_configuration_scope(home): settings,_=load_profile_configuration(home)
                            settings=resolve_settings(conn,settings)
                            view=_ObservationView(path,conn,settings)
                            if name=='organization.previewGoalContinuation':
                                result['preview']=view._continuation_preview(conn,params['goalId'],params['templates'],params['deadlineTimestamp'])
                            else:
                                ids=[r[0] for r in conn.execute('SELECT DISTINCT id FROM organization_goal_revisions ORDER BY id LIMIT 129')] if _present(conn) else []
                                if len(ids)>128: raise ValueError('Goal capacity exceeded')
                                result['goals']=[goal_projection(conn,ident) for ident in ids]
                                plans=[]
                                for plan in _records(conn):
                                    from .methods_organization_goals import _plan_view
                                    plans.append(_plan_view(conn,view,plan))
                                result['plans']=plans
                                result['objectives']=[dict(r) for r in conn.execute('SELECT id,title FROM objectives ORDER BY created DESC LIMIT 1000')]
                                result['leaders']=[dict(r) for r in conn.execute("SELECT id,name,role,manager_id FROM agents WHERE role IN ('Executive','Manager') ORDER BY id")]
                                result['projectIds']=[p.id for p in settings.projects]
                else:
                    if params['confirmed'] is not True: raise ValueError('Review and explicitly confirm this goal action')
                    from eidolon_cli.organization_service import get_service
                    service=get_service()
                    if service.store.path.resolve()!=path: raise ValueError('Goal service is outside served profile')
                    common={'idempotency_key':params['idempotencyKey'],'confirmed':True}
                    if name=='organization.configureGoal':
                        receipt=service.store.configure_goal_allowance(params['goalId'],objective_ids=params['objectiveIds'],limits=params['limits'],paused=params['paused'],expected_revision=params['expectedRevision'],**common)
                    elif name=='organization.approveGoalContinuation':
                        receipt=service.store.approve_goal_continuation(params['goalId'],templates=params['templates'],deadline_timestamp=params['deadlineTimestamp'],preview_sha256=params['previewSha256'],**common)
                    else:
                        receipt=service.store.set_goal_continuation_enabled(params['goalId'],enabled=params['enabled'],expected_revision=params['expectedRevision'],**common)
                    result['receipt']=receipt
                return _ok(rid,result)
            finally: reset_eidolon_home_override(token)
        except (ValueError,TypeError,KeyError) as exc:
            return _err(rid,-32602,str(exc))
        except Exception:
            logger.exception('Goal owner controls failed')
            return _err(rid,5071,'Goal owner controls are unavailable')
    return handler


for _name in _FIELDS: _method(_name)


def register(server):
    _registry.install(server)

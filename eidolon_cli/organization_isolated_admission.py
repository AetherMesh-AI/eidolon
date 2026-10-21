"""Ephemeral scheduler authority for isolated organization source work.

No configuration or grants are written here. Admission comes from the live ledger,
not serialized context, callbacks supplied by a caller, or model output.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import threading
import os

_active = ContextVar('organization_isolated_admission', default=None)
_live = set()
_lock = threading.RLock()
_UNSET = object()
_ROUTE_FIELDS = ('provider', 'model', 'api_mode', 'base_url')
_CALLBACKS = ('recordToolStart', 'recordToolFinish', 'requestToolReceipts',
              'reserveModelCall', 'recordModelObservation', 'selectModelRoute',
              'recordContextReceipt', 'recordEvidencePass', 'resolveWorkspaceSource')


def _fail():
    from eidolon_cli.profile_isolation import ProfileIsolationError
    raise ProfileIsolationError('Isolated source work requires current scheduler-owned authority')


class _Admission:
    def __init__(self, home, store, claim, context, cancelled):
        from eidolon_cli.profile_isolation import local_path, verify_isolation
        self.home = local_path(home)
        self.store, self.claim = store, deepcopy(claim)
        if local_path(store.path) != self.home / 'organization' / 'state.db':
            _fail()
        self.marker = verify_isolation(self.home)
        if self.marker is None or claim['type'] not in {'work.inspect', 'work.edit'}:
            _fail()
        self.context, self.cancelled = context, cancelled
        canonical = store.context(self.claim, mark_read=False)
        if any(context.get(key) != canonical.get(key) for key in ('agent', 'toolPolicy')):
            _fail()
        self.policy = deepcopy(canonical['toolPolicy'])
        self.agent = deepcopy(canonical['agent'])
        # Bind durable operations here to the verified ledger/claim. Caller-supplied
        # functions are replaced, never accepted as evidence of authorization.
        context.update({
            'recordToolStart': lambda call_id, name, args: store.record_tool_start(self.claim, call_id, name, args),
            'recordToolFinish': lambda ident, result, status: store.record_tool_finish(self.claim, ident, result, status),
            'requestToolReceipts': lambda: store.request_tool_receipts(self.claim),
            'reserveModelCall': lambda **values: store.reserve_model_call(self.claim, **values),
            'recordModelObservation': lambda call_id, **values: store.record_model_observation(self.claim, call_id, **values),
            'selectModelRoute': lambda **values: store.select_model_route(self.claim, **values),
            'recordContextReceipt': lambda report: store.record_context_receipt(self.claim, report),
            'recordEvidencePass': lambda report: store.record_evidence_pass(self.claim, report),
        })
        context.pop('resolveWorkspaceSource', None)
        if claim['type'] == 'work.edit':
            context['resolveWorkspaceSource'] = lambda path, loader: store.capture_workspace_source(self.claim, path, loader)
        self.callbacks = {key: context.get(key) for key in _CALLBACKS}
        self.roots = {}
        self.execution = None
        self.runtime = None
        self.retired = False
        for alias in (self.policy or {}).get('readRootAliases', []):
            index = int(alias.removeprefix('root'))
            path = local_path(self.policy['readRoots'][index])
            info = path.stat()
            self.roots[alias] = (path, info.st_dev, info.st_ino)

    def bind_runtime(self, kwargs):
        from eidolon_cli.profile_isolation import isolated_route
        self.check()
        route = isolated_route(home=self.home, requested=kwargs['provider'],
                               target_model=kwargs['model'], explicit_base_url=kwargs['base_url'])
        signature = tuple(kwargs[field] for field in _ROUTE_FIELDS)
        if signature != tuple(route[field] for field in _ROUTE_FIELDS):
            _fail()
        if self.runtime is not None and signature != self.runtime:
            _fail()
        self.runtime = signature

    def check(self, *, request=None, context=None, execution=None, agent=None):
        from eidolon_constants import get_eidolon_home
        from eidolon_cli.profile_isolation import local_path, verify_isolation
        with _lock:
            if self not in _live or self.retired:
                _fail()
        if (self.cancelled() or local_path(get_eidolon_home()) != self.home
                or verify_isolation(self.home) != self.marker):
            _fail()
        if agent is not None and (self.runtime is None or
                tuple(getattr(agent, field, None) for field in _ROUTE_FIELDS) != self.runtime):
            _fail()
        if agent is not None and self.execution is not None and execution is not self.execution:
            _fail()
        if request is not None and any(request.get(key) != self.claim.get(key)
                for key in ('id', 'token', 'agent_id', 'objective_id', 'task_id', 'type')):
            _fail()
        if context is not None and context is not self.context:
            _fail()
        if (self.context.get('_credentialCapsule') is not None
                or self.context.get('toolPolicy') != self.policy
                or self.context.get('agent') != self.agent
                or any(self.context.get(key) is not value for key, value in self.callbacks.items())):
            _fail()
        with self.store._connect() as conn:
            owned = self.store._owned(conn, self.claim)
            if (owned is None or owned['agent_id'] != self.claim['agent_id']
                    or owned['type'] != self.claim['type']
                    or self.store._tool_policy(conn, owned) != self.policy):
                _fail()
            journal = conn.execute('SELECT dispatched,policy_generation FROM organization_execution_journal WHERE request_id=? AND token=?',
                                   (self.claim['id'], self.claim['token'])).fetchone()
            if journal is None or journal['dispatched'] != 1 or journal['policy_generation'] != self.store._policy_generation:
                _fail()
            from eidolon_cli.organization_root_grants import revoked_aliases
            if set(self.roots) & set(revoked_aliases(conn)):
                _fail()
            identity = conn.execute('SELECT identity_id FROM agent_identity WHERE agent_id=?',
                                    (owned['agent_id'],)).fetchone()
            if identity is None or identity[0] != self.agent.get('identityId'):
                _fail()
        if not self.roots or 'read_file' not in self.policy['tools']:
            _fail()
        for path, device, inode in self.roots.values():
            info = local_path(path).stat()
            if (info.st_dev, info.st_ino) != (device, inode):
                _fail()
        if execution is not None:
            if (execution.names != frozenset(self.policy['tools'])
                    or execution.max_calls != self.policy['maxToolCalls']
                    or execution.max_result_chars != self.policy['maxResultChars']
                    or execution.scope.max_result_chars != self.policy['maxResultChars']
                    or set(execution.scope._roots) != set(self.roots)):
                _fail()
            for alias, (path, device, inode) in self.roots.items():
                bound_path, fd = execution.scope._roots[alias]
                info = os.fstat(fd)
                if Path(bound_path) != path or (info.st_dev, info.st_ino) != (device, inode):
                    _fail()
            if self.execution is None:
                # Only the executor-created scope with these exact scheduler callbacks
                # may be bound. A callable alone is never authority.
                if (execution.start is not self.callbacks['recordToolStart']
                        or execution.finish is not self.callbacks['recordToolFinish']
                        or execution.receipts is not self.callbacks['requestToolReceipts']
                        or execution.scope.resolve_workspace_source is not self.callbacks['resolveWorkspaceSource']):
                    _fail()
                self.execution = execution
            elif execution is not self.execution:
                _fail()
            if (execution.start is not self.callbacks['recordToolStart']
                    or execution.finish is not self.callbacks['recordToolFinish']
                    or execution.receipts is not self.callbacks['requestToolReceipts']
                    or execution.scope.resolve_workspace_source is not self.callbacks['resolveWorkspaceSource']):
                _fail()


@contextmanager
def scheduler_admission(home, store, claim, context, cancelled):
    """Enter only around the scheduler's already-owned dispatch, retire on unwind."""
    from eidolon_cli.profile_isolation import read_isolation
    if read_isolation(home) is None or claim.get('type') not in {'work.inspect', 'work.edit'}:
        yield
        return
    admission = _Admission(Path(home), store, claim, context, cancelled)
    with _lock:
        _live.add(admission)
    token = _active.set(admission)
    try:
        admission.check(request=claim, context=context)
        yield
    finally:
        admission.retired = True
        with _lock:
            _live.discard(admission)
        _active.reset(token)


def require_admission(*, request=None, context=None, execution=None, admission=_UNSET, agent=None):
    value = _active.get() if admission is _UNSET else admission
    with _lock:
        if value not in _live:
            _fail()
    value.check(request=request, context=context, execution=execution, agent=agent)
    return value


def check_active(**kwargs):
    """A profile switch must not downgrade an admitted turn to ordinary execution."""
    value = _active.get()
    if value is not None:
        return require_admission(admission=value, **kwargs)
    return None

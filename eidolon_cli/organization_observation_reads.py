"""Allowlisted passive detail reads without service construction or mutable SQL."""
from contextlib import closing, contextmanager
import sqlite3

from eidolon_cli.organization_observation import _ObservationView, _storage_failure, read_observation


class _ReadView(_ObservationView):
    def __init__(self, view):
        self.path = view.path
        self.settings = view.settings
        self._policy_generation = view._policy_generation
        self._policy_fingerprint = view._policy_fingerprint

    @contextmanager
    def _connect(self):
        # Existing read projections own their BEGIN boundary. Never use the
        # mutable store connector or permit a future projection to write.
        with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=0.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            yield conn


def _scoped_thread(value):
    # Match the existing owner-chat RPC contract using only the authenticated
    # served profile; never a caller-supplied routing label.
    from eidolon_cli.profiles import get_active_profile_name
    return {**value, 'profile': get_active_profile_name()}


# Public method -> projection and accepted argument names. No dynamic getattr
# from caller input, profile routing, execution methods or fallback dispatch.
_READS = {
    'organization.attention': (_ReadView.attention, {'limit', 'before', 'unreadOnly'}),
    'organization.outcomes': (_ReadView.outcomes, {'limit', 'before', 'unreadOnly'}),
    'organization.history': (_ReadView.history, {'query', 'state', 'before', 'limit'}),
    'organization.historyObjective': (lambda view, id: view.history_objective(id), {'id'}),
    'organization.ownerChat.read': (lambda view, **params: _scoped_thread(view.owner_chat_read(**params)), {'threadId', 'identityId', 'beforeMessageId', 'limit'}),
    'organization.evidence': (lambda view, id: view.evidence(id), {'id'}),
    'organization.toolReceipts': (lambda view, id: view.tool_receipts(id), {'id'}),
    'organization.executionAudit': (lambda view, id: view.execution_audit(id), {'id'}),
    'organization.previewReplacement': (lambda view, id: view.preview_replacement(id), {'id'}),
}
_NAMES = {'unreadOnly': 'unread_only', 'threadId': 'thread_id', 'identityId': 'identity_id',
          'beforeMessageId': 'before_message_id'}


def _setup(home, params):
    from eidolon_cli.organization_project_setup import project_setup
    return project_setup()


def _draft(home, params):
    from eidolon_cli.organization_project_setup import project_draft
    return project_draft(params.get('project'), params.get('expectedRevision'))


def _admission(home, params):
    from eidolon_cli.organization_admission import check_submission
    from eidolon_cli.profiles import get_active_profile_name
    return {**check_submission(home, params.get('idempotencyKey')), 'profile': get_active_profile_name()}


_DIRECT_READS = {
    'organization.projectSetup': (_setup, set()),
    'organization.projectDraft': (_draft, {'project', 'expectedRevision'}),
    'organization.checkSubmission': (_admission, {'idempotencyKey'}),
}


def read_detail(home, method, params):
    if isinstance(method, str) and method in _DIRECT_READS:
        project, allowed = _DIRECT_READS[method]
        if not isinstance(params, dict) or set(params) - allowed:
            raise ValueError('Unsupported observation read parameters')
        try:
            data = project(home, params)
        except sqlite3.Error as exc:
            # These helpers deliberately support a missing execution ledger, so
            # retain their cold-read semantics instead of snapshot preflight.
            return _storage_failure(exc)
        return {'capabilityVersion': 1, 'state': 'available', 'snapshot': None,
                'data': data}

    if not isinstance(method, str) or method not in _READS or not isinstance(params, dict):
        raise ValueError('Unsupported observation read')
    project, allowed = _READS[method]
    if set(params) - allowed:
        raise ValueError('Unsupported observation read parameters')

    def projection(view):
        # Construct only the explicitly read-only context, retaining the
        # preflight's settings and policy comparison; never initialize a store.
        reader = _ReadView(view)
        return project(reader, **{_NAMES.get(key, key): value for key, value in params.items()})

    return read_observation(home, projection=projection, archival=method in {
        'organization.evidence', 'organization.toolReceipts', 'organization.executionAudit',
        'organization.history', 'organization.historyObjective'})

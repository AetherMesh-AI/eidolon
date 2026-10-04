"""Reviewed edits to an agent-owned SQLite workspace, never to source files.

A source read captures an immutable baseline. Proposal, approval, and application
receipts bind those exact bytes; the mutable head advances with its receipt in
one lease-fenced transaction. Original-project merges remain explicit gates.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone


MAX_EDIT_BYTES = 32768
EDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL UNIQUE REFERENCES objectives(id));
CREATE TABLE IF NOT EXISTS workspace_roots (
 objective_id TEXT NOT NULL REFERENCES objectives(id), alias TEXT NOT NULL,
 root_identity TEXT NOT NULL, PRIMARY KEY(objective_id,alias));
CREATE TABLE IF NOT EXISTS workspace_revisions (
 objective_id TEXT NOT NULL REFERENCES objectives(id), path TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=0), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 content TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(objective_id,path,revision));
CREATE TABLE IF NOT EXISTS workspace_heads (
 objective_id TEXT NOT NULL REFERENCES objectives(id), path TEXT NOT NULL,
 revision INTEGER NOT NULL, PRIMARY KEY(objective_id,path),
 FOREIGN KEY(objective_id,path,revision) REFERENCES workspace_revisions(objective_id,path,revision));
CREATE TABLE IF NOT EXISTS edit_proposals (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 objective_id TEXT NOT NULL REFERENCES objectives(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 evidence_id TEXT NOT NULL UNIQUE REFERENCES evidence(id), evidence_sha256 TEXT NOT NULL,
 author_id TEXT NOT NULL REFERENCES agents(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 path TEXT NOT NULL, base_revision INTEGER NOT NULL, base_sha256 TEXT NOT NULL,
 old_text TEXT NOT NULL, new_text TEXT NOT NULL, new_content TEXT NOT NULL,
 new_sha256 TEXT NOT NULL, diff TEXT NOT NULL, sha256 TEXT NOT NULL,
 source_receipt_id TEXT NOT NULL REFERENCES tool_receipts(id), created REAL NOT NULL,
 FOREIGN KEY(objective_id,path,base_revision) REFERENCES workspace_revisions(objective_id,path,revision));
CREATE TABLE IF NOT EXISTS edit_reviews (
 request_id TEXT PRIMARY KEY REFERENCES reviews(request_id),
 proposal_id TEXT NOT NULL REFERENCES edit_proposals(id), proposal_sha256 TEXT NOT NULL,
 evidence_sha256 TEXT NOT NULL, reviewer_id TEXT NOT NULL REFERENCES agents(id),
 approved INTEGER NOT NULL CHECK(approved IN (0,1)), created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS edit_applications (
 proposal_id TEXT PRIMARY KEY REFERENCES edit_proposals(id),
 request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 review_request_id TEXT NOT NULL REFERENCES edit_reviews(request_id),
 proposal_sha256 TEXT NOT NULL, applied_revision INTEGER NOT NULL,
 applied_sha256 TEXT NOT NULL, created REAL NOT NULL);
"""
# Immutable records are also protected from accidental SQL updates by future
# consumers. Hash verification remains necessary when reading durable evidence.
for _table in ('workspaces', 'workspace_roots', 'workspace_revisions', 'edit_proposals',
               'edit_reviews', 'edit_applications'):
    for _operation in ('UPDATE', 'DELETE'):
        EDIT_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_operation.lower()} '
                        f'BEFORE {_operation} ON {_table} BEGIN '
                        "SELECT RAISE(ABORT, 'Immutable workspace record'); END;\n")


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _exact_text(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be exact UTF-8 text')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:
        raise ValueError(f'{field} must be valid UTF-8 text') from None
    if size > MAX_EDIT_BYTES:
        raise ValueError(f'{field} exceeds the {MAX_EDIT_BYTES} UTF-8 byte limit')
    return value


def _safe_content(value, field):
    value = _exact_text(value, field)
    from agent.redact import redact_sensitive_text
    if any(ord(char) < 32 and char not in '\n\r\t\f' for char in value):
        raise ValueError(f'{field} contains binary or control-bearing content')
    if redact_sensitive_text(value, force=True, file_read=True, redact_url_credentials=True) != value:
        raise ValueError(f'{field} requires secret redaction and cannot be retained as an exact edit')
    return value


def _alias_path(path):
    if (not isinstance(path, str) or not path or len(path) > 1024 or '\\' in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise ValueError('Edit path must be a canonical read-root alias path')
    parts = path.split('/')
    if (len(parts) < 2 or not re.fullmatch(r'root(?:0|[1-9][0-9]*)', parts[0])
            or any(part in ('', '.', '..') or part.startswith('~') for part in parts)):
        raise ValueError('Edit path must be a canonical read-root alias path')
    return parts[0], int(parts[0][4:])


def _lines(content):
    # splitlines() also splits Unicode separators, which are ordinary UTF-8 file
    # bytes rather than patch line endings.
    parts = content.split('\n')
    return [part + '\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _diff(path, before, after):
    chunks = difflib.unified_diff(_lines(before), _lines(after),
                                  fromfile='a/' + path, tofile='b/' + path)
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                   for line in chunks)


def _proposal_digest(row):
    # Versioned explicit fields avoid trusting a model-supplied diff or digest.
    fields = ('id', 'request_id', 'objective_id', 'task_id', 'evidence_id', 'evidence_sha256',
              'author_id', 'workspace_id', 'path', 'base_revision', 'base_sha256',
              'old_text', 'new_text', 'new_content', 'new_sha256', 'diff', 'source_receipt_id')
    return _hash(json.dumps({'version': 1, **{field: row[field] for field in fields}},
                            sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def _revision(conn, objective_id, path, revision=None):
    if revision is None:
        return conn.execute('SELECT v.* FROM workspace_revisions v JOIN workspace_heads h '
                            'USING(objective_id,path,revision) WHERE v.objective_id=? AND v.path=?',
                            (objective_id, path)).fetchone()
    return conn.execute('SELECT * FROM workspace_revisions WHERE objective_id=? AND path=? AND revision=?',
                        (objective_id, path, revision)).fetchone()


def _verify_revision(row):
    if row is None or _hash(_safe_content(row['content'], 'Workspace content')) != row['sha256']:
        raise ValueError('Workspace revision is missing or has changed')
    return row


def _source_read(row, source):
    if (row is None or row['status'] != 'completed' or row['tool_name'] != 'read_file'
            or not isinstance(row['result'], str) or _hash(row['result']) != row['result_sha256']):
        return False
    try:
        result, arguments = json.loads(row['result']), json.loads(row['arguments'])
    except (ValueError, TypeError):
        return False
    return (isinstance(result, dict) and isinstance(arguments, dict)
            and result.get('success') is True and not result.get('error') and not result.get('blocked')
            and not result.get('redacted')
            and arguments.get('path') == source['path'] and result.get('path', source['path']) == source['path']
            and isinstance(result.get('content'), str) and result['content'] in source['content']
            and (not source['content'] or bool(result['content'])) and result.get('contentFormat') == 'raw'
            and result.get('workspaceId') == source['workspace_id']
            and type(result.get('workspaceRevision')) is int
            and result['workspaceRevision'] == source['revision']
            and result.get('sourceSha256') == source['sha256'])


def _verify_proposal(conn, proposal):
    if proposal is None or _proposal_digest(proposal) != proposal['sha256']:
        raise ValueError('Edit proposal is missing or has changed')
    source = _verify_revision(_revision(conn, proposal['objective_id'], proposal['path'], proposal['base_revision']))
    if (source['sha256'] != proposal['base_sha256'] or source['workspace_id'] != proposal['workspace_id']
            or _hash(_safe_content(proposal['new_content'], 'Proposed content')) != proposal['new_sha256']
            or not proposal['old_text'] or source['content'].find(proposal['old_text']) < 0
            or source['content'].find(proposal['old_text']) != source['content'].rfind(proposal['old_text'])
            or source['content'].replace(proposal['old_text'], proposal['new_text'], 1) != proposal['new_content']
            or _diff(proposal['path'], source['content'], proposal['new_content']) != proposal['diff']):
        raise ValueError('Edit proposal does not match its exact source and replacement')
    evidence = conn.execute('SELECT * FROM evidence WHERE id=?', (proposal['evidence_id'],)).fetchone()
    if (evidence is None or evidence['request_id'] != proposal['request_id']
            or evidence['task_id'] != proposal['task_id'] or evidence['objective_id'] != proposal['objective_id']
            or _hash(evidence['content']) != evidence['sha256']
            or evidence['sha256'] != proposal['evidence_sha256']):
        raise ValueError('Edit proposal evidence is missing or has changed')
    receipt = conn.execute('SELECT r.* FROM tool_receipts r JOIN evidence_tools e ON e.receipt_id=r.id '
                           'WHERE r.id=? AND e.evidence_id=?',
                           (proposal['source_receipt_id'], proposal['evidence_id'])).fetchone()
    if not _source_read(receipt, source) or receipt['request_id'] != proposal['request_id']:
        raise ValueError('Edit proposal has no matching persisted successful source read')
    return source


def _verified_review(conn, proposal, review_id, *, require_approved=False):
    review = conn.execute('SELECT er.*,r.approved AS actual_approved,r.agent_id AS actual_agent, '
                          'r.task_id AS actual_task,r.evidence_ids,r.summary,q.objective_id AS review_objective, '
                          'q.type AS review_type,q.status AS review_status FROM edit_reviews er '
                          'JOIN reviews r ON r.request_id=er.request_id JOIN requests q ON q.id=er.request_id '
                          'WHERE er.request_id=?', (review_id,)).fetchone()
    if (review is None or (require_approved and not review['approved'])
            or review['approved'] != review['actual_approved']
            or review['proposal_id'] != proposal['id'] or review['proposal_sha256'] != proposal['sha256']
            or review['evidence_sha256'] != proposal['evidence_sha256']
            or review['reviewer_id'] != review['actual_agent'] or review['reviewer_id'] == proposal['author_id']
            or review['actual_task'] != proposal['task_id'] or review['review_objective'] != proposal['objective_id']
            or review['review_type'] != 'request.review' or review['review_status'] != 'completed'
            or json.loads(review['evidence_ids']) != [proposal['evidence_id']]):
        raise ValueError('Application requires the exact independent approved edit review' if require_approved
                         else 'Persisted edit review is missing or has changed')
    return review


def proposal_view(conn, proposal_id, *, full=True):
    row = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (proposal_id,)).fetchone()
    if row is None:
        return None
    if full:
        _verify_proposal(conn, row)
    applied = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal_id,)).fetchone()
    review = conn.execute('SELECT er.request_id,er.approved,r.summary FROM edit_reviews er JOIN reviews r '
                          'ON r.request_id=er.request_id WHERE er.proposal_id=? ORDER BY er.created DESC LIMIT 1',
                          (proposal_id,)).fetchone()
    if full and review:
        review = _verified_review(conn, row, review['request_id'])
    if full and applied:
        _verified_review(conn, row, applied['review_request_id'], require_approved=True)
        revision = _verify_revision(_revision(conn, row['objective_id'], row['path'], applied['applied_revision']))
        if (applied['proposal_sha256'] != row['sha256'] or applied['applied_sha256'] != row['new_sha256']
                or applied['applied_revision'] != row['base_revision'] + 1
                or revision['content'] != row['new_content'] or revision['sha256'] != row['new_sha256']):
            raise ValueError('Persisted edit application is missing or has changed')
    application = conn.execute("SELECT reason FROM requests WHERE type='request.apply' AND task_id=? "
                               "AND json_extract(payload,'$.proposalId')=? ORDER BY created DESC LIMIT 1",
                               (row['task_id'], proposal_id)).fetchone()
    current = _revision(conn, row['objective_id'], row['path'])
    value = {'id': row['id'], 'workspaceId': row['workspace_id'], 'sourcePath': row['path'],
             'baseRevision': row['base_revision'], 'baseSha256': row['base_sha256'],
             'newSha256': row['new_sha256'], 'proposalSha256': row['sha256'],
             'status': 'applied' if applied else 'approved' if review and review['approved'] else 'proposed',
             'currentRevision': current['revision'] if current else row['base_revision'],
             'reviewStatus': 'pending' if review is None else 'approved' if review['approved'] else 'rejected'}
    if review:
        value['reviewReason'] = review['summary']
    if application and application['reason']:
        value['applicationReason'] = application['reason']
    if full:
        source = _revision(conn, row['objective_id'], row['path'], row['base_revision'])
        value.update({'diff': row['diff'], 'newContent': row['new_content'],
                      'baseContent': source['content'] if source else None})
    if applied:
        value.update({'appliedRevision': applied['applied_revision'],
                      'appliedAt': datetime.fromtimestamp(applied['created'], timezone.utc).isoformat()})
    return value


def evidence_proposal(conn, evidence_id, *, full=True):
    row = conn.execute('SELECT id FROM edit_proposals WHERE evidence_id=?', (evidence_id,)).fetchone()
    return proposal_view(conn, row['id'], full=full) if row else None


class OrganizationEditStore:
    """Mixin using the store's BEGIN IMMEDIATE and current-lease primitives."""

    def _edit_grant_reason(self, conn, author_id, team, *, applying=False):
        staff = self._staff(author_id)
        if applying and self.settings.roster is None:
            return 'work.edit requires an explicitly configured staff patch grant.'
        if (staff is None or not staff.enabled or staff.team != team
                or 'work.edit' not in staff.capabilities or 'work.edit' not in self.settings.capabilities):
            return 'The edit author no longer has the configured work.edit route.'
        required = {'read_file', 'patch'} if applying else {'read_file'}
        if (not required.issubset(self.settings.tool_grants)
                or not required.issubset(staff.tool_grants)):
            return ('Explicit organization and author read_file and patch grants are required.' if applying
                    else 'Explicit organization and author read_file grants are required.')
        state = conn.execute('SELECT active FROM staff_state WHERE agent_id=?', (author_id,)).fetchone()
        if not state or not state['active']:
            return 'The edit author is no longer activated.'
        return None

    def _require_edit_grant(self, conn, author_id, team, *, applying=False):
        reason = self._edit_grant_reason(conn, author_id, team, applying=applying)
        if reason:
            raise ValueError(reason)

    def edit_apply_unavailability(self, conn, request):
        """Preclaim routing diagnosis; final application repeats every check."""
        try:
            proposal = self._request_proposal(conn, request)
            self._require_edit_grant(conn, proposal['author_id'], request['team'], applying=True)
            original = conn.execute('SELECT team FROM requests WHERE id=?', (proposal['request_id'],)).fetchone()
            if original is None or original['team'] != request['team']:
                raise ValueError('Application must preserve the exact source request team')
            self._verify_root(conn, request['objective_id'], proposal['path'])
        except ValueError as error:
            return str(error)
        return None

    def _root_identity(self, path):
        alias, index = _alias_path(path)
        if index >= len(self.settings.read_roots):
            raise ValueError('The edit source root is no longer configured')
        return alias, _hash(self.settings.read_roots[index])

    def _verify_root(self, conn, objective_id, path):
        alias, identity = self._root_identity(path)
        old = conn.execute('SELECT root_identity FROM workspace_roots WHERE objective_id=? AND alias=?',
                           (objective_id, alias)).fetchone()
        if old is not None and old['root_identity'] != identity:
            raise ValueError('The source root alias changed; restore its original root before continuing')
        return alias, identity

    def capture_workspace_source(self, claim, path, loader):
        """Capture exact source once; loader() performs granted no-follow raw I/O.

        Existing revisions are canonical agent-owned content. Loader is called
        only for an uncaptured path, outside the writer lock, and must reject
        protected, non-regular, redacted, or oversized sources before returning.
        """
        with self._connect() as conn:
            request = self._owned(conn, claim)
            if request is None or request['type'] != 'work.edit':
                raise ValueError('Edit source capture requires a current work.edit lease')
            self._require_edit_grant(conn, request['agent_id'], request['team'])
            alias, identity = self._verify_root(conn, request['objective_id'], path)
            existing = _revision(conn, request['objective_id'], path)
        content = None
        if existing is None:
            content = loader()
            if isinstance(content, bytes):
                try:
                    content = content.decode('utf-8')
                except UnicodeDecodeError:
                    raise ValueError('Source must be valid UTF-8 text') from None
            content = _safe_content(content, 'Source content')
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None or request['type'] != 'work.edit':
                raise ValueError('Edit source capture lease is no longer owned')
            self._require_edit_grant(conn, request['agent_id'], request['team'])
            if self._verify_root(conn, request['objective_id'], path) != (alias, identity):
                raise ValueError('Source root changed during capture')
            current = _revision(conn, request['objective_id'], path)
            if current is None:
                if content is None:
                    raise ValueError('Captured workspace source is missing')
                conn.execute('INSERT OR IGNORE INTO workspace_roots VALUES (?,?,?)',
                             (request['objective_id'], alias, identity))
                conn.execute('INSERT OR IGNORE INTO workspaces VALUES (?,?)',
                             (request['objective_id'], request['objective_id']))
                workspace = conn.execute('SELECT id FROM workspaces WHERE objective_id=?',
                                         (request['objective_id'],)).fetchone()['id']
                conn.execute('INSERT INTO workspace_revisions VALUES (?,?,0,?,?,?,?)',
                             (request['objective_id'], path, workspace, content, _hash(content), time.time()))
                conn.execute('INSERT INTO workspace_heads VALUES (?,?,0)', (request['objective_id'], path))
                current = _revision(conn, request['objective_id'], path)
            _verify_revision(current)
            return {'content': current['content'], 'sourceSha256': current['sha256'],
                    'workspaceRevision': current['revision'], 'workspaceId': current['workspace_id']}

    def _finish_edit_work(self, conn, request, result):
        request = self._owned(conn, request)
        if request is None or request['type'] != 'work.edit' or not request['task_id']:
            raise ValueError('Edit proposal requires a current work.edit lease')
        self._require_edit_grant(conn, request['agent_id'], request['team'])
        edit = result.get('edit')
        fields = {'path', 'baseRevision', 'baseSha256', 'oldText', 'newText'}
        if not isinstance(edit, dict) or set(edit) != fields:
            raise ValueError('Edit must specify exactly path, baseRevision, baseSha256, oldText, and newText')
        self._verify_root(conn, request['objective_id'], edit['path'])
        if type(edit['baseRevision']) is not int or edit['baseRevision'] < 0:
            raise ValueError('Edit baseRevision must identify a captured workspace revision')
        source = _verify_revision(_revision(conn, request['objective_id'], edit['path'], edit['baseRevision']))
        if source['sha256'] != edit['baseSha256']:
            raise ValueError('Edit baseSha256 does not match the captured source')
        old, new = _exact_text(edit['oldText'], 'oldText'), _exact_text(edit['newText'], 'newText')
        if not old or source['content'].find(old) < 0 or source['content'].find(old) != source['content'].rfind(old):
            raise ValueError('oldText must match exactly one nonempty source substring')
        updated = _safe_content(source['content'].replace(old, new, 1), 'Proposed content')
        if updated == source['content']:
            raise ValueError('Edit replacement must change the captured content')
        receipts = conn.execute('SELECT * FROM tool_receipts WHERE request_id=? AND attempt=? ORDER BY created,id',
                                (request['id'], request['attempts'])).fetchall()
        if any(row['status'] not in {'completed', 'failed'} for row in receipts):
            raise ValueError('Edit source receipts include unresolved or blocked tool calls')
        receipt = next((row for row in receipts if _source_read(row, source)), None)
        if receipt is None:
            raise ValueError('Edit requires a matching persisted successful workspace source read')
        diff = _diff(edit['path'], source['content'], updated)
        proposal_id = 'proposal_' + uuid.uuid4().hex
        descriptor = (f'Edit proposal {proposal_id} for {edit["path"]}.\n'
                      f'Base revision: {source["revision"]}; source SHA-256: {source["sha256"]}.\n'
                      f'Proposed SHA-256: {_hash(updated)}.\n'
                      'Exact content and backend-generated diff are retained in the linked proposal. '
                      'Original source files are unchanged.')
        self._finish_work(conn, request, {'summary': result.get('summary'), 'deliverable': descriptor})
        evidence = conn.execute('SELECT * FROM evidence WHERE request_id=?', (request['id'],)).fetchone()
        proposal = {'id': proposal_id, 'request_id': request['id'],
                    'objective_id': request['objective_id'], 'task_id': request['task_id'],
                    'evidence_id': evidence['id'], 'evidence_sha256': evidence['sha256'],
                    'author_id': request['agent_id'], 'workspace_id': source['workspace_id'],
                    'path': edit['path'], 'base_revision': source['revision'], 'base_sha256': source['sha256'],
                    'old_text': old, 'new_text': new, 'new_content': updated, 'new_sha256': _hash(updated),
                    'diff': diff, 'source_receipt_id': receipt['id'], 'created': time.time()}
        proposal['sha256'] = _proposal_digest(proposal)
        columns = ','.join(proposal)
        conn.execute(f'INSERT INTO edit_proposals ({columns}) VALUES ({",".join("?" for _ in proposal)})',
                     tuple(proposal.values()))
        for review in conn.execute("SELECT id,payload FROM requests WHERE task_id=? AND type='request.review' AND status='queued'",
                                   (request['task_id'],)).fetchall():
            payload = json.loads(review['payload'])
            if payload.get('evidenceIds') == [evidence['id']]:
                payload.update({'proposalId': proposal['id'], 'proposalSha256': proposal['sha256']})
                conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), review['id']))

    def validate_edit_review(self, conn, request, result):
        task = conn.execute('SELECT type FROM tasks WHERE id=?', (request['task_id'],)).fetchone()
        if task is None or task['type'] != 'work.edit':
            return
        proposal = self._request_proposal(conn, request)
        if result.get('proposalId') != proposal['id'] or result.get('proposalSha256') != proposal['sha256']:
            raise ValueError('Edit review must identify the exact proposal ID and proposalSha256')
        _verify_proposal(conn, proposal)

    @staticmethod
    def _request_proposal(conn, request):
        payload = json.loads(request['payload'])
        proposal = conn.execute('SELECT * FROM edit_proposals WHERE id=?', (payload.get('proposalId'),)).fetchone()
        if (proposal is None or proposal['objective_id'] != request['objective_id']
                or proposal['task_id'] != request['task_id'] or proposal['sha256'] != payload.get('proposalSha256')
                or payload.get('evidenceIds') != [proposal['evidence_id']]):
            raise ValueError('Request is not bound to the exact edit proposal and evidence')
        return proposal

    def on_edit_review(self, conn, request, task, approved):
        if task['type'] != 'work.edit':
            return False
        proposal = self._request_proposal(conn, request)
        _verify_proposal(conn, proposal)
        review = conn.execute('SELECT * FROM reviews WHERE request_id=?', (request['id'],)).fetchone()
        if (review is None or review['agent_id'] != request['agent_id'] or review['agent_id'] == proposal['author_id']
                or review['approved'] != int(approved) or json.loads(review['evidence_ids']) != [proposal['evidence_id']]):
            raise ValueError('An independent evidence-bound edit review is required')
        conn.execute('INSERT INTO edit_reviews VALUES (?,?,?,?,?,?,?)',
                     (request['id'], proposal['id'], proposal['sha256'], proposal['evidence_sha256'],
                      request['agent_id'], int(approved), time.time()))
        if not approved:
            return False
        self._request(conn, request['objective_id'], 'request.apply', request['team'], request['priority'],
                      request['task_id'], {'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                                           'evidenceIds': [proposal['evidence_id']], 'reviewRequestId': request['id']})
        conn.execute("UPDATE tasks SET status='queued',feedback=? WHERE id=?", (review['summary'], task['id']))
        self._event(conn, request['objective_id'], 'Exact edit approved; managed-workspace application queued.',
                    'review', request['agent_id'])
        return True

    def _finish_apply(self, conn, request, result):
        request = self._owned(conn, request)
        if request is None or request['type'] != 'request.apply':
            raise ValueError('Managed-workspace application requires a current application lease')
        if not isinstance(result, dict) or result:
            raise ValueError('Managed-workspace application takes no model-provided result or instructions')
        agent = conn.execute('SELECT * FROM agents WHERE id=?', (request['agent_id'],)).fetchone()
        if (agent is None or agent['role'] != 'Employee' or agent['team'] != request['team']
                or json.loads(agent['accepts']) != ['request.apply'] or self._staff(agent['id']) is not None):
            raise ValueError('Only the matching backend-controlled workspace applier can apply edits')
        reason = self.edit_apply_unavailability(conn, request)
        if reason:
            raise ValueError(reason)
        proposal = self._request_proposal(conn, request)
        source = _verify_proposal(conn, proposal)
        self._verify_root(conn, request['objective_id'], proposal['path'])
        self._require_edit_grant(conn, proposal['author_id'], request['team'], applying=True)
        payload = json.loads(request['payload'])
        approval = _verified_review(conn, proposal, payload.get('reviewRequestId'), require_approved=True)
        previous = conn.execute('SELECT * FROM edit_applications WHERE proposal_id=?', (proposal['id'],)).fetchone()
        if previous:
            if (previous['proposal_sha256'] != proposal['sha256'] or previous['applied_sha256'] != proposal['new_sha256']
                    or previous['applied_revision'] != proposal['base_revision'] + 1
                    or previous['review_request_id'] != approval['request_id']):
                raise ValueError('Previous application receipt has changed')
            committed = _verify_revision(_revision(conn, request['objective_id'], proposal['path'], previous['applied_revision']))
            if committed['sha256'] != previous['applied_sha256']:
                raise ValueError('Committed application revision has changed')
            conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (request['task_id'],))
            return proposal_view(conn, proposal['id'])
        head = _verify_revision(_revision(conn, request['objective_id'], proposal['path']))
        if head['revision'] != source['revision'] or head['sha256'] != proposal['base_sha256']:
            raise ValueError('Workspace base is stale; a new source read, proposal, and review are required')
        applied_revision = head['revision'] + 1
        now = time.time()
        conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                     (request['objective_id'], proposal['path'], applied_revision, proposal['workspace_id'],
                      proposal['new_content'], proposal['new_sha256'], now))
        conn.execute('UPDATE workspace_heads SET revision=? WHERE objective_id=? AND path=? AND revision=?',
                     (applied_revision, request['objective_id'], proposal['path'], source['revision']))
        conn.execute('INSERT INTO edit_applications VALUES (?,?,?,?,?,?,?)',
                     (proposal['id'], request['id'], approval['request_id'], proposal['sha256'],
                      applied_revision, proposal['new_sha256'], now))
        # This gate has no task_id: pending original-project integration must not
        # retroactively relabel an already committed workspace application.
        merge = self._request(conn, request['objective_id'], 'request.merge', request['team'], request['priority'],
                              payload={'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                                       'taskId': request['task_id'], 'workspaceId': proposal['workspace_id'],
                                       'sourcePath': proposal['path'], 'appliedRevision': applied_revision})
        self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (merge,)).fetchone(),
                      'The reviewed edit was committed to the managed workspace. Original source files are unchanged; '
                      'merging into the original project requires explicit intervention.')
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (request['task_id'],))
        self._event(conn, request['objective_id'],
                    f'Managed-workspace revision {applied_revision} committed with its application receipt; source files are unchanged.',
                    'completion', request['agent_id'])
        return proposal_view(conn, proposal['id'])

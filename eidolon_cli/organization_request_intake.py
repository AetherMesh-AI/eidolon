"""Durable intake proposals and at-most-once inference debit receipts.

Admission reuses the ordinary objective transaction. These methods never run a
provider themselves; reserve_intake_call is the physical-send boundary.
"""
from decimal import Decimal
import json
import time
import uuid

from eidolon_cli.organization_intake_context import digest, intake_context
from eidolon_cli.organization_intake_contract import PRIORITIES, _text, validate_proposal
from eidolon_cli.organization_intake_duplicates import (DUPLICATE_SCHEMA, exact_duplicate, link_duplicate, remember_admission, separate_requested)

INTAKE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_request_intakes (
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 owner_identity_id TEXT NOT NULL, request_text TEXT NOT NULL, owner_priority TEXT,
 snapshot_id TEXT NOT NULL, context_json TEXT NOT NULL,
 status TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
 provider TEXT, model TEXT, input_limit INTEGER NOT NULL DEFAULT 0,
 output_limit INTEGER NOT NULL DEFAULT 0, reserved_cost TEXT, shared_receipt TEXT,
 input_tokens INTEGER, output_tokens INTEGER, proposal_json TEXT,
 objective_id TEXT UNIQUE REFERENCES objectives(id), reason TEXT, created REAL NOT NULL,
 parent_intake_id TEXT UNIQUE REFERENCES organization_request_intakes(id));
''' + DUPLICATE_SCHEMA


def _view(row):
    return {'id': row['id'], 'idempotencyKey': row['idempotency_key'],
            'request': row['request_text'], 'status': row['status'], 'parentIntakeId': row['parent_intake_id'],
            'snapshotId': row['snapshot_id'], 'dispatched': bool(row['dispatched']),
            'proposal': json.loads(row['proposal_json']) if row['proposal_json'] else None,
            'objectiveId': row['objective_id'] or (row['linked_objective_id'] if 'linked_objective_id' in row.keys() else None), 'reason': row['reason'],
            'usage': {'provider': row['provider'], 'model': row['model'],
                      'reservedTokens': row['input_limit'] + row['output_limit'],
                      'reservedCostUsd': row['reserved_cost'], 'inputTokens': row['input_tokens'],
                      'outputTokens': row['output_tokens']}}


class OrganizationIntakeStore:
    def intake_snapshot(self, *, owner_priority=None):
        if owner_priority is not None and owner_priority not in PRIORITIES:
            raise ValueError('Owner priority must be P1–P5')
        with self._connect() as conn:
            return intake_context(self, conn, owner_priority)[1]

    def _intake_row(self, conn, intake_id):
        row = conn.execute('SELECT i.*,l.objective_id linked_objective_id FROM organization_request_intakes i LEFT JOIN organization_intake_duplicate_links l ON l.intake_id=i.id WHERE i.id=?', (intake_id,)).fetchone()
        owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
        if row is None or owner is None or row['owner_identity_id'] != owner[0]:
            raise ValueError('Intake is not available to the current organization owner')
        return row

    def unfinished_intake_ids(self):
        with self._connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT id FROM organization_request_intakes WHERE status IN ('pending','proposed') "
                "AND owner_identity_id=(SELECT identity_id FROM agent_identity WHERE agent_id='owner') ORDER BY created")]

    def recent_intakes(self):
        with self._connect() as conn:
            owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
            if owner is None:
                raise ValueError('Organization owner identity is missing')
            return [_view(row) for row in conn.execute(
                'SELECT i.*,l.objective_id linked_objective_id FROM organization_request_intakes i LEFT JOIN organization_intake_duplicate_links l ON l.intake_id=i.id WHERE i.owner_identity_id=? ORDER BY i.created DESC,i.id DESC LIMIT 20',
                (owner[0],))]

    def intake_read(self, intake_id):
        with self._connect() as conn:
            return _view(self._intake_row(conn, _text(intake_id, 'Intake ID', 128)))

    def begin_intake(self, text, *, idempotency_key, owner_priority=None, parent_intake_id=None, force_separate=False):
        text = _text(text, 'Request', 12000)
        key = _text(idempotency_key, 'Idempotency key', 128)
        if owner_priority is not None and owner_priority not in PRIORITIES:
            raise ValueError('Owner priority must be P1–P5')
        if type(force_separate) is not bool:
            raise ValueError('forceSeparate must be a boolean')
        fingerprint = digest([text, owner_priority, parent_intake_id] + ([True] if force_separate else []))
        with self._write() as conn:
            prior = conn.execute('SELECT * FROM organization_request_intakes WHERE idempotency_key=?', (key,)).fetchone()
            if prior is not None:
                row = self._intake_row(conn, prior['id'])
                if row['input_hash'] != fingerprint:
                    raise ValueError('Intake key already belongs to another request')
                return _view(row), False
            if parent_intake_id is not None:
                parent = self._intake_row(conn, _text(parent_intake_id, 'Parent intake ID', 128))
                if parent['status'] != 'needs_answer':
                    raise ValueError('This intake is not waiting for an answer')
                if owner_priority is not None and owner_priority != parent['owner_priority']:
                    raise ValueError('Clarification cannot silently replace the owner priority')
                owner_priority = parent['owner_priority']
                parent_option = conn.execute('SELECT force_separate FROM organization_intake_options WHERE intake_id=?', (parent_intake_id,)).fetchone()
                force_separate = bool(parent_option[0]) if parent_option is not None else False
                question = json.loads(parent['proposal_json'])['question']['text']
                text = _text(parent['request_text'] + '\n\nClarification question: ' + question +
                             '\nOwner answer: ' + text, 'Clarified request', 12000)
            context, payload = intake_context(self, conn, owner_priority)
            if not context.routes:
                raise ValueError('No eligible configured organization manager route')
            active = conn.execute("SELECT count(*) FROM organization_request_intakes WHERE status='pending' AND owner_identity_id=?",
                                  (payload['ownerIdentityId'],)).fetchone()[0]
            if active >= 2:
                raise ValueError('Two intake proposals are already pending')
            ident = 'intake_' + uuid.uuid4().hex
            conn.execute('INSERT INTO organization_request_intakes '
                '(id,idempotency_key,input_hash,owner_identity_id,request_text,owner_priority,snapshot_id,context_json,status,created,parent_intake_id) '
                "VALUES (?,?,?,?,?,?,?,?, 'pending',?,?)", (ident, key, fingerprint, payload['ownerIdentityId'], text,
                    owner_priority, context.snapshot_id, json.dumps(payload), time.time(), parent_intake_id))
            conn.execute('INSERT INTO organization_intake_options VALUES (?,?)', (ident, int(force_separate)))
            if parent_intake_id is not None:
                conn.execute("UPDATE organization_request_intakes SET status='answered' WHERE id=?", (parent_intake_id,))
            return _view(self._intake_row(conn, ident)), True

    def intake_prompt(self, intake_id):
        with self._connect() as conn:
            row = self._intake_row(conn, intake_id)
            return {'request': row['request_text'], 'evidence': [{'id': 'owner-request',
                    'text': row['request_text'], 'provenance': 'owner_report_unverified'},
                    *json.loads(row['context_json']).get('retainedEvidence', {}).get('items', []),
                    *json.loads(row['context_json']).get('freshInspection', {}).get('items', [])],
                    'organization': json.loads(row['context_json'])}

    def reserve_intake_call(self, intake_id, *, provider, model, input_limit, output_limit, selected_provider=None):
        if (type(input_limit) is not int or type(output_limit) is not int or
                not 0 < input_limit <= 32768 or not 0 < output_limit <= 8000):
            raise ValueError('Intake model call exceeds its bounded token allowance')
        provider = _text(selected_provider or provider, 'Provider', 300)
        model = _text(model, 'Model', 300)
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            context, _ = intake_context(self, conn, row['owner_priority'], intake_id=intake_id)
            if row['status'] != 'pending' or row['dispatched']:
                raise ValueError('Intake allows exactly one physical provider send')
            if row['snapshot_id'] != context.snapshot_id:
                raise ValueError('Intake context changed before dispatch')
            previous = conn.execute('SELECT input_limit,output_limit,reserved_cost FROM organization_request_intakes WHERE dispatched=1').fetchall()
            tokens = input_limit + output_limit
            if (len(previous) >= min(20, self.settings.max_model_calls) or
                    sum(item['input_limit'] + item['output_limit'] for item in previous) + tokens > min(800000, self.settings.max_total_tokens) or
                    tokens > self.settings.max_context_tokens or output_limit > self.settings.max_output_tokens):
                raise ValueError('Finite intake allowance exhausted; manual submission remains available')
            from eidolon_cli.organization_shared_budget import configured_call_cost, reserve_shared_call
            cost = configured_call_cost(self.settings, provider, model, input_limit, output_limit)
            if self.settings.max_cost_usd is not None:
                if cost is None or any(item['reserved_cost'] is None for item in previous):
                    raise ValueError('Intake needs exact configured model prices under a cost limit')
                total = sum((Decimal(item['reserved_cost']) for item in previous), Decimal(0)) + Decimal(cost)
                if total > Decimal(self.settings.max_cost_usd):
                    raise ValueError('Intake configured cost limit exhausted')
            shared = reserve_shared_call(self, conn, {'kind': 'request_intake', 'intakeId': intake_id,
                'provider': provider, 'model': model, 'inputLimit': input_limit,
                'outputLimit': output_limit, 'reservedCostUsd': cost})
            receipt = None
            if shared is not None:
                _, receipt, pin = shared
                conn.execute('INSERT OR IGNORE INTO organization_shared_scope_pin VALUES (1,?)',
                             (json.dumps(pin, sort_keys=True, separators=(',', ':')),))
            conn.execute('UPDATE organization_request_intakes SET dispatched=1,provider=?,model=?,input_limit=?,output_limit=?,reserved_cost=?,shared_receipt=? WHERE id=?',
                         (provider, model, input_limit, output_limit, cost, json.dumps(receipt) if receipt else None, intake_id))

    def finish_intake(self, intake_id, raw, *, usage=None):
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            if row['status'] != 'pending':
                return _view(row)
            if not row['dispatched']:
                raise ValueError('Intake response has no reserved physical send')
            context, _ = intake_context(self, conn, row['owner_priority'], intake_id=intake_id)
            if context.snapshot_id != row['snapshot_id']:
                raise ValueError('Intake context changed before response acceptance')
            result = validate_proposal(raw, context)
            status = result['status']
            review_reason = None
            if status == 'proposed':
                from eidolon_cli.organization_intake_dependencies import validate_automatic_dependencies
                _, review_reason = validate_automatic_dependencies(self, conn, row['request_text'], result,
                    json.loads(row['context_json']))
                if review_reason:
                    status = 'needs_review'
            if status == 'proposed' and result['duplicateCandidateIds']:
                status = 'proposed' if (exact_duplicate(self, conn, row, result) or
                    separate_requested(conn, intake_id)) else 'needs_review'
            usage = usage or {}
            counts = [usage.get(key) for key in ('inputTokens', 'outputTokens')]
            if any(value is not None and (type(value) is not int or value < 0) for value in counts):
                raise ValueError('Usage must contain nonnegative actual token counts or null')
            conn.execute('UPDATE organization_request_intakes SET status=?,proposal_json=?,input_tokens=?,output_tokens=?,reason=? WHERE id=?',
                         (status, json.dumps(result), *counts, review_reason, intake_id))
            return _view(self._intake_row(conn, intake_id))

    def record_intake_usage(self, intake_id, usage):
        counts = [usage.get(key) for key in ('inputTokens', 'outputTokens')]
        if any(value is not None and (type(value) is not int or value < 0) for value in counts):
            raise ValueError('Usage must contain nonnegative actual token counts or null')
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            if not row['dispatched']:
                raise ValueError('Usage has no reserved physical send')
            if any(row[key] is not None and row[key] != value for key, value in zip(('input_tokens', 'output_tokens'), counts)):
                raise ValueError('Intake usage receipt already has different counts')
            conn.execute('UPDATE organization_request_intakes SET input_tokens=?,output_tokens=? WHERE id=?', (*counts, intake_id))

    def fail_intake(self, intake_id, reason, *, known_response=False):
        reason = _text(reason, 'Intake failure', 1000)
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            if row['status'] == 'pending':
                status = 'uncertain' if row['dispatched'] and not known_response else 'failed'
                conn.execute('UPDATE organization_request_intakes SET status=?,reason=? WHERE id=?', (status, reason, intake_id))
            elif row['status'] == 'proposed':
                conn.execute("UPDATE organization_request_intakes SET status='needs_review',reason=? WHERE id=?", (reason, intake_id))
            return _view(self._intake_row(conn, intake_id))

    def admit_intake(self, intake_id):
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            if row['status'] in ('admitted', 'linked'):
                return _view(row)
            if row['status'] != 'proposed':
                raise ValueError('Intake requires an answer or review before admission')
            context, _ = intake_context(self, conn, row['owner_priority'], intake_id=intake_id)
            if context.snapshot_id != row['snapshot_id']:
                raise ValueError('Intake context changed before admission')
            proposal = json.loads(row['proposal_json'])
            match = exact_duplicate(self, conn, row, proposal)
            if match is not None:
                link_duplicate(conn, intake_id, match)
                return _view(self._intake_row(conn, intake_id))
            if proposal['duplicateCandidateIds'] and not separate_requested(conn, intake_id):
                raise ValueError('Intake duplicate requires review')
            from eidolon_cli.organization_intake_dependencies import validate_automatic_dependencies
            dependency_pins, dependency_reason = validate_automatic_dependencies(self, conn, row['request_text'],
                proposal, json.loads(row['context_json']))
            if dependency_reason:
                raise ValueError(dependency_reason)
            from eidolon_cli.organization_projects import selected_projects
            projects = selected_projects(proposal['projectIds'], self.settings)
            delivery = proposal['deliveryMode']
            if delivery == 'source_project' and (not projects or any(p['recipe'] is None for p in projects)):
                raise ValueError('Requested project changes need an existing approved execution recipe; the outcome was not downgraded to a report')
            objective_id = self._create_objective(conn, row['request_text'][:500], row['request_text'],
                proposal['effectivePriority'], idempotency_key='intake:' + intake_id,
                acceptance_criteria=proposal['acceptanceCriteria'], delivery_mode=delivery,
                executive_id=proposal['executiveId'], manager_id=proposal['managerId'],
                project_ids=proposal['projectIds'])
            if dependency_pins:
                from eidolon_cli.organization_milestones import _objective
                from eidolon_cli.organization_objective_dependencies import add_objective_dependency
                _, _, scope_sha256 = _objective(conn, objective_id)
                for pin in dependency_pins:
                    add_objective_dependency(conn, pin['objectiveId'], objective_id,
                        predecessor_receipt_sha256=pin['receiptSha256'], predecessor_receipt=pin['receipt'],
                        successor_scope_sha256=scope_sha256, owner_identity_id=row['owner_identity_id'],
                        manager_id=proposal['managerId'])
            remember_admission(conn, intake_id, objective_id)
            conn.execute("UPDATE organization_request_intakes SET status='admitted',objective_id=? WHERE id=?", (objective_id, intake_id))
            return _view(self._intake_row(conn, intake_id))

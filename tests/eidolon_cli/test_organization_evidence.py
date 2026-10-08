"""Complete exact evidence admission, hierarchical coverage and immutable proof."""
import copy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli.organization_evidence import (
    CONTEXT_RESERVE_TOKENS,
    project_evidence, verify_context_receipt, wire_input_bound,
)


def artifact(identifier, content, **extra):
    return {'id': identifier, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'toolReceipts': [], **extra}


@pytest.fixture
def model(monkeypatch):
    state = SimpleNamespace(prompts=[], instances=[], reports=[], passes=[], reservations=[], negative=None,
                            window=128000, stage='request.integrate', evidence=[], criteria=['Combine every finding'])
    monkeypatch.setattr(executor, '_guard_plugin_integrations', lambda **_: None)
    monkeypatch.setattr(executor, '_runtime_kwargs', lambda context, timeout: {
        'model': 'bounded-fixture', 'provider': 'custom', 'api_mode': 'chat_completions',
        'base_url': 'http://127.0.0.1:1/v1', 'request_overrides': {}, 'max_tokens': 8000,
        'ephemeral_system_prompt': executor._SYSTEM})

    class Agent(executor._ToolFreeBoundary):
        def __init__(self, kwargs, cancel, deadline, execution=None):
            self.__dict__.update(kwargs)
            self.context_compressor = SimpleNamespace(context_length=state.window)
            self.tools, self.valid_tool_names = [], set()
            self._organization_cancel, self._organization_deadline = cancel, deadline
            self._organization_intervention = None
            self._organization_tool_execution = None
            self.closed = False
            state.instances.append(self)

        def run_conversation(self, *, user_message):
            self._organization_check()
            wire = {'model': self.model, 'max_tokens': 8000,
                    'messages': [{'role': 'system', 'content': executor._SYSTEM}, {'role': 'user', 'content': user_message}]}
            self._organization_reserve_call(wire)
            assert wire_input_bound(wire) + 8000 + CONTEXT_RESERVE_TOKENS <= min(state.window, 128000)
            state.prompts.append(user_message)
            payload = json.loads(user_message.split('Submitted context:\n', 1)[1])
            if 'sourceChunk' in payload:
                negative = state.negative is not None and state.negative in payload['sourceChunk']
                output = {'approved': not negative,
                          'findings': 'Observed complete exact range ' + str(payload['sourceRange']) +
                                      (' with a contradictory claim.' if negative else '; retained scoped findings for synthesis.'),
                          'conflicts': ['The original contradicts the final output.'] if negative else []}
            elif state.stage == 'request.accept':
                output = {'approved': True, 'summary': 'All original exact sources checked against the candidate.',
                          'evidenceIds': [row['id'] for row in state.evidence], 'conflicts': [],
                          'criteriaResults': [{'criterion': criterion, 'satisfied': True,
                                               'evidenceIds': [row['id'] for row in state.evidence],
                                               'reason': 'Exact independent read passes and final comparison support this criterion.'}
                                              for criterion in state.criteria]}
            else:
                output = {'summary': 'Integrated all retained sources.', 'deliverable': 'A complete final synthesis.'}
            return {'final_response': json.dumps(output)}

        def interrupt(self, *args, **kwargs):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(executor, '_create_agent', Agent)
    return state


def large_context(state):
    rows = [artifact(f'artifact-{index}', f'Unique opening {index}\n' + chr(65 + index) * 27500 + f'\nUnique ending {index}')
            for index in range(6)]
    state.evidence = rows
    return {'objective': {'description': 'Integrate all six complete findings', 'acceptanceCriteria': state.criteria},
            'evidence': rows, 'maxContextTokens': 128000, 'maxOutputTokens': 8000,
            'recordEvidencePass': state.passes.append, 'recordContextReceipt': state.reports.append,
            'reserveModelCall': lambda **entry: state.reservations.append(entry)}


def test_large_integrate_and_edit_evidence_recover_with_bounded_complete_reads(model):
    context = large_context(model)
    # The real failure input exceeds 128k chars before any dependency duplication.
    assert sum(len(row['content']) for row in context['evidence']) > 165000
    context['dependencies'] = [{'taskId': f'task-{index}', 'evidenceId': row['id'], 'sha256': row['sha256'],
                                'deliverable': row['content'], 'toolReceipts': []}
                               for index, row in enumerate(context['evidence'])]
    original = copy.deepcopy({key: value for key, value in context.items() if not callable(value)})
    result = executor.execute({'type': 'request.integrate'}, context, threading.Event())
    assert result.get('deliverable') == 'A complete final synthesis.', result
    assert len(model.reports) == 1 and model.reports[0]['mode'] == 'hierarchical'
    verify_context_receipt(context, model.reports[0], model.passes, approved=True)
    assert {key: value for key, value in context.items() if not callable(value)} == original
    assert all(agent.closed for agent in model.instances)
    assert len(model.reservations) == len(model.prompts) == len(model.passes) + 1
    # Every unique start/end made it to exact input, even while all dependency
    # aliases refer to the same bytes without duplicating those bodies.
    chunks = [json.loads(prompt.split('Submitted context:\n', 1)[1]).get('sourceChunk') for prompt in model.prompts]
    for row in context['evidence']:
        assert row['content'] in chunks
    assert 'NOT original artifact bytes' in model.prompts[-1]
    assert model.reports[0]['fullEvidence'] is False

    # Edit-artifact evidence accepted by the store above the old 128k parser
    # limit can flow through integration and replanning without losing proof.
    model.prompts.clear(); model.passes.clear(); model.reports.clear(); model.reservations.clear()
    content = 'Managed source descriptor\n' + 'x' * 139000
    base, new = 'before\n' * 4000, 'after\n' * 4000
    proposal = {'id': 'proposal-one', 'proposalSha256': 'a' * 64, 'baseContent': base, 'newContent': new,
                'files': [{'path': 'root0/module.py', 'baseContent': base, 'newContent': new,
                           'diff': '-before\n+after\n' * 4000}], 'validations': []}
    context['evidence'] = [artifact('edit-artifact', content, editProposal=proposal)]
    context['dependencies'] = [{'taskId': 'edited', 'evidenceId': 'edit-artifact', 'deliverable': content,
                                'sha256': context['evidence'][0]['sha256'], 'editProposal': proposal}]
    projected = project_evidence(context)
    assert list(projected['evidenceBodies'].values()).count(base) == 1
    assert list(projected['evidenceBodies'].values()).count(content) == 1
    assert executor._evidence_ids(context) == ['edit-artifact']
    result = executor.execute({'type': 'request.integrate'}, context, threading.Event())
    assert 'deliverable' in result, result
    verify_context_receipt(context, model.reports[0], model.passes, approved=True)
    assert model.reports[0]['passCount'] == len(model.passes)


def test_acceptance_rejects_negative_missing_tampered_or_impossibly_large_proof(model):
    context = large_context(model)
    candidate = artifact('final', 'A complete integrated candidate.', kind='integrated_deliverable')
    context['evidence'].append(candidate)
    model.stage = 'request.accept'
    result = executor.execute({'type': model.stage}, context, threading.Event())
    assert result.get('approved') is True, result
    report, passes = model.reports[0], model.passes
    verify_context_receipt(context, report, passes, approved=True)
    for change in (
        lambda rows: rows.pop(),
        lambda rows: rows.reverse(),
        lambda rows: rows[0].update(start=1),
        lambda rows: rows[0].update(chunkSha256='0' * 64),
        lambda rows: rows[0].update(approved=False, conflicts=['Rejected local proof']),
    ):
        changed = copy.deepcopy(passes)
        change(changed)
        with pytest.raises(executor.OrganizationExecutionError):
            verify_context_receipt(context, report, changed, approved=True)
    tampered = copy.deepcopy({key: value for key, value in context.items() if not callable(value)})
    tampered['evidence'][0]['content'] += 'Unreviewed tail'
    with pytest.raises(executor.OrganizationExecutionError, match='hash'):
        verify_context_receipt(tampered, report, passes, approved=True)

    model.prompts.clear(); model.passes.clear(); model.reports.clear(); model.reservations.clear()
    model.negative = 'Unique ending 5'
    rejected = executor.execute({'type': model.stage}, context, threading.Event())
    assert rejected.get('approved') is False, rejected
    assert rejected['conflicts'] and not all(row['satisfied'] for row in rejected['criteriaResults'])
    # No optimistic final model is called after an independent exact read rejects.
    assert len(model.prompts) == len(model.passes)

    model.prompts.clear(); model.passes.clear(); model.reports.clear(); model.reservations.clear()
    model.window = 4096
    blocked = executor.execute({'type': model.stage}, context, threading.Event())
    assert 'intervention' in blocked
    assert not model.prompts and not model.reservations


def test_exact_clarification_overflow_is_read_and_cannot_be_omitted_from_acceptance(model):
    context = large_context(model)
    context['evidence'].append(artifact('final', 'The candidate makes an unsupported public-release claim.',
                                        kind='integrated_deliverable'))
    model.stage = 'request.accept'
    # Each response fits the ledger's 12,000-character limit. The collection
    # exceeds the model window and must use complete audited source reads.
    context['objectiveClarifications'] = [
        {'id': f'question-{index}', 'type': 'request.question', 'requesterId': 'manager',
         'requestedOutcome': f'What applies to component {index}?', 'round': 0, 'historical': False,
         'response': {'responderId': 'owner', 'decision': 'answered', 'createdAt': '2026-10-08T00:00:00Z',
                      'text': f'Component {index}: ' + ('Exact retained details. ' * 490) +
                              (' No public release was authorized.' if index == 11 else '')}}
        for index in range(12)]
    model.negative = 'No public release was authorized.'
    result = executor.execute({'type': model.stage}, context, threading.Event())
    assert result.get('approved') is False, result
    assert result['conflicts']
    chunks = [json.loads(prompt.split('Submitted context:\n', 1)[1])['sourceChunk']
              for prompt in model.prompts]
    for clarification in context['objectiveClarifications']:
        assert clarification['response']['text'] in chunks
    verify_context_receipt(context, model.reports[0], model.passes, approved=False)
    changed = copy.deepcopy({key: value for key, value in context.items() if not callable(value)})
    changed['objectiveClarifications'][-1]['response']['text'] = 'Release authorized.'
    with pytest.raises(executor.OrganizationExecutionError):
        verify_context_receipt(changed, model.reports[0], model.passes, approved=False)

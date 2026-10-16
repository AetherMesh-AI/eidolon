"""Behavioral tests of the untrusted intake proposal boundary, not model quality."""
import json
from dataclasses import replace

import pytest

from eidolon_cli.organization_intake_contract import Context, Evidence, Route, validate_proposal


@pytest.fixture
def context():
    return Context('revision-a', (Route('eidolon', 'exec', 'eidolon-manager', ('desktop', 'cli')),
                                 Route('docs', 'exec', 'docs-manager', ('plain-folder',))),
                   (Evidence('owner-report'), Evidence('startup-reproduction', True, ('eidolon',))), ('emoji-request', 'backend-fix'))


def proposal(**changes):
    value = dict(snapshotId='revision-a', routeId='eidolon', projectIds=['desktop', 'cli'], deliveryMode='source_project', priority='P3',
                 rationale='User requests an optional display improvement.', evidenceIds=['owner-report'],
                 acceptanceCriteria=['Emoji render consistently in messages.'], assumptions=[],
                 duplicateCandidateIds=[], dependencyCandidateIds=[], question=None)
    return json.dumps({**value, **changes})


def test_multirepo_route_is_pinned_by_server(context):
    result = validate_proposal(proposal(), context)
    assert result['managerId'] == 'eidolon-manager'
    assert result['projectIds'] == ['desktop', 'cli']
    assert result['status'] == 'proposed'


def test_plain_folder_is_an_equal_route(context):
    assert validate_proposal(proposal(routeId='docs', projectIds=['plain-folder']), context)['projectIds'] == ['plain-folder']


def test_provisional_outage_and_verified_outage(context):
    assert validate_proposal(proposal(priority='P2'), context)['inferredPriority'] == 'P2'
    with pytest.raises(ValueError, match='verified current blocker'):
        validate_proposal(proposal(priority='P1'), context)
    assert validate_proposal(proposal(priority='P1', evidenceIds=['startup-reproduction']), context)['effectivePriority'] == 'P1'


def test_owner_override_survives_inference(context):
    result = validate_proposal(proposal(priority='P4'), replace(context, owner_priority='P1'))
    assert result['inferredPriority'] == 'P4'
    assert result['effectivePriority'] == 'P1'
    assert result['ownerOverride']


def test_essential_question_preserves_ambiguous_route(context):
    question = dict(text='Which installation fails?', neededFor='Two installations have different managers.',
                    checkedEvidenceIds=['owner-report'])
    result = validate_proposal(proposal(routeId=None, projectIds=[], question=question), context)
    assert result['status'] == 'needs_answer'
    assert result['managerId'] is None
    assert result['question'] == question


@pytest.mark.parametrize('changes', [
    {'snapshotId': 'stale'}, {'routeId': 'invented-project'}, {'routeId': []},
    {'priority': 'critical'}, {'priority': []}, {'projectIds': ['secret-folder']},
    {'budget': 1000}, {'evidenceIds': ['invented-proof']}, {'evidenceIds': ['owner-report', 'owner-report']},
    {'duplicateCandidateIds': ['foreign-objective']}, {'dependencyCandidateIds': ['foreign-objective']},
    {'duplicateCandidateIds': ['emoji-request'], 'dependencyCandidateIds': ['emoji-request']},
    {'question': {'text': 'Why?'}}, {'routeId': None}, {'acceptanceCriteria': []},
    {'rationale': ''}, {'assumptions': 'anything'},
], ids=lambda value: next(iter(value)))
def test_rejects_stale_invented_or_malformed_proposals(context, changes):
    with pytest.raises(ValueError):
        validate_proposal(proposal(**changes), context)


def test_candidates_do_not_mutate_existing_backlog(context):
    before = context.open_objective_ids
    result = validate_proposal(proposal(duplicateCandidateIds=['emoji-request'], dependencyCandidateIds=['backend-fix']), context)
    assert context.open_objective_ids == before
    assert result['duplicateCandidateIds'] == ['emoji-request']
    assert result['dependencyCandidateIds'] == ['backend-fix']


@pytest.mark.parametrize('raw', ['', '{}', '[]', '{"x":NaN}', '{"x":1,"x":2}', 'x' * 32001])
def test_rejects_invalid_wire_data(context, raw):
    with pytest.raises(ValueError):
        validate_proposal(raw, context)


def test_other_project_blocker_cannot_promote_this_route(context):
    with pytest.raises(ValueError, match='verified current blocker'):
        validate_proposal(proposal(routeId='docs', priority='P1', evidenceIds=['startup-reproduction']), context)


@pytest.mark.parametrize('field', ['rationale', 'acceptanceCriteria', 'assumptions'])
def test_rejects_lone_surrogate_text(context, field):
    value = '\ud800' if field == 'rationale' else ['\ud800']
    with pytest.raises(ValueError, match='Unicode'):
        validate_proposal(proposal(**{field: value}), context)


@pytest.mark.parametrize('bad', ['\ud800', '\udfff'])
def test_rejects_surrogate_in_question(context, bad):
    question = dict(text=bad, neededFor='Determine project', checkedEvidenceIds=[])
    with pytest.raises(ValueError, match='Unicode'):
        validate_proposal(proposal(question=question), context)


def test_accepts_real_emoji_text(context):
    result = validate_proposal(proposal(rationale='Add emoji reactions 😀'), context)
    assert result['rationale'].endswith('😀')
    json.dumps(result, ensure_ascii=False).encode('utf-8')


def test_route_can_narrow_project_scope(context):
    result = validate_proposal(proposal(projectIds=['cli']), context)
    assert result['projectIds'] == ['cli']


def test_accepted_dependency_ids_are_separate_from_duplicate_candidates(context):
    eligible = replace(context, accepted_dependency_ids=('accepted-report',))
    value = validate_proposal(proposal(dependencyCandidateIds=['accepted-report']), eligible)
    assert value['dependencyCandidateIds'] == ['accepted-report']
    with pytest.raises(ValueError, match='unavailable context'):
        validate_proposal(proposal(duplicateCandidateIds=['accepted-report']), eligible)
    with pytest.raises(ValueError, match='unavailable context'):
        validate_proposal(proposal(dependencyCandidateIds=['other-accepted']), eligible)

"""Real accepted receipts, exact owner references and read-only scope validation."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3

import pytest

from eidolon_cli import organization_intake_dependencies as dependencies
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_dependency_receipts import accepted_dependency_receipt
from eidolon_cli.organization_projects import OrganizationProject
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_roster import OrganizationStaff
from tests.eidolon_cli.test_organization_acceptance import _plan, _work_and_review, _integrate
from tests.eidolon_cli.test_organization_dependency_receipts import accepted, damage
from tests.eidolon_cli.test_organization_outcome_contracts import decision
from tests.organization_package_helpers import decompose


def collect(store):
    with store._connect() as conn:
        return dependencies.collect_dependency_context(store, conn)


def proposal(*identifiers, **changes):
    return {'routeId': 'manager', 'managerId': 'manager', 'executiveId': 'executive',
            'projectIds': [], 'dependencyCandidateIds': list(identifiers), **changes}


def directive(identifier):
    return f'Use objective {identifier} to prepare the follow-on report.'


def validate(store, request, value, context, **kwargs):
    with store._connect() as conn:
        return dependencies.validate_automatic_dependencies(store, conn, request, value, context, **kwargs)


def complete(store, key, *, title='Recommendation', description='Choose a supported option.', project_ids=None):
    objective = store.create_objective(title, description, idempotency_key=key, delivery_mode='managed_artifact',
        project_ids=project_ids, acceptance_criteria=[
            {'id': 'recommendation', 'text': 'One supported recommendation', 'kind': 'deliverable'}])
    if project_ids:
        decompose(store)
        claim = store.claim_next()
        assert store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Support the recommendation',
                                             'type': 'work.analyze', 'projectId': project_ids[0]}]})
    else:
        _plan(store)
    _work_and_review(store)
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    return objective['id']


def project_store(tmp_path):
    roots = [tmp_path / 'private-root-a', tmp_path / 'private-root-b']
    for root in roots:
        root.mkdir()
    settings = replace(OrganizationSettings(), read_roots=tuple(map(str, roots)),
        projects=(OrganizationProject('alpha', 'root0', None, 'general'),
                  OrganizationProject('beta', 'root1', None, 'general')))
    store = OrganizationStore(tmp_path / 'state.db', settings)
    identifier = complete(store, 'alpha-brief', project_ids=['alpha'])
    return store, identifier


def test_real_accepted_dependency_is_exact_pinned_deterministic_and_passive(tmp_path, monkeypatch):
    store, identifier, _ = accepted(tmp_path)
    from tools import organization_file_read
    def forbidden(*args, **kwargs):
        raise AssertionError('Dependency intake must never read live source')
    monkeypatch.setattr(organization_file_read, 'organization_file_read_scope', forbidden)
    context = collect(store)
    assert context == collect(OrganizationStore(store.path))
    assert context['dependencyContextStatus'] == 'available'
    assert not context['dependencyContextTruncated']
    assert context['dependencyContextLimitations'] == []
    with sqlite3.connect(f'file:{store.path}?mode=ro', uri=True, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
        conn.set_authorizer(lambda operation, *args: sqlite3.SQLITE_OK if operation in allowed else sqlite3.SQLITE_DENY)
        assert dependencies.collect_dependency_context(store, conn) == context
        pins, reason = dependencies.validate_automatic_dependencies(
            store, conn, directive(identifier), proposal(identifier), context)
        assert reason is None and len(pins) == 1
        receipt = accepted_dependency_receipt(conn, identifier)
        assert pins[0]['receipt'] == receipt.to_dict()
        assert pins[0]['receiptSha256'] == receipt.sha256
        assert pins[0]['ownerIdentityId'] == context['ownerIdentityId']
        assert conn.total_changes == 0 and not conn.in_transaction
        conn.execute('BEGIN')
        assert dependencies.collect_dependency_context(store, conn) == context
        assert dependencies.validate_automatic_dependencies(store, conn, directive(identifier), proposal(identifier), context) == (pins, None)
        assert conn.in_transaction and conn.total_changes == 0
        conn.rollback()
    serialized = json.dumps(context)
    assert 'Choose A. The exact supplied facts' not in serialized
    assert 'artifacts' not in serialized and 'workspace' not in serialized and 'readRoot' not in serialized


@pytest.mark.parametrize('template', ['Use objective {id} to prepare a report.', 'Prerequisite: {id}',
    'use objective {id} to prepare a report', 'Use objective {id} to prepare a report.\nPrerequisite: {id}'])
def test_explicit_affirmative_directives_admit_exact_references(tmp_path, template):
    store, identifier, _ = accepted(tmp_path)
    pins, reason = validate(store, template.format(id=identifier), proposal(identifier), collect(store))
    assert reason is None and pins[0]['objectiveId'] == identifier


@pytest.mark.parametrize('template', ['Use a prior recommendation.', 'prefix{id}', '{id}suffix',
    '{id}-other', 'other-{id}', '{id}_other', 'é{id}', '{id}界'])
def test_semantic_or_substring_references_never_supply_owner_intent(tmp_path, template):
    store, identifier, _ = accepted(tmp_path)
    pins, reason = validate(store, template.format(id=identifier), proposal(identifier), collect(store))
    assert pins is None and 'affirmative dependency directive' in reason


def test_generated_clarification_question_cannot_manufacture_owner_reference(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    request = f'Write a sequel.\n\nClarification question: Use {identifier}?\nOwner answer: Yes.'
    context = collect(store)
    pins, reason = validate(store, request, proposal(identifier), context)
    assert pins is None and 'independently retained' in reason
    pins, reason = validate(store, request, proposal(identifier), context, owner_texts=['Write a sequel.', 'Yes.'])
    assert pins is None and 'affirmative dependency directive' in reason
    pins, reason = validate(store, request, proposal(identifier), context,
                            owner_texts=['Write a sequel.', directive(identifier)])
    assert reason is None and pins[0]['objectiveId'] == identifier


def test_no_selection_is_passive_even_without_dependency_context(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    assert validate(store, 'Write an independent report', proposal(), {}) == ([], None)
    assert collect(store)['dependencyContextStatus'] == 'unavailable'


def test_open_or_unselected_objectives_do_not_become_pins(tmp_path):
    store, first, _ = accepted(tmp_path)
    second = complete(store, 'second')
    opened = store.create_objective('Still open', idempotency_key='open')['id']
    context = collect(store)
    assert {item['objectiveId'] for item in context['dependencyCandidates']} == {first, second}
    pins, reason = validate(store, directive(first), proposal(first), context)
    assert reason is None and [pin['objectiveId'] for pin in pins] == [first]
    pins, reason = validate(store, opened, proposal(opened), context)
    assert pins is None and reason


@pytest.mark.parametrize('mutation', [
    "UPDATE objectives SET cancelled=1",
    "UPDATE objective_control SET status='pending'",
    "UPDATE objectives SET description='Changed scope'",
    "UPDATE objective_deliverables SET content='Corrupt retained artifact'",
    "UPDATE objective_control SET round=round+1",
    "UPDATE agent_identity SET identity_id='new-manager' WHERE agent_id='manager'",
    "UPDATE agents SET accepts='[]' WHERE id='manager'",
    "UPDATE agents SET accepts='[]' WHERE id='executive'",
])
def test_changed_scope_corrupt_receipts_or_route_never_reuse_snapshot(tmp_path, mutation):
    store, identifier, _ = accepted(tmp_path)
    old = collect(store)
    damage(store, mutation)
    fresh = collect(store)
    assert fresh['dependencyContextDigest'] != old['dependencyContextDigest']
    pins, reason = validate(store, directive(identifier), proposal(identifier), old)
    assert pins is None and reason


def test_owner_rotation_has_explicit_reason(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    old = collect(store)
    damage(store, "UPDATE agent_identity SET identity_id='replacement-owner' WHERE agent_id='owner'")
    pins, reason = validate(store, directive(identifier), proposal(identifier), old)
    assert pins is None and 'owner identity changed' in reason


def test_archived_and_legacy_objectives_are_not_candidates(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    old = collect(store)
    store.set_objective_archived(identifier, True, expected_revision=0, idempotency_key='archive')
    assert collect(store)['dependencyCandidates'] == []
    assert validate(store, directive(identifier), proposal(identifier), old)[0] is None
    legacy_dir = tmp_path / 'legacy'
    legacy_dir.mkdir()
    legacy, _, _ = accepted(legacy_dir, legacy=True)
    assert collect(legacy)['dependencyCandidates'] == []


def test_corruption_invalidates_entire_context_with_explicit_limitation(tmp_path):
    store, first, _ = accepted(tmp_path)
    second = complete(store, 'second')
    old = collect(store)
    damage(store, 'UPDATE objective_deliverables SET content=? WHERE objective_id=?', ('Corrupted', second))
    context = collect(store)
    assert context['dependencyContextStatus'] == 'unavailable'
    assert context['dependencyCandidates'] == []
    assert any('storage or authority could not be verified' in item for item in context['dependencyContextLimitations'])
    assert context['dependencyContextDigest'] != old['dependencyContextDigest']
    assert validate(store, directive(first), proposal(first), old)[0] is None
    assert validate(store, directive(first), proposal(first), context)[0] is None


@pytest.mark.parametrize('changes', [{'routeId': 'absent'}, {'managerId': 'director'},
    {'executiveId': 'owner'}, {'projectIds': ['unknown']}])
def test_proposed_route_and_projects_require_current_authority(tmp_path, changes):
    store, identifier, _ = accepted(tmp_path)
    pins, reason = validate(store, directive(identifier), proposal(identifier, **changes), collect(store))
    assert pins is None and reason


def test_project_dependencies_require_identical_exact_selected_bindings(tmp_path):
    store, identifier = project_store(tmp_path)
    context = collect(store)
    assert context['dependencyCandidates'][0]['projectIds'] == ['alpha']
    pins, reason = validate(store, directive(identifier), proposal(identifier, projectIds=['alpha']), context)
    assert reason is None and pins[0]['receipt']['projects'][0]['id'] == 'alpha'
    for selection in ([], ['beta'], ['alpha', 'beta']):
        pins, reason = validate(store, directive(identifier), proposal(identifier, projectIds=selection), context)
        assert pins is None and 'identical selected project bindings' in reason
    public = json.dumps(context)
    assert all(root not in public for root in store.settings.read_roots)
    changed = OrganizationStore(store.path, replace(store.settings, read_roots=tuple(reversed(store.settings.read_roots))))
    assert collect(changed)['dependencyContextStatus'] == 'unavailable'
    assert validate(changed, directive(identifier), proposal(identifier, projectIds=['alpha']), context)[0] is None


def test_revoked_project_and_stale_store_never_supply_candidates(tmp_path):
    store, identifier = project_store(tmp_path)
    context = collect(store)
    revoked = OrganizationStore(store.path, replace(store.settings, revoked_root_aliases=('root0',)))
    assert collect(revoked)['dependencyCandidates'] == []
    assert collect(store)['dependencyContextStatus'] == 'unavailable'
    assert validate(revoked, directive(identifier), proposal(identifier, projectIds=['alpha']), context)[0] is None


def test_snapshot_list_and_receipt_hash_cannot_be_substituted(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    original = collect(store)
    for field in ('dependencyContextDigest', 'ownerIdentityId'):
        changed = deepcopy(original)
        changed[field] = 'changed'
        assert validate(store, directive(identifier), proposal(identifier), changed)[0] is None
    changed = deepcopy(original)
    changed['dependencyCandidates'][0]['receiptSha256'] = '0' * 64
    assert validate(store, directive(identifier), proposal(identifier), changed)[0] is None
    changed = deepcopy(original)
    changed['dependencyCandidates'] = []
    assert validate(store, directive(identifier), proposal(identifier), changed)[0] is None


def test_bounded_candidates_are_deterministic_and_omissions_stay_review_only(tmp_path, monkeypatch):
    store, first, _ = accepted(tmp_path)
    identifiers = [first, complete(store, 'second'), complete(store, 'third')]
    monkeypatch.setattr(dependencies, 'MAX_CANDIDATES', 2)
    context = collect(store)
    assert context['dependencyContextTruncated']
    assert [item['objectiveId'] for item in context['dependencyCandidates']] == sorted(identifiers)[:2]
    assert context == collect(store)
    assert any('omits objectives or metadata' in item for item in context['dependencyContextLimitations'])
    omitted = sorted(identifiers)[2]
    assert validate(store, directive(omitted), proposal(omitted), context)[0] is None
    first = sorted(identifiers)[0]
    assert validate(store, directive(first), proposal(first), context)[1] is None
    monkeypatch.setattr(dependencies, 'MAX_PUBLIC_BYTES', 1)
    bounded = collect(store)
    assert bounded['dependencyContextTruncated'] and bounded['dependencyCandidates'] == []


def test_public_metadata_is_redacted_and_text_truncation_is_explicit(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    identifier = complete(store, 'private', title='Long recommendation ' * 20,
        description='Private /Users/owner/Documents/secret.txt and C:\\Private\\secret.txt. ' + 'Evidence ' * 100)
    context = collect(store)
    candidate = context['dependencyCandidates'][0]
    assert candidate['objectiveId'] == identifier
    assert candidate['metadataRedacted'] and candidate['metadataTruncated']
    assert context['dependencyContextTruncated']
    assert len(candidate['title']) <= dependencies.MAX_TITLE_CHARS
    assert len(candidate['description']) <= dependencies.MAX_DESCRIPTION_CHARS
    assert '/Users/owner' not in json.dumps(context) and 'C:\\\\Private' not in json.dumps(context)
    assert len(json.dumps(context['dependencyCandidates']).encode()) < dependencies.MAX_PUBLIC_BYTES
    assert validate(store, directive(identifier), proposal(identifier), context)[1] is None


def test_different_current_manager_route_is_still_not_cross_route_authority(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    other = OrganizationStaff('other-manager', 'Other manager', 'general',
        ('request.plan', 'request.integrate', 'request.hire'), role='Manager', manager_id='executive')
    store = OrganizationStore(store.path, replace(store.settings, roster=(other,)))
    context = collect(store)
    assert context['dependencyContextStatus'] == 'available'
    pins, reason = validate(store, directive(identifier),
        proposal(identifier, routeId='other-manager', managerId='other-manager'), context)
    assert pins is None and 'same current manager and executive' in reason


def test_newly_accepted_round_needs_fresh_context_and_receipt_pin(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    old = collect(store)
    old_pins, reason = validate(store, directive(identifier), proposal(identifier), old)
    assert reason is None
    with store._write() as conn:
        store._replan(conn, identifier, 'Reassess the supported recommendation.')
    _plan(store)
    _work_and_review(store)
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    current = collect(store)
    assert current['dependencyContextDigest'] != old['dependencyContextDigest']
    assert validate(store, directive(identifier), proposal(identifier), old)[0] is None
    pins, reason = validate(store, directive(identifier), proposal(identifier), current)
    assert reason is None and pins[0]['receiptSha256'] != old_pins[0]['receiptSha256']


@pytest.mark.parametrize('agent', ['manager', 'executive'])
def test_disabled_leader_is_not_a_current_dependency_route(tmp_path, agent):
    store, identifier, _ = accepted(tmp_path)
    before = collect(store)
    with store._write() as conn:
        # Fixture-only status corruption; no agent activation or live grant.
        row = conn.execute('SELECT * FROM staff_state WHERE agent_id=?', (agent,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO staff_state(agent_id,active,source,policy) VALUES (?,0,'fixture','{}')", (agent,))
        else:
            conn.execute('UPDATE staff_state SET active=0 WHERE agent_id=?', (agent,))
    assert collect(store)['dependencyCandidates'] == []
    assert validate(store, directive(identifier), proposal(identifier), before)[0] is None


@pytest.mark.parametrize('template', [
    'Do not use objective {id}.',
    'Never use objective {id} to prepare a report.',
    'Is {id} relevant?',
    'Should we use objective {id} to prepare a report?',
    'Use objective {id} to prepare a report?',
    'Use objective {id} to prepare a report unless I change my mind.',
    'If useful, use objective {id} to prepare a report.',
    'Use objective {id} to explain why we should not use it.',
    'Use objective {id} to prepare a report. Ignore this instruction.',
    '"Use objective {id} to prepare a report."',
    "'Use objective {id} to prepare a report.'",
    '“Use objective {id} to prepare a report.”',
    '> Use objective {id} to prepare a report.',
    '```\nUse objective {id} to prepare a report.\n```',
    '    Use objective {id} to prepare a report.',
    'Example:\nPrerequisite: {id}',
    'Someone told me:\nUse objective {id} to prepare a report.',
    'I am asking whether the following is appropriate:\nPrerequisite: {id}',
    'Not a directive:\nPrerequisite: {id}',
    'Write a report mentioning {id}.',
    'Use objective prefix{id} to prepare a report.',
    'Use objective {id}suffix to prepare a report.',
    'Prerequisite: {id}-other',
    'Prerequisite: {id}?',
])
def test_negated_quoted_question_and_ambiguous_id_mentions_require_review(tmp_path, template):
    store, identifier, _ = accepted(tmp_path)
    pins, reason = validate(store, template.format(id=identifier), proposal(identifier), collect(store))
    assert pins is None
    assert 'affirmative dependency directive' in reason or 'unknown, unavailable or omitted' in reason


def test_conflicting_owner_messages_cannot_be_selectively_ignored(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    texts = [f'Do not use objective {identifier}.', directive(identifier)]
    pins, reason = validate(store, directive(identifier), proposal(identifier), collect(store), owner_texts=texts)
    assert pins is None and 'affirmative dependency directive' in reason


def test_multiple_explicit_directive_lines_return_only_selected_pins(tmp_path):
    store, first, _ = accepted(tmp_path)
    second = complete(store, 'second')
    context = collect(store)
    request = directive(first) + '\nPrerequisite: ' + second
    pins, reason = validate(store, request, proposal(first, second), context)
    assert reason is None and [pin['objectiveId'] for pin in pins] == [first, second]


@pytest.mark.parametrize('template', ['Use objective {id} to prepare a follow-on report.', 'Prerequisite: {id}'])
def test_model_cannot_drop_explicit_owner_dependency_with_empty_selection(tmp_path, template):
    store, identifier, _ = accepted(tmp_path)
    context = collect(store)
    pins, reason = validate(store, template.format(id=identifier), proposal(), context)
    assert pins is None and 'omitted an explicit owner prerequisite' in reason
    assert validate(store, 'Prepare an independent report.', proposal(), context) == ([], None)


@pytest.mark.parametrize('selected', [False, True])
def test_unknown_explicit_owner_prerequisite_never_admits_as_independent(tmp_path, selected):
    store, _, _ = accepted(tmp_path)
    unknown = 'obj_' + 'f' * 32
    pins, reason = validate(store, directive(unknown), proposal(*([unknown] if selected else [])), collect(store))
    assert pins is None and 'unknown, unavailable or omitted' in reason


def test_partially_selected_multiple_owner_prerequisites_require_review(tmp_path):
    store, first, _ = accepted(tmp_path)
    second = complete(store, 'second')
    context = collect(store)
    request = directive(first) + '\nPrerequisite: ' + second
    for selection in ((), (first,), (second,)):
        pins, reason = validate(store, request, proposal(*selection), context)
        assert pins is None and 'omitted an explicit owner prerequisite' in reason
    pins, reason = validate(store, request, proposal(first, second), context)
    assert reason is None and {pin['objectiveId'] for pin in pins} == {first, second}


def test_owner_prerequisite_in_surrounding_prose_cannot_be_silently_dropped(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    request = 'Prepare the follow-on report.\nPrerequisite: ' + identifier
    pins, reason = validate(store, request, proposal(), collect(store))
    assert pins is None and 'omitted an explicit owner prerequisite' in reason
    # Surrounding prose is still outside the automatic affirmative language.
    pins, reason = validate(store, request, proposal(identifier), collect(store))
    assert pins is None and 'affirmative dependency directive' in reason


def test_unselected_declared_prerequisite_still_requires_trusted_clarification_provenance(tmp_path):
    store, identifier, _ = accepted(tmp_path)
    request = directive(identifier) + '\n\nClarification question: Which report?\nOwner answer: The annual one.'
    pins, reason = validate(store, request, proposal(), collect(store))
    assert pins is None and 'independently retained owner messages' in reason
    pins, reason = validate(store, request, proposal(), collect(store),
        owner_texts=[directive(identifier), 'The annual one.'])
    assert pins is None and 'omitted an explicit owner prerequisite' in reason


def test_ordinary_clarification_without_prerequisite_keeps_empty_selection_fast_path(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    request = 'Prepare a report.\n\nClarification question: Which report?\nOwner answer: The annual one.'
    assert validate(store, request, proposal(), {}) == ([], None)


@pytest.mark.parametrize('template', ['Prerequisite: {id}.', 'Use objective {id} to prepare a report?',
    'Use objective {id} to prepare a report unless I change my mind.'])
def test_uncertain_directive_cannot_hide_prerequisite_by_empty_selection(tmp_path, template):
    store, identifier, _ = accepted(tmp_path)
    pins, reason = validate(store, template.format(id=identifier), proposal(), collect(store))
    assert pins is None and 'omitted an explicit owner prerequisite' in reason

"""Only the active finish's classified original source can skip reclassification."""
from copy import copy, deepcopy
from dataclasses import replace

import pytest

from eidolon_cli import organization_prepared_validation_current as current
from eidolon_cli import organization_prepared_validation_finish as finish
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import (
    running, confirmed, folder_profile, owner, prepared,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_finish import ready

pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize('kind', ['copied', 'deepcopied', 'replaced', 'wire'])
def test_unissued_source_observation_is_rejected_before_read(running, monkeypatch, kind):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        original = token.targets['notes']['observation']
        forged = {'copied': lambda: copy(original), 'deepcopied': lambda: deepcopy(original),
                  'replaced': lambda: replace(original), 'wire': lambda: {}}[kind]
        try:
            value = forged()
        except TypeError:  # The dependency capability also refuses deepcopy.
            assert kind == 'deepcopied'
            return
        monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Unissued source was read'))
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                with pytest.raises(ValueError, match='observation'):
                    current.recheck_finish_external_observation(running.store, value)


@pytest.mark.parametrize('kind', ['paths', 'limits', 'exceptions', 'root', 'original', 'effective', 'classifier'])
def test_issued_source_policy_or_original_substitution_rejects_before_read(running, monkeypatch, kind):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        target = token.targets['notes']
        observation = target['observation']
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                if kind == 'classifier':
                    object.__setattr__(observation, 'classifier_policy', tuple(list(observation.classifier_policy)))
                elif kind in {'original', 'effective'}:
                    substituted = target['snapshot'] if kind == 'original' else observation.fresh.effective
                    assert substituted is not observation.fresh.original
                    object.__setattr__(observation, 'fresh', replace(observation.fresh, original=substituted))
                elif kind == 'root':
                    observation.authority['contract']['root']['inode'] += 1
                elif kind == 'paths':
                    observation.authority['contract']['source']['files'] = ['different.ts']
                elif kind == 'limits':
                    observation.authority['contract']['source']['limits']['max_files'] += 1
                else:
                    observation.authority['contract']['source']['executionOnlyHashes']['extra.ts'] = '0' * 64
                monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Substituted source was read'))
                with pytest.raises(ValueError, match='source or policy was replaced'):
                    current.recheck_finish_external_observation(running.store, observation)
                with pytest.raises(ValueError, match='consumed'):
                    current.recheck_finish_external_observation(running.store, observation)


def test_source_capability_expires_and_successful_recheck_is_single_use(running, monkeypatch):
    ready(running, monkeypatch)
    before = len(current._SOURCE_OBSERVATIONS)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        observation = token.targets['notes']['observation']
        assert observation in current._SOURCE_OBSERVATIONS
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                recheck = current._recheck_project_snapshot
                def exact_original(source, expected, **kwargs):
                    assert expected is observation.fresh.original
                    assert expected is not token.targets['notes']['snapshot']
                    assert expected is not observation.fresh.effective
                    return recheck(source, expected, **kwargs)
                monkeypatch.setattr(current, '_recheck_project_snapshot', exact_original)
                assert current.recheck_finish_external_observation(running.store, observation) is True
                monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Consumed source was read'))
                with pytest.raises(ValueError, match='consumed'):
                    current.recheck_finish_external_observation(running.store, observation)
    assert len(current._SOURCE_OBSERVATIONS) == before
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        expired = token.targets['notes']['observation']
    assert expired not in current._SOURCE_OBSERVATIONS
    with pytest.raises(ValueError, match='expired'):
        current.recheck_finish_external_observation(running.store, expired)


@pytest.mark.parametrize('kind', ['store', 'thread', 'inactive', 'target'])
def test_source_capability_requires_its_store_thread_active_scope_and_target(running, monkeypatch, kind):
    from contextvars import copy_context
    import threading
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        observation = token.targets['notes']['observation']
        monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Foreign source was read'))
        if kind == 'inactive':
            with pytest.raises(ValueError, match='foreign'):
                current.recheck_finish_external_observation(running.store, observation)
        else:
            with running.store._write() as conn:
                request = running.store._owned(conn, running.claim)
                with finish.finish_verification_scope(token, running.store, conn, request):
                    if kind == 'thread':
                        errors = []
                        def foreign():
                            try:
                                current.recheck_finish_external_observation(running.store, observation)
                            except ValueError as error:
                                errors.append(str(error))
                        context = copy_context()
                        worker = threading.Thread(target=lambda: context.run(foreign))
                        worker.start()
                        worker.join(5)
                        assert not worker.is_alive() and len(errors) == 1 and 'foreign' in errors[0]
                    else:
                        if kind == 'target':
                            token.targets['notes']['observation'] = replace(observation)
                        with pytest.raises(ValueError, match='foreign|replaced'):
                            current.recheck_finish_external_observation(object() if kind == 'store' else running.store, observation)
        assert observation not in current._SOURCE_OBSERVATIONS


def test_exception_context_invalidates_unconsumed_source_observation(running, monkeypatch):
    ready(running, monkeypatch)
    before = len(current._SOURCE_OBSERVATIONS)
    with pytest.raises(RuntimeError, match='interrupted'):
        with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
            observation = token.targets['notes']['observation']
            assert observation in current._SOURCE_OBSERVATIONS
            raise RuntimeError('interrupted')
    assert len(current._SOURCE_OBSERVATIONS) == before and observation not in current._SOURCE_OBSERVATIONS
    monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Expired source was read'))
    with pytest.raises(ValueError, match='expired'):
        current.recheck_finish_external_observation(running.store, observation)


@pytest.fixture
def register_classifier(monkeypatch):
    from agent import redact
    # Keep any pre-existing plugin policy intact when this isolated test ends.
    monkeypatch.setattr(redact, '_PLUGIN_PREFIX_PATTERNS', dict(redact._PLUGIN_PREFIX_PATTERNS))
    monkeypatch.setattr(redact, '_PREFIX_RE', redact._PREFIX_RE)
    monkeypatch.setattr(redact, '_PREFIX_SUBSTRINGS', redact._PREFIX_SUBSTRINGS)
    def register():
        assert redact.register_redaction_patterns(
            [r'observation_test_[A-Za-z0-9]{24}'], source='source-observation-test') == 1
    return register


@pytest.mark.parametrize('phase', ['capture', 'issuance'])
def test_changed_classifier_prevents_observation_issuance(running, monkeypatch, register_classifier, phase):
    ready(running, monkeypatch)
    before = len(current._SOURCE_OBSERVATIONS)
    name = 'capture_effective_project_snapshot' if phase == 'capture' else '_authority_value'
    original = getattr(current, name)
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        register_classifier()
        return result
    monkeypatch.setattr(current, name, changed)
    with pytest.raises(ValueError, match='classifier policy changed'):
        with finish.prepare_finish_verification(running.store, running.claim, {}):
            pytest.fail('Changed classifier issued a source observation')
    assert len(current._SOURCE_OBSERVATIONS) == before


@pytest.mark.parametrize('phase', ['before_read', 'checkpoint', 'during_match'])
def test_classifier_changes_reject_and_consume_final_observation(running, monkeypatch, register_classifier, phase):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        observation = token.targets['notes']['observation']
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                if phase == 'before_read':
                    register_classifier()
                elif phase == 'checkpoint':
                    checkpoint = current._checkpoint
                    def changed_checkpoint(*args, **kwargs):
                        check = checkpoint(*args, **kwargs)
                        def changed(**options):
                            result = check(**options)
                            register_classifier()
                            return result
                        return changed
                    monkeypatch.setattr(current, '_checkpoint', changed_checkpoint)
                else:
                    recheck = current._recheck_project_snapshot
                    def changed_match(*args, **kwargs):
                        result = recheck(*args, **kwargs)
                        register_classifier()
                        return result
                    monkeypatch.setattr(current, '_recheck_project_snapshot', changed_match)
                if phase != 'during_match':
                    monkeypatch.setattr(current, '_recheck_project_snapshot', lambda *a, **k: pytest.fail('Changed classifier reached source read'))
                with pytest.raises(ValueError, match='classifier policy changed'):
                    current.recheck_finish_external_observation(running.store, observation)
                with pytest.raises(ValueError, match='consumed'):
                    current.recheck_finish_external_observation(running.store, observation)


@pytest.mark.parametrize('targets', [1, 2])
def test_classifier_raw_policy_is_counted_once_at_exact_capacity(monkeypatch, targets):
    from types import SimpleNamespace
    policy = current._source_classifier_policy()
    observation = SimpleNamespace(classifier_policy=policy, envelope='', retained_manifest='',
        authority_identity='', value='', source_binding='',
        dependency=SimpleNamespace(metadata=lambda: {}),
        fresh=SimpleNamespace(original=SimpleNamespace(files=[]), effective=SimpleNamespace(files=[])))
    target = {'observation': observation, 'envelope': '', 'proof': '', 'snapshot': SimpleNamespace(files=[])}
    token = SimpleNamespace(inventory=SimpleNamespace(raw_bytes=0, rows=[]), static_records={},
        request='', operation='', agents='', artifacts={}, claim={}, decision=None,
        targets={str(index): target for index in range(targets)})
    raw = {id(value): value for value in (policy[0].pattern, *policy[1])}
    expected = sum(len(value.encode('utf-8')) for value in raw.values()) + 2 * targets
    monkeypatch.setattr(finish, '_MAX_RETAINED_RAW_BYTES', expected)
    finish._check_retained_capacity(token)
    assert token.retained_raw_bytes == expected
    monkeypatch.setattr(finish, '_MAX_RETAINED_RAW_BYTES', expected - 1)
    with pytest.raises(ValueError, match='retained raw-byte capacity'):
        finish._check_retained_capacity(token)

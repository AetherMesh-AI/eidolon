"""Synthetic diagnostic evidence; no provider or installed profile access."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.ci import organization_timing as timing
from eidolon_cli.organization_ledger_writer import LedgerWriterUnavailable
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from tests.eidolon_cli import test_organization_owner_policy as policy_tests


def test_suppressed_chain_retained_without_messages_locals_or_parent_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, '_output_directory', tmp_path)
    private_value = 'PRIVATE-TOKEN-PAYLOAD'
    try:
        try:
            raise LedgerWriterUnavailable('deadline')
        except LedgerWriterUnavailable:
            raise OwnerPolicyError(private_value) from None
    except OwnerPolicyError as exc:
        timing.record_policy_exception(exc, 'worker')
    text = next(tmp_path.glob('organization-timing-*.jsonl')).read_text()
    event = json.loads(text)
    assert [e['type'] for e in event['chain']] == ['OwnerPolicyError', 'LedgerWriterUnavailable']
    assert [e['reason'] for e in event['chain']] == [None, 'deadline']
    assert all(e['frames'] for e in event['chain'])
    assert private_value not in text and str(tmp_path) not in text
    assert 'private_value' not in text and 'raise OwnerPolicyError' not in text


def test_disabled_capture_and_failed_write_do_not_replace_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, '_output_directory', None)
    before = set(tmp_path.iterdir())
    timing.record_policy_exception(OwnerPolicyError('locked'), 'worker')
    assert set(tmp_path.iterdir()) == before
    monkeypatch.setattr(timing, '_output_directory', tmp_path)
    def unavailable(*args, **kwargs):
        raise OSError('synthetic unavailable evidence sink')
    monkeypatch.setattr(timing, '_record', unavailable)
    timing.record_policy_exception(OwnerPolicyError('locked'), 'worker')


def test_worker_exception_recorded_before_executor_waits_for_peer(tmp_path, monkeypatch):
    profile = policy_tests.create_profile(tmp_path / 'synthetic-profile')
    monkeypatch.setattr(timing, '_output_directory', tmp_path)
    captured, release_peer = threading.Event(), threading.Event()
    original_record = timing._record
    error = OwnerPolicyError('locked')
    lock, entered = threading.Lock(), []
    def apply(*args, **kwargs):
        with lock:
            entered.append(True)
            first = len(entered) == 1
        if first:
            raise error
        assert release_peer.wait(10)
        raise error
    def record(value, **kwargs):
        original_record(value, **kwargs)
        captured.set()
    monkeypatch.setattr(policy_tests.owner_policy, 'apply_policy', apply)
    monkeypatch.setattr(timing, '_record', record)
    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(policy_tests.test_same_key_concurrent_apply_returns_one_receipt_and_one_generation, profile)
        try:
            assert captured.wait(10)
            assert not result.done()
            assert 'concurrent-policy-exception' in next(tmp_path.glob('organization-timing-*.jsonl')).read_text()
        finally:
            release_peer.set()
        with pytest.raises(OwnerPolicyError) as caught:
            result.result(timeout=15)
        assert caught.value is error
    assert len(entered) == 2

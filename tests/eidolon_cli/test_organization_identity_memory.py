"""Atomic organization memory validation, stale claims and competing writers."""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_identity_helpers import (
    _memory,
    _work_claim,
)


@pytest.mark.parametrize('invalidation', ['cancel', 'expire', 'changed-policy'])
def test_stale_claim_cannot_mutate_memory_or_history(tmp_path, invalidation):
    store = OrganizationStore(tmp_path / 'state.db')
    objective, claim = _work_claim(store, 'stale')
    before = _memory(store)
    if invalidation == 'cancel':
        store.cancel(objective['id'])
    elif invalidation == 'expire':
        with store._write() as conn:
            conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
    else:
        OrganizationStore(store.path, replace(store.settings, capabilities=('work.analyze',)))
    assert store.finish(claim, {'summary': 'Stale', 'deliverable': 'Must not persist',
                                'memory': {'facts': ['Poisoned stale context']}}) is False
    assert _memory(store) == before
    with store._connect() as conn:
        assert conn.execute('SELECT 1 FROM evidence WHERE request_id=?', (claim['id'],)).fetchone() is None


@pytest.mark.parametrize('memory', [None, [], {'transcript': ['raw messages']}, {'facts': 'not a list'},
                                   {'facts': ['']}, {'facts': ['x' * 1001]}, {'facts': ['item'] * 25}])
def test_invalid_memory_rolls_back_work_evidence_usage_and_review_queue(tmp_path, memory):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'invalid-memory')
    before = _memory(store)
    with pytest.raises(ValueError, match='Agent memory'):
        store.finish(claim, {'summary': 'Otherwise valid', 'deliverable': 'Complete output', 'memory': memory})
    assert _memory(store) == before
    with store._connect() as conn:
        assert conn.execute('SELECT 1 FROM evidence WHERE request_id=?', (claim['id'],)).fetchone() is None
        assert conn.execute('SELECT status FROM requests WHERE id=?', (claim['id'],)).fetchone()[0] == 'running'
        assert conn.execute('SELECT completed FROM objective_usage WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0
        assert conn.execute("SELECT 1 FROM requests WHERE task_id=? AND type='request.review'", (claim['task_id'],)).fetchone() is None


def test_invalid_result_and_intervention_do_not_save_proposed_memory(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'bad-result')
    with pytest.raises(ValueError, match='Deliverable'):
        store.finish(claim, {'summary': 'Incomplete', 'memory': {'facts': ['Cannot retain']}})
    assert _memory(store)['revision'] == 0
    assert store.finish(claim, {'intervention': 'Need more source data.', 'memory': {'facts': ['Cannot retain']}})
    assert _memory(store)['revision'] == 0
    assert _memory(store)['recentHistory'] == []


def test_competing_finishes_commit_one_history_and_memory_revision(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'concurrent')
    reopened = [OrganizationStore(store.path) for _ in range(4)]
    def finish(index):
        return reopened[index].finish(claim, {'summary': f'Output {index}', 'deliverable': 'Full retained text',
                                              'memory': {'facts': [f'Completed result {index}']}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(finish, range(4)))
    assert outcomes.count(True) == 1
    context = _memory(store)
    assert context['revision'] == 1 and len(context['recentHistory']) == 1
    assert len(context['memory']['facts']) == 1
    assert context['memory']['facts'] == [f'Completed result {outcomes.index(True)}']

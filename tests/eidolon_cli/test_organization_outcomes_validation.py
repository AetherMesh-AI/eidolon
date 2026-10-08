"""Organization outcomes reject invalid inputs and cross-profile references."""
import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_outcomes_helpers import (
    cancelled,
    item,
)
from tests.organization_outcomes_helpers import (
    store as store,
)


@pytest.mark.parametrize('revision', [None, True, False, 0, -1, 1.0, '1', 2**53, [], {}])
def test_invalid_revisions_are_rejected(store, revision):
    objective = cancelled(store)
    with pytest.raises(ValueError, match='revision'):
        store.mark_outcome_seen(objective['id'], revision)
    assert store.outcomes()['unread'] == 1


@pytest.mark.parametrize('params', [{'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': '1'},
                                  {'unread_only': 1}, {'unread_only': None}, {'before': ''}, {'before': []}])
def test_invalid_pages_are_rejected(store, params):
    with pytest.raises(ValueError):
        store.outcomes(**params)


def test_profiles_and_unknown_ids_fail_closed(store, tmp_path):
    objective = cancelled(store)
    other = OrganizationStore(tmp_path / 'other-profile' / 'state.db')
    assert other.outcomes()['total'] == 0
    for objective_id in [objective['id'], 'missing', '', [], None]:
        with pytest.raises(ValueError):
            other.mark_outcome_seen(objective_id, item(store)['revision'])
    with pytest.raises(ValueError, match='this profile'):
        other.outcomes(before=objective['id'])
    assert store.outcomes()['unread'] == 1

"""Intake inference retains ordinary, single-send shared allowance evidence."""
from contextlib import closing
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_pacing import OrganizationPacingDeferred
from eidolon_cli.organization_shared_allowance import (
    SharedAllowanceError, _payload, debit, physical_call_key, resolve_binding,
    verify_receipt,
)
from eidolon_cli.organization_shared_budget import _read_scope_pin, reserve_shared_call
from tests.eidolon_cli.test_organization_shared_allowance import (
    configure, installation, iso, reserve,
)


def test_intake_descriptors_conserve_scoped_ordinary_debits_without_replay(installation, monkeypatch):
    root = installation
    now = [int(time.time())]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    configure(root)
    root.ledger.observe({'remainingPercent': 5, 'asOf': iso(now[0]),
        'expiresAt': iso(now[0] + 600), 'resetAt': None}, expected_revision=1,
        idempotency_key='intake-pacing', root_organization_id=root.organization_id,
        root_owner_id=root.owner_id)
    data = {'kind': 'request_intake', 'intakeId': 'shared-identity',
        'provider': 'local-fixture', 'model': 'bounded', 'inputLimit': 50,
        'outputLimit': 50, 'reservedCostUsd': None}
    assert _payload(data) == data
    invalid = [
        {key: value for key, value in data.items() if key != 'intakeId'},
        {**data, 'generationId': data['intakeId']},
        {**data, 'requestType': 'request.review'},
        *({**data, 'intakeId': value} for value in ('', ' padded ', 'a' * 301, 1)),
        {**data, 'inputLimit': True}, {**data, 'outputLimit': 0},
    ]
    binding = resolve_binding(root.home, root.organization_id)
    for value in invalid:
        with pytest.raises(ValueError):
            physical_call_key(binding, value)
    key = physical_call_key(binding, data)
    assert key != physical_call_key(binding, {**data, 'intakeId': 'another-intake'})
    for change in ({'home': str(root.alpha.home)}, {'organizationId': root.alpha.organization_id}):
        assert key != physical_call_key({**binding, **change}, data)
    setup = {**{field: value for field, value in data.items() if field != 'intakeId'},
             'kind': 'team_setup', 'generationId': data['intakeId']}
    assert key != physical_call_key(binding, setup)

    # An intake's local rollback cannot refund its already committed shared debit.
    with pytest.raises(RuntimeError, match='local rollback'):
        with root.store._write() as conn:
            reserved_key, receipt, _pin = reserve_shared_call(root.store, conn, data)
            assert reserved_key == key
            raise RuntimeError('local rollback')
    assert receipt['payload'] == data
    assert receipt['classification'] == 'ordinary'
    assert receipt['reserved'] == {'modelCalls': 1, 'tokens': 100, 'configuredCostUsd': None}
    assert verify_receipt(binding, receipt, key, data) == receipt
    assert root.ledger.read()['member']['reserved'] == receipt['reserved']
    for retry in (data, {**data, 'model': 'changed'}, {**data, 'inputLimit': 51}):
        assert physical_call_key(binding, retry) == key
        with pytest.raises(SharedAllowanceError, match='already_debited_no_replay'):
            debit(binding, key, retry)
    with pytest.raises(SharedAllowanceError, match='matching_immutable_receipt_required'):
        verify_receipt(binding, receipt, key, {**data, 'model': 'changed'})
    # A matching identifier in another kind/profile is not an admitted intake stage.
    for scope, candidate in ((root, setup), (root.alpha, data),
                             (root, {**data, 'intakeId': 'next-intake'})):
        with pytest.raises(OrganizationPacingDeferred):
            reserve(scope, candidate)
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 1
    now[0] += 301
    reserve(root, {**data, 'intakeId': 'next-intake'})
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 2


@pytest.mark.parametrize('retained_receipt', ['{}', 'null', ''])
def test_retained_intake_receipts_require_readable_scope_provenance(retained_receipt):
    with closing(sqlite3.connect(':memory:')) as conn:
        assert _read_scope_pin(conn) is None
        conn.execute('CREATE TABLE organization_request_intakes (shared_receipt TEXT)')
        conn.execute('INSERT INTO organization_request_intakes VALUES (NULL)')
        assert _read_scope_pin(conn) is None
        conn.execute('INSERT INTO organization_request_intakes VALUES (?)', (retained_receipt,))
        with pytest.raises(ValueError, match='scope provenance is missing'):
            _read_scope_pin(conn)
        conn.execute('CREATE TABLE organization_shared_scope_pin (singleton INTEGER, binding_json TEXT)')
        pin = {'root': '/installation', 'home': '/installation', 'ledgerId': 'ledger',
               'organizationId': 'organization', 'ownerId': 'owner'}
        conn.execute('INSERT INTO organization_shared_scope_pin VALUES (1,?)', (json.dumps(pin),))
        assert _read_scope_pin(conn) == pin
        conn.execute("UPDATE organization_shared_scope_pin SET binding_json='null'")
        with pytest.raises(ValueError, match='scope provenance is unreadable'):
            _read_scope_pin(conn)


@pytest.mark.parametrize('state', ['pending-before-send', 'pending-after-send', 'uncertain', 'failed'])
def test_shared_configuration_fences_pending_and_uncertain_intakes(installation, state):
    root = installation
    config = configure(root)
    intake, created = root.alpha.store.begin_intake('Review the backlog', idempotency_key='intake-fence')
    assert created
    if state != 'pending-before-send':
        root.alpha.store.reserve_intake_call(intake['id'], provider='local-fixture',
            model='bounded', input_limit=50, output_limit=50)
    if state in {'uncertain', 'failed'}:
        root.alpha.store.fail_intake(intake['id'], 'Interrupted inference', known_response=state == 'failed')
    retained = root.alpha.store.intake_read(intake['id'])
    before = root.ledger.read()

    if state == 'failed':
        configure(root, config, revision=1, key='intake-reconfigure')
    else:
        with pytest.raises(SharedAllowanceError, match='scope_execution_active'):
            configure(root, config, revision=1, key='intake-reconfigure')
        after = root.ledger.read()
        assert after['revision'] == before['revision']
        assert after['observation'] == before['observation']
    assert root.alpha.store.intake_read(intake['id']) == retained
    assert root.ledger.read()['pool']['reserved'] == before['pool']['reserved']

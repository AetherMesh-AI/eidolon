"""Native project registration recovers atomically from crashes and blocked writers."""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_project_setup import project_save, project_setup
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_project_registry_helpers import (
    _EXECUTION_PROCESS,
    _SAVE_PROCESS,
    PROJECT,
    dump,
    ledger,
    process_payload,
    run_save,
    wait_for_file,
)
from tests.organization_project_registry_helpers import (
    profile as profile,
)


@pytest.mark.parametrize('boundary', ['adopted_policy', 'inserted_receipt'])
def test_process_death_before_commit_rolls_back_identity_policy_and_receipt(profile, boundary):
    services.get_service()
    revision = project_setup()['revision']
    before = dump(profile)
    source = (profile / 'config.yaml').read_bytes()
    crashed = run_save(revision, mode=boundary)
    assert crashed.returncode == 73, (crashed.stdout, crashed.stderr)
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source
    assert OrganizationStore(ledger(profile)).settings.projects == ()
    assert project_setup()['revision'] == revision
    retry = run_save(revision)
    assert retry.returncode == 0, retry.stderr
    result = json.loads(retry.stdout)
    assert result['saved'] is True and result['project'] == PROJECT
    assert project_setup()['projects'] == [PROJECT]


def test_process_death_after_commit_retries_exact_receipt_without_another_registration(profile):
    services.get_service()
    revision = project_setup()['revision']
    source = (profile / 'config.yaml').read_bytes()
    crashed = run_save(revision, mode='committed')
    assert crashed.returncode == 74, (crashed.stdout, crashed.stderr)
    before = dump(profile)
    with sqlite3.connect(ledger(profile)) as conn:
        receipt = json.loads(conn.execute('SELECT result FROM organization_project_registration_receipts').fetchone()[0])
    retry = run_save(revision)
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout) == receipt
    assert dump(profile) == before
    assert (profile / 'config.yaml').read_bytes() == source
    assert project_setup()['projects'] == [PROJECT]


@pytest.mark.parametrize('change', ['yaml', 'policy', 'open_objective'])
def test_blocked_native_writer_validates_fresh_source_and_ledger_after_lock(profile, change):
    service = services.get_service()
    revision = project_setup()['revision']
    ready, waiting = profile / 'writer-ready', profile / 'writer-waiting'
    payload = process_payload(revision, mode='blocked_writer', ready=str(ready), waiting=str(waiting))
    process = subprocess.Popen([sys.executable, '-u', '-c', _SAVE_PROCESS, payload],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ.copy())
    try:
        wait_for_file(ready, process)
        with service.store._write() as conn:
            process.stdin.write('save\n')
            process.stdin.flush()
            wait_for_file(waiting, process)
            if change == 'yaml':
                with (profile / 'config.yaml').open('a') as stream:
                    stream.write('# Independent owner edit while the save waited for SQLite.\n')
            elif change == 'policy':
                # Another ledger host commits an authority generation while the
                # already initialized writer waits. It must not use a cached one.
                conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
            else:
                now = time.time()
                conn.execute('INSERT INTO objectives(id,idempotency_key,input_hash,title,description,priority,created) '
                             'VALUES (?,?,?,?,?,?,?)',
                             ('blocked-objective', 'concurrent-intake', 'synthetic-hash',
                              'Concurrent intake', 'Legacy open objective without a request', 3, now))
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        assert 'changed' in json.loads(stdout)['error'] or 'open objectives' in json.loads(stdout)['error']
        with sqlite3.connect(ledger(profile)) as conn:
            assert conn.execute('SELECT * FROM organization_project_registry').fetchall() == []
            assert conn.execute('SELECT * FROM organization_project_registration_receipts').fetchall() == []
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=30)


def test_cancelled_native_execution_holds_capacity_until_it_really_exits(profile):
    service = services.get_service()
    objective = service.store.create_objective('Synthetic execution will keep unwinding', idempotency_key='unwinding')
    ready = profile / 'execution-ready'
    process = subprocess.Popen([sys.executable, '-u', '-c', _EXECUTION_PROCESS, str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ.copy())
    try:
        wait_for_file(ready, process)
        assert service.cancel(objective['id'])
        setup = project_setup()
        assert not setup['blocked']
        before = dump(profile)
        with pytest.raises(ValueError, match='still stopping in another runtime'):
            project_save(PROJECT, setup['revision'], 'after-cancel', confirm_save=True)
        assert dump(profile) == before
        process.stdin.write('release\n')
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        result = project_save(PROJECT, setup['revision'], 'after-cancel', confirm_save=True)
        assert result['saved'] and project_setup()['projects'] == [PROJECT]
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=30)

"""Temporary mechanism evidence, outside all product/canonical test trees."""
from contextlib import nullcontext, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

import pytest

from vfs_probe import ClosePause
from eidolon_cli import organization_ledger_writer as writers
from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_team_budget as budget
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_setup_subscription_admission import activated, request, infer, state
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger
from tests.eidolon_cli.test_organization_setup_subscription_recovery import recovery_ledger
from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response

OUT = Path(os.environ['EIDOLON_PROOF_OUTPUT'])

def readonly_observation(store):
    # Exact candidate production connection, no copied SQL sequence.
    return store._connect()


@pytest.mark.parametrize('mode', ['uncoordinated_close', 'coordinated_close', 'coordinated_stale_owner', 'ordinary_open_reader', 'released_close', 'readonly_close', 'readonly_stale_owner'])
def test_production_admission_at_real_wal_close(recovery_ledger, monkeypatch, mode):
    rig = recovery_ledger
    path = rig.home / 'organization' / 'state.db'
    assert rig.conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    before_policy = [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_policy')]
    value = request(rig)
    socket, connects = install(monkeypatch, wire(event(response=response())))
    store = object.__new__(OrganizationStore)
    store.path = path
    original_guard = admission._Admission.try_send_allowed
    original_wait = writers._Gate._wait_for_turn
    read_errors, guards, queued, hits_at_guard = [], [], [], []
    holder_ready, holder_close = threading.Event(), threading.Event()
    failures = []
    reader_lane = threading.get_ident()

    readonly = mode.startswith('readonly_')
    readonly_evidence = {}
    with ClosePause(pause_readonly_refusal=readonly) as pause:
        def holder():
            try:
                with (writers.ledger_writer(path, deadline=time.monotonic()+5)
                      if mode.startswith('coordinated_') else nullcontext()):
                    # Exact production candidate reader, or explicit writable positive control; no row write.
                    with (readonly_observation(store) if readonly else store._connect(write=True)) as conn:
                        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
                        assert conn.execute('SELECT count(*) FROM organization_policy').fetchone()[0] == 1
                        if readonly:
                            assert conn.execute('PRAGMA query_only').fetchone()[0] == 0
                            with pytest.raises(sqlite3.OperationalError) as refused:
                                conn.execute('DELETE FROM organization_policy WHERE 0')
                            assert refused.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
                            readonly_evidence.update(queryOnlyFalse=True, dmlError='SQLITE_READONLY')
                        holder_ready.set()
                        assert holder_close.wait(5)
                        pause.arm_close(path)
                    pause.disarm_close()
            except BaseException as error:
                failures.append(type(error).__name__)
        def waiting(gate, ticket, deadline, cancelled):
            if mode.startswith('coordinated_') and threading.get_ident() == reader_lane and gate.owner is not None:
                queued.append(True)
                assert deadline <= time.monotonic() + writers.PUBLICATION_WAIT_SECONDS
                if mode == 'coordinated_stale_owner':
                    rig.transport.closed = True
                pause.release.set()
            return original_wait(gate, ticket, deadline, cancelled)
        def trace(frame, kind, arg):
            if kind == 'exception' and frame.f_code is original_guard.__code__:
                error = arg[1]
                if isinstance(error, sqlite3.Error):
                    read_errors.append({'line': frame.f_lineno, 'class':type(error).__name__,
                        'code':getattr(error,'sqlite_errorcode',None),'name':getattr(error,'sqlite_errorname',None)})
            return trace
        def guard(instance):
            if guards:
                return original_guard(instance)
            worker = threading.Thread(target=holder, name='probe-observation-close', daemon=True)
            worker.start()
            previous_trace = sys.gettrace()
            try:
                assert holder_ready.wait(5) and not failures
                # The test fixture otherwise holds a persistent reader that the
                # native gateway does not have. Close it before the final holder.
                rig.conn.close()
                if mode != 'ordinary_open_reader':
                    holder_close.set()
                    assert (pause.readonly_refused if readonly else pause.acquired).wait(5), pause.events
                if mode == 'readonly_stale_owner':
                    rig.transport.closed = True
                if mode == 'released_close':
                    pause.release.set()
                    worker.join(5)
                    assert not worker.is_alive() and not failures
                sys.settrace(trace)
                allowed = original_guard(instance)
                hits_at_guard.append(pause.hits)
                guards.append(allowed)
                return allowed
            finally:
                sys.settrace(previous_trace)
                holder_close.set()
                pause.release.set()
                worker.join(5)
                assert not worker.is_alive() and not failures
                rig.conn = sqlite3.connect(path, isolation_level=None)
                rig.conn.row_factory = sqlite3.Row
        monkeypatch.setattr(writers._Gate, '_wait_for_turn', waiting)
        monkeypatch.setattr(admission._Admission, 'try_send_allowed', guard)
        if mode in {'uncoordinated_close', 'coordinated_stale_owner', 'readonly_stale_owner'}:
            with pytest.raises(Exception) as caught:
                infer(rig, value)
            assert guards == [False]
            assert read_errors == ([{'line':290,'class':'OperationalError','code':5,'name':'SQLITE_BUSY'}]
                                   if mode == 'uncoordinated_close' else [])
            assert len(connects) == 1 and socket.sent == []
            assert state(rig)[0]['status'] == 'uncertain' and state(rig)[0]['dispatched'] == 1
            assert budget.operation_counts(rig.conn)['total'] == 1
            assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 1
            assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
            assert admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport) is None
            old_rows = list(rig.conn.iterdump())
            for replay in (value, request(rig, 'new-intent')):
                with pytest.raises(Exception):
                    infer(rig, replay)
                assert len(connects) == 1 and socket.sent == []
                assert list(rig.conn.iterdump()) == old_rows
            assert pause.hits == (0 if readonly else 1)
            assert queued == ([True] if mode == 'coordinated_stale_owner' else [])
        else:
            assert infer(rig, value)['suggestion'] == 'Ready'
            assert guards == [True] and read_errors == []
            assert state(rig)[0]['status'] == 'completed' and state(rig)[0]['dispatched'] == 1
            assert budget.operation_counts(rig.conn)['total'] == 1
            assert len(connects) == 1 and len(socket.sent) == 2
            assert infer(rig, value)['suggestion'] == 'Ready' and len(connects) == 1
            assert queued == ([True] if mode == 'coordinated_close' else [])
            assert pause.hits == (0 if readonly else 1)
            assert hits_at_guard == ([0] if mode == 'ordinary_open_reader' or readonly else [1])
        if readonly:
            assert pause.refusal_hits == 1 and not pause.acquired.is_set()
            assert pause.events == [{'phase':'inside_connection_close','lock':'exclusive','result':sqlite3.SQLITE_IOERR_LOCK}]
            readonly_evidence.update(pause.target_open_flags)
            assert readonly_evidence == {'queryOnlyFalse':True,'dmlError':'SQLITE_READONLY',
                'requestedReadonly':True,'requestedReadwrite':False,'returnedReadonly':True,'returnedFlagsPresent':True,
                'methodVersion':3}
        assert before_policy == [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_policy')]
        result = {'mode':mode, 'identity':pause.identity(), 'locks':pause.events,
            'guardResults':guards, 'closeLocksAtGuard':hits_at_guard, 'readErrors':read_errors, 'coordinatorWaits':len(queued),
            'sendBytesCalls':len(socket.sent), 'connections':len(connects),
            'generationStatus':state(rig)[0]['status'], 'dispatched':state(rig)[0]['dispatched'],
            'callsCharged':budget.operation_counts(rig.conn)['total'], 'policyUnchanged':True,
            'sameKeyReplayAddsSend':False, 'probeFailures':failures+pause.failures}
        result['readonlyEvidence'] = readonly_evidence
        result['readonlyRefusalPauses'] = pause.refusal_hits
        rig.conn.close()
        (OUT / (mode+'.json')).write_text(json.dumps(result,indent=2)+'\n')


def test_unsuccessful_exclusive_lock_does_not_pause(tmp_path):
    path = tmp_path / 'state.db'
    keeper = sqlite3.connect(path, isolation_level=None)
    assert keeper.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    keeper.execute('CREATE TABLE t(a)')
    keeper.execute('INSERT INTO t VALUES(1)')
    # Keep a real competing read snapshot so final-close EXCLUSIVE cannot succeed.
    keeper.execute('BEGIN')
    keeper.execute('SELECT * FROM t').fetchall()
    try:
        with ClosePause() as pause:
            conn = sqlite3.connect(path, isolation_level=None)
            assert conn.execute('SELECT * FROM t').fetchall() == [(1,)]
            pause.arm_close(path)
            conn.close()
            pause.disarm_close()
            assert pause.hits == 0 and not pause.acquired.is_set()
            assert pause.events == [{'phase':'inside_connection_close','lock':'exclusive','result':5}]
            assert not pause.files
        assert pause.restored
    finally:
        keeper.rollback()
        keeper.close()


def test_probe_restores_default_after_body_exception(tmp_path):
    path = tmp_path / 'state.db'
    failure = RuntimeError('synthetic probe-body failure')
    pause = ClosePause()
    with pytest.raises(RuntimeError) as caught:
        with pause:
            conn = sqlite3.connect(path, isolation_level=None)
            try:
                conn.execute('CREATE TABLE t(a)')
                raise failure
            finally:
                conn.close()
    assert caught.value is failure and pause.restored and not pause.files
    # A new ordinary connection works after removal, using the prior default.
    with sqlite3.connect(path) as check:
        assert check.execute('SELECT count(*) FROM t').fetchone() == (0,)
    check.close()


def test_probe_restores_default_before_reporting_live_connection(tmp_path):
    path = tmp_path / 'state.db'
    pause = ClosePause()
    conn = None
    try:
        with pytest.raises(AssertionError, match='Probe connections must close'):
            with pause:
                conn = sqlite3.connect(path, isolation_level=None)
                conn.execute('CREATE TABLE t(a)')
        assert pause.restored
        # The old connection still owns its callback references safely, though
        # the probe is unregistered and no longer the default for new opens.
        assert conn.execute('SELECT count(*) FROM t').fetchone() == (0,)
    finally:
        if conn is not None:
            conn.close()
    assert not pause.files


@pytest.mark.parametrize('stage', ['open_before_proxy', 'open_after_proxy', 'lock_observation', 'close_tracking'])
def test_callback_observation_failure_preserves_native_result_and_cleanup(tmp_path, stage):
    pause = ClosePause()
    path = tmp_path / 'state.db'
    fired = []
    def fail(actual):
        if actual == stage and not fired:
            fired.append(actual)
            raise RuntimeError('synthetic observer failure')
    with pytest.raises(AssertionError, match='Probe observation incomplete'):
        with pause:
            pause.fault = fail if stage.startswith('open_') else lambda _: None
            conn = sqlite3.connect(path, isolation_level=None)
            assert conn.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
            conn.execute('CREATE TABLE t(a)')
            conn.execute('INSERT INTO t VALUES(1)')
            assert conn.execute('SELECT * FROM t').fetchone() == (1,)
            if not stage.startswith('open_'):
                pause.arm_close(path)
                pause.release.set()
                pause.fault = fail
            conn.close()
            pause.disarm_close()
            assert fired == [stage] and not pause.files
    assert pause.restored and pause.observation_failed
    with sqlite3.connect(path) as after:
        assert after.execute('SELECT * FROM t').fetchall() == [(1,)]
    after.close()


def test_callback_bundle_lifetime_survives_close_and_gc(tmp_path):
    import gc
    with ClosePause() as pause:
        conn = sqlite3.connect(tmp_path / 'state.db', isolation_level=None)
        conn.execute('CREATE TABLE t(a)')
        retained = tuple(pause.retained_bundles)
        assert retained and pause.files
        conn.close()
        assert not pause.files and tuple(pause.retained_bundles) == retained
        del conn
        gc.collect()
        assert tuple(pause.retained_bundles) == retained
    assert pause.restored


def test_callback_retention_exhaustion_preserves_native_connection(tmp_path):
    pause = ClosePause()
    pause.max_bundles = 0
    with pytest.raises(AssertionError, match='Probe observation incomplete'):
        with pause:
            conn = sqlite3.connect(tmp_path / 'state.db', isolation_level=None)
            try:
                conn.execute('CREATE TABLE t(a)')
                assert conn.execute('SELECT count(*) FROM t').fetchone() == (0,)
            finally:
                conn.close()
            assert not pause.files and not pause.retained_bundles
    assert pause.restored

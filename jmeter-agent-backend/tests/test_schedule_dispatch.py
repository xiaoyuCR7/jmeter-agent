"""Opt-in schedule claims, durable batch identity and inert OS-process races."""
import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event

pytestmark = pytest.mark.requires_patched_sqlite

from app.core.time import utc_now_naive
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_schedule_dispatch_primitive_exists():
    assert importlib.util.find_spec('app.services.schedule_dispatch') is not None


@pytest_asyncio.fixture
async def dispatch_db(tmp_path, monkeypatch):
    from app.models import db
    from app.services import execution_lifecycle, notifications
    from app.services.db_store import db_store
    from app.services.coordination import Coordinator
    path = tmp_path / 'dispatch.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async def session():
        return factory()
    monkeypatch.setattr(db_store, '_session', session)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    source = tmp_path / 'plan.jmx'
    source.write_text('<jmeterTestPlan/>', encoding='utf-8')
    due = datetime(2000, 1, 1)
    async with factory() as session:
        session.add(db.ProjectModel(id='shared', name='Shared'))
        await session.flush()
        session.add(db.ProjectAccessPolicyModel(project_id='shared', mode='legacy_shared', version=1))
        session.add(db.TestPlanModel(id='plan', project_id='shared', name='Plan', jmx_path=str(source)))
        await session.flush()
        schedule = db.ScheduleModel(id=1, project_id='shared', test_plan_id='plan', name='Job',
            cron_expression='* * * * *', jmx_path=str(source), next_run_at=due)
        session.add(schedule)
        await session.commit()
    coordinator = Coordinator(path, capacities={})
    coordinator.register()
    async def execute(path, execution_id, **kwargs):
        if kwargs.get('before_execute'):
            await kwargs['before_execute'](SimpleNamespace(id=execution_id))
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    fake = AsyncMock(side_effect=execute)
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', fake)
    monkeypatch.setattr(notifications, 'notify_test_started', AsyncMock())
    monkeypatch.setattr(notifications, 'notify_test_completed', AsyncMock())
    yield SimpleNamespace(path=path, factory=factory, schedule=schedule, coordinator=coordinator,
                          execute=fake, due=due, tmp_path=tmp_path)
    await engine.dispose()


def rows(db, statement):
    with sqlite3.connect(db.path) as connection:
        return connection.execute(statement).fetchall()


@pytest.mark.asyncio
async def test_schedule_dispatch_commits_batch_before_execution_and_releases_after_completion(dispatch_db):
    from app.services.scheduler import ScheduleRunner
    observed = []
    async def execute(path, execution_id, **kwargs):
        observed.extend(rows(dispatch_db, 'SELECT execution_id,state FROM schedule_dispatches'))
        assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
        assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('active',)]
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    dispatch_db.execute.side_effect = execute
    def verify_inert_completion(execution_id, result):
        # Test-only fake has no child processes, containers or background work.
        return execution_id == observed[0][0]
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator,
                            completion_verifier=verify_inert_completion)
    await runner._check_and_run()
    await asyncio.gather(*runner._jobs.values())
    assert len(observed) == 1 and observed[0][1] == 'running'
    assert dispatch_db.execute.await_args.args[1] == observed[0][0]
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('completed',)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]


@pytest.mark.asyncio
@pytest.mark.parametrize('proof', ['absent', False, None, 1, 'error'])
async def test_schedule_dispatch_terminal_business_status_without_proof_stays_quarantined(dispatch_db, proof):
    from app.models.schemas import TestExecutionInfo, TestExecutionStatus
    from app.services import notifications
    from app.services.scheduler import ScheduleRunner
    from app.services.coordination import Busy
    result = TestExecutionInfo(id='inert-result', project_id='shared', test_plan_id='plan',
                               jmx_path=dispatch_db.schedule.jmx_path,
                               status=TestExecutionStatus.COMPLETED)
    async def execute(*args, **kwargs):
        return result
    dispatch_db.execute.side_effect = execute
    def verify(execution_id, result):
        if proof == 'error':
            raise RuntimeError('completion proof unavailable')
        return proof
    kwargs = {} if proof == 'absent' else {'completion_verifier': verify}
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator, **kwargs)
    await runner._check_and_run()
    await asyncio.gather(*runner._jobs.values())
    assert result.status is TestExecutionStatus.COMPLETED
    assert notifications.notify_test_completed.await_args.kwargs['status'] == 'completed'
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('unknown',)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('quarantined',)]
    with pytest.raises(Busy):
        dispatch_db.coordinator.claim('later-without-proof', ['schedule:1'])


@pytest.mark.asyncio
async def test_schedule_dispatch_busy_skips_without_advancing_count(dispatch_db):
    from app.services.scheduler import ScheduleRunner
    from app.services.coordination import Coordinator
    holder = dispatch_db.coordinator.claim('holder', ['schedule:1']).handle
    other = Coordinator(dispatch_db.path, capacities={})
    other.register()
    await ScheduleRunner(coordinator=other)._check_and_run()
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(0,)]
    assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(0,)]
    dispatch_db.execute.assert_not_awaited()
    dispatch_db.coordinator.release(holder, work_completed=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['next_run_at', 'is_enabled', 'cron_expression', 'delete'])
async def test_schedule_dispatch_checks_current_enabled_and_expected_due_atomically(dispatch_db, change):
    from app.services.schedule_dispatch import ScheduleDispatchStore
    with sqlite3.connect(dispatch_db.path) as connection:
        if change == 'delete':
            connection.execute('DELETE FROM schedules')
        else:
            value = {'next_run_at': '2099-01-01 00:00:00.000000', 'is_enabled': 0,
                     'cron_expression': '0 * * * *'}[change]
            connection.execute(f'UPDATE schedules SET {change}=?', (value,))
    store = ScheduleDispatchStore(dispatch_db.coordinator)
    assert store.claim(dispatch_db.schedule, utc_now_naive(), datetime(2099, 1, 2)) is None
    assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(0,)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == ([] if change == 'delete' else [(0,)])


@pytest.mark.asyncio
async def test_schedule_dispatch_fault_rolls_back_both_advance_and_dispatch(dispatch_db):
    from app.services.schedule_dispatch import ScheduleDispatchStore
    from app.services.coordination import StorageUnavailable
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute("CREATE TRIGGER dispatch_fault BEFORE UPDATE ON schedules "
                           "BEGIN SELECT RAISE(ABORT,'injected advance failure'); END")
    store = ScheduleDispatchStore(dispatch_db.coordinator)
    with pytest.raises(StorageUnavailable):
        store.claim(dispatch_db.schedule, utc_now_naive(), datetime(2099, 1, 1))
    assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(0,)]
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(0,)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]


@pytest.mark.asyncio
async def test_schedule_dispatch_committed_batch_cannot_replay_even_if_due_is_restored(dispatch_db):
    from app.services.schedule_dispatch import ScheduleDispatchStore
    store = ScheduleDispatchStore(dispatch_db.coordinator)
    first = store.claim(dispatch_db.schedule, utc_now_naive(), datetime(2099, 1, 1))
    store.finish(first)
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute('UPDATE schedules SET next_run_at=?', (dispatch_db.due.isoformat(' ', timespec='microseconds'),))
    assert store.claim(dispatch_db.schedule, utc_now_naive(), datetime(2099, 1, 1)) is None
    assert rows(dispatch_db, 'SELECT count(*),state FROM schedule_dispatches') == [(1, 'abandoned')]
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['cancel', 'nonterminal', 'exception'])
async def test_schedule_dispatch_unknown_external_outcome_quarantines_schedule(dispatch_db, failure):
    from app.services.scheduler import ScheduleRunner
    from app.services.coordination import Busy
    entered = asyncio.Event()
    async def execute(*args, **kwargs):
        entered.set()
        if failure == 'cancel':
            await asyncio.Event().wait()
        if failure == 'exception':
            raise RuntimeError('unknown external outcome')
        return SimpleNamespace(status=SimpleNamespace(value='running'), report_dir='', log_path='', error_message=None)
    dispatch_db.execute.side_effect = execute
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator)
    await runner._check_and_run()
    tasks = list(runner._jobs.values())
    await asyncio.wait_for(entered.wait(), 5)
    if failure == 'cancel':
        await runner.stop()
    else:
        await asyncio.gather(*tasks, return_exceptions=True)
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('unknown',)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('quarantined',)]
    with pytest.raises(Busy):
        dispatch_db.coordinator.claim('later', ['schedule:1'])


@pytest.mark.asyncio
async def test_schedule_dispatch_cancel_before_job_body_releases_safe_claim(dispatch_db):
    from app.services.scheduler import ScheduleRunner
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator)
    await runner._check_and_run()
    # No event-loop yield between task creation and cancellation.
    await runner.stop()
    dispatch_db.execute.assert_not_awaited()
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('abandoned',)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]


@pytest.mark.asyncio
async def test_schedule_dispatch_pre_execution_acl_denial_releases_safe_claim(dispatch_db):
    from app.services.scheduler import ScheduleRunner
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute("UPDATE project_access_policies SET mode='private'")
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator)
    await runner._check_and_run()
    await asyncio.gather(*runner._jobs.values(), return_exceptions=True)
    dispatch_db.execute.assert_not_awaited()
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('abandoned',)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]


@pytest.mark.asyncio
async def test_schedule_dispatch_keeps_resource_across_future_ticks(dispatch_db):
    from app.services.scheduler import ScheduleRunner
    from app.services.coordination import Coordinator
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(*args, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    dispatch_db.execute.side_effect = execute
    # Test-only inert body is done once execute returns after this gate.
    first = ScheduleRunner(coordinator=dispatch_db.coordinator,
                           completion_verifier=lambda execution_id, result: release.is_set())
    other = Coordinator(dispatch_db.path, capacities={})
    other.register()
    await first._check_and_run()
    tasks = list(first._jobs.values())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with sqlite3.connect(dispatch_db.path) as connection:
            connection.execute('UPDATE schedules SET next_run_at=?',
                ((dispatch_db.due + timedelta(minutes=1)).isoformat(' ', timespec='microseconds'),))
        await ScheduleRunner(coordinator=other)._check_and_run()
        assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
        assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(1,)]
        assert dispatch_db.execute.await_count == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_schedule_dispatch_storage_error_never_notifies_or_executes(dispatch_db):
    from app.services import notifications
    from app.services.scheduler import ScheduleRunner
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute("CREATE TRIGGER effects_fault BEFORE UPDATE ON coordination_operations "
                           "WHEN NEW.sideeffects_possible=1 BEGIN SELECT RAISE(ABORT,'injected fault'); END")
    runner = ScheduleRunner(coordinator=dispatch_db.coordinator)
    await runner._check_and_run()
    await asyncio.gather(*runner._jobs.values(), return_exceptions=True)
    dispatch_db.execute.assert_not_awaited()
    notifications.notify_test_started.assert_not_awaited()
    notifications.notify_test_completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_dispatch_refuses_different_schedule_and_coordination_databases(dispatch_db, tmp_path):
    from app.services.coordination import Coordinator, ConfigurationMismatch
    from app.services.scheduler import ScheduleRunner
    other = Coordinator(tmp_path / 'different.db', capacities={})
    with pytest.raises(ConfigurationMismatch):
        await ScheduleRunner(coordinator=other)._check_and_run()
    dispatch_db.execute.assert_not_awaited()
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(0,)]


@pytest.mark.asyncio
async def test_schedule_dispatch_task_creation_fault_keeps_committed_batch_and_releases(dispatch_db, monkeypatch):
    from app.services import scheduler
    def fail(coroutine):
        # DB state is committed before create_task is even attempted.
        assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('committed',)]
        raise RuntimeError('task creation fault')
    runner = scheduler.ScheduleRunner(coordinator=dispatch_db.coordinator)
    # Patch the scheduling boundary, without affecting SQLAlchemy's internal tasks.
    monkeypatch.setattr(scheduler, 'asyncio', SimpleNamespace(create_task=fail))
    with pytest.raises(RuntimeError, match='task creation fault'):
        await runner._check_and_run()
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('abandoned',)]
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]
    dispatch_db.execute.assert_not_awaited()
    assert not runner._jobs


@pytest.fixture
def dispatch_workers(dispatch_db, tmp_path):
    helper = tmp_path / 'schedule_worker.py'
    helper.write_text(Path(__file__).with_name('schedule_dispatch_worker.py').read_text(encoding='utf-8'), encoding='utf-8')
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute('CREATE TABLE fake_calls(execution_id TEXT PRIMARY KEY,pid INTEGER NOT NULL)')
    children = []
    def start(*, gate=None, release_gate=None, crash=False):
        name = uuid.uuid4().hex
        request, response = tmp_path / (name + '-request.json'), tmp_path / (name + '-response.json')
        request.write_text(json.dumps(dict(database=str(dispatch_db.path), response=str(response),
            gate=str(gate) if gate else None, release_gate=str(release_gate) if release_gate else None,
            crash=crash)), encoding='utf-8')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        if sys.platform == 'win32':
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            kernel.GetModuleFileNameW.restype = ctypes.c_uint
            buffer = ctypes.create_unicode_buffer(32768)
            assert kernel.GetModuleFileNameW(kernel.GetModuleHandleW('sqlite3.dll'), buffer, len(buffer))
            env['COORDINATION_TEST_SQLITE_DLL'] = buffer.value
            env['COORDINATION_TEST_SQLITE_VERSION'] = sqlite3.sqlite_version
        process = subprocess.Popen([sys.executable, '-B', str(helper), str(request)], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(process)
        return process, response
    yield start
    for process in children:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)


def worker_file(process, path, *, json_data=True):
    deadline = time.monotonic() + 20
    last_read_error = None
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8')) if json_data else None
            except (FileNotFoundError, PermissionError) as error:
                # Windows can briefly deny the first open immediately after an
                # atomic replace. The response is immutable once readable.
                last_read_error = error
        if process.poll() is not None:
            if last_read_error is not None:
                time.sleep(.01)
                continue
            stdout, stderr = process.communicate()
            pytest.fail(f'scheduler worker exited {process.returncode}: {stdout!r} {stderr!r}')
        time.sleep(.01)
    pytest.fail(f'bounded scheduler response timeout: {last_read_error!r}')


@pytest.mark.asyncio
async def test_schedule_dispatch_two_os_processes_same_due_one_batch_count_and_fake_call(dispatch_db, dispatch_workers, tmp_path):
    gate, release = tmp_path / 'both-due', tmp_path / 'finish-execution'
    first, path1 = dispatch_workers(gate=gate, release_gate=release)
    second, path2 = dispatch_workers(gate=gate, release_gate=release)
    # Each process has already read the exact same due ORM schedule before gate.
    worker_file(first, path1.with_suffix('.due'), json_data=False)
    worker_file(second, path2.with_suffix('.due'), json_data=False)
    gate.touch()
    one, two = worker_file(first, path1), worker_file(second, path2)
    assert one['pid'] != two['pid'] and os.getpid() not in (one['pid'], two['pid'])
    assert sorted([one['status'], two['status']]) == ['executed', 'skipped']
    assert one['sqlite'] == two['sqlite'] == sqlite3.sqlite_version
    assert one['journal_mode'] == two['journal_mode'] == 'wal'
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
    assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(1,)]
    assert rows(dispatch_db, 'SELECT count(*) FROM fake_calls') == [(1,)]
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('active',)]
    release.touch()
    holder, response = (first, path1) if one['status'] == 'executed' else (second, path2)
    worker_file(holder, response.with_suffix('.finished.json'))
    assert rows(dispatch_db, 'SELECT state FROM resource_leases') == [('released',)]
    print('schedule-two-process-contention', json.dumps([one, two]))


@pytest.mark.asyncio
async def test_schedule_dispatch_os_crash_after_commit_before_launch_never_replays(dispatch_db, dispatch_workers, tmp_path):
    gate = tmp_path / 'crash-after-commit'
    first, response = dispatch_workers(gate=gate, crash=True)
    worker_file(first, response.with_suffix('.due'), json_data=False)
    gate.touch()
    stdout, stderr = first.communicate(timeout=20)
    assert first.returncode == 73, (stdout, stderr)
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
    assert rows(dispatch_db, 'SELECT state FROM schedule_dispatches') == [('committed',)]
    assert rows(dispatch_db, 'SELECT count(*) FROM fake_calls') == [(0,)]
    second, response2 = dispatch_workers()
    info = worker_file(second, response2)
    assert info['status'] == 'skipped' and info['pid'] != first.pid
    # Even restoring the old due value cannot replay that permanent batch.
    with sqlite3.connect(dispatch_db.path) as connection:
        connection.execute('UPDATE schedules SET next_run_at=?', (dispatch_db.due.isoformat(' ', timespec='microseconds'),))
    third, response3 = dispatch_workers()
    retry = worker_file(third, response3)
    assert retry['status'] == 'skipped'
    assert rows(dispatch_db, 'SELECT run_count FROM schedules') == [(1,)]
    assert rows(dispatch_db, 'SELECT count(*) FROM schedule_dispatches') == [(1,)]
    assert rows(dispatch_db, 'SELECT count(*) FROM fake_calls') == [(0,)]
    print('schedule-commit-before-launch-crash', json.dumps(dict(crashed_pid=first.pid, exit_code=73, after=info, replay=retry)))

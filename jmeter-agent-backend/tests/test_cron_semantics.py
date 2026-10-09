"""Cron arithmetic and owned dispatch; executors/subprocesses are replaced."""
from datetime import datetime
import pytest
from app.services import scheduler
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest_asyncio


@pytest.mark.parametrize('day', ['0', '7'])
def test_cron_sunday_is_zero_or_seven(day):
    assert scheduler._next_cron_time(f'0 0 * * {day}', datetime(2026, 8, 29, 12)) == datetime(2026, 8, 30)


def test_restricted_day_of_month_and_week_are_or():
    expression = '0 0 1 * 1'
    assert scheduler._cron_matches(expression, datetime(2026, 9, 1))
    assert scheduler._cron_matches(expression, datetime(2026, 9, 7))
    assert not scheduler._cron_matches(expression, datetime(2026, 9, 8))


def test_cron_ranges_lists_and_steps():
    parsed = scheduler._parse_cron_fields('5-15/5,40 2-8/3 * * 1-5/2')
    assert parsed['minute'] == {5, 10, 15, 40}
    assert parsed['hour'] == {2, 5, 8}
    assert parsed['dow'] == {1, 3, 5}


@pytest.mark.parametrize('expression', [
    '60 * * * *', '* 24 * * *', '* * 0 * *', '* * * 13 *', '* * * * 8',
    '*/0 * * * *', '10-2 * * * *', '*/-1 * * * *', '1,,2 * * * *',
])
def test_invalid_cron_rejected_before_search(expression):
    with pytest.raises(ValueError):
        scheduler._parse_cron_fields(expression)


def test_sparse_schedule_parses_once_and_finds_next_leap_day(monkeypatch):
    original = scheduler._parse_cron_fields
    calls = 0
    def parse(expression):
        nonlocal calls
        calls += 1
        assert calls <= 1, 'Do not parse/scan every minute on the event loop'
        return original(expression)
    monkeypatch.setattr(scheduler, '_parse_cron_fields', parse)
    assert scheduler._next_cron_time('0 0 29 2 *', datetime(2025, 3, 1)) == datetime(2028, 2, 29)
    assert calls == 1


def test_impossible_date_returns_none_without_minute_scan(monkeypatch):
    original = scheduler._parse_cron_fields
    calls = 0
    def parse(expression):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('Cron reparsed during search')
        return original(expression)
    monkeypatch.setattr(scheduler, '_parse_cron_fields', parse)
    assert scheduler._next_cron_time('0 0 31 2 *', datetime(2026, 1, 1)) is None
    assert calls == 1


def test_cron_wildcard_step_day_uses_other_day_constraint():
    expression = '0 0 */2 * 1'
    assert scheduler._cron_matches(expression, datetime(2026, 9, 7))
    assert not scheduler._cron_matches(expression, datetime(2026, 9, 9))


def test_next_cron_is_strictly_later_and_handles_year_boundary():
    assert scheduler._next_cron_time('0 0 1 1 *', datetime(2026, 1, 1)) == datetime(2027, 1, 1)


@pytest_asyncio.fixture
async def scheduled_jobs(tmp_path, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import db
    from app.services import execution_lifecycle, notifications
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('Real subprocess execution is forbidden in scheduler tests')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'cron.db'))
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=DELETE')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    await db.init_db()
    async with factory() as session:
        session.add(db.ProjectModel(id='default', name='Default'))
        await session.flush()
        session.add(db.ProjectAccessPolicyModel(project_id='default', mode='legacy_shared', version=1))
        paths = set()
        for identifier in (1, 2):
            path = tmp_path / f'plan-{identifier}.jmx'
            path.write_text('<jmeterTestPlan/>', encoding='utf-8')
            paths.add(str(path))
            session.add(db.TestPlanModel(id=f'plan-{identifier}', project_id='default',
                                        name=f'Plan {identifier}', jmx_path=str(path)))
        await session.flush()
        for identifier in (1, 2):
            session.add(db.ScheduleModel(id=identifier, project_id='default', name=f'Job {identifier}',
                test_plan_id=f'plan-{identifier}', cron_expression='* * * * *',
                jmx_path=str(tmp_path / f'plan-{identifier}.jmx'),
                next_run_at=datetime(2000, 1, 1)))
        await session.commit()
    fake = SimpleNamespace(execute=AsyncMock(), paths=paths)
    monkeypatch.setattr(execution_lifecycle, 'execution_lifecycle', fake)
    monkeypatch.setattr(notifications, 'notify_test_started', AsyncMock())
    monkeypatch.setattr(notifications, 'notify_test_completed', AsyncMock())
    yield fake
    await engine.dispose()


@pytest.mark.asyncio
async def test_blocking_job_does_not_block_dispatch_of_other_job(scheduled_jobs):
    started = set()
    ready = asyncio.Event()
    async def execute(path, execution_id, **kwargs):
        started.add(path)
        if started == scheduled_jobs.paths:
            ready.set()
        await asyncio.Event().wait()
    scheduled_jobs.execute.side_effect = execute
    runner = scheduler.ScheduleRunner()
    try:
        await asyncio.wait_for(runner._check_and_run(), timeout=0.5)
        await asyncio.wait_for(ready.wait(), timeout=5)
        assert started == scheduled_jobs.paths
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_same_job_never_overlaps_and_stop_awaits_cleanup(scheduled_jobs):
    started, cleaned = set(), set()
    ready = asyncio.Event()
    async def execute(path, execution_id, **kwargs):
        started.add(path)
        if started == scheduled_jobs.paths:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.add(path)
    scheduled_jobs.execute.side_effect = execute
    runner = scheduler.ScheduleRunner()
    try:
        await asyncio.wait_for(runner._check_and_run(), timeout=0.5)
        await asyncio.wait_for(ready.wait(), timeout=5)
        # Make schedules due again while their owned tasks are still blocked.
        from app.models.db import get_session_factory, ScheduleModel
        async with get_session_factory()() as session:
            for identifier in (1, 2):
                (await session.get(ScheduleModel, identifier)).next_run_at = datetime(2000, 1, 1)
            await session.commit()
        await runner._check_and_run()
        assert scheduled_jobs.execute.await_count == 2
        assert len(runner._jobs) == 2
    finally:
        await runner.stop()
    assert cleaned == started == scheduled_jobs.paths
    assert not runner._jobs

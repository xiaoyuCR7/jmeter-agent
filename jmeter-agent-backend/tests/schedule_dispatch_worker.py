"""Real scheduler process with an inert lifecycle; no application startup."""
import os
import sys

if sys.platform == 'win32' and os.environ.get('COORDINATION_TEST_SQLITE_DLL'):
    import ctypes
    _sqlite_dll = ctypes.WinDLL(os.environ['COORDINATION_TEST_SQLITE_DLL'])
if os.environ.get('JMETER_AGENT_TEST_PYSQLITE3') == '1':
    import pysqlite3 as sqlite3
    # The application imports `sqlite3` itself; it must resolve to the verified binding.
    sys.modules['sqlite3'] = sqlite3
else:
    import sqlite3
if os.environ.get('COORDINATION_TEST_SQLITE_VERSION'):
    assert sqlite3.sqlite_version == os.environ['COORDINATION_TEST_SQLITE_VERSION']

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
deadline = time.monotonic() + 30
response = Path(request['response'])


def publish(data, suffix=None):
    path = response.with_suffix(suffix) if suffix else response
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.replace(path)


def wait_for(path):
    while path and not Path(path).exists():
        if time.monotonic() > deadline:
            raise RuntimeError('bounded scheduler gate timeout')
        time.sleep(.01)


async def main():
    from app.core import config
    config.PATHS_CONFIG_FILE = str(response.parent / 'no-paths.json')
    from app.models import db
    from app.services import scheduler, execution_lifecycle, notifications
    from app.services.coordination import Coordinator
    from app.services.db_store import db_store
    engine = create_async_engine('sqlite+aiosqlite:///' + request['database'])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    db._engine, db._async_session_factory = engine, factory
    async def session():
        return factory()
    db_store._session = session
    coordinator = Coordinator(request['database'], capacities={})
    coordinator.register()
    completed_inert_ids = set()
    # Test-only proof: fake work has no descendants or independent background jobs.
    runner = scheduler.ScheduleRunner(coordinator=coordinator,
        completion_verifier=lambda execution_id, result: execution_id in completed_inert_ids)
    with sqlite3.connect(request['database']) as connection:
        journal_mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
    info = dict(pid=os.getpid(), creation_time=coordinator.owner.creation_time,
                generation=coordinator.owner.generation, sqlite=sqlite3.sqlite_version,
                journal_mode=journal_mode)
    original_claim = runner._dispatches.claim
    def gated_claim(schedule, now, next_run):
        response.with_suffix('.due').touch()
        wait_for(request['gate'])
        return original_claim(schedule, now, next_run)
    runner._dispatches.claim = gated_claim
    async def notify(**kwargs):
        pass
    notifications.notify_test_started = notify
    notifications.notify_test_completed = notify
    async def execute(path, execution_id, **kwargs):
        with sqlite3.connect(request['database']) as connection:
            assert connection.execute('SELECT run_count FROM schedules WHERE id=1').fetchone() == (1,)
            assert connection.execute('SELECT execution_id,state FROM schedule_dispatches').fetchone() == (execution_id, 'running')
            connection.execute('INSERT INTO fake_calls(execution_id,pid) VALUES (?,?)', (execution_id, os.getpid()))
        publish(dict(info, status='executed', execution_id=execution_id))
        while request['release_gate'] and not Path(request['release_gate']).exists():
            if time.monotonic() > deadline:
                raise RuntimeError('bounded fake execution gate timeout')
            await asyncio.sleep(.01)
        completed_inert_ids.add(execution_id)
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    execution_lifecycle.execution_lifecycle.execute = execute
    if request['crash']:
        def crash_before_task(coroutine):
            # _check_and_run reached this boundary only after dispatch commit.
            with sqlite3.connect(request['database']) as connection:
                assert connection.execute('SELECT state FROM schedule_dispatches').fetchall() == [('committed',)]
            os._exit(73)
        scheduler.asyncio = SimpleNamespace(create_task=crash_before_task)
    response.with_suffix('.ready').touch()
    await runner._check_and_run()
    tasks = list(runner._jobs.values())
    if not tasks:
        publish(dict(info, status='skipped'))
    else:
        await asyncio.gather(*tasks)
        publish(dict(info, status='completed'), '.finished.json')
    await engine.dispose()


asyncio.run(main())

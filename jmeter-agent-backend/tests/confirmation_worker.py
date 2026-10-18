"""Lazy real-process helper for shared confirmation tests."""
import asyncio
import ctypes
import json
import os
import sys
from pathlib import Path


if sys.platform == 'win32' and os.environ.get('CONFIRMATION_TEST_SQLITE_DLL'):
    ctypes.WinDLL(os.environ['CONFIRMATION_TEST_SQLITE_DLL'])

if os.environ.get('JMETER_AGENT_TEST_PYSQLITE3') == '1':
    import pysqlite3 as sqlite3
    # The application imports `sqlite3` itself; it must resolve to the verified binding.
    sys.modules['sqlite3'] = sqlite3
else:
    import sqlite3


async def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.coordination import Coordinator
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.confirmations import ConfirmationManager
    engine = create_async_engine('sqlite+aiosqlite:///' + request['database'])
    db._async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = Coordinator(request['database'], capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    runtime = AgentOperationRuntime(coordinator)
    operation = runtime.acquire('real-child-confirmation')
    owner = UserInfo(**request['owner'])
    manager = ConfirmationManager(coordinator=coordinator, poll_seconds=.01)
    with operation.activate():
        waiting = asyncio.create_task(manager.request(owner, 'danger', {'path': 'child', 'secret': 'child-secret'}))
    try:
        for _ in range(500):
            async with db.get_session_factory()() as session:
                count = (await session.execute(select(func.count()).select_from(db.ToolConfirmationModel))).scalar_one()
            if count:
                break
            await asyncio.sleep(.01)
        else:
            raise RuntimeError('confirmation was not persisted')
        raw = sqlite3.connect(request['database'])
        journal = raw.execute('PRAGMA journal_mode').fetchone()[0]
        raw.close()
        Path(request['ready']).write_text(json.dumps(
            {'pid': os.getpid(), 'sqlite': sqlite3.sqlite_version, 'journal_mode': journal}), encoding='utf-8')
        approved = await asyncio.wait_for(waiting, 10)
        Path(request['result']).write_text(json.dumps({'approved': approved}), encoding='utf-8')
    finally:
        waiting.cancel(); await asyncio.gather(waiting, return_exceptions=True)
        operation.close()
        coordinator.close()
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))

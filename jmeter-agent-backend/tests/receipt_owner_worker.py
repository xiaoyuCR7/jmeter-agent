"""Bounded receipt-owning child, isolated DB and fake local work only."""
import asyncio
import ctypes
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

if sys.platform == 'win32':
    _sqlite_dll = ctypes.WinDLL(os.environ['RECEIPT_TEST_SQLITE_DLL'])
if os.environ.get('JMETER_AGENT_TEST_PYSQLITE3') == '1':
    import pysqlite3 as sqlite3
else:
    import sqlite3
assert sqlite3.sqlite_version == os.environ['RECEIPT_TEST_SQLITE_VERSION']

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import db
from app.models.schemas import AgentRunRequest
from app.services.auth import UserInfo
from app.services.security_context import current_principal
from app.services.project_scope import internal_scope
from app.services.coordination import Coordinator
from app.services.ai.analysis_receipts import AnalysisReceipts
from app.services.ai.operation_runtime import AgentOperationRuntime


async def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    engine = create_async_engine('sqlite+aiosqlite:///' + settings['database'])
    db._engine = engine
    db._async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = Coordinator(settings['database'], capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    service = AnalysisReceipts(coordinator=coordinator)
    principal = current_principal.set(UserInfo(user_id=settings['user_id'], username='alice', role='operator'))
    try:
        with internal_scope():
            handle, _ = await service.claim(AgentRunRequest(**settings['request']))
            operation = AgentOperationRuntime(coordinator).acquire('session', analysis=True)
            await service.bind_operation(handle, operation)
            with sqlite3.connect(settings['database']) as connection:
                journal = connection.execute('PRAGMA journal_mode').fetchone()[0]
            payload = dict(pid=os.getpid(), creation_time=coordinator.owner.creation_time,
                           generation=coordinator.owner.generation, handle=asdict(handle),
                           sqlite=sqlite3.sqlite_version, journal_mode=journal)
            response = Path(settings['response'])
            temporary = response.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload), encoding='utf-8')
            temporary.replace(response)
            await asyncio.sleep(30)  # Parent owns bounded termination; no heartbeat/replay.
    finally:
        current_principal.reset(principal)
        await engine.dispose()


asyncio.run(main())

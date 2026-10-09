"""Real-process probes for optional cross-worker cache and RAG coordination."""
import asyncio
import ctypes
import json
import os
import sys
from pathlib import Path


if sys.platform == 'win32' and os.environ.get('COORDINATION_TEST_SQLITE_DLL'):
    ctypes.WinDLL(os.environ['COORDINATION_TEST_SQLITE_DLL'])

if os.environ.get('JMETER_AGENT_TEST_PYSQLITE3') == '1':
    import pysqlite3 as sqlite3
else:
    import sqlite3


async def update_project(request):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import db
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    engine = create_async_engine('sqlite+aiosqlite:///' + request['database'])
    db._async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with internal_scope():
            result = await db_store.update_project(request['project_id'], {'name': request['name']})
        if result is None:
            raise RuntimeError('project disappeared before the child update')
    finally:
        await engine.dispose()


async def write_rag(request):
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import Coordinator
    from app.services.project_scope import internal_scope
    coordinator = Coordinator(request['database'], capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    operation_runtime.agent_operations = AgentOperationRuntime(coordinator)
    try:
        from app.services.ai.rag.store import LanceRAGStore, RAGDocument
        LanceRAGStore._init_db = lambda self: None
        document = RAGDocument(
            id=request['document_id'], doc_type='known_issue', module='api', version='v1',
            title=request['document_id'],
            content='A sufficiently detailed durable knowledge record from another process.',
        )
        with internal_scope():
            LanceRAGStore(request['rag_path']).add(document)
    finally:
        coordinator.close()


async def submit_mcp(request):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.coordination import Coordinator
    from app.services.mcp_mailbox import MCPMailbox
    engine = create_async_engine('sqlite+aiosqlite:///' + request['database'])
    db._async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = Coordinator(request['database'], capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    try:
        await MCPMailbox(coordinator).submit(
            request['session_id'], UserInfo(**request['owner']), request['payload'].encode())
    finally:
        coordinator.close()
        await engine.dispose()


async def publish_ws(request):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import db
    from app.services.coordination import Coordinator
    from app.services.ws_event_bus import WSEventBus
    engine = create_async_engine('sqlite+aiosqlite:///' + request['database'])
    db._async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = Coordinator(request['database'], capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    try:
        await WSEventBus(coordinator).publish(request['execution_id'], request['payload'])
    finally:
        coordinator.close()
        await engine.dispose()


async def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    if request['mode'] == 'cache':
        await update_project(request)
    elif request['mode'] == 'rag':
        await write_rag(request)
    elif request['mode'] == 'mcp':
        await submit_mcp(request)
    elif request['mode'] == 'ws':
        await publish_ws(request)
    else:
        raise ValueError('unknown coordination test mode')
    raw = sqlite3.connect(request['database'])
    journal_mode = raw.execute('PRAGMA journal_mode').fetchone()[0]
    raw.close()
    Path(request['result']).write_text(json.dumps({
        'pid': os.getpid(), 'sqlite': sqlite3.sqlite_version, 'journal_mode': journal_mode,
    }), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))

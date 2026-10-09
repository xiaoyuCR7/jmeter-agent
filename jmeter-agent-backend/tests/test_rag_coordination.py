"""RAG fallback reads and writes use the same optional cross-process authority."""
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.requires_patched_sqlite


@pytest_asyncio.fixture
async def rag_coordination(tmp_path, monkeypatch):
    from app.models import db
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import Coordinator
    from app.services.ai.rag.store import LanceRAGStore
    database = tmp_path / 'coordination.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(database))
    monkeypatch.setattr(db, '_async_session_factory', async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    raw = sqlite3.connect(database); raw.execute('PRAGMA journal_mode=WAL'); raw.commit(); raw.close()
    coordinator = Coordinator(database, capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    monkeypatch.setattr(operation_runtime, 'agent_operations', AgentOperationRuntime(coordinator))
    monkeypatch.setattr(LanceRAGStore, '_init_db', lambda self: None)
    try:
        yield coordinator, tmp_path / 'rag'
    finally:
        coordinator.close()
        await engine.dispose()


def document(identity):
    from app.services.ai.rag.store import RAGDocument
    return RAGDocument(id=identity, doc_type='known_issue', module='api', version='v1',
                       title=identity, content='A sufficiently detailed durable knowledge record for coordination.')


@pytest.mark.asyncio
async def test_coordinated_fallback_refreshes_other_worker_cache(rag_coordination):
    from app.services.ai.rag.store import LanceRAGStore
    from app.services.project_scope import internal_scope
    _, path = rag_coordination
    with internal_scope():
        first = LanceRAGStore(str(path))
        first.add(document('one'))
        second = LanceRAGStore(str(path))
        assert [item.id for item in second.list_all()] == ['one']
        first.add(document('two'))
        assert {item.id for item in second.list_all()} == {'one', 'two'}
        second.update('one', title='updated')
        assert first.get('one').title == 'updated'
        first.delete('two')
        assert second.get('two') is None


@pytest.mark.asyncio
async def test_real_process_rag_write_refreshes_existing_store(rag_coordination, tmp_path):
    from app.services.ai.rag.store import LanceRAGStore
    from app.services.project_scope import internal_scope
    coordinator, path = rag_coordination
    with internal_scope():
        existing = LanceRAGStore(str(path))
        existing.add(document('parent'))
    helper = tmp_path / 'worker.py'
    helper.write_bytes(Path(__file__).with_name('cross_feature_worker.py').read_bytes())
    result_path, request_path = tmp_path / 'result.json', tmp_path / 'request.json'
    request_path.write_text(json.dumps({
        'mode': 'rag', 'database': str(coordinator.database_path), 'rag_path': str(path),
        'document_id': 'child', 'result': str(result_path),
    }), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        buffer = ctypes.create_unicode_buffer(32768)
        assert kernel.GetModuleFileNameW(kernel.GetModuleHandleW('sqlite3.dll'), buffer, len(buffer))
        environment['COORDINATION_TEST_SQLITE_DLL'] = buffer.value
    child = subprocess.run([sys.executable, '-B', str(helper), str(request_path)], env=environment,
                           capture_output=True, timeout=15)
    assert child.returncode == 0, child.stderr.decode(errors='replace')
    proof = json.loads(result_path.read_text(encoding='utf-8'))
    assert proof['pid'] != os.getpid() and proof['sqlite'] == sqlite3.sqlite_version
    assert proof['journal_mode'] == 'wal'
    with internal_scope():
        assert {item.id for item in existing.list_all()} == {'parent', 'child'}


"""WebSocket workers exchange short-lived encrypted events, not subscriber tokens."""
import asyncio
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
async def ws_coordination(tmp_path, monkeypatch):
    from app.models import db
    from app.services.coordination import Coordinator
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    path = tmp_path / 'ws.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    monkeypatch.setattr(db, '_async_session_factory', async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    raw = sqlite3.connect(path); raw.execute('PRAGMA journal_mode=WAL'); raw.commit(); raw.close()
    coordinator = Coordinator(path, capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    monkeypatch.setattr(operation_runtime, 'agent_operations', AgentOperationRuntime(coordinator))
    try:
        yield path, coordinator
    finally:
        coordinator.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_process_event_is_encrypted_and_delivered_to_subscribing_worker(ws_coordination, tmp_path):
    from app.services.ws_event_bus import WSEventBus
    path, coordinator = ws_coordination
    bus = WSEventBus(coordinator)
    await bus.subscribe('run')
    helper = tmp_path / 'worker.py'
    helper.write_bytes(Path(__file__).with_name('cross_feature_worker.py').read_bytes())
    result_path, request_path = tmp_path / 'result.json', tmp_path / 'request.json'
    request_path.write_text(json.dumps({
        'mode': 'ws', 'database': str(path), 'execution_id': 'run',
        'payload': {'type': 'progress', 'pct': 42, 'message': 'child-secret'},
        'result': str(result_path),
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
    assert b'child-secret' not in path.read_bytes()
    received = []
    assert await bus.drain(lambda execution_id, data: received.append((execution_id, data))) == 1
    assert received == [('run', {'message': 'child-secret', 'pct': 42, 'type': 'progress'})]
    await bus.unsubscribe('run')
    await bus.close()


@pytest.mark.asyncio
async def test_manager_publishes_once_and_routed_delivery_still_rechecks_acl(monkeypatch):
    from app.services.ws_manager import WSManager
    manager = WSManager()
    published = []
    class Bus:
        enabled = True
        async def publish(self, execution_id, data): published.append((execution_id, data))
    manager._event_bus = Bus()
    websocket = type('Socket', (), {'send_text': lambda self, value: asyncio.sleep(0)})()
    manager._channels['run'] = {websocket}
    monkeypatch.setattr(manager, '_authorized', lambda ws, execution_id: asyncio.sleep(0, result=True))
    await manager.broadcast('run', {'type': 'status', 'status': 'running'})
    assert published == [('run', {'type': 'status', 'status': 'running'})]
    await manager._broadcast_local('run', {'type': 'status', 'status': 'completed'})
    assert len(published) == 1


@pytest.mark.asyncio
async def test_touch_recreates_expired_subscription_for_live_local_channel(ws_coordination):
    from sqlalchemy import delete, select
    from app.models import db
    from app.services.ws_event_bus import WSEventBus

    _, coordinator = ws_coordination
    bus = WSEventBus(coordinator)
    await bus.subscribe('run')
    async with db.get_session_factory()() as session:
        await session.execute(delete(db.WSWorkerSubscriptionModel).where(
            db.WSWorkerSubscriptionModel.execution_id == 'run'))
        await session.commit()

    await bus.touch(['run'])

    async with db.get_session_factory()() as session:
        row = (await session.execute(select(db.WSWorkerSubscriptionModel).where(
            db.WSWorkerSubscriptionModel.execution_id == 'run'))).scalar_one()
    assert row.process_generation == coordinator.owner.generation
    assert row.subscriber_count == 1


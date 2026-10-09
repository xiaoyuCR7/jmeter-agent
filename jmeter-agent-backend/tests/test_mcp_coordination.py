"""MCP POST messages can reach the process that owns the SSE stream."""
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
async def mcp_coordination(tmp_path, monkeypatch):
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.coordination import Coordinator
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    path = tmp_path / 'mcp.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    coordinator = Coordinator(path, capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    monkeypatch.setattr(operation_runtime, 'agent_operations', AgentOperationRuntime(coordinator))
    async with factory() as session:
        first = db.UserModel(username='alice', role='operator', password_hash='fixture')
        second = db.UserModel(username='bob', role='operator', password_hash='fixture')
        session.add_all([first, second]); await session.flush()
        alice = UserInfo(username='alice', role='operator', user_id=first.id)
        bob = UserInfo(username='bob', role='operator', user_id=second.id)
        await session.commit()
    try:
        yield path, coordinator, alice, bob
    finally:
        coordinator.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_server_post_is_encrypted_routed_and_owner_bound(mcp_coordination):
    from app.services.mcp_mailbox import MCPMailbox
    from app.services.mcp_server import MCPServer, MCPTransportError
    path, coordinator, alice, bob = mcp_coordination
    owner_server, other_server = MCPServer(), MCPServer()
    mailbox = MCPMailbox(coordinator)
    session = owner_server.create_session(alice.username)
    await mailbox.register(session, alice)
    body = json.dumps({'jsonrpc': '2.0', 'id': 'secret-id', 'method': 'ping'}).encode()
    with pytest.raises(MCPTransportError) as denied:
        await mailbox.submit(session.id, bob, body)
    assert denied.value.status_code == 404
    assert await mailbox.submit(session.id, alice, body)
    assert b'secret-id' not in path.read_bytes()
    assert await mailbox.drain(session) == 1
    result = await asyncio.wait_for(session.queue.get(), 1)
    assert result == {'jsonrpc': '2.0', 'id': 'secret-id', 'result': {}}
    await owner_server.close_session(session)
    with pytest.raises(MCPTransportError) as closed:
        await mailbox.submit(session.id, alice, body)
    assert closed.value.status_code in (404, 409)


@pytest.mark.asyncio
async def test_real_process_posts_to_sse_owner_mailbox(mcp_coordination, tmp_path):
    from app.services.mcp_mailbox import MCPMailbox
    from app.services.mcp_server import MCPServer
    path, coordinator, alice, _ = mcp_coordination
    server, mailbox = MCPServer(), MCPMailbox(coordinator)
    session = server.create_session(alice.username)
    await mailbox.register(session, alice)
    helper = tmp_path / 'worker.py'
    helper.write_bytes(Path(__file__).with_name('cross_feature_worker.py').read_bytes())
    result_path, request_path = tmp_path / 'result.json', tmp_path / 'request.json'
    request_path.write_text(json.dumps({
        'mode': 'mcp', 'database': str(path), 'session_id': session.id,
        'owner': {'username': alice.username, 'role': alice.role, 'user_id': alice.user_id},
        'payload': json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'ping'}),
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
    proof = json.loads(result_path.read_text(encoding='utf-8'))
    assert proof['pid'] != os.getpid() and proof['sqlite'] == sqlite3.sqlite_version
    assert await mailbox.drain(session) == 1
    assert (await asyncio.wait_for(session.queue.get(), 1))['id'] == 7
    await server.close_session(session)


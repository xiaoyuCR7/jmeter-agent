"""WebSocket lifecycle tests without a server or real network."""
import asyncio
from unittest.mock import AsyncMock

import pytest

import app.services.ws_manager as wsmod


@pytest.mark.asyncio
async def test_heartbeat_timeout_disconnects_without_lock_recursion(monkeypatch):
    monkeypatch.setattr(wsmod, 'HEARTBEAT_INTERVAL', 0)
    manager = wsmod.WSManager()
    ws = AsyncMock()
    manager._channels['test'] = {ws}
    manager._last_pong[id(ws)] = -1000
    await asyncio.wait_for(manager._heartbeat(ws, 'test'), timeout=0.2)
    assert 'test' not in manager._channels
    assert not manager._lock.locked()


@pytest.mark.asyncio
async def test_broadcast_does_not_hold_global_lock_while_sending():
    manager = wsmod.WSManager()
    ws = AsyncMock()

    async def send(text):
        assert not manager._lock.locked()

    ws.send_text.side_effect = send
    manager._channels['test'] = {ws}
    await manager.broadcast('test', {'type': 'progress'})
    assert ws in manager._channels.get('test', set())


@pytest.mark.asyncio
async def test_manager_background_tasks_restart_after_stop():
    manager = wsmod.WSManager()
    manager.start()
    await asyncio.sleep(0)
    await manager.stop()

    manager.start()
    await asyncio.sleep(0)
    assert manager._cleanup_task is not None
    assert not manager._cleanup_task.done()
    await manager.stop()

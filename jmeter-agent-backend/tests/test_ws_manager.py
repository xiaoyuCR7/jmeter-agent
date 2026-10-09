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
async def test_concurrent_connect_recovers_when_first_cross_worker_subscribe_fails(monkeypatch):
    manager = wsmod.WSManager()
    monkeypatch.setattr(manager, '_authorized', AsyncMock(return_value=True))
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    class Bus:
        enabled = True

        def __init__(self):
            self.subscribe_calls = 0

        async def subscribe(self, execution_id):
            self.subscribe_calls += 1
            if self.subscribe_calls == 1:
                first_entered.set()
                await release_first.wait()
                raise RuntimeError('temporary coordination failure')

        async def unsubscribe(self, execution_id):
            return None

        async def close(self):
            return None

    manager._event_bus = Bus()
    first, second = AsyncMock(), AsyncMock()
    first_connect = asyncio.create_task(manager.connect('run', first))
    await first_entered.wait()
    second_connect = asyncio.create_task(manager.connect('run', second))
    await asyncio.sleep(0)
    release_first.set()
    outcomes = await asyncio.gather(first_connect, second_connect, return_exceptions=True)

    assert isinstance(outcomes[0], RuntimeError)
    assert outcomes[1] is None
    assert manager._event_bus.subscribe_calls == 2
    assert manager._channels['run'] == {second}
    await manager.disconnect('run', second)


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

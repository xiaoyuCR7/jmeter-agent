"""Local single-user mode: no login, but foreign origins and hosts stay blocked."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI


def guarded_app():
    from app.middleware.local_guard import OriginGuardMiddleware, TrustedHostMiddleware
    app = FastAPI()
    app.add_api_route('/api/v1/projects', lambda: {'ok': True}, methods=['GET', 'POST'])
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['test', 'localhost'])
    return app


@pytest.mark.asyncio
async def test_requests_need_no_credentials():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://test') as client:
        assert (await client.get('/api/v1/projects')).status_code == 200
        assert (await client.post('/api/v1/projects')).status_code == 200


@pytest.mark.asyncio
async def test_cross_site_writes_are_rejected_but_same_origin_allowed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://test') as client:
        assert (await client.post('/api/v1/projects', headers={'Origin': 'https://evil.invalid'})).status_code == 403
        assert (await client.post('/api/v1/projects', headers={'Origin': 'http://test'})).status_code == 200
        # Reads carry no side effects; the browser's CORS policy still hides the body.
        assert (await client.get('/api/v1/projects', headers={'Origin': 'https://evil.invalid'})).status_code == 200


@pytest.mark.asyncio
async def test_untrusted_host_is_rejected():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://rebind.invalid') as client:
        assert (await client.get('/api/v1/projects')).status_code == 400


@pytest.mark.asyncio
async def test_websocket_from_foreign_origin_is_closed():
    from app.middleware.local_guard import OriginGuardMiddleware
    target, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    guard = OriginGuardMiddleware(target)
    await guard({'type': 'websocket', 'path': '/ws/executions/x', 'scheme': 'ws', 'server': ('test', 80),
                 'headers': [(b'host', b'test'), (b'origin', b'https://evil.invalid')]}, receive, send)
    send.assert_awaited_once_with({'type': 'websocket.close', 'code': 1008})
    target.assert_not_awaited()


def test_non_loopback_binding_is_refused(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'HOST', '0.0.0.0')
    with pytest.raises(RuntimeError, match='loopback'):
        config.validate_deployment_security()
    monkeypatch.setattr(config.settings, 'HOST', '127.0.0.1')
    config.validate_deployment_security()


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [True, False])
async def test_confirmation_is_decided_once_with_exact_arguments(approved):
    from app.services.confirmations import ConfirmationManager
    manager = ConfirmationManager(timeout_seconds=5)
    request = asyncio.create_task(manager.request('execute_test', {'jmx_path': 'a.jmx'}))
    while not manager.list_pending():
        await asyncio.sleep(0)
    pending = manager.list_pending()
    assert pending[0]['tool'] == 'execute_test' and pending[0]['arguments'] == {'jmx_path': 'a.jmx'}
    manager.decide(pending[0]['id'], approved)
    assert await request is approved
    with pytest.raises(KeyError):
        manager.decide(pending[0]['id'], True)
    assert manager.list_pending() == []


@pytest.mark.asyncio
async def test_confirmation_timeout_is_denial_and_leaves_nothing_pending():
    from app.services.confirmations import ConfirmationManager
    manager = ConfirmationManager(timeout_seconds=0.01)
    assert await manager.request('execute_test', {}) is False
    assert manager.list_pending() == []

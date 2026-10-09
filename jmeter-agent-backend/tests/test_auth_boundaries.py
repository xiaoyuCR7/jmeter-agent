"""Authentication contracts against a temporary database; no real credentials."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.time import utc_now_naive


def _legacy_hs256_token(payload: dict, secret: str) -> str:
    """Build the standard compact token emitted by the former jose dependency."""
    def encoded(value):
        raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    signing_input = encoded({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encoded(payload)
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + '.' + base64.urlsafe_b64encode(signature).rstrip(b'=').decode()


def test_decoder_accepts_existing_hs256_access_tokens(monkeypatch):
    from app.services import auth
    secret = 'legacy-compatibility-secret-at-least-32-bytes'
    monkeypatch.setattr(auth.settings, 'JWT_SECRET', secret)
    monkeypatch.setattr(auth.settings, 'JWT_ALGORITHM', 'HS256')
    token = _legacy_hs256_token({
        'exp': 4102444800,
        'iat': 1704067200,
        'role': 'operator',
        'sid': 'session-hash',
        'sub': 'legacy-user',
        'type': 'access',
    }, secret)

    assert auth.decode_token(token) == {
        'exp': 4102444800,
        'iat': 1704067200,
        'role': 'operator',
        'sid': 'session-hash',
        'sub': 'legacy-user',
        'type': 'access',
    }


@pytest_asyncio.fixture
async def auth_db(tmp_path, monkeypatch):
    from app.models.db import Base, UserModel
    from app.services import auth
    from app.services.db_store import db_store
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'auth.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        return factory()
    monkeypatch.setattr(db_store, '_session', session)
    monkeypatch.setattr(auth, 'verify_password', lambda plain, hashed: plain == hashed)
    async with factory() as session:
        session.add_all([UserModel(username=role, role=role, password_hash='test-only')
                         for role in ['admin', 'operator', 'viewer']])
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_login_refresh_rotation_and_logout_use_existing_schema(auth_db):
    from app.services import auth
    result = await auth.login('operator', 'test-only')
    assert result and result.access_token
    rotated = await auth.refresh_access_token(result.refresh_token)
    assert rotated and rotated.refresh_token != result.refresh_token
    assert await auth.refresh_access_token(result.refresh_token) is None
    await auth.logout(rotated.refresh_token)
    assert await auth.refresh_access_token(rotated.refresh_token) is None
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=rotated.access_token))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_access_checks_current_role_and_disabled_user(auth_db):
    from app.models.db import UserModel
    from app.services import auth
    result = await auth.login('admin', 'test-only')
    credentials = HTTPAuthorizationCredentials(scheme='Bearer', credentials=result.access_token)
    async with auth_db() as session:
        user = (await session.execute(select(UserModel).where(UserModel.username == 'admin'))).scalar_one()
        user.role = 'viewer'
        await session.commit()
    current = await auth.get_current_user(credentials)
    assert current.role == 'viewer'
    assert current.user_id == user.id
    async with auth_db() as session:
        user = (await session.execute(select(UserModel).where(UserModel.username == 'admin'))).scalar_one()
        user.is_active = False
        await session.commit()
    with pytest.raises(HTTPException):
        await auth.get_current_user(credentials)


@pytest.mark.asyncio
async def test_expired_refresh_token_cannot_authenticate(auth_db):
    from app.services import auth
    from app.services.db_store import db_store
    await db_store.store_refresh_token('operator', auth._hash_token('expired'), utc_now_naive() - timedelta(seconds=1))
    assert await auth.refresh_access_token('expired') is None


def guarded_app():
    from app.api.auth_routes import router
    from app.middleware.authentication import AuthenticationMiddleware
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    for path in ['/api/v1/projects', '/api/v1/system/paths', '/api/v1/mcp/messages', '/reports/run/result.jtl']:
        app.add_api_route(path, lambda: {'called': True}, methods=['GET', 'POST', 'PUT'])
    app.add_api_route('/health', lambda: {'status': 'healthy'})
    app.add_middleware(AuthenticationMiddleware)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('role, expected', [(None, 401), ('viewer', 403), ('operator', 404), ('admin', 404)])
async def test_registered_analysis_retains_operator_authorization_before_evidence_lookup(auth_db, monkeypatch, stream, role, expected):
    from unittest.mock import AsyncMock, Mock
    from app.api import routes
    from app.services.auth import login
    from app.middleware.authentication import AuthenticationMiddleware
    from app.services.ai.agents.analysis_agent import AnalysisExecutionError
    resolve = AsyncMock(side_effect=AnalysisExecutionError('Missing fixture execution', 404))
    monkeypatch.setattr(routes.AnalysisAgent, 'resolve_execution', resolve)
    factory = Mock(side_effect=AssertionError('No model should be created'))
    monkeypatch.setattr(routes.AIAdapterFactory, 'create', factory)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    app.add_middleware(AuthenticationMiddleware)
    headers = {'Authorization': 'Bearer ' + (await login(role, 'test-only')).access_token} if role else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers) as client:
        response = await client.post('/api/v1/agent/run' + ('/stream' if stream else ''),
            json={'agent_type': 'analysis', 'execution_id': 'run-A', 'task': 'task'})
    assert response.status_code == expected
    assert resolve.await_count == (1 if role in ('admin', 'operator') else 0)
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_production_app_rejects_untrusted_host_even_with_matching_origin():
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://rebind.invalid') as client:
        response = await client.post('/api/v1/auth/login', headers={'Origin': 'http://rebind.invalid'},
                                     json={'username': 'not-a-user', 'password': 'not-a-password'})
        assert response.status_code == 400
        assert (await client.get('/health')).status_code == 400
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
        assert (await client.get('/health')).status_code == 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://[::1]:8000') as client:
        assert (await client.get('/health')).status_code == 200


@pytest.mark.asyncio
async def test_invalid_host_websocket_denial_uses_websocket_protocol():
    from unittest.mock import AsyncMock
    from app.middleware.authentication import TrustedHostMiddleware
    target, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    guard = TrustedHostMiddleware(target, ['localhost'])
    await guard({'type': 'websocket', 'headers': [(b'host', b'evil.invalid')]}, receive, send)
    send.assert_awaited_once_with({'type': 'websocket.close', 'code': 1008})
    target.assert_not_awaited()


def test_new_passwords_use_bcrypt_byte_limit_not_character_limit():
    from pydantic import ValidationError
    from app.api.auth_routes import CreateUserRequest, ChangePasswordRequest
    for model, payload in [(CreateUserRequest, {'username': 'new', 'password': '汉' * 25}),
                           (ChangePasswordRequest, {'old_password': 'old', 'new_password': '汉' * 25})]:
        with pytest.raises(ValidationError):
            model(**payload)


def test_public_debug_binding_cannot_use_default_credentials(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'DEBUG', True)
    monkeypatch.setattr(config.settings, 'HOST', '0.0.0.0')
    monkeypatch.setattr(config.settings, 'JWT_SECRET', 'change-me-in-production-use-env-var')
    with pytest.raises(RuntimeError, match='JWT_SECRET'):
        config.validate_deployment_security()


@pytest.mark.asyncio
async def test_all_business_surfaces_require_authentication(auth_db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://test') as client:
        for path in ['/api/v1/projects', '/api/v1/system/paths', '/api/v1/mcp/messages', '/reports/run/result.jtl']:
            assert (await client.get(path)).status_code == 401
        assert (await client.get('/health')).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('role,write,config', [('viewer', 403, 403), ('operator', 200, 403), ('admin', 200, 200)])
async def test_server_enforces_read_write_and_admin_roles(auth_db, role, write, config):
    from app.services.auth import login
    tokens = await login(role, 'test-only')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://test',
                                headers={'Authorization': 'Bearer ' + tokens.access_token}) as client:
        assert (await client.get('/api/v1/projects')).status_code == 200
        assert (await client.post('/api/v1/projects')).status_code == write
        assert (await client.put('/api/v1/system/paths')).status_code == config


@pytest.mark.asyncio
async def test_cookie_login_supports_reports_and_blocks_cross_origin_writes(auth_db):
    from app.models.db import ProjectModel, ExecutionModel
    async with auth_db() as session:
        session.add(ProjectModel(id='shared-report-project', name='Shared'))
        await session.flush()
        session.add(ExecutionModel(id='run', project_id='shared-report-project', status='completed'))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded_app()), base_url='http://test') as client:
        response = await client.post('/api/v1/auth/login', json={'username': 'operator', 'password': 'test-only'})
        assert response.status_code == 200
        assert any('HttpOnly' in cookie and 'SameSite=strict' in cookie for cookie in response.headers.get_list('set-cookie'))
        assert (await client.get('/reports/run/result.jtl')).status_code == 200
        assert (await client.get('/api/v1/auth/me')).json()['username'] == 'operator'
        assert (await client.post('/api/v1/projects', headers={'Origin': 'https://untrusted.invalid'})).status_code == 403
        assert (await client.post('/api/v1/projects', headers={'Origin': 'http://test'})).status_code == 200
        assert (await client.post('/api/v1/auth/refresh')).status_code == 200
        assert (await client.post('/api/v1/auth/logout')).status_code == 200
        assert (await client.get('/api/v1/projects')).status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_has_single_concurrent_consumer(auth_db):
    import asyncio
    from app.services import auth
    token = (await auth.login('operator', 'test-only')).refresh_token
    results = await asyncio.gather(auth.refresh_access_token(token), auth.refresh_access_token(token))
    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_logout_of_rotated_token_revokes_its_replacement(auth_db):
    from app.services import auth
    original = await auth.login('operator', 'test-only')
    replacement = await auth.refresh_access_token(original.refresh_token)
    # Simulates logout arriving before the browser receives the refresh response.
    await auth.logout(original.refresh_token)
    assert await auth.refresh_access_token(replacement.refresh_token) is None
    with pytest.raises(HTTPException):
        await auth.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=replacement.access_token))


@pytest.mark.asyncio
async def test_sse_stops_and_closes_generator_after_logout(auth_db):
    from app.services import auth
    from app.services.security_context import current_access_token
    from app.services.authenticated_stream import AuthenticatedStreamingResponse
    tokens = await auth.login('operator', 'test-only')
    closed = []
    async def events():
        try:
            yield 'first'
            await auth.logout(tokens.refresh_token)
            yield 'must-not-leak'
        finally:
            closed.append(True)
    marker = current_access_token.set(tokens.access_token)
    try:
        response = AuthenticatedStreamingResponse(events(), media_type='text/event-stream')
        chunks = [chunk async for chunk in response.body_iterator]
        assert chunks == ['first']
        assert closed == [True]
    finally:
        current_access_token.reset(marker)


@pytest.mark.asyncio
async def test_sse_send_failure_closes_source_before_return():
    import asyncio
    from app.services.authenticated_stream import AuthenticatedStreamingResponse
    closed = []
    async def events():
        try:
            yield 'first'
            yield 'second'
        finally:
            closed.append(True)
    async def receive():
        await asyncio.Event().wait()
    async def send(message):
        if message['type'] == 'http.response.body':
            raise OSError('client disconnected')
    response = AuthenticatedStreamingResponse(events(), media_type='text/event-stream')
    try:
        await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    except Exception:
        pass
    assert closed == [True]


@pytest.mark.asyncio
async def test_websocket_revocation_blocks_next_broadcast(auth_db):
    from unittest.mock import AsyncMock
    from fastapi.security import HTTPAuthorizationCredentials
    from app.models import db
    from app.services import auth
    from app.services.ws_manager import WSManager
    tokens = await auth.login('operator', 'test-only')
    user = await auth.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=tokens.access_token))
    async with auth_db() as session:
        session.add(db.ProjectModel(id='ws-project', name='Fixture'))
        await session.flush()
        session.add(db.ExecutionModel(id='run', project_id='ws-project'))
        await session.commit()
    class Socket:
        scope = {'state': {'auth_token': tokens.access_token, 'auth_user': user}}
        accept = AsyncMock()
        send_text = AsyncMock()
        close = AsyncMock()
    ws = Socket()
    manager = WSManager()
    await manager.connect('run', ws)
    await manager.broadcast('run', {'test': 'visible'})
    assert ws.send_text.await_count == 1
    await auth.logout(tokens.refresh_token)
    await manager.broadcast('run', {'test': 'must-not-leak'})
    assert ws.send_text.await_count == 1
    ws.close.assert_awaited_once_with(code=1008)
    assert manager.get_channel_count() == {}
    await manager.stop()

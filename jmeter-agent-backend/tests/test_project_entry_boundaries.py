"""Authorization is refreshed at HTTP, model, tool, stream and session boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException


def acl_scope(revision=0):
    from app.services.project_scope import AccessScope
    auth = SimpleNamespace(user_id=501, revision=revision, incarnation='account-A',
        session_key=(501, 'operator', revision, 'account-A'),
        project_modes={'mine': 'private', 'hidden': 'private'},
        allows=lambda pid, action='read': pid == 'mine')
    return AccessScope(auth, executions={'visible-run': {'project_id': 'mine'},
                                         'hidden-run': {'project_id': 'hidden'}})


@pytest.mark.asyncio
async def test_project_http_binds_scope_and_static_reports_check_owner(monkeypatch):
    from app.middleware import authentication
    from app.services import project_scope
    from app.services.auth import UserInfo
    monkeypatch.setattr(authentication, 'get_current_user', AsyncMock(return_value=
        UserInfo(username='fixture', role='operator', user_id=501)))
    monkeypatch.setattr(project_scope, 'load_user_scope', AsyncMock(return_value=acl_scope()))
    monkeypatch.setattr(project_scope, 'recheck_scope', AsyncMock(return_value=acl_scope()))
    from app.services import auth
    monkeypatch.setattr(auth, 'get_current_user', authentication.get_current_user)
    app = FastAPI()
    @app.get('/api/v1/check')
    async def check():
        return {'key': list(project_scope.current_scope().session_key)}
    @app.get('/reports/{execution_id}/index.html')
    async def report(execution_id: str):
        return {'content': 'private report'}
    app.add_middleware(authentication.AuthenticationMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
            headers={'Authorization': 'Bearer fixture-token'}) as client:
        assert (await client.get('/api/v1/check')).json()['key'][0] == 501
        assert (await client.get('/reports/visible-run/index.html')).status_code == 200
        assert (await client.get('/reports/hidden-run/index.html')).status_code == 404
        assert (await client.get('/reports/unknown/index.html')).status_code == 404


@pytest.mark.asyncio
async def test_project_model_preflight_denies_before_sdk_and_discards_revoked_response(monkeypatch):
    from app.services import project_scope
    from app.services.ai.adapters.base import managed_request
    from contextlib import asynccontextmanager
    sdk = AsyncMock(return_value='secret output')
    class Adapter:
        @asynccontextmanager
        async def _operation(self):
            yield
        @managed_request
        async def chat(self):
            return await sdk()
    check = AsyncMock(side_effect=HTTPException(403, 'revoked'))
    monkeypatch.setattr(project_scope, 'refresh_scope', check)
    with pytest.raises(HTTPException):
        await Adapter().chat()
    sdk.assert_not_awaited()
    check.side_effect = [None, HTTPException(403, 'revoked')]
    with pytest.raises(HTTPException):
        await Adapter().chat()
    sdk.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_tool_rechecks_after_confirmation_before_side_effect(monkeypatch):
    from app.services import project_scope
    from app.services.confirmations import confirmations
    from app.services.ai.agent.tool_registry import Tool, ToolRegistry
    handler = AsyncMock(return_value={'secret': 'value'})
    registry = ToolRegistry()
    registry.register(Tool('write', 'write', {'type': 'object'}, handler, require_confirmation=True))
    check = AsyncMock(side_effect=[None, HTTPException(403, 'revoked')])
    monkeypatch.setattr(project_scope, 'refresh_scope', check)
    monkeypatch.setattr(confirmations, 'request', AsyncMock(return_value=True))
    result = await registry.execute('write', {})
    assert not result.success
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_stream_does_not_emit_revoked_queued_content(monkeypatch):
    from app.services import project_scope
    from app.services.authenticated_stream import AuthenticatedStreamingResponse
    monkeypatch.setattr(project_scope, 'recheck_scope', AsyncMock(side_effect=HTTPException(403, 'revoked')))
    async def source():
        yield 'secret queued content'
    response = AuthenticatedStreamingResponse(source())
    assert [chunk async for chunk in response.body_iterator] == []


def test_project_session_namespace_changes_on_revocation_and_account_recreation():
    from app.services.project_scope import bind_scope
    from app.services.security_context import current_principal
    from app.services.auth import UserInfo
    from app.services.ai.agent.base import BaseAgent
    token = current_principal.set(UserInfo(username='fixture', user_id=501, role='operator'))
    try:
        with bind_scope(acl_scope(0)):
            first = BaseAgent('agent', None, session_id='browser-session').session_id
        with bind_scope(acl_scope(1)):
            second = BaseAgent('agent', None, session_id='browser-session').session_id
        assert first.startswith('v3:') and first != second
    finally:
        current_principal.reset(token)

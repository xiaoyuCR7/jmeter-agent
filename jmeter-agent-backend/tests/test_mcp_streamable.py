"""Streamable HTTP MCP endpoint and the evidence toolset, without external services."""
import json

import httpx
import pytest
from fastapi import FastAPI

from app.services.mcp_server import MCPServer, SUPPORTED_PROTOCOL_VERSIONS


@pytest.fixture
def client(monkeypatch):
    from app.api import mcp_routes
    server = MCPServer()
    server.register_all_agent_tools()
    server.register_all_prompts()
    monkeypatch.setattr(mcp_routes, 'mcp_server', server)
    app = FastAPI()
    app.include_router(mcp_routes.router, prefix='/api/v1')
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


def rpc(method, req_id=1, params=None):
    body = {'jsonrpc': '2.0', 'method': method, **({'id': req_id} if req_id is not None else {})}
    if params is not None:
        body['params'] = params
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,expected', [('2025-06-18', '2025-06-18'), ('2024-11-05', '2024-11-05'),
                                                ('2099-01-01', SUPPORTED_PROTOCOL_VERSIONS[0])])
async def test_initialize_negotiates_protocol_version(client, requested, expected):
    async with client:
        response = await client.post('/api/v1/mcp', json=rpc('initialize', params={
            'protocolVersion': requested, 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}))
    assert response.status_code == 200
    result = response.json()['result']
    assert result['protocolVersion'] == expected
    assert 'get_execution_evidence' in result['instructions']


@pytest.mark.asyncio
async def test_tools_prompts_notifications_and_get(client):
    async with client:
        tools = (await client.post('/api/v1/mcp', json=rpc('tools/list'))).json()['result']['tools']
        names = {tool['name'] for tool in tools}
        assert {'get_execution_evidence', 'extract_performance_requirements', 'save_performance_requirements',
                'get_performance_requirements', 'promote_execution_to_baseline'} <= names
        prompt = (await client.post('/api/v1/mcp', json=rpc('prompts/get', params={
            'name': 'analyze-execution', 'arguments': {'execution_id': 'run-1'}}))).json()['result']
        assert 'get_execution_evidence' in prompt['messages'][0]['content']['text']
        assert (await client.post('/api/v1/mcp', json=rpc('notifications/initialized', req_id=None))).status_code == 202
        assert (await client.get('/api/v1/mcp')).status_code == 405
        bad = await client.post('/api/v1/mcp', content=b'{not json')
        assert bad.json()['error']['code'] == -32700


@pytest.mark.asyncio
async def test_evidence_tool_reports_missing_execution_without_crashing(client, monkeypatch):
    from app.services.storage import store
    monkeypatch.setattr(store, '_executions', {})
    async with client:
        result = (await client.post('/api/v1/mcp', json=rpc('tools/call', params={
            'name': 'get_execution_evidence', 'arguments': {'execution_id': 'nope'}}))).json()['result']
    assert result['isError'] is True
    assert 'Execution not found' in json.loads(result['content'][0]['text'])['error']

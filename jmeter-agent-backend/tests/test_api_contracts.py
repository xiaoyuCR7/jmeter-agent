"""HTTP contracts; ASGI transport never opens a network connection."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest


@pytest.fixture
def agent_api(monkeypatch):
    from fastapi import FastAPI
    from app.api import routes
    from app.services.ai.agent.types import AgentResult, AgentEvent
    from unittest.mock import Mock
    agent = SimpleNamespace(run=AsyncMock(return_value=AgentResult(True, 'task', output='legacy')),
        run_analysis=AsyncMock(return_value=AgentResult(True, 'task', output='registered',
            analysis={'execution_id': 'run-A', 'success': True})))
    async def legacy_stream(*args, **kwargs):
        yield AgentEvent('done', 'done', {'success': True})
    async def registered_stream(*args, **kwargs):
        yield AgentEvent('done', 'done', {'success': True, 'analysis': {'execution_id': 'run-A'}})
    agent.run_stream = Mock(side_effect=legacy_stream)
    agent.run_analysis_stream = Mock(side_effect=registered_stream)
    resolve = AsyncMock(return_value={'execution_id': 'run-A'})
    monkeypatch.setattr(routes.AnalysisAgent, 'resolve_execution', resolve, raising=False)
    constructor = Mock(return_value=agent)
    constructor.resolve_execution = resolve
    monkeypatch.setattr(routes, 'AnalysisAgent', constructor)
    factory = Mock(return_value=object())
    monkeypatch.setattr(routes.AIAdapterFactory, 'create', factory)
    app = FastAPI()
    app.include_router(routes.router)
    return app, agent, resolve, factory


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('registered', [False, True])
async def test_registered_analysis_http_sse_opt_in_and_legacy_contract(agent_api, stream, registered):
    import json
    app, agent, resolve, factory = agent_api
    payload = dict(agent_type='analysis', task='task', context='reference', session_id='session')
    if registered:
        payload['execution_id'] = 'run-A'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/agent/run' + ('/stream' if stream else ''), json=payload)
    assert response.status_code == 200
    result = json.loads(response.text.removeprefix('data: '))['data'] if stream else response.json()
    assert ('analysis' in result) is registered
    if registered:
        resolve.assert_awaited_once_with('run-A')
        method = agent.run_analysis_stream if stream else agent.run_analysis
        method.assert_called_once_with('task', {'execution_id': 'run-A'}, context='reference')
        agent.run.assert_not_called()
        agent.run_stream.assert_not_called()
    else:
        resolve.assert_not_called()
        if not stream:
            assert set(result) == {'agent_type', 'task', 'success', 'output', 'plan_steps',
                                   'iterations', 'error', 'duration_seconds'}


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('status', [400, 404, 409])
async def test_registered_analysis_rejection_precedes_model_and_stream_headers(agent_api, stream, status):
    from app.services.ai.agents.analysis_agent import AnalysisExecutionError
    app, agent, resolve, factory = agent_api
    resolve.side_effect = AnalysisExecutionError('Evidence unavailable', status)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/agent/run' + ('/stream' if stream else ''),
            json=dict(agent_type='analysis', task='task', execution_id='run-A'))
    assert response.status_code == status
    assert response.json() == {'detail': 'Evidence unavailable'}
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('extra', [dict(agent_type='workflow', execution_id='run-A'),
    dict(agent_type='test_plan', execution_id='run-A'), dict(execution_id=''), dict(execution_id='x' * 51)])
async def test_registered_analysis_rejects_ambiguous_or_empty_contract(agent_api, stream, extra):
    app, _, resolve, factory = agent_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/agent/run' + ('/stream' if stream else ''),
                                     json=dict(agent_type='analysis', task='task') | extra)
    assert response.status_code == 422
    factory.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, -1])
async def test_rag_api_rejects_nonpositive_limits(limit):
    from fastapi import FastAPI
    from app.api import routes
    app = FastAPI()
    app.get('/documents')(routes.list_rag_documents)
    app.get('/search')(routes.search_rag)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        assert (await client.get('/documents', params={'limit': limit})).status_code == 422
        assert (await client.get('/search', params={'q': 'fixture', 'limit': limit})).status_code == 422


@pytest.mark.asyncio
async def test_baseline_accepts_metrics_in_json_and_identity_in_query(monkeypatch):
    from fastapi import FastAPI
    from app.api import baseline_routes
    app = FastAPI()
    app.include_router(baseline_routes.router)
    create = AsyncMock(side_effect=lambda data: data)
    monkeypatch.setattr(baseline_routes.db_store, 'create_baseline', create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/baselines', params={'project_id': 'p', 'name': 'baseline'},
            json={'thresholds': {'p99_max': 42}, 'metrics_snapshot': {'throughput': 5}})
    assert response.status_code == 200
    assert response.json()['thresholds'] == {'p99_max': 42}
    assert response.json()['metrics_snapshot'] == {'throughput': 5}



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
    app.dependency_overrides[baseline_routes.require_auth] = lambda: None
    create = AsyncMock(side_effect=lambda data: data)
    monkeypatch.setattr(baseline_routes.db_store, 'create_baseline', create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/baselines', params={'project_id': 'p', 'name': 'baseline'},
            json={'thresholds': {'p99_max': 42}, 'metrics_snapshot': {'throughput': 5}})
    assert response.status_code == 200
    assert response.json()['thresholds'] == {'p99_max': 42}
    assert response.json()['metrics_snapshot'] == {'throughput': 5}


@pytest.mark.asyncio
async def test_analysis_routes_registered_and_cost_returns_real_contract():
    from app.main import app
    paths = {route.path for route in app.routes}
    for name in ('anomalies', 'correlate', 'estimate-cost', 'drift', 'compare-versions'):
        assert '/api/v1/analysis/' + name in paths


@pytest.mark.asyncio
async def test_analysis_missing_execution_and_drift_are_not_fake_success(monkeypatch):
    from fastapi import FastAPI
    from app.api import analysis_routes
    app = FastAPI()
    app.include_router(analysis_routes.router)
    monkeypatch.setattr(analysis_routes.store, '_executions', {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        missing = await client.post('/analysis/anomalies', params={'execution_id': 'missing'})
        drift = await client.post('/analysis/drift', params={'project_id': 'empty'})
    assert missing.status_code == 404
    assert drift.status_code == 200 and drift.json()['health'] == 'unknown'


@pytest.mark.asyncio
async def test_drift_history_is_project_scoped(monkeypatch):
    from app.api import analysis_routes
    from app.models.schemas import DetailedTestSummary
    summary = DetailedTestSummary(total_requests=10, success_count=10, error_count=0, error_rate=0,
        avg_response_time=100, min_response_time=1, max_response_time=200, throughput=1,
        p50_response_time=100, p90_response_time=100, p95_response_time=100, p99_response_time=100,
        received_bytes_per_sec=0, sent_bytes_per_sec=0)
    executions = {p: SimpleNamespace(id=p, project_id=p, status='completed', summary=summary,
        test_plan_id=p, start_time=datetime.now()) for p in ('allowed', 'unrelated')}
    monkeypatch.setattr(analysis_routes.store, '_executions', executions)
    result = await analysis_routes.analyze_drift(project_id='allowed', days=30, execution_id=None)
    assert result['data_points'] == 1 and result['health'] == 'unknown'


@pytest.mark.asyncio
async def test_drift_http_and_tool_agree_on_mixed_timezones_and_cutoff(monkeypatch):
    from datetime import timedelta, timezone
    from fastapi import FastAPI
    from app.api import analysis_routes
    from app.services.ai.agent.tools.resource import detect_performance_drift

    now = datetime.now(timezone.utc)
    east = timezone(timedelta(hours=8))
    west = timezone(timedelta(hours=-5))
    dates = [(now - timedelta(days=3)).astimezone().replace(tzinfo=None),
             (now - timedelta(days=2)).astimezone(east),
             (now - timedelta(days=1)).astimezone(west),
             (now - timedelta(days=30, hours=2)).astimezone(east),
             (now - timedelta(days=30, hours=2)).astimezone().replace(tzinfo=None)]
    executions = {str(i): SimpleNamespace(
        id=str(i), project_id='allowed', test_plan_id='plan', status='completed', start_time=date,
        summary=SimpleNamespace(total_requests=10, p99_response_time=100 * (i + 1),
            avg_response_time=100, error_rate=0, throughput=10)) for i, date in enumerate(dates)}
    monkeypatch.setattr(analysis_routes.store, '_executions', executions)
    app = FastAPI()
    app.include_router(analysis_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/analysis/drift', params={'project_id': 'allowed', 'days': 30})
    assert response.status_code == 200
    assert response.json() == await detect_performance_drift(project_id='allowed', days=30)
    assert response.json()['data_points'] == 3
    assert response.json()['slope_per_day']['p99_response_time'] == pytest.approx(100)
    assert response.json()['health'] == 'degrading'
    assert [execution.start_time for execution in executions.values()] == dates

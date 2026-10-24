"""AutoTune/SelfHeal outcomes with temporary plans and fully fake execution."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from app.services.ai.agents.autotune_agent import (AutoTuneAgent, AutoTuneConfig, AutoTuneIteration,
    OptimizationGoal, OptimizationStrategy)
from app.services.ai.agents.selfheal_agent import SelfHealAgent


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['autotune', 'selfheal'])
async def test_auxiliary_agent_budget_fails_before_model_and_preserves_fallback(monkeypatch, kind):
    from app.core.config import settings
    from app.services.ai.adapters.deepseek_adapter import DeepSeekAdapter
    from app.services.ai.adapters.factory import AIAdapterFactory
    adapter = object.__new__(DeepSeekAdapter)
    adapter.max_tokens = 8000
    adapter.chat = AsyncMock(return_value='[]')
    monkeypatch.setattr(settings, 'AGENT_CONTEXT_TOKEN_BUDGET', 400)
    monkeypatch.setattr(AIAdapterFactory, 'create', lambda: adapter)
    if kind == 'selfheal':
        assert await SelfHealAgent()._llm_diagnose([], [], 'fixture log') == []
    else:
        agent = AutoTuneAgent()
        current = {'concurrency': 10}
        params = [SimpleNamespace(name='concurrency', min_value=1, max_value=20)]
        result = await agent._ask_llm(adapter, 'fixture task', current, params, {'concurrency': 6})
        assert result == {'concurrency': 8}
    adapter.chat.assert_not_awaited()


@pytest.fixture
def plan(tmp_path, monkeypatch):
    import subprocess
    from app.core.config import settings
    from app.services import execution_lifecycle
    from app.services.jmeter.jmx_generator import JMXGenerator
    from app.models.schemas import APIEndpoint, TestConfig, ExtractionRule
    def forbidden(*args, **kwargs):
        raise AssertionError('No real JMeter, subprocess or LLM in outcome tests')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(execution_lifecycle, 'get_executor', forbidden)
    monkeypatch.setattr(settings, 'TEST_PLANS_DIR_DEFAULT', str(tmp_path / 'plans'))
    monkeypatch.setattr(settings, 'REPORTS_DIR_DEFAULT', str(tmp_path / 'reports'))
    monkeypatch.setattr('app.core.config.load_paths_config', lambda: {})
    from app.services.ai.adapters.factory import AIAdapterFactory
    monkeypatch.setattr(AIAdapterFactory, 'create', forbidden)
    path = tmp_path / 'plans' / 'fixture.jmx'
    path.parent.mkdir()
    path.write_text(JMXGenerator().generate_basic_jmx([APIEndpoint(name='auth', method='GET', path='/',
        extractions=[ExtractionRule(ref_name='token', extract_type='json_path', expression='$.old.token')])],
        TestConfig(test_type='baseline'), 'https://fixture.invalid'), encoding='utf-8')
    report = tmp_path / 'reports' / 'failed'
    report.mkdir(parents=True)
    (report / 'result.jtl').write_text('timeStamp,elapsed,label,responseCode,success\n1,10,auth,401,false\n')
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled', 'completed'])
async def test_autotune_missing_successful_report_is_iteration_error(plan, monkeypatch, status):
    from app.services import execution_lifecycle
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', AsyncMock(return_value=SimpleNamespace(
        status=SimpleNamespace(value=status), report_dir=None, error_message='fixture process error')))
    result = await AutoTuneAgent()._run_iteration(str(plan), {'concurrency': 5}, AutoTuneConfig(cooldown_between_runs=0), 1)
    assert result.error and not result.constraint_met


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy', [OptimizationStrategy.BINARY_SEARCH, OptimizationStrategy.LINEAR_SCAN])
async def test_no_accepted_candidate_is_not_optimization_success(plan, monkeypatch, strategy):
    agent = AutoTuneAgent()
    monkeypatch.setattr(agent, '_run_iteration', AsyncMock(return_value=AutoTuneIteration(1, 5, 1, 1,
        metrics={'total_requests': 1, 'p99_response_time': 999}, constraint_met=False)))
    result = await agent.optimize(str(plan), AutoTuneConfig(strategy=strategy, max_iterations=1, cooldown_between_runs=0))
    assert not result.success and result.optimal_concurrency == 0
    assert 'candidate' in result.error.lower()


@pytest.mark.parametrize('operator,actual,expected', [('lt', 5, False), ('lte', 5, True),
    ('gt', 5, False), ('gte', 5, True)])
def test_autotune_constraint_operators(operator, actual, expected):
    config = AutoTuneConfig(constraint_operator=operator, constraint_value=5)
    assert AutoTuneAgent()._evaluate_constraint({'p99_response_time': actual}, config)[1] is expected


@pytest.mark.parametrize('metric,operator', [(float('nan'), 'lt'), (None, 'lt'), (2, 'unknown')])
def test_autotune_missing_nonfinite_or_unknown_constraint_fails(metric, operator):
    with pytest.raises(ValueError):
        AutoTuneAgent()._evaluate_constraint({'p99_response_time': metric}, AutoTuneConfig(constraint_operator=operator))


@pytest.mark.asyncio
async def test_unsupported_goal_fails_before_any_execution(plan, monkeypatch):
    agent = AutoTuneAgent()
    run = AsyncMock()
    monkeypatch.setattr(agent, '_run_iteration', run)
    result = await agent.optimize(str(plan), AutoTuneConfig(goal=OptimizationGoal.MIN_LATENCY))
    assert not result.success and 'unsupported' in result.error.lower()
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_failure_is_visible_and_backup_retained(plan, monkeypatch):
    agent = AutoTuneAgent()
    backup = plan.with_suffix('.backup')
    backup.write_bytes(plan.read_bytes())
    async def search(*args):
        agent._backup_path = str(backup)
        return agent._success_result(AutoTuneConfig(), 5, {'total_requests': 1}, 'fixture', [])
    monkeypatch.setattr(agent, '_binary_search', search)
    monkeypatch.setattr(agent, '_restore_jmx', lambda *args: False)
    result = await agent.optimize(str(plan))
    assert not result.success and 'restore' in result.error.lower()
    assert str(backup) in result.error and backup.exists()


@pytest.mark.asyncio
async def test_selfheal_parses_the_jtl_off_the_event_loop(plan, monkeypatch):
    import threading
    from app.services.jmeter.report_parser import ReportParser
    threads = []
    def parse(self, path):
        threads.append(threading.current_thread())
        return ReportParser()._empty_summary()
    monkeypatch.setattr(ReportParser, 'parse_jtl', parse)
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_llm_diagnose', AsyncMock(return_value=[]))
    await agent.diagnose_and_fix(str(plan), 'failed', auto_re_run=False)
    assert threads and threading.current_thread() not in threads


@pytest.mark.asyncio
async def test_selfheal_without_response_evidence_never_edits(plan, monkeypatch):
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_llm_diagnose', AsyncMock(return_value=[]))
    before = plan.read_bytes()
    result = await agent.diagnose_and_fix(str(plan), 'failed', auto_re_run=False)
    assert not result.success
    assert plan.read_bytes() == before
    assert not result.applied and not result.verified


@pytest.mark.asyncio
@pytest.mark.parametrize('status,count,errors', [('failed', 0, 0), ('cancelled', 0, 0),
    ('completed', 0, 0), ('completed', 1, 1), ('completed', 1, 0)])
async def test_selfheal_verifies_real_samples_and_cleared_errors(plan, monkeypatch, status, count, errors):
    from app.services import execution_lifecycle
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_extract_failed_response_bodies', lambda path: [
        {'response_code': '401', 'label': 'auth', 'response_body': '{"new":{"token":"abcdefghijk"}}'}])
    execution = SimpleNamespace(status=SimpleNamespace(value=status), error_message='fixture',
        summary=SimpleNamespace(total_requests=count, error_count=errors, error_rate=100 if errors else 0))
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', AsyncMock(return_value=execution))
    result = await agent.diagnose_and_fix(str(plan), 'failed')
    expected = status == 'completed' and count > 0 and errors == 0
    assert result.applied and result.verified is expected
    assert result.success is expected and result.re_run_success is expected
    if not expected:
        assert result.error


@pytest.mark.asyncio
async def test_agents_reject_unsafe_paths_and_execution_ids(plan, monkeypatch):
    original = plan.read_bytes()
    result = await SelfHealAgent().diagnose_and_fix(str(plan), '../escape')
    assert not result.success and result.error
    outside = plan.parent.parent / 'outside.jmx'
    outside.write_bytes(original)
    agent = AutoTuneAgent()
    run = AsyncMock()
    monkeypatch.setattr(agent, '_run_iteration', run)
    result = await agent.optimize(str(outside))
    assert not result.success and result.error
    run.assert_not_awaited()
    assert outside.read_bytes() == original


@pytest.mark.asyncio
async def test_llm_strategy_keeps_a_measured_candidate_at_minimum(plan, monkeypatch):
    from app.services.ai.adapters.factory import AIAdapterFactory
    monkeypatch.setattr(AIAdapterFactory, 'create', lambda: SimpleNamespace(chat=AsyncMock()))
    agent = AutoTuneAgent()
    monkeypatch.setattr(agent, '_run_iteration', AsyncMock(return_value=AutoTuneIteration(1, 5, 1, 1,
        metrics={'total_requests': 1, 'p99_response_time': 2}, constraint_met=True)))
    monkeypatch.setattr(agent, '_ask_llm', AsyncMock(return_value={'concurrency': 5}))
    result = await agent.optimize(str(plan), AutoTuneConfig(strategy=OptimizationStrategy.LLM_GUIDED,
        min_concurrency=5, max_concurrency=5, max_iterations=1, cooldown_between_runs=0))
    assert result.success and result.optimal_concurrency == 5 and result.optimal_metrics


@pytest.mark.asyncio
async def test_selfheal_unverified_llm_suggestion_does_not_write(plan, monkeypatch):
    from app.services.ai.agents.selfheal_agent import ExtractionFix
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_extract_failed_response_bodies', lambda path: [
        {'response_code': '401', 'label': 'auth', 'response_body': '{"message":"denied"}'}])
    monkeypatch.setattr(agent, '_llm_diagnose', AsyncMock(return_value=[
        ExtractionFix('$.old.token', '$.missing.token', 'token', .9, 'llm')]))
    before = plan.read_bytes()
    result = await agent.diagnose_and_fix(str(plan), 'failed', auto_re_run=False)
    assert not result.success and not result.applied and result.suggestions
    assert plan.read_bytes() == before


@pytest.mark.asyncio
async def test_selfheal_rerun_exception_is_not_success(plan, monkeypatch):
    from app.services import execution_lifecycle
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_extract_failed_response_bodies', lambda path: [
        {'response_code': '401', 'label': 'auth', 'response_body': '{"new":{"token":"abcdefghijk"}}'}])
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', AsyncMock(side_effect=RuntimeError('fixture exception')))
    result = await agent.diagnose_and_fix(str(plan), 'failed')
    assert result.applied and not result.verified and not result.success
    assert 'fixture exception' in result.error


@pytest.mark.asyncio
async def test_selfheal_unknown_variable_is_a_suggestion_not_an_applied_fix(plan, monkeypatch):
    from app.services.ai.agents.selfheal_agent import ExtractionFix
    agent = SelfHealAgent()
    monkeypatch.setattr(agent, '_extract_failed_response_bodies', lambda path: [
        {'response_code': '401', 'label': 'auth', 'response_body': '{"value":"abcdefghijk"}'}])
    monkeypatch.setattr(agent, '_llm_diagnose', AsyncMock(return_value=[
        ExtractionFix('$.old', '$.value', 'unknown', .9, 'llm')]))
    before = plan.read_bytes()
    result = await agent.diagnose_and_fix(str(plan), 'failed', auto_re_run=False)
    assert not result.applied and not result.success and result.suggestions
    assert plan.read_bytes() == before


@pytest.mark.asyncio
async def test_linear_scan_honors_max_iterations(plan, monkeypatch):
    agent = AutoTuneAgent()
    fake = AsyncMock(return_value=AutoTuneIteration(1, 5, 1, 1,
        metrics={'total_requests': 1}, constraint_met=True, constraint_value=0))
    monkeypatch.setattr(agent, '_run_iteration', fake)
    await agent.optimize(str(plan), AutoTuneConfig(strategy=OptimizationStrategy.LINEAR_SCAN,
        min_concurrency=5, max_concurrency=1000, max_iterations=2, cooldown_between_runs=0))
    assert fake.await_count <= 2


@pytest.mark.asyncio
async def test_empty_measured_report_is_not_a_valid_iteration(plan, monkeypatch):
    from app.services import execution_lifecycle
    from app.services.jmeter.report_parser import ReportParser
    report_dir = plan.parent.parent / 'reports' / 'failed'
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', AsyncMock(return_value=SimpleNamespace(
        status=SimpleNamespace(value='completed'), report_dir=str(report_dir), summary=ReportParser()._empty_summary())))
    result = await AutoTuneAgent()._run_iteration(str(plan), {'concurrency': 5}, AutoTuneConfig(cooldown_between_runs=0), 1)
    assert result.error and 'samples' in result.error


@pytest.mark.asyncio
async def test_autotune_api_preserves_constraint_operator(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from app.api import routes
    captured = []
    async def optimize(self, jmx_path, config):
        captured.append(config)
        return SimpleNamespace(model_dump=lambda: {'success': False, 'error': 'fixture'})
    monkeypatch.setattr(AutoTuneAgent, 'optimize', optimize)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/agent/autotune', json={'jmx_path': 'fixture.jmx', 'constraint_operator': 'gte'})
    assert response.status_code == 200
    assert captured[0].constraint_operator == 'gte'


@pytest.mark.asyncio
async def test_max_concurrency_goal_requires_a_concurrency_dimension(plan, monkeypatch):
    from app.services.ai.agents.autotune_agent import TunableParameter
    agent = AutoTuneAgent()
    run = AsyncMock()
    monkeypatch.setattr(agent, '_run_iteration', run)
    result = await agent.optimize(str(plan), AutoTuneConfig(strategy=OptimizationStrategy.LLM_GUIDED,
        tunable_params=[TunableParameter('think_time', 1, 10)]))
    assert not result.success and 'requires concurrency' in result.error
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_optimization_restores_original_bytes(plan, monkeypatch):
    from app.services import execution_lifecycle
    original = plan.read_bytes()
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', AsyncMock(return_value=SimpleNamespace(
        status=SimpleNamespace(value='failed'), report_dir=None, error_message='fixture failed')))
    result = await AutoTuneAgent().optimize(str(plan), AutoTuneConfig(max_iterations=1, cooldown_between_runs=0))
    assert not result.success
    assert plan.read_bytes() == original


@pytest.mark.asyncio
async def test_applied_without_rerun_is_unverified_and_keeps_existing_backup(plan, monkeypatch):
    agent = SelfHealAgent()
    legacy_backup = Path(str(plan) + '.bak')
    legacy_backup.write_bytes(b'preserve user backup')
    monkeypatch.setattr(agent, '_extract_failed_response_bodies', lambda path: [
        {'response_code': '401', 'label': 'auth', 'response_body': '{"new":{"token":"abcdefghijk"}}'}])
    result = await agent.diagnose_and_fix(str(plan), 'failed', auto_re_run=False)
    assert result.applied and not result.verified and not result.test_re_run
    assert legacy_backup.read_bytes() == b'preserve user backup'
    assert list(plan.parent.glob('*.selfheal.*.bak'))

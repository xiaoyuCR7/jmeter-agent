"""AutoTune must not change or restore JMX after project write access is lost."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException


@pytest.fixture
def tune_env(tmp_path, monkeypatch):
    from app.core import config
    from app.services import project_scope, execution_lifecycle
    from app.services.jmeter import jmx_parser, jmx_builder
    from app.services.ai.agents import autotune_agent
    plans = tmp_path / 'plans'
    project = plans / 'project'
    project.mkdir(parents=True)
    path = project / 'source.jmx'
    path.write_text('ORIGINAL', encoding='utf-8')
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(plans))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'no-paths.json'))
    state = SimpleNamespace(permitted=True, writable=True, readable=True, backup_readable=True,
                            backup_writable=True, refreshes=0, on_refresh=None, checks=[])
    async def refresh():
        state.refreshes += 1
        if state.on_refresh:
            state.on_refresh()
        state.writable = state.permitted
    def require_path(value, *, write=False, **kwargs):
        target = Path(value)
        state.checks.append((target, write))
        if not state.readable or ('.autotune.' in str(target) and not state.backup_readable and not write):
            raise HTTPException(404, 'Project file not found')
        if write and (not state.writable or ('.autotune.' in str(target) and not state.backup_writable)):
            raise HTTPException(403, 'Project write permission required')
    monkeypatch.setattr(project_scope, 'refresh_scope', AsyncMock(side_effect=refresh))
    monkeypatch.setattr(project_scope, 'require_path', require_path)
    parser = Mock(return_value={'children': [{'testclass': 'ThreadGroup',
        'properties': {'ThreadGroup.num_threads': '1'}}]})
    monkeypatch.setattr(jmx_parser, 'parse_jmx', parser)
    monkeypatch.setattr(jmx_builder, 'build_jmx', Mock(return_value='REBUILT'))
    execute = AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value='failed'),
                                                    report_dir=None, error_message='fixture failure'))
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', execute)
    copy = Mock(wraps=autotune_agent.shutil.copy2)
    monkeypatch.setattr(autotune_agent.shutil, 'copy2', copy)
    return SimpleNamespace(path=path, state=state, execute=execute, parser=parser, copy=copy)


def tune_config(**kwargs):
    from app.services.ai.agents.autotune_agent import AutoTuneConfig
    return AutoTuneConfig(max_iterations=kwargs.pop('max_iterations', 2), cooldown_between_runs=0, **kwargs)


@pytest.mark.asyncio
async def test_autotune_reader_cannot_backup_modify_or_execute(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    tune_env.state.permitted = False  # Refresh must observe current policy, not stale write scope.
    result = await AutoTuneAgent().optimize(str(tune_env.path), tune_config())
    assert not result.success
    assert tune_env.path.read_text() == 'ORIGINAL'
    tune_env.copy.assert_not_called()
    tune_env.parser.assert_not_called()
    tune_env.execute.assert_not_awaited()
    assert list(tune_env.path.parent.glob('*.bak')) == []


@pytest.mark.asyncio
async def test_iteration_direct_call_requires_write_before_backup(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    tune_env.state.permitted = False
    try:
        result = await AutoTuneAgent()._run_iteration(str(tune_env.path), {'concurrency': 4}, tune_config(), 1)
    except HTTPException as error:
        assert error.status_code == 403
    else:
        assert result.error
    tune_env.copy.assert_not_called()
    tune_env.execute.assert_not_awaited()
    assert tune_env.path.read_text() == 'ORIGINAL'


@pytest.mark.asyncio
async def test_iteration_rechecks_after_optimize_entry_before_first_write(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    def revoke():
        if tune_env.state.refreshes >= 2:
            tune_env.state.permitted = False
    tune_env.state.on_refresh = revoke
    result = await AutoTuneAgent().optimize(str(tune_env.path), tune_config(max_iterations=1))
    assert not result.success
    tune_env.copy.assert_not_called()
    tune_env.execute.assert_not_awaited()
    assert tune_env.path.read_text() == 'ORIGINAL'


@pytest.mark.asyncio
async def test_revocation_between_iterations_blocks_next_write_and_blind_restore(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    async def execute(*args, **kwargs):
        tune_env.state.permitted = False
        tune_env.path.write_text('OTHER EDIT', encoding='utf-8')
        return SimpleNamespace(status=SimpleNamespace(value='failed'), report_dir=None, error_message='fixture failure')
    tune_env.execute.side_effect = execute
    agent = AutoTuneAgent()
    result = await agent.optimize(str(tune_env.path), tune_config())
    assert not result.success
    assert tune_env.execute.await_count == 1
    assert tune_env.path.read_text() == 'OTHER EDIT'
    assert 'restore' in result.error.lower() and 'backup retained' in result.error.lower()
    assert Path(agent._backup_path).read_text() == 'ORIGINAL'
    assert tune_env.copy.call_count == 1


@pytest.mark.parametrize('denial', ['source', 'destination'])
def test_backup_authorizes_source_read_and_destination_write(tune_env, denial):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    if denial == 'source':
        tune_env.state.readable = False
    else:
        tune_env.state.backup_writable = False
    with pytest.raises(HTTPException):
        AutoTuneAgent()._backup_jmx(str(tune_env.path))
    tune_env.copy.assert_not_called()
    assert list(tune_env.path.parent.glob('*.bak')) == []


@pytest.mark.parametrize('denial', ['source', 'destination'])
def test_restore_denial_keeps_backup_and_current_jmx(tune_env, denial):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    agent = AutoTuneAgent()
    backup = agent._backup_jmx(str(tune_env.path))
    tune_env.copy.reset_mock()
    tune_env.path.write_text('OTHER EDIT')
    if denial == 'source':
        tune_env.state.backup_readable = False
    else:
        tune_env.state.writable = False
    assert not agent._restore_jmx(str(tune_env.path), backup)
    assert tune_env.path.read_text() == 'OTHER EDIT'
    assert Path(backup).read_text() == 'ORIGINAL'
    tune_env.copy.assert_not_called()


@pytest.mark.asyncio
async def test_restore_refresh_failure_is_reported_without_overwriting(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    def unavailable():
        if tune_env.state.refreshes >= 3:
            raise HTTPException(503, 'Authorization unavailable')
    tune_env.state.on_refresh = unavailable
    async def execute(*args, **kwargs):
        tune_env.path.write_text('OTHER EDIT')
        return SimpleNamespace(status=SimpleNamespace(value='failed'), report_dir=None, error_message='fixture failure')
    tune_env.execute.side_effect = execute
    agent = AutoTuneAgent()
    result = await agent.optimize(str(tune_env.path), tune_config(max_iterations=1))
    assert not result.success
    assert 'restore' in result.error.lower() and 'backup retained' in result.error.lower()
    assert tune_env.path.read_text() == 'OTHER EDIT'
    assert Path(agent._backup_path).read_text() == 'ORIGINAL'


@pytest.mark.asyncio
async def test_authorized_tuning_restores_original_content(tune_env):
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    agent = AutoTuneAgent()
    await agent.optimize(str(tune_env.path), tune_config(max_iterations=1))
    assert tune_env.execute.await_count == 1
    assert tune_env.path.read_text() == 'ORIGINAL'
    assert Path(agent._backup_path).read_text() == 'ORIGINAL'


@pytest.mark.asyncio
@pytest.mark.parametrize('writable', [False, True])
async def test_real_scope_operator_respects_project_role_and_registered_backup(tmp_path, monkeypatch, writable):
    from app.core import config
    from app.services import project_scope, execution_lifecycle
    from app.services.project_access import AccessSnapshot, ProjectPermissions
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    from app.services.jmeter import jmx_parser, jmx_builder
    plans = tmp_path / 'plans'
    plans.mkdir()
    path = plans / 'legacy-flat.jmx'
    path.write_text('ORIGINAL')
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(plans))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'no-paths.json'))
    authorization = AccessSnapshot(user_id=987002, global_role='operator', revision=0,
        incarnation='autotune-test', projects={'project': ProjectPermissions(read=True, write=writable)},
        project_modes={'project': 'private'})
    scope = project_scope.AccessScope(authorization,
        plans={'plan': {'project_id': 'project', 'jmx_path': str(path)}})
    monkeypatch.setattr(project_scope, 'refresh_scope', AsyncMock(return_value=scope))
    parser = Mock(return_value={'children': []})
    monkeypatch.setattr(jmx_parser, 'parse_jmx', parser)
    monkeypatch.setattr(jmx_builder, 'build_jmx', Mock(return_value='REBUILT'))
    execute = AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value='failed'),
                                                    report_dir=None, error_message='fixture failure'))
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', execute)
    agent = AutoTuneAgent()
    with project_scope.bind_scope(scope):
        result = await agent.optimize(str(path), tune_config(max_iterations=1))
    assert not result.success
    assert path.read_text() == 'ORIGINAL'
    if writable:
        execute.assert_awaited_once()
        assert Path(agent._backup_path).read_text() == 'ORIGINAL'
    else:
        parser.assert_not_called()
        execute.assert_not_awaited()
        assert list(plans.glob('*.bak')) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('revoked', [False, True])
async def test_cancelled_optimization_rechecks_restore_permission(tune_env, revoked, caplog):
    import asyncio
    from app.services.ai.agents.autotune_agent import AutoTuneAgent
    async def cancel(*args, **kwargs):
        tune_env.state.permitted = not revoked
        tune_env.path.write_text('OTHER EDIT')
        raise asyncio.CancelledError()
    tune_env.execute.side_effect = cancel
    agent = AutoTuneAgent()
    with pytest.raises(asyncio.CancelledError):
        await agent.optimize(str(tune_env.path), tune_config(max_iterations=1))
    assert tune_env.path.read_text() == ('OTHER EDIT' if revoked else 'ORIGINAL')
    assert Path(agent._backup_path).read_text() == 'ORIGINAL'
    if revoked:
        assert 'Failed to restore original JMX; backup retained' in caplog.text


@pytest.mark.asyncio
async def test_llm_decision_refreshes_read_access_before_sending_metrics(tune_env, monkeypatch):
    from app.services import project_scope
    from app.services.ai.adapters.factory import AIAdapterFactory
    from app.services.ai.agents.autotune_agent import AutoTuneAgent, AutoTuneIteration, OptimizationStrategy
    def revoke():
        if tune_env.state.refreshes >= 2:
            tune_env.state.readable = False
    tune_env.state.on_refresh = revoke
    monkeypatch.setattr(AIAdapterFactory, 'create', Mock(return_value=object()))
    agent = AutoTuneAgent()
    monkeypatch.setattr(agent, '_run_iteration', AsyncMock(return_value=AutoTuneIteration(
        iteration=1, concurrent_users=5, ramp_up=1, duration=1, constraint_met=True,
        metrics={'p99_response_time': 42, 'error_rate': 0, 'throughput': 10})))
    ask = AsyncMock(return_value={'concurrency': 5})
    monkeypatch.setattr(agent, '_ask_llm', ask)
    result = await agent.optimize(str(tune_env.path), tune_config(
        max_iterations=1, strategy=OptimizationStrategy.LLM_GUIDED))
    ask.assert_not_awaited()
    assert not result.success
    assert project_scope.refresh_scope.await_count >= 2

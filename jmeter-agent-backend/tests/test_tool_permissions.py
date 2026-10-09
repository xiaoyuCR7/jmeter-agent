"""Dangerous calls must wait for the owning user's server-side decision."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from app.services.ai.agent.tool_registry import Tool, ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize('args', [{'path': 'x', 'confirmed': True}, {'path': 3}, {}, {'path': 'x', 'skip_resource_check': True}])
async def test_invalid_or_undeclared_arguments_never_reach_handler(args):
    handler = AsyncMock(return_value={'ok': True})
    registry = ToolRegistry()
    registry.register(Tool('test', 'test', {'type': 'object', 'properties': {'path': {'type': 'string'}},
                                           'required': ['path']}, handler))
    result = await registry.execute('test', args)
    assert not result.success
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dangerous_tool_without_authenticated_owner_is_denied():
    handler = AsyncMock(return_value={})
    registry = ToolRegistry()
    registry.register(Tool('danger', 'danger', {}, handler, require_confirmation=True))
    assert not (await registry.execute('danger', {})).success
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('approve', [True, False])
async def test_confirmation_binds_owner_and_exact_arguments(approve):
    from app.services.auth import UserInfo
    from app.services.confirmations import confirmations
    from app.services.security_context import current_principal
    owner = UserInfo(username='alice', role='operator')
    token = current_principal.set(owner)
    handler = AsyncMock(return_value={'ok': True})
    registry = ToolRegistry()
    registry.register(Tool('danger', 'danger', {'properties': {'path': {'type': 'string'}}}, handler,
                           require_confirmation=True))
    args = {'path': 'original'}
    task = asyncio.create_task(registry.execute('danger', args))
    try:
        for _ in range(10):
            await asyncio.sleep(0)
            pending = await confirmations.list_pending(owner)
            if pending:
                break
        assert len(pending) == 1
        handler.assert_not_awaited()
        with pytest.raises(PermissionError):
            await confirmations.decide(pending[0]['id'], 'bob', True)
        args['path'] = 'changed-after-request'
        await confirmations.decide(pending[0]['id'], owner, approve)
        result = await task
        assert result.success is approve
        if approve:
            handler.assert_awaited_once_with(path='original')
        else:
            handler.assert_not_awaited()
        assert await confirmations.list_pending(owner) == []
        with pytest.raises(KeyError):
            await confirmations.decide(pending[0]['id'], owner, True)
    finally:
        task.cancel()
        current_principal.reset(token)


@pytest.mark.asyncio
async def test_confirmation_timeout_cannot_execute_or_leak_pending_request():
    from app.services.auth import UserInfo
    from app.services.confirmations import ConfirmationManager
    manager = ConfirmationManager(timeout_seconds=0.001)
    assert not await manager.request(UserInfo(username='alice', role='operator'), 'test', {})
    assert await manager.list_pending('alice') == []


@pytest.mark.asyncio
async def test_mcp_uses_same_parameter_and_business_failure_contract():
    import json
    from app.services.mcp_server import MCPServer
    handler = AsyncMock(return_value={'success': False, 'error': None})
    server = MCPServer()
    server.register_tool('test', 'test', {'type': 'object', 'properties': {}}, handler)
    invalid = await server.handle_request(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                                     'params': {'name': 'test', 'arguments': {'bypass': True}}}))
    assert invalid['error']['code'] == -32602
    handler.assert_not_awaited()
    failed = await server.handle_request(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                                    'params': {'name': 'test', 'arguments': {}}}))
    assert failed['result']['isError']


def test_general_tool_paths_cannot_read_backend_configuration(tmp_path, monkeypatch):
    from app.core.config import safe_path, settings
    monkeypatch.setattr(settings, 'BASE_DIR', str(tmp_path))
    with pytest.raises(ValueError, match='Access denied'):
        safe_path(str(tmp_path / 'ai_models_config.json'))


@pytest.mark.asyncio
async def test_file_mutations_cannot_change_execution_evidence(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.ai.agent.tools import file
    reports = tmp_path / 'reports'; reports.mkdir()
    plan = tmp_path / 'plans'; plan.mkdir()
    monkeypatch.setattr(settings, 'REPORTS_DIR_DEFAULT', str(reports))
    monkeypatch.setattr(settings, 'TEST_PLANS_DIR_DEFAULT', str(plan))
    # Exercise both old static-root and new dynamic-root implementations.
    monkeypatch.setattr(file, '_ALLOWED_ROOTS', {str(reports), str(plan)}, raising=False)
    snapshot = reports / 'snapshot.jmx'; snapshot.write_text('original')
    assert 'error' in await file.write_file(str(snapshot), 'changed')
    assert 'error' in await file.delete_file(str(snapshot))
    assert snapshot.read_text() == 'original'
    assert (await file.write_file(str(plan / 'editable.jmx'), 'valid'))['written']


def test_file_path_resolves_links_before_checking_root(tmp_path, monkeypatch):
    from app.services.ai.agent.tools import file
    from app.core.config import settings
    root = tmp_path / 'plans'; root.mkdir()
    secret = tmp_path / 'outside.txt'; secret.write_text('do-not-read')
    monkeypatch.setattr(settings, 'TEST_PLANS_DIR_DEFAULT', str(root))
    monkeypatch.setattr(file, '_ALLOWED_ROOTS', {str(root)}, raising=False)
    # Simulate an OS-resolved link, without requiring Windows symlink privileges.
    realpath = file.os.path.realpath
    monkeypatch.setattr(file.os.path, 'realpath', lambda path: str(secret) if str(path) == str(root / 'link') else realpath(path))
    with pytest.raises(ValueError, match='Access denied'):
        file._resolve_path(str(root / 'link'))


def test_composite_execution_tools_require_owner_confirmation():
    from app.services.ai.agent.tools.resource import resource_tools
    tools = {tool.name: tool for tool in resource_tools}
    assert tools['selfheal_test'].require_confirmation
    assert tools['import_openapi_spec'].require_confirmation


@pytest.mark.asyncio
async def test_analysis_paths_are_checked_before_parser_access(tmp_path, monkeypatch):
    from app.services.ai.agent.tools import resource
    from app.services.jmeter.report_parser import ReportParser
    from unittest.mock import Mock
    parser = Mock(side_effect=AssertionError('must not open untrusted path'))
    monkeypatch.setattr(ReportParser, 'parse_jtl', parser)
    result = await resource.evaluate_sla(str(tmp_path / 'outside.jtl'))
    assert 'Access denied' in result['error']
    parser.assert_not_called()
    with pytest.raises(ValueError):
        await resource.detect_anomalies('../outside')


@pytest.mark.asyncio
async def test_resource_wait_rejects_unbounded_or_busy_loop_parameters():
    from app.services.ai.agent.tools import resource
    with pytest.raises(ValueError):
        await resource.start_resource_monitoring(0)
    with pytest.raises(ValueError):
        await resource.wait_for_resources(float('inf'))


@pytest.mark.asyncio
async def test_file_read_bounds_single_long_line_and_rejects_zero_limit(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.ai.agent.tools import file
    monkeypatch.setattr(settings, 'TEST_PLANS_DIR_DEFAULT', str(tmp_path))
    path = tmp_path / 'large.txt'; path.write_text('a' * 100000)
    result = await file.read_file(str(path))
    assert result['truncated']
    assert len(result['content']) <= 65536
    assert 'error' in await file.read_file(str(path), max_lines=0)


@pytest.mark.asyncio
async def test_fact_analysis_rejects_outside_jtl_before_parsing(tmp_path, monkeypatch):
    from app.services.ai.agents.analysis_agent import AnalysisAgent
    from app.services.ai.agents import analysis_agent
    outside = tmp_path / 'outside.jtl'; outside.write_text('untrusted')
    compute = AsyncMock()
    monkeypatch.setattr(analysis_agent.fact_ledger, 'compute_from_jtl', compute)
    with pytest.raises(ValueError, match='Access denied'):
        await object.__new__(AnalysisAgent)._build_fact_sheet(str(outside), 'safe-id', None, 'api', 'v1', 'load')
    compute.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_execution_cannot_claim_a_different_jtl_as_its_evidence(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.db_store import db_store
    from app.services.ai.agents.analysis_agent import AnalysisAgent
    monkeypatch.setattr(settings, 'REPORTS_DIR_DEFAULT', str(tmp_path))
    monkeypatch.setattr(db_store, 'get_execution', AsyncMock(return_value={'report_dir': str(tmp_path / 'registered')}))
    with pytest.raises(ValueError, match='registered execution'):
        await object.__new__(AnalysisAgent)._build_fact_sheet(
            str(tmp_path / 'different.jtl'), 'registered', None, 'api', 'v1', 'load')

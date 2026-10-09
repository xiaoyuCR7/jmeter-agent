"""Durable request receipts, using only isolated SQLite and fake operations."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def receipt_env(tmp_path, monkeypatch):
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.security_context import current_principal
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'receipts.db'))
    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    await db.init_db()
    await db.init_db()  # additive and repeatable
    async with factory() as session:
        users = [db.UserModel(username=name, role='operator', password_hash='fixture')
                 for name in ('alice', 'bob')]
        session.add_all(users)
        await session.commit()
        user_ids = {user.username: user.id for user in users}
    def principal_info(**values):
        values.setdefault('user_id', user_ids.get(values.get('username')))
        return UserInfo(**values)
    token = current_principal.set(principal_info(username='alice', role='operator'))
    yield SimpleNamespace(factory=factory, principal=current_principal, UserInfo=principal_info)
    current_principal.reset(token)
    await engine.dispose()


def request(**changes):
    from app.models.schemas import AgentRunRequest
    return AgentRunRequest(**(dict(agent_type='analysis', execution_id='run-A',
        request_id='request-A', task='analyze', context='private context', session_id='session') | changes))


@pytest.mark.asyncio
async def test_receipt_concurrent_claim_has_one_winner_and_conflicts_do_not_reclaim(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    services = [AnalysisReceipts(), AnalysisReceipts()]
    claims = await asyncio.gather(*(services[i % 2].claim(request()) for i in range(6)))
    assert sum(handle is not None for handle, _ in claims) == 1
    with pytest.raises(ReceiptError) as error:
        await services[0].claim(request(task='different'))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_receipt_finished_replay_excludes_private_request_and_survives_restart(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    operation = AsyncMock(return_value=AgentResult(True, 'analyze', output='report', analysis={'success': True}))
    result = await service.run(handle, operation)
    assert result.receipt['state'] == 'succeeded'
    restored = AnalysisReceipts()
    replay_handle, replay = await restored.claim(request(stream=True))
    assert replay_handle is None and replay['result']['output'] == 'report'
    assert replay['state'] == 'succeeded'
    assert 'task' not in replay['result'] and 'context' not in replay['result']
    assert 'private context' not in str(replay)
    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_owner_isolation_and_unresolved_restart_never_reexecutes(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts()
    handle, first = await service.claim(request())
    restarted = AnalysisReceipts()
    assert (await restarted.get('request-A'))['state'] == 'unknown'
    duplicate, _ = await restarted.claim(request())
    assert duplicate is None
    # Another process reading must not overwrite the actual owner's live row.
    assert (await service.get('request-A'))['state'] == 'running'
    receipt_env.principal.set(receipt_env.UserInfo(username='bob', role='operator'))
    with pytest.raises(ReceiptError) as error:
        await service.get('request-A')
    assert error.value.status_code == 404
    other, _ = await service.claim(request())
    assert other is not None and other.owner_id != handle.owner_id


@pytest.mark.asyncio
async def test_receipt_requires_active_authenticated_operator(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts()
    for principal in (None, receipt_env.UserInfo(username='missing', role='operator'),
                      receipt_env.UserInfo(username='alice', role='viewer')):
        receipt_env.principal.set(principal)
        with pytest.raises(ReceiptError) as error:
            await service.claim(request())
        assert error.value.status_code in (401, 403)


@pytest.mark.asyncio
async def test_receipt_cancel_preserves_checkpoint_and_does_not_release_key(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, checkpoint
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    entered = asyncio.Event()
    async def operation():
        await checkpoint('archive', archive={'fact_archived': True, 'hypotheses_archived': ['h1']})
        entered.set()
        await asyncio.Future()
    runner = asyncio.create_task(service.run(handle, operation))
    await entered.wait()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    receipt = await service.get('request-A')
    assert receipt['state'] == 'cancelled'
    assert receipt['partial_effects_possible'] is True
    assert receipt['archive']['hypotheses_archived'] == ['h1']
    assert (await service.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_receipt_storage_failure_is_safe_and_never_starts_operation(receipt_env, monkeypatch):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.models import db
    service = AnalysisReceipts()
    def broken():
        raise RuntimeError('private database connection detail')
    monkeypatch.setattr(db, 'get_session_factory', broken)
    with pytest.raises(ReceiptError) as error:
        await service.claim(request())
    assert error.value.status_code == 503
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('changes', [dict(execution_id=None), dict(agent_type='workflow', execution_id=None),
    dict(request_id=''), dict(request_id='../bad'), dict(request_id='x' * 65)])
def test_receipt_request_requires_registered_analysis_and_safe_identity(changes):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request(**changes)


@pytest_asyncio.fixture
async def receipt_api(receipt_env, monkeypatch):
    from fastapi import FastAPI
    from unittest.mock import Mock
    from app.api import routes
    from app.services.ai.agents.analysis_agent import AnalysisAgent
    from app.services.ai.agent.types import AgentResult
    from app.services.ai import analysis_receipts as module
    service = module.AnalysisReceipts()
    monkeypatch.setattr(module, 'analysis_receipts', service)
    agent = AnalysisAgent(object())
    agent.run_analysis = AsyncMock(return_value=AgentResult(True, 'analyze', output='durable report',
        analysis={'success': True, 'execution_id': 'run-A'}))
    constructor = Mock(return_value=agent)
    constructor.resolve_execution = AsyncMock(return_value={'execution_id': 'run-A'})
    monkeypatch.setattr(routes, 'AnalysisAgent', constructor)
    factory = Mock(return_value=object())
    monkeypatch.setattr(routes.AIAdapterFactory, 'create', factory)
    app = FastAPI()
    app.include_router(routes.router)
    yield SimpleNamespace(app=app, service=service, agent=agent, factory=factory, resolve=constructor.resolve_execution)
    await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('first_stream', [False, True])
async def test_receipt_http_sse_share_result_without_repeating_work(receipt_api, first_stream):
    import httpx
    import json
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receipt_api.app), base_url='http://fixture') as client:
        payload = request().model_dump()
        endpoint = '/agent/run/stream' if first_stream else '/agent/run'
        first = await client.post(endpoint, json=payload)
        assert first.status_code == 200
        if first_stream:
            events = [json.loads(event[6:]) for event in first.text.strip().split('\n\n')]
            assert events[-1]['type'] == 'done'
            assert events[-1]['data']['receipt']['state'] == 'succeeded'
        else:
            assert first.json()['receipt']['state'] == 'succeeded'
        replay = await client.post('/agent/run', json=payload)
        assert replay.json()['output'] == 'durable report'
        stream = await client.post('/agent/run/stream', json=payload)
        assert len(stream.text.strip().split('\n\n')) == 1
        assert json.loads(stream.text[6:])['data']['receipt']['state'] == 'succeeded'
        fetched = await client.get('/agent/analysis-requests/request-A')
        assert fetched.status_code == 200 and fetched.json()['result']['output'] == 'durable report'
        assert fetched.headers['cache-control'] == 'no-store'
        conflict = await client.post('/agent/run', json=payload | {'task': 'different'})
        assert conflict.status_code == 409
    receipt_api.agent.run_analysis.assert_awaited_once()
    receipt_api.resolve.assert_awaited_once()
    receipt_api.factory.assert_called_once()


@pytest.mark.asyncio
async def test_receipt_in_progress_and_unowned_requests_do_not_start_agent(receipt_api, receipt_env):
    import httpx
    handle, _ = await receipt_api.service.claim(request())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receipt_api.app), base_url='http://fixture') as client:
        for path in ('/agent/run', '/agent/run/stream'):
            response = await client.post(path, json=request().model_dump())
            assert response.status_code == 202
            assert response.json()['receipt']['state'] == 'running'
            assert 'success' not in response.json()
        receipt_env.principal.set(receipt_env.UserInfo(username='bob', role='operator'))
        assert (await client.get('/agent/analysis-requests/request-A')).status_code == 404
    receipt_api.agent.run_analysis.assert_not_called()
    receipt_api.factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_receipt_commit_failure_never_publishes_success(receipt_api, monkeypatch, stream):
    import httpx
    from app.services.ai.analysis_receipts import ReceiptError
    monkeypatch.setattr(receipt_api.service, 'finish', AsyncMock(side_effect=ReceiptError()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receipt_api.app), base_url='http://fixture') as client:
        response = await client.post('/agent/run' + ('/stream' if stream else ''), json=request().model_dump())
    if stream:
        assert '"type": "done"' not in response.text
        assert '"type": "error"' in response.text
    else:
        assert response.status_code == 503
    receipt = await receipt_api.service.get('request-A')
    assert receipt['state'] == 'unknown' and receipt['result'] is None


@pytest.mark.asyncio
async def test_receipt_preflight_failure_is_not_retried(receipt_api):
    import httpx
    from app.services.ai.agents.analysis_agent import AnalysisExecutionError
    receipt_api.resolve.side_effect = AnalysisExecutionError('Execution not found', 404)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receipt_api.app), base_url='http://fixture') as client:
        first = await client.post('/agent/run', json=request().model_dump())
        second = await client.post('/agent/run', json=request().model_dump())
    assert first.status_code == 404 and second.status_code == 409
    assert second.json()['receipt']['state'] == 'failed'
    receipt_api.resolve.assert_awaited_once()
    receipt_api.factory.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_shutdown_cancels_live_work_without_reclaiming_key(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    entered = asyncio.Event()
    async def operation():
        entered.set()
        await asyncio.Future()
    task = asyncio.create_task(service.run(handle, operation))
    await entered.wait()
    await service.shutdown()
    await service.shutdown()
    assert task.cancelled()
    assert (await service.get('request-A'))['state'] == 'cancelled'
    service.start()
    assert (await service.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_receipt_user_deletion_remains_possible_and_recreated_user_cannot_read_old_report(receipt_env):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    await service.run(handle, AsyncMock(return_value=AgentResult(True, 'task', output='old private report')))
    async with receipt_env.factory() as session:
        await session.execute(delete(db.UserModel).where(db.UserModel.username == 'alice'))
        await session.commit()
        recreated = db.UserModel(username='alice', role='operator', password_hash='new user')
        session.add(recreated)
        await session.commit()
    receipt_env.principal.set(receipt_env.UserInfo(username='alice', role='operator', user_id=recreated.id))
    with pytest.raises(ReceiptError) as error:
        await service.get('request-A')
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['returned', 'failed', 'cancelled'])
async def test_receipt_abandoned_owner_is_unknown_even_without_process_restart(receipt_env, ending):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    service = AnalysisReceipts()
    claimed = asyncio.Event()
    async def owner():
        await service.claim(request())
        claimed.set()
        if ending == 'failed':
            raise RuntimeError('fixture owner failed')
        if ending == 'cancelled':
            await asyncio.Future()
    task = asyncio.create_task(owner())
    await asyncio.wait_for(claimed.wait(), 5)
    if ending == 'cancelled':
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # completed tasks dispatch callbacks on the next loop turn
    assert not service._active
    assert (await service.get('request-A'))['state'] == 'unknown'
    from app.models import db
    async with receipt_env.factory() as session:
        row = (await session.execute(select(db.AnalysisReceiptModel))).scalar_one()
        assert row.state == 'running' and row.phase == 'accepted'
    service.start()
    assert (await service.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_receipt_completed_claimant_does_not_remove_transferred_runner(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handles = asyncio.Queue()
    owner_release, entered, runner_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def owner():
        handle, _ = await service.claim(request())
        await handles.put(handle)
        await owner_release.wait()
    async def operation():
        entered.set()
        await runner_release.wait()
        return AgentResult(True, 'task', output='report')
    claimant = asyncio.create_task(owner())
    runner = None
    try:
        handle = await asyncio.wait_for(handles.get(), 5)
        runner = asyncio.create_task(service.run(handle, operation))
        await asyncio.wait_for(entered.wait(), 5)
        owner_release.set()
        await claimant
        await asyncio.sleep(0)  # allow the previous owner's completion callback
        assert service._active[handle] is runner
        assert (await service.get('request-A'))['state'] == 'running'
        with pytest.raises(RuntimeError, match='active requests'):
            service.start()
        runner_release.set()
        await runner
        assert not service._active
        assert (await service.get('request-A'))['state'] == 'succeeded'
        assert (await service.claim(request()))[0] is None
    finally:
        owner_release.set()
        runner_release.set()
        await asyncio.gather(claimant, *([runner] if runner is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_receipt_unstarted_stream_releases_finished_owner_without_reclaiming_key(receipt_api):
    from app.api import routes
    response = await asyncio.create_task(routes.agent_run_stream(request()))
    await asyncio.sleep(0)
    assert not receipt_api.service._active
    assert (await receipt_api.service.get('request-A'))['state'] == 'unknown'
    replay = await routes.agent_run_stream(request())
    assert replay.status_code == 409
    receipt_api.agent.run_analysis.assert_not_called()
    receipt_api.resolve.assert_awaited_once()
    receipt_api.factory.assert_called_once()
    await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_output', [float('nan'), 'oversized'])
async def test_receipt_unpersistable_result_is_unknown_and_never_reclaimed(receipt_env, monkeypatch, bad_output):
    from app.services.ai import analysis_receipts as module
    from app.services.ai.agent.types import AgentResult
    monkeypatch.setattr(module, 'MAX_RESULT_BYTES', 8)
    service = module.AnalysisReceipts()
    handle, _ = await service.claim(request())
    with pytest.raises(module.ReceiptError) as error:
        await service.run(handle, AsyncMock(return_value=AgentResult(True, 'task', output=bad_output)))
    assert error.value.status_code == 503
    assert (await service.get('request-A'))['state'] == 'unknown'
    assert (await service.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_receipt_terminal_state_cannot_be_overwritten(receipt_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    await service.run(handle, AsyncMock(return_value=AgentResult(False, 'task', error='model failed')))
    with pytest.raises(ReceiptError):
        await service.finish(handle, AgentResult(True, 'task', output='overwrite'))
    receipt = await service.get('request-A')
    assert receipt['state'] == 'failed' and receipt['result']['error'] == 'model failed'


@pytest.mark.asyncio
async def test_receipt_stream_busy_session_is_durable_failure(receipt_api):
    import httpx
    import json
    receipt_api.agent._run_active = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receipt_api.app), base_url='http://fixture') as client:
        response = await client.post('/agent/run/stream', json=request().model_dump())
    final = json.loads(response.text.strip().split('\n\n')[-1][6:])
    assert final['type'] == 'done'
    assert final['data']['success'] is False
    assert final['data']['receipt']['state'] == 'failed'
    receipt_api.agent.run_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_stream_close_before_runner_marks_cancelled_without_work(receipt_api):
    from app.api import routes
    response = await routes.agent_run_stream(request())
    iterator = response.body_iterator
    first = await anext(iterator)
    assert 'analysis_receipt' in first
    await iterator.aclose()
    assert (await receipt_api.service.get('request-A'))['state'] == 'cancelled'
    receipt_api.agent.run_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_stream_disconnect_cancels_worker_and_keeps_partial_checkpoint(receipt_api):
    from app.api import routes
    from app.services.ai.analysis_receipts import checkpoint
    from app.services.ai.agent.types import AgentResult
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def operation(*args, **kwargs):
        await checkpoint('model')
        receipt_api.agent._emit('tool_call', 'fixture event')
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    receipt_api.agent.run_analysis.side_effect = operation
    response = await routes.agent_run_stream(request())
    iterator = response.body_iterator
    await anext(iterator)  # reservation
    await anext(iterator)  # starts and observes actual worker
    assert entered.is_set()
    await iterator.aclose()
    assert stopped.is_set()
    receipt = await receipt_api.service.get('request-A')
    assert receipt['state'] == 'cancelled' and receipt['partial_effects_possible']
    assert receipt_api.agent._stream_runner is None


@pytest.mark.asyncio
async def test_receipt_final_commit_precedes_done_and_early_complete_is_not_published(receipt_api, monkeypatch):
    import json
    from app.api import routes
    from app.services.ai.agent.types import AgentResult
    entered, release = asyncio.Event(), asyncio.Event()
    original = receipt_api.service.finish
    async def finish(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(receipt_api.service, 'finish', finish)
    async def operation(*args, **kwargs):
        receipt_api.agent._emit('complete', 'model finished')
        return AgentResult(True, 'task', output='report')
    receipt_api.agent.run_analysis.side_effect = operation
    response = await routes.agent_run_stream(request())
    iterator = response.body_iterator
    try:
        await anext(iterator)
        progress = json.loads((await anext(iterator))[6:])
        assert progress['type'] == 'analysis_ready'
        await entered.wait()
        assert not (await receipt_api.service.get('request-A'))['result_available']
        release.set()
        final = json.loads((await anext(iterator))[6:])
        assert final['type'] == 'done'
        assert (await receipt_api.service.get('request-A'))['state'] == 'succeeded'
    finally:
        release.set()
        await iterator.aclose()


@pytest.mark.asyncio
async def test_receipt_ambiguous_commit_does_not_overwrite_committed_success(receipt_env, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    original = AsyncSession.commit
    async def committed_but_response_lost(session):
        await original(session)
        raise OSError('private connection detail')
    monkeypatch.setattr(AsyncSession, 'commit', committed_but_response_lost)
    with pytest.raises(ReceiptError):
        await service.run(handle, AsyncMock(return_value=AgentResult(True, 'task', output='committed report')))
    # The transaction actually committed; cancellation/error handling must not demote it.
    receipt = await service.get('request-A')
    assert receipt['state'] == 'succeeded' and receipt['result']['output'] == 'committed report'
    assert (await service.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_receipt_additive_initialization_preserves_project_and_final_receipt(receipt_env):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts
    from app.services.ai.agent.types import AgentResult
    async with receipt_env.factory() as session:
        session.add(db.ProjectModel(id='existing', name='keep', environment_data={'old': 'value'}))
        await session.commit()
    service = AnalysisReceipts()
    handle, _ = await service.claim(request())
    await service.run(handle, AsyncMock(return_value=AgentResult(True, 'task', output='saved')))
    await db.init_db()
    async with receipt_env.factory() as session:
        assert (await session.get(db.ProjectModel, 'existing')).environment_data == {'old': 'value'}
    assert (await service.get('request-A'))['result']['output'] == 'saved'

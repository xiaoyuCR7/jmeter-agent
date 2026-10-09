"""Agent admission and real thread drain against temporary durable ownership."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.requires_patched_sqlite


@pytest.fixture
def coordinators(tmp_path):
    from sqlalchemy import create_engine
    from app.models.db import Base
    from app.services.coordination import Coordinator
    path = tmp_path / 'coordination.db'
    engine = create_engine('sqlite:///' + str(path))
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA journal_mode=WAL').scalar() == 'wal'
    engine.dispose()
    first = Coordinator(path, capacities={'agent': 1, 'analysis': 1})
    second = Coordinator(path, capacities={'agent': 1, 'analysis': 1})
    first.register()
    second.register()
    return first, second


def test_agent_coordination_close_waits_for_real_future(coordinators):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import Busy
    first, second = (AgentOperationRuntime(coordinator) for coordinator in coordinators)
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = first.acquire('conversation', analysis=True)
        future = executor.submit(work)
        operation.track_future(future)
        assert entered.wait(5)
        try:
            operation.close()
            assert first.active_count == 1
            with pytest.raises(Busy):
                second.acquire('conversation', analysis=True)
        finally:
            release.set()
            future.result(timeout=5)
    assert first.active_count == 0
    replacement = second.acquire('conversation', analysis=True)
    replacement.close()
    assert second.active_count == 0


@pytest.mark.asyncio
async def test_agent_coordination_guard_claims_before_work_and_keeps_owner_through_save(coordinators, monkeypatch):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.ai import operation_runtime
    from app.services.ai.agent.base import BaseAgent
    from app.services.ai.agent.types import AgentResult
    first, second = (AgentOperationRuntime(coordinator) for coordinator in coordinators)
    monkeypatch.setattr(operation_runtime, 'agent_operations', first)
    agent = BaseAgent('fixture', object(), session_id='conversation')
    entered, finish = asyncio.Event(), asyncio.Event()
    async def operation():
        entered.set()
        await finish.wait()  # Represents history save that is still pending.
        return AgentResult(True, 'fixture')
    task = asyncio.create_task(agent._run_guarded('fixture', operation))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        monkeypatch.setattr(operation_runtime, 'agent_operations', second)
        other = BaseAgent('fixture', object(), session_id='different-conversation')
        calls = []
        async def forbidden():
            calls.append(True)
            return AgentResult(True, 'fixture')
        assert not (await other._run_guarded('fixture', forbidden)).success
        assert not calls
    finally:
        finish.set()
        await task
    assert first.active_count == 0


def test_agent_coordination_closed_context_cannot_start_effect_or_track_work(coordinators):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import StaleClaim
    from concurrent.futures import Future
    operation = AgentOperationRuntime(coordinators[0]).acquire('conversation')
    operation.close()
    with pytest.raises(StaleClaim):
        operation.before_effect()
    with pytest.raises(StaleClaim):
        operation.track_future(Future())


def test_agent_coordination_storage_failure_never_releases_locally(coordinators, monkeypatch):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import StorageUnavailable, Busy
    first = AgentOperationRuntime(coordinators[0])
    operation = first.acquire('conversation')
    def unavailable(*args, **kwargs):
        raise StorageUnavailable('fixture failure')
    monkeypatch.setattr(coordinators[0], 'release', unavailable)
    operation.close()
    assert first.active_count == 1
    with pytest.raises(Busy):
        AgentOperationRuntime(coordinators[1]).acquire('conversation')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['model', 'tool', 'archive'])
async def test_agent_coordination_effect_commit_failure_blocks_actual_call(coordinators, monkeypatch, kind):
    from contextlib import asynccontextmanager
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import StorageUnavailable
    from app.services.ai.adapters.base import managed_request
    from app.services.ai.agent.tool_registry import ToolRegistry, Tool
    from app.services.ai.analysis_receipts import checkpoint
    calls = []
    class Adapter:
        @asynccontextmanager
        async def _operation(self):
            yield
        @managed_request
        async def chat(self):
            calls.append(True)
    async def handler():
        calls.append(True)
    tools = ToolRegistry()
    tools.register(Tool('fixture', 'fixture', {}, handler))
    def unavailable(*args):
        raise StorageUnavailable('fixture commit failure')
    operation = AgentOperationRuntime(coordinators[0]).acquire('conversation')
    monkeypatch.setattr(coordinators[0], 'mark_side_effects', unavailable)
    try:
        with operation.activate():
            if kind == 'tool':
                assert not (await tools.execute('fixture', {})).success
            else:
                with pytest.raises(StorageUnavailable):
                    await (Adapter().chat() if kind == 'model' else checkpoint('archive'))
        assert not calls
    finally:
        operation.close()


@pytest.mark.asyncio
async def test_agent_coordination_cancelled_sdk_outcome_retains_quarantined_claim(coordinators):
    from contextlib import asynccontextmanager
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.coordination import Busy, StaleClaim
    from app.services.ai.adapters.base import managed_request
    entered = asyncio.Event()
    class Adapter:
        @asynccontextmanager
        async def _operation(self):
            yield
        @managed_request
        async def chat(self):
            entered.set()
            await asyncio.Event().wait()
    runtime = AgentOperationRuntime(coordinators[0])
    operation = runtime.acquire('conversation')
    with operation.activate():
        task = asyncio.create_task(Adapter().chat())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    operation.close()
    assert runtime.active_count == 1 and operation.unresolved
    with pytest.raises(Busy):
        AgentOperationRuntime(coordinators[1]).acquire('different-conversation')
    with pytest.raises(StaleClaim):
        coordinators[0].release(operation.handle, work_completed=True)


@pytest.mark.asyncio
async def test_agent_coordination_analysis_lease_tracks_actual_thread_after_cancel(coordinators):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.ai.analysis_runtime import AnalysisRuntime, run_analysis_sync
    from app.services.coordination import Busy
    runtime = AgentOperationRuntime(coordinators[0])
    operation = runtime.acquire('conversation', analysis=True)
    analysis = AnalysisRuntime(limit=1)
    analysis.start()
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert release.wait(5)
    lease = analysis.try_acquire()
    async def run():
        with operation.activate(), lease.activate():
            await run_analysis_sync(read)
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        lease.close()
        operation.close()
        with pytest.raises(Busy):
            AgentOperationRuntime(coordinators[1]).acquire('conversation', analysis=True)
    finally:
        release.set()
        lease.close()
        operation.close()
        await asyncio.gather(task, return_exceptions=True)
        await analysis.shutdown()
    async with asyncio.timeout(3):
        while runtime.active_count:
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_agent_coordination_durable_admission_does_not_wait_for_second_local_slot(coordinators, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.ai.agent.base import BaseAgent
    from app.services.ai.agent.types import PlanStep
    runtime = AgentOperationRuntime(coordinators[0])
    monkeypatch.setattr(operation_runtime, 'agent_operations', runtime)
    monkeypatch.setattr(BaseAgent, '_concurrency_semaphore', asyncio.Semaphore(0))
    agent = BaseAgent('fixture', object())
    agent.planner.plan = AsyncMock(return_value=[PlanStep(step_id=1, description='fixture')])
    agent._execute_thinking_step = AsyncMock()
    agent._generate_final_response = AsyncMock(return_value='fixture complete')
    async with asyncio.timeout(2):
        assert (await agent.run('fixture')).success
    assert runtime.active_count == 0


@pytest.mark.asyncio
async def test_agent_coordination_memory_delete_and_cleanup_skip_active_session(coordinators, monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from app.core.config import settings
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.ai.agent.memory import ConversationMemory, _db_connection
    monkeypatch.setattr(settings, 'MEMORY_DB_PATH', str(tmp_path / 'memory.db'))
    monkeypatch.setattr(settings, 'MEMORY_PERSIST_ENABLED', True)
    for key in ('active', 'idle'):
        memory = ConversationMemory(session_id=key)
        memory.add_user(key)
        assert await memory.save()
    async with _db_connection() as db:
        await db.execute('UPDATE conversation_sessions SET updated_at=?',
                         ((datetime.now() - timedelta(days=10)).isoformat(),))
        await db.commit()
    owner = AgentOperationRuntime(coordinators[0]).acquire('active')
    monkeypatch.setattr(operation_runtime, 'agent_operations', AgentOperationRuntime(coordinators[1]))
    try:
        assert not await ConversationMemory.delete('active')
        assert await ConversationMemory.cleanup_expired() == 1
        async with _db_connection() as db:
            rows = await (await db.execute('SELECT session_id FROM conversation_sessions')).fetchall()
        assert rows == [('active',)]
    finally:
        owner.close()
    assert await ConversationMemory.delete('active')


@pytest.mark.asyncio
async def test_agent_coordination_memory_load_and_save_require_claim(coordinators, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.ai.agent.memory import ConversationMemory, MemoryLoadError
    monkeypatch.setattr(settings, 'MEMORY_DB_PATH', str(tmp_path / 'memory.db'))
    monkeypatch.setattr(settings, 'MEMORY_PERSIST_ENABLED', True)
    runtime = AgentOperationRuntime(coordinators[0])
    monkeypatch.setattr(operation_runtime, 'agent_operations', runtime)
    memory = ConversationMemory(session_id='conversation')
    memory.add_user('fixture')
    assert not await memory.save()
    with pytest.raises(MemoryLoadError):
        await ConversationMemory.load('conversation')
    operation = runtime.acquire('conversation')
    try:
        with operation.activate():
            assert await memory.save()
            assert await ConversationMemory.load('conversation') is not None
            assert not await ConversationMemory(session_id='other').save()
    finally:
        operation.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('binding_fails', [False, True])
async def test_agent_coordination_receipt_binding_precedes_work_and_finish_follows_release(
        coordinators, monkeypatch, binding_fails):
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import select
    from app.models import db
    from app.models.schemas import AgentRunRequest
    from app.services.auth import UserInfo
    from app.services.security_context import current_principal
    from app.services.ai import operation_runtime
    from app.services.ai.operation_runtime import AgentOperationRuntime, current_operation
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.base import BaseAgent
    from app.services.ai.agent.types import AgentResult
    engine = create_async_engine('sqlite+aiosqlite:///' + str(coordinators[0].database_path))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with factory() as session:
        user = db.UserModel(username='fixture', role='operator', password_hash='fixture')
        session.add(user)
        await session.commit()
    principal = current_principal.set(UserInfo(username=user.username, role=user.role, user_id=user.id))
    runtime = AgentOperationRuntime(coordinators[0])
    monkeypatch.setattr(operation_runtime, 'agent_operations', runtime)
    service = AnalysisReceipts(coordinators[0])
    invoked = []
    try:
        handle, _ = await service.claim(AgentRunRequest(agent_type='analysis', execution_id='run',
                                                       request_id='fixture', task='analyze'))
        agent = BaseAgent('analysis', object(), session_id='conversation')
        async def operation():
            invoked.append(True)
            async with factory() as session:
                owner = (await session.execute(select(db.AnalysisReceiptOwnerModel))).scalar_one()
                assert owner.operation_id == current_operation().handle.operation_id
                assert owner.claim_id == current_operation().handle.claim_id
            return AgentResult(True, 'fixture')
        if binding_fails:
            monkeypatch.setattr(service, 'bind_operation', AsyncMock(side_effect=ReceiptError('fixture bind failed')))
            with pytest.raises(ReceiptError):
                await service.run(handle, lambda: agent._run_guarded('fixture', operation, _analysis=True))
            assert not invoked
        else:
            result = await service.run(handle, lambda: agent._run_guarded('fixture', operation, _analysis=True))
            assert result.success and result.receipt['state'] == 'succeeded'
        assert runtime.active_count == 0
    finally:
        current_principal.reset(principal)
        await engine.dispose()

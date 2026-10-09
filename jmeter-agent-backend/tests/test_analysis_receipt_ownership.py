"""Receipt/process binding on the same isolated application SQLite authority."""
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.requires_patched_sqlite


def request(**changes):
    from app.models.schemas import AgentRunRequest
    return AgentRunRequest(**(dict(agent_type='analysis', execution_id='run-A', request_id='request-A',
        task='private task', context='private context', session_id='session') | changes))


def result():
    from app.services.ai.agent.types import AgentResult
    return AgentResult(True, 'private task', output='final report')


def test_receipt_service_supports_explicit_coordinator():
    from app.services.ai.analysis_receipts import AnalysisReceipts
    assert 'coordinator' in inspect.signature(AnalysisReceipts).parameters


def test_receipt_owner_binding_schema_exists():
    from app.models import db
    assert hasattr(db, 'AnalysisReceiptOwnerModel')


@pytest_asyncio.fixture
async def ownership_env(tmp_path, monkeypatch):
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.security_context import current_principal
    from app.services.coordination import Coordinator
    from app.services.ai.operation_runtime import AgentOperationRuntime
    path = tmp_path / 'application.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path), connect_args={'timeout': .25})
    @event.listens_for(engine.sync_engine, 'connect')
    def pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    async with factory() as session:
        user = db.UserModel(username='alice', role='operator', password_hash='fixture')
        session.add(user)
        await session.commit()
        user_id = user.id
    first, second = (Coordinator(path, capacities={'agent': 2, 'analysis': 2}) for _ in range(2))
    first.register()
    second.register()
    principal = current_principal.set(UserInfo(user_id=user_id, username='alice', role='operator'))
    yield SimpleNamespace(first=first, second=second, runtime=AgentOperationRuntime(first),
                          path=path, factory=factory, engine=engine, user_id=user_id)
    current_principal.reset(principal)
    await engine.dispose()


@pytest.mark.asyncio
async def test_receipt_and_owner_binding_commit_atomically(ownership_env):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, view = await service.claim(request())
    async with ownership_env.factory() as session:
        receipt = (await session.execute(select(db.AnalysisReceiptModel))).scalar_one()
        binding = await session.get(db.AnalysisReceiptOwnerModel, receipt.id)
        assert binding.process_generation == ownership_env.first.owner.generation
        assert binding.operation_id is None and binding.claim_id is None
        assert set(binding.__table__.columns.keys()) == {'receipt_id', 'process_generation', 'operation_id', 'claim_id'}
    assert handle is not None and view['state'] == 'running'
    assert 'private task' not in str(view) and 'private context' not in str(view)


@pytest.mark.asyncio
async def test_binding_insert_failure_rolls_back_receipt(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    async with ownership_env.factory() as session:
        await session.execute(text("CREATE TRIGGER reject_owner BEFORE INSERT ON analysis_receipt_owners "
                                   "BEGIN SELECT RAISE(ABORT,'fixture unavailable'); END"))
        await session.commit()
    with pytest.raises(ReceiptError):
        await AnalysisReceipts(coordinator=ownership_env.first).claim(request())
    async with ownership_env.factory() as session:
        assert (await session.execute(text('SELECT count(*) FROM analysis_receipts'))).scalar_one() == 0
        assert (await session.execute(text('SELECT count(*) FROM analysis_receipt_owners'))).scalar_one() == 0


@pytest.mark.asyncio
async def test_receipt_requires_same_application_database_and_registered_owner(ownership_env, tmp_path):
    from app.services.coordination import Coordinator
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    unregistered = Coordinator(ownership_env.path, capacities={'agent': 2, 'analysis': 2})
    with pytest.raises(ReceiptError):
        await AnalysisReceipts(coordinator=unregistered).claim(request())
    wrong_path = Coordinator(tmp_path / 'other.db', capacities={'agent': 2, 'analysis': 2})
    wrong_path.owner = ownership_env.first.owner
    with pytest.raises(ReceiptError):
        await AnalysisReceipts(coordinator=wrong_path).claim(request())


@pytest.mark.asyncio
async def test_other_registered_worker_reads_live_bound_receipt_without_local_task(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    first = AnalysisReceipts(coordinator=ownership_env.first)
    second = AnalysisReceipts(coordinator=ownership_env.second)
    await first.claim(request())
    assert not second._active
    assert (await second.get('request-A'))['state'] == 'running'
    duplicate, view = await second.claim(request())
    assert duplicate is None and view['state'] == 'running'


@pytest.mark.asyncio
@pytest.mark.parametrize('liveness', ['DEAD', 'UNKNOWN'])
async def test_dead_or_unknown_receipt_owner_is_unknown_never_replayed(ownership_env, monkeypatch, liveness):
    from app.services.ai import analysis_receipts as module
    from app.services.process_identity import Liveness
    first = module.AnalysisReceipts(coordinator=ownership_env.first)
    second = module.AnalysisReceipts(coordinator=ownership_env.second)
    await first.claim(request())
    monkeypatch.setattr(module, 'probe', lambda identity: getattr(Liveness, liveness))
    assert (await second.get('request-A'))['state'] == 'unknown'
    assert (await second.claim(request()))[0] is None
    async with ownership_env.factory() as session:
        assert (await session.execute(text('SELECT state FROM analysis_receipts'))).scalar_one() == 'running'


@pytest.mark.asyncio
async def test_copied_receipt_handle_cannot_write_or_cleanup_from_other_worker(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    first = AnalysisReceipts(coordinator=ownership_env.first)
    second = AnalysisReceipts(coordinator=ownership_env.second)
    handle, _ = await first.claim(request())
    second.generation = first.generation  # Even copying the service generation does not copy process ownership.
    with pytest.raises(ReceiptError):
        await second.checkpoint(handle, 'model')
    with pytest.raises(ReceiptError):
        await second.abandon(handle)
    assert (await first.get('request-A'))['phase'] == 'accepted'


@pytest.mark.asyncio
async def test_bound_receipt_writes_fail_when_coordination_is_unconfigured(ownership_env, monkeypatch):
    from app.services.ai import analysis_receipts as module, operation_runtime
    service = module.AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    service._coordinator_override = None
    monkeypatch.setattr(operation_runtime, 'agent_operations', operation_runtime.AgentOperationRuntime())
    with pytest.raises(module.ReceiptError):
        await service.checkpoint(handle, 'model')
    with pytest.raises(module.ReceiptError):
        await service.abandon(handle)


@pytest.mark.asyncio
async def test_bind_operation_checks_owner_claim_and_is_immutable(ownership_env):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.operation_runtime import AgentOperationRuntime
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    foreign = AgentOperationRuntime(ownership_env.second).acquire('foreign', analysis=True)
    with pytest.raises(ReceiptError):
        await service.bind_operation(handle, foreign)
    foreign.close()
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    await service.bind_operation(handle, operation)
    async with ownership_env.factory() as session:
        binding = (await session.execute(select(db.AnalysisReceiptOwnerModel))).scalar_one()
        assert binding.operation_id == operation.handle.operation_id
        assert binding.claim_id == operation.handle.claim_id
    other = ownership_env.runtime.acquire('other', analysis=True)
    with pytest.raises(ReceiptError):
        await service.bind_operation(handle, other)
    operation.close()
    other.close()


@pytest.mark.asyncio
async def test_finish_after_release_accepts_original_claim_even_after_resources_reused(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    operation.close()
    replacement = ownership_env.runtime.acquire('session', analysis=True)
    view = await service.finish(handle, result())
    assert view['state'] == 'succeeded' and view['result']['output'] == 'final report'
    replacement.close()


@pytest.mark.asyncio
async def test_released_operation_cannot_write_late_checkpoint(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    operation.close()
    with pytest.raises(ReceiptError):
        await service.checkpoint(handle, 'model')
    assert (await service.finish(handle, result()))['state'] == 'succeeded'


@pytest.mark.asyncio
async def test_bound_receipt_detects_stale_active_resource_fence(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    async with ownership_env.factory() as session:
        await session.execute(text('UPDATE resource_leases SET fence=fence+1'))
        await session.commit()
    with pytest.raises(ReceiptError):
        await service.checkpoint(handle, 'model')
    assert (await service.get('request-A'))['state'] == 'unknown'


@pytest.mark.asyncio
async def test_original_owner_can_cleanup_metadata_after_acl_revocation(ownership_env, monkeypatch):
    from app.services.ai import analysis_receipts as module
    service = module.AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    async def revoked(*a, **kw):
        raise module.ReceiptError('Access revoked', 403)
    monkeypatch.setattr(module, '_authorize_execution', revoked)
    with pytest.raises(module.ReceiptError):
        await service.checkpoint(handle, 'model')
    await service.abandon(handle, 'unknown')
    async with ownership_env.factory() as session:
        row = (await session.execute(text('SELECT state,result_data FROM analysis_receipts'))).one()
        assert row.state == 'unknown' and row.result_data is None
    operation.close()


@pytest.mark.asyncio
async def test_global_binding_hook_noops_without_context_and_binds_inside_run(ownership_env):
    from app.services.ai import analysis_receipts as module
    await module.bind_current_operation(object())
    service = module.AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    async def work():
        await module.bind_current_operation(operation)
        operation.close()
        return result()
    completed = await service.run(handle, work)
    assert completed.receipt['state'] == 'succeeded'


@pytest.mark.asyncio
async def test_receipt_does_not_open_sync_coordinator_transactions_inside_async_writer(ownership_env, monkeypatch):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    service = AnalysisReceipts(coordinator=ownership_env.first)
    operation = ownership_env.runtime.acquire('session', analysis=True)
    monkeypatch.setattr(ownership_env.first, '_transaction', lambda: pytest.fail('nested synchronous transaction'))
    handle, _ = await service.claim(request())
    await service.bind_operation(handle, operation)
    await service.checkpoint(handle, 'preparation')
    await service.abandon(handle)


@pytest.mark.asyncio
async def test_legacy_unbound_receipt_stays_unknown_in_other_service(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts
    legacy = AnalysisReceipts()
    await legacy.claim(request())
    other = AnalysisReceipts(coordinator=ownership_env.second)
    assert (await other.get('request-A'))['state'] == 'unknown'
    assert (await other.claim(request()))[0] is None


@pytest.mark.asyncio
async def test_live_os_worker_receipt_is_running_then_death_unknown_without_replay(ownership_env, tmp_path):
    import asyncio
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptHandle, ReceiptError
    helper = tmp_path / 'receipt_owner_worker.py'
    helper.write_bytes(Path(__file__).with_name('receipt_owner_worker.py').read_bytes())
    request_path, response = tmp_path / 'request.json', tmp_path / 'response.json'
    request_path.write_text(json.dumps(dict(database=str(ownership_env.path), user_id=ownership_env.user_id,
        request=request().model_dump(), response=str(response))), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       RECEIPT_TEST_SQLITE_VERSION=sqlite3.sqlite_version)
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        kernel.GetModuleFileNameW.restype = ctypes.c_uint
        path = ctypes.create_unicode_buffer(32768)
        assert kernel.GetModuleFileNameW(kernel.GetModuleHandleW('sqlite3.dll'), path, len(path))
        environment['RECEIPT_TEST_SQLITE_DLL'] = path.value
    child = subprocess.Popen([sys.executable, '-B', str(helper), str(request_path)], env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not response.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(f'child exited: {child.communicate()!r}')
            await asyncio.sleep(.02)
        assert response.exists(), 'bounded receipt child timeout'
        info = json.loads(response.read_text(encoding='utf-8'))
        assert info['pid'] != os.getpid() and info['journal_mode'] == 'wal'
        assert info['sqlite'] == sqlite3.sqlite_version
        other = AnalysisReceipts(coordinator=ownership_env.second)
        assert not other._active and (await other.get('request-A'))['state'] == 'running'
        stolen = ReceiptHandle(**info['handle'])
        other.generation = stolen.generation
        with pytest.raises(ReceiptError):
            await other.checkpoint(stolen, 'model')
        with pytest.raises(ReceiptError):
            await other.abandon(stolen)
        child.terminate()
        child.wait(timeout=10)
        assert (await other.get('request-A'))['state'] == 'unknown'
        assert (await other.claim(request()))[0] is None
        print('real-process-receipt-owner', json.dumps(info))
    finally:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=10)


@pytest.mark.asyncio
async def test_partial_binding_is_unknown_and_not_writable(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    async with ownership_env.factory() as session:
        await session.execute(text("UPDATE analysis_receipt_owners SET claim_id='corrupt'"))
        await session.commit()
    assert (await service.get('request-A'))['state'] == 'unknown'
    with pytest.raises(ReceiptError):
        await service.checkpoint(handle, 'model')


@pytest.mark.asyncio
async def test_quarantined_bound_operation_only_allows_metadata_cleanup(ownership_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    operation.before_effect()
    operation.close(uncertain=True)
    assert (await service.get('request-A'))['state'] == 'unknown'
    with pytest.raises(ReceiptError):
        await service.finish(handle, result())
    await service.abandon(handle)


@pytest.mark.asyncio
async def test_global_runtime_configuration_creates_receipt_owner_binding(ownership_env, monkeypatch):
    from app.services.ai import operation_runtime
    from app.services.ai.analysis_receipts import AnalysisReceipts
    from app.models import db
    monkeypatch.setattr(operation_runtime, 'agent_operations', ownership_env.runtime)
    await AnalysisReceipts().claim(request())
    async with ownership_env.factory() as session:
        binding = (await session.execute(select(db.AnalysisReceiptOwnerModel))).scalar_one()
        assert binding.process_generation == ownership_env.first.owner.generation


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['claim', 'bind', 'checkpoint'])
async def test_first_real_acl_scope_does_not_initialize_identity_under_receipt_write_lock(ownership_env, action):
    from app.models import db
    from app.services import project_scope
    from app.services.ai.analysis_receipts import AnalysisReceipts
    event.listen(ownership_env.engine.sync_engine, 'handle_error',
                 lambda context: print('acl-first-scope-sql-error', str(context.original_exception)))
    async with ownership_env.factory() as session:
        session.add(db.ProjectModel(id='owned', name='shared fixture'))
        await session.flush()
        session.add(db.ExecutionModel(id='run-A', project_id='owned'))
        await session.commit()
        assert await session.get(db.UserAccessRevisionModel, ownership_env.user_id) is None
    service = AnalysisReceipts(coordinator=ownership_env.first)
    operation = None
    if action != 'claim':
        handle, _ = await service.claim(request())  # Internal fixture path deliberately did not initialize ACL identity.
        operation = ownership_env.runtime.acquire('session', analysis=True)
        if action == 'checkpoint':
            await service.bind_operation(handle, operation)
    try:
        with project_scope.bind_scope(None):
            if action == 'claim':
                assert (await service.claim(request()))[0] is not None
            elif action == 'bind':
                await service.bind_operation(handle, operation)
            else:
                await service.checkpoint(handle, 'preparation')
        async with ownership_env.factory() as session:
            assert await session.get(db.UserAccessRevisionModel, ownership_env.user_id) is not None
    finally:
        if operation is not None:
            operation.close()


@pytest.mark.asyncio
async def test_acl_revision_change_after_preparation_is_rejected_inside_receipt_transaction(ownership_env, monkeypatch):
    from app.models import db
    from app.services import project_scope
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    async with ownership_env.factory() as session:
        session.add(db.ProjectModel(id='owned', name='shared fixture'))
        await session.flush()
        session.add(db.ExecutionModel(id='run-A', project_id='owned'))
        await session.commit()
    service = AnalysisReceipts(coordinator=ownership_env.first)
    handle, _ = await service.claim(request())
    operation = ownership_env.runtime.acquire('session', analysis=True)
    await service.bind_operation(handle, operation)
    original = service._owner
    async def changed_after_preparation(*args, **kwargs):
        user_id = await original(*args, **kwargs)
        # Commit a revocation revision after the outer snapshot but before the
        # receipt writer starts; do not use the process-local notification cache.
        async with ownership_env.factory() as session:
            await session.execute(text('UPDATE user_access_revisions SET revision=revision+1 WHERE user_id=:id'),
                                  {'id': user_id})
            await session.commit()
        return user_id
    monkeypatch.setattr(service, '_owner', changed_after_preparation)
    try:
        with project_scope.bind_scope(None):
            with pytest.raises(ReceiptError) as error:
                await service.checkpoint(handle, 'model')
            assert error.value.status_code == 403
        async with ownership_env.factory() as session:
            assert (await session.execute(text('SELECT phase FROM analysis_receipts'))).scalar_one() == 'accepted'
    finally:
        operation.close()

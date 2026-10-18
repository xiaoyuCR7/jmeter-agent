"""Cross-worker confirmations are one-shot, owner-bound, and encrypted at rest."""
import asyncio
import sqlite3

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.requires_patched_sqlite


@pytest_asyncio.fixture
async def confirmation_env(tmp_path, monkeypatch):
    from app.models import db
    from app.services.auth import UserInfo
    from app.services.coordination import Coordinator
    path = tmp_path / 'confirmations.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    raw = sqlite3.connect(path)
    raw.execute('PRAGMA journal_mode=WAL')
    raw.commit(); raw.close()
    coordinator = Coordinator(path, capacities={'agent': 2, 'analysis': 2})
    coordinator.register()
    async with factory() as session:
        user = db.UserModel(username='alice', role='operator', password_hash='fixture')
        other = db.UserModel(username='bob', role='operator', password_hash='fixture')
        session.add_all([user, other]); await session.flush()
        alice = UserInfo(username=user.username, role=user.role, user_id=user.id)
        bob = UserInfo(username=other.username, role=other.role, user_id=other.id)
        await session.commit()
    try:
        yield path, factory, coordinator, alice, bob
    finally:
        coordinator.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_other_manager_can_decide_exact_encrypted_request_once(confirmation_env):
    from app.models import db
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.confirmations import ConfirmationManager
    path, factory, coordinator, alice, bob = confirmation_env
    runtime = AgentOperationRuntime(coordinator)
    operation = runtime.acquire('confirmation-session')
    first = ConfirmationManager(coordinator=coordinator, poll_seconds=.01)
    second = ConfirmationManager(coordinator=coordinator, poll_seconds=.01)
    with operation.activate():
        waiting = asyncio.create_task(first.request(
            alice, 'import_openapi_spec', {'url': 'https://example.test', 'token': 'secret-value'}))
    try:
        async with asyncio.timeout(2):
            while not (pending := await second.list_pending(alice)):
                await asyncio.sleep(.01)
        assert pending[0]['tool'] == 'import_openapi_spec'
        assert pending[0]['arguments']['url'] == 'https://example.test'
        assert pending[0]['arguments']['token'] == 'secret-value'
        assert b'secret-value' not in path.read_bytes()
        with pytest.raises(PermissionError):
            await second.decide(pending[0]['id'], bob, True)
        await second.decide(pending[0]['id'], alice, True)
        assert await asyncio.wait_for(waiting, 2) is True
        assert await second.list_pending(alice) == []
        with pytest.raises(KeyError):
            await second.decide(pending[0]['id'], alice, True)
        async with factory() as session:
            row = (await session.execute(select(db.ToolConfirmationModel))).scalar_one()
            assert row.state == 'consumed' and row.operation_id == operation.handle.operation_id
            assert row.encrypted_arguments == ''
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        operation.close()


@pytest.mark.asyncio
async def test_cancelled_request_abandons_shared_decision(confirmation_env):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.confirmations import ConfirmationManager
    _, _, coordinator, alice, _ = confirmation_env
    operation = AgentOperationRuntime(coordinator).acquire('confirmation-session')
    manager = ConfirmationManager(coordinator=coordinator, poll_seconds=.01)
    with operation.activate():
        waiting = asyncio.create_task(manager.request(alice, 'danger', {'path': 'fixture'}))
        async with asyncio.timeout(2):
            while not (pending := await manager.list_pending(alice)):
                await asyncio.sleep(.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert await manager.list_pending(alice) == []
    with pytest.raises(KeyError):
        await manager.decide(pending[0]['id'], alice, True)
    operation.close()


@pytest.mark.asyncio
async def test_coordinated_confirmation_requires_current_claim_and_bounds_global_pending(confirmation_env):
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.confirmations import ConfirmationManager
    _, _, coordinator, alice, _ = confirmation_env
    manager = ConfirmationManager(coordinator=coordinator, max_pending=1, poll_seconds=.01)
    assert not await manager.request(alice, 'danger', {})
    operation = AgentOperationRuntime(coordinator).acquire('confirmation-session')
    with operation.activate():
        first = asyncio.create_task(manager.request(alice, 'danger', {'value': 1}))
    async with asyncio.timeout(2):
        while not await manager.list_pending(alice):
            await asyncio.sleep(.01)
    other = AgentOperationRuntime(coordinator).acquire('other-session')
    try:
        with other.activate():
            assert not await ConfirmationManager(coordinator=coordinator, max_pending=1).request(
                alice, 'danger', {'value': 2})
    finally:
        first.cancel(); await asyncio.gather(first, return_exceptions=True)
        operation.close(); other.close()


@pytest.mark.asyncio
async def test_recreated_username_cannot_see_or_decide_old_confirmation(confirmation_env):
    from app.models import db
    from app.services.ai.operation_runtime import AgentOperationRuntime
    from app.services.auth import UserInfo
    from app.services.confirmations import ConfirmationManager
    _, factory, coordinator, alice, _ = confirmation_env
    operation = AgentOperationRuntime(coordinator).acquire('confirmation-session')
    manager = ConfirmationManager(coordinator=coordinator, poll_seconds=.01)
    with operation.activate():
        waiting = asyncio.create_task(manager.request(alice, 'danger', {}))
    async with asyncio.timeout(2):
        while not (pending := await manager.list_pending(alice)):
            await asyncio.sleep(.01)
    async with factory() as session:
        await session.execute(text('PRAGMA foreign_keys=OFF'))
        await session.execute(text('DELETE FROM users WHERE id=:id'), {'id': alice.user_id})
        replacement = db.UserModel(username='alice', role='operator', password_hash='fixture')
        session.add(replacement); await session.commit()
        recreated = UserInfo(username='alice', role='operator', user_id=replacement.id)
    assert await manager.list_pending(recreated) == []
    with pytest.raises(PermissionError):
        await manager.decide(pending[0]['id'], recreated, True)
    waiting.cancel(); await asyncio.gather(waiting, return_exceptions=True)
    operation.close()

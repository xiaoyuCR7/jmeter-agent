"""Subscriber/receipt authorization uses current recipient identity, not producer."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def delivery_env(tmp_path, monkeypatch):
    from app.models import db
    from app.services import auth, project_scope, notifications
    from app.services.security_context import current_principal, current_access_token
    from app.services.ws_manager import WSManager
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'delivery.db'))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    async with factory() as session:
        session.add_all([db.UserModel(id=11, username='alice', role='operator', password_hash='fixture'),
                         db.UserModel(id=22, username='bob', role='operator', password_hash='fixture')])
        session.add_all([db.ProjectModel(id=name, name=name) for name in ('private', 'shared')])
        await session.flush()
        session.add_all([db.ProjectAccessPolicyModel(project_id='private', mode='private', owner_id=11),
                         db.ProjectAccessPolicyModel(project_id='shared', mode='legacy_shared'),
                         db.ExecutionModel(id='run-private', project_id='private'),
                         db.ExecutionModel(id='run-shared', project_id='shared')])
        await session.commit()
    users = {name: auth.UserInfo(username=name, user_id=identifier, role='operator')
             for name, identifier in [('alice', 11), ('bob', 22)]}
    access = {11: {'private', 'shared'}, 22: {'shared'}}
    valid_tokens = {name + '-token': user for name, user in users.items()}
    async def user(credentials):
        if credentials.credentials not in valid_tokens:
            raise HTTPException(401, 'Token revoked')
        return valid_tokens[credentials.credentials]
    monkeypatch.setattr(auth, 'get_current_user', AsyncMock(side_effect=user))
    async def load():
        principal = current_principal.get()
        if principal is None:
            raise HTTPException(401, 'Identity required')
        permissions = set(access.get(principal.user_id, set()))
        authorization = SimpleNamespace(user_id=principal.user_id, global_role=principal.role,
            revision=0, incarnation='delivery', session_key=(principal.user_id, principal.role, 0, 'delivery'),
            project_modes={'private': 'private', 'shared': 'legacy_shared'},
            allows=lambda project, action: project in permissions)
        return project_scope.AccessScope(authorization, executions={
            'run-private': {'project_id': 'private'}, 'run-shared': {'project_id': 'shared'}})
    load_scope = AsyncMock(side_effect=load)
    monkeypatch.setattr(project_scope, 'load_user_scope', load_scope)
    monkeypatch.setattr(notifications, '_sse_subscribers', [])
    principal_token = current_principal.set(users['alice'])
    access_token = current_access_token.set('alice-token')
    managers = []
    def manager():
        value = WSManager()
        managers.append(value)
        return value
    def socket(name='alice', *, token=True):
        return SimpleSocket({'auth_user': users[name], 'auth_token': name + '-token'} if token else {})
    with project_scope.bind_scope(None):
        try:
            yield SimpleNamespace(factory=factory, users=users, access=access, valid_tokens=valid_tokens,
                                  load=load_scope, manager=manager, socket=socket,
                                  principal=current_principal, token=current_access_token)
        finally:
            for value in managers:
                await value.stop()
            current_principal.reset(principal_token)
            current_access_token.reset(access_token)
    await engine.dispose()


class SimpleSocket:
    def __init__(self, state):
        self.scope = {'state': state}
        self.accept = AsyncMock()
        self.send_text = AsyncMock()
        self.close = AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['outsider', 'no-token', 'unavailable'])
async def test_websocket_denies_before_accept(delivery_env, case):
    manager = delivery_env.manager()
    ws = delivery_env.socket('bob' if case == 'outsider' else 'alice', token=case != 'no-token')
    if case == 'unavailable':
        delivery_env.load.side_effect = HTTPException(503, 'Authorization unavailable')
    await manager.connect('run-private', ws)
    ws.accept.assert_not_awaited()
    ws.close.assert_awaited_once_with(code=1008)
    assert manager.get_channel_count() == {}


@pytest.mark.asyncio
async def test_websocket_tokenless_maintenance_requires_explicit_internal_scope(delivery_env):
    from app.services.project_scope import internal_scope
    manager, ws = delivery_env.manager(), delivery_env.socket(token=False)
    with internal_scope():
        await manager.connect('maintenance', ws)
        await manager.broadcast('maintenance', {'type': 'fixture'})
    ws.accept.assert_awaited_once()
    ws.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_each_recipient_has_fresh_scope_independent_of_producer(delivery_env):
    from app.services import project_scope
    manager, alice, bob = delivery_env.manager(), delivery_env.socket(), delivery_env.socket('bob')
    delivery_env.access[22].add('private')
    await manager.connect('run-private', alice)
    await manager.connect('run-private', bob)
    delivery_env.access[22].remove('private')
    delivery_env.principal.set(delivery_env.users['bob'])
    delivery_env.token.set('bob-token')
    producer = await delivery_env.load()
    with project_scope.bind_scope(producer):
        await manager.broadcast('run-private', {'report': 'private result'})
        assert project_scope.current_scope() is producer
        assert delivery_env.principal.get().user_id == 22
        assert delivery_env.token.get() == 'bob-token'
    alice.send_text.assert_awaited_once()
    bob.send_text.assert_not_awaited()
    bob.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
async def test_websocket_recreated_username_cannot_inherit_old_socket(delivery_env):
    from app.services.auth import UserInfo
    manager, ws = delivery_env.manager(), delivery_env.socket()
    await manager.connect('run-private', ws)
    delivery_env.valid_tokens['alice-token'] = UserInfo(username='alice', user_id=33, role='operator')
    await manager.broadcast('run-private', {'report': 'old private result'})
    ws.send_text.assert_not_awaited()
    ws.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
async def test_websocket_heartbeat_rechecks_project_membership(delivery_env, monkeypatch):
    from app.services import ws_manager
    manager, ws = delivery_env.manager(), delivery_env.socket()
    await manager.connect('run-private', ws)
    task = manager._heartbeat_tasks[id(ws)]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    delivery_env.access[11].remove('private')
    monkeypatch.setattr(ws_manager, 'HEARTBEAT_INTERVAL', 0)
    await asyncio.wait_for(manager._heartbeat(ws, 'run-private'), timeout=1)
    ws.send_text.assert_not_awaited()
    ws.close.assert_awaited_once_with(code=1008)


def receipt_request(execution_id='run-private'):
    from app.models.schemas import AgentRunRequest
    return AgentRunRequest(agent_type='analysis', execution_id=execution_id, request_id='receipt-one', task='analyze')


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['get', 'claim'])
async def test_receipt_owner_cannot_replay_after_project_revocation(delivery_env, operation):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(receipt_request())
    await service.finish(handle, AgentResult(True, 'analyze', output='private result'))
    delivery_env.access[11].remove('private')
    with pytest.raises(ReceiptError) as error:
        await (service.get('receipt-one') if operation == 'get' else service.claim(receipt_request()))
    assert error.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_receipt_unknown_execution_is_denied_before_reservation(delivery_env):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    with pytest.raises(ReceiptError) as error:
        await AnalysisReceipts().claim(receipt_request('unknown'))
    assert error.value.status_code == 404
    async with delivery_env.factory() as session:
        assert (await session.execute(select(db.AnalysisReceiptModel))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('user_id', [None, 22])
async def test_receipt_owner_uses_stable_id_and_username(delivery_env, user_id):
    from app.services.auth import UserInfo
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    delivery_env.principal.set(UserInfo(username='alice', role='operator', user_id=user_id))
    with pytest.raises(ReceiptError) as error:
        await AnalysisReceipts().claim(receipt_request())
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['checkpoint', 'finish'])
async def test_receipt_write_rechecks_access_but_abandon_can_finalize_metadata(delivery_env, operation):
    from app.models import db
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    from app.services.ai.agent.types import AgentResult
    service = AnalysisReceipts()
    handle, _ = await service.claim(receipt_request())
    delivery_env.access[11].remove('private')
    with pytest.raises(ReceiptError) as error:
        if operation == 'checkpoint':
            await service.checkpoint(handle, 'archive', {'raw_report_archived': True})
        else:
            await service.finish(handle, AgentResult(True, 'analyze', output='private result'))
    assert error.value.status_code in (403, 404)
    delivery_env.principal.set(None)
    delivery_env.token.set('')
    await service.abandon(handle)
    async with delivery_env.factory() as session:
        row = (await session.execute(select(db.AnalysisReceiptModel))).scalar_one()
        assert row.state == 'unknown' and row.phase == 'accepted' and row.result_data is None


@pytest.mark.asyncio
async def test_receipt_handle_does_not_authorize_another_user_to_write(delivery_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts()
    handle, _ = await service.claim(receipt_request())
    delivery_env.access[22].add('private')
    delivery_env.principal.set(delivery_env.users['bob'])
    delivery_env.token.set('bob-token')
    with pytest.raises(ReceiptError):
        await service.checkpoint(handle, 'archive')


@pytest.mark.asyncio
async def test_receipt_maintenance_finalization_cannot_write_result_content(delivery_env):
    from app.services.ai.analysis_receipts import AnalysisReceipts, ReceiptError
    service = AnalysisReceipts()
    handle, _ = await service.claim(receipt_request())
    delivery_env.principal.set(None)
    with pytest.raises(ReceiptError):
        await service._update(handle, authorize=False, state='unknown', result_data={'output': 'unauthorized'})


@pytest.mark.asyncio
async def test_sse_registry_does_not_register_unbound_anonymous_subscriber(delivery_env):
    from app.services import notifications
    delivery_env.token.set('')
    with pytest.raises(HTTPException):
        notifications.register_sse_subscriber()
    assert notifications._sse_subscribers == []


@pytest.mark.asyncio
async def test_sse_events_use_subscriber_identity_and_ignore_unknown_execution(delivery_env):
    from app.services import notifications
    alice = notifications.register_sse_subscriber()
    delivery_env.principal.set(delivery_env.users['bob'])
    delivery_env.token.set('bob-token')
    bob = notifications.register_sse_subscriber()
    await notifications._push_sse_event('test.completed', {'execution_id': 'run-private', 'report': 'private'})
    assert alice.qsize() == 1 and bob.qsize() == 0
    await notifications._push_sse_event('test.completed', {'execution_id': 'run-shared'})
    assert alice.qsize() == 2 and bob.qsize() == 1
    await notifications._push_sse_event('test.completed', {'execution_id': 'unknown'})
    assert alice.qsize() == 2 and bob.qsize() == 1


@pytest.mark.asyncio
async def test_sse_revoked_token_cannot_receive_next_event(delivery_env):
    from app.services import notifications
    queue = notifications.register_sse_subscriber()
    delivery_env.valid_tokens.pop('alice-token')
    await notifications._push_sse_event('test.completed', {'execution_id': 'run-private'})
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize('execution_id,expected', [('run-private', 0), ('unknown', 0), ('run-shared', 1)])
async def test_global_webhook_sends_only_registered_legacy_shared_events(delivery_env, monkeypatch, execution_id, expected):
    from app.core.config import settings
    from app.services import notifications
    monkeypatch.setattr(settings, 'WEBHOOK_URL', 'https://fixture.invalid/webhook')
    post = AsyncMock(return_value=SimpleNamespace(status_code=200))
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(post=post)
    monkeypatch.setattr(notifications.httpx, 'AsyncClient', lambda **kwargs: client)
    await notifications._fire_webhook('test.completed', execution_id, 'completed', {'private': 'report'})
    assert post.await_count == expected

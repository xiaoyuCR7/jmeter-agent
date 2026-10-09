"""Scheduled work must restore current project authorization before side effects."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest_asyncio.fixture
async def schedule_db(tmp_path, monkeypatch):
    from app.models import db
    from app.services.db_store import db_store
    from app.services import project_scope, execution_lifecycle, notifications
    from app.services.security_context import current_principal
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'schedules.db'))
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=DELETE')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        return factory()
    monkeypatch.setattr(db_store, '_session', session)
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    users, paths = {}, {}
    async with factory() as session:
        for name, role in [('owner', 'operator'), ('viewer', 'viewer'), ('other', 'operator'), ('admin', 'admin')]:
            user = db.UserModel(username=name, role=role, password_hash='test-only')
            users[name] = user
            session.add(user)
        await session.flush()
        for project_id in ('own', 'shared', 'hidden'):
            path = tmp_path / project_id / 'plan.jmx'
            path.parent.mkdir()
            path.write_text('<jmeterTestPlan/>', encoding='utf-8')
            paths[project_id] = str(path)
            session.add(db.ProjectModel(id=project_id, name=project_id))
        await session.flush()
        for project_id in paths:
            session.add(db.TestPlanModel(id=project_id + '-plan', project_id=project_id,
                                        name='plan', jmx_path=paths[project_id]))
            session.add(db.ProjectAccessPolicyModel(project_id=project_id,
                mode='legacy_shared' if project_id == 'shared' else 'private',
                owner_id=None if project_id == 'shared' else users['owner' if project_id == 'own' else 'other'].id,
                version=1))
        await session.commit()
    async def execute_registered(path, execution_id, **kwargs):
        if kwargs.get('before_execute'):
            await kwargs['before_execute'](SimpleNamespace(id=execution_id))
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    execute = AsyncMock(side_effect=execute_registered)
    started, completed = AsyncMock(), AsyncMock()
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'execute', execute)
    monkeypatch.setattr(notifications, 'notify_test_started', started)
    monkeypatch.setattr(notifications, 'notify_test_completed', completed)
    markers = [current_principal.set(None), project_scope._scope.set(None), project_scope._internal.set(False)]
    yield SimpleNamespace(factory=factory, users=users, paths=paths, execute=execute,
                          started=started, completed=completed)
    project_scope._internal.reset(markers[2])
    project_scope._scope.reset(markers[1])
    current_principal.reset(markers[0])
    await engine.dispose()


@asynccontextmanager
async def as_user(db, name='owner'):
    from app.services.project_scope import bind_scope, load_user_scope
    from app.services.security_context import current_principal
    user = db.users[name]
    marker = current_principal.set(SimpleNamespace(user_id=user.id, username=user.username, role=user.role))
    try:
        with bind_scope(await load_user_scope()) as scope:
            yield scope
    finally:
        current_principal.reset(marker)


async def create(db, project_id='own', *, user='owner'):
    from app.api.schedule_routes import create_schedule, CreateScheduleRequest
    from app.models.db import ScheduleModel
    async with as_user(db, user):
        result = await create_schedule(CreateScheduleRequest(project_id=project_id,
            test_plan_id=project_id + '-plan', name='Job', cron_expression='* * * * *'))
    async with db.factory() as session:
        return await session.get(ScheduleModel, result['id'])


@pytest.mark.asyncio
async def test_schedule_create_persists_creator_incarnation_atomically(schedule_db):
    from app.models import db
    schedule = await create(schedule_db)
    assert hasattr(db, 'SchedulePrincipalModel'), 'Schedule creator persistence missing'
    async with schedule_db.factory() as session:
        principal = await session.get(db.SchedulePrincipalModel, schedule.id)
    async with as_user(schedule_db) as scope:
        assert principal.user_id == scope.authorization.user_id
        assert principal.incarnation == scope.authorization.incarnation
    assert schedule.jmx_path == schedule_db.paths['own']


@pytest.mark.asyncio
@pytest.mark.parametrize('project_id,user,expected', [('hidden', 'owner', 404), ('own', 'viewer', 404), ('shared', 'viewer', 403)])
async def test_schedule_create_denies_before_database_insert(schedule_db, project_id, user, expected):
    from app.models.db import ScheduleModel
    with pytest.raises(HTTPException) as denied:
        await create(schedule_db, project_id, user=user)
    assert denied.value.status_code == expected
    async with schedule_db.factory() as session:
        assert not (await session.execute(select(ScheduleModel))).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize('plan_id,path_project,expected', [('shared-plan', '', 422), (None, 'shared', 422), (None, 'hidden', 404)])
async def test_schedule_create_rejects_source_project_and_real_path_mismatch(schedule_db, plan_id, path_project, expected):
    from app.api.schedule_routes import create_schedule, CreateScheduleRequest
    async with as_user(schedule_db):
        with pytest.raises(HTTPException) as denied:
            await create_schedule(CreateScheduleRequest(project_id='own', test_plan_id=plan_id,
                jmx_path=schedule_db.paths[path_project] if path_project else '', name='Job', cron_expression='* * * * *'))
    assert denied.value.status_code == expected


@pytest.mark.asyncio
async def test_schedule_list_filters_using_project_read_grants(schedule_db):
    from app.api.schedule_routes import list_schedules
    first = await create(schedule_db, 'own')
    await create(schedule_db, 'hidden', user='other')
    second = await create(schedule_db, 'shared')
    async with as_user(schedule_db):
        assert {row['id'] for row in await list_schedules(None)} == {first.id, second.id}
        with pytest.raises(HTTPException) as denied:
            await list_schedules('hidden')
        assert denied.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['delete_schedule', 'toggle_schedule'])
async def test_schedule_mutation_resolves_actual_project_before_write(schedule_db, method):
    from app.api import schedule_routes
    from app.models.db import ScheduleModel
    schedule = await create(schedule_db, 'hidden', user='other')
    async with as_user(schedule_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(schedule_routes, method)(schedule.id)
        assert denied.value.status_code == 404
    async with schedule_db.factory() as session:
        assert (await session.get(ScheduleModel, schedule.id)).is_enabled


@pytest.mark.asyncio
async def test_schedule_runner_restores_creator_and_current_scope(schedule_db):
    from app.services.scheduler import ScheduleRunner
    from app.services.project_scope import current_scope
    from app.services.security_context import current_principal
    schedule = await create(schedule_db)
    observed = []
    async def execute(*args, **kwargs):
        observed.append((current_scope().authorization.user_id, current_principal.get().user_id))
        if kwargs.get('before_execute'):
            await kwargs['before_execute'](SimpleNamespace(id=args[1]))
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    schedule_db.execute.side_effect = execute
    await ScheduleRunner()._run_schedule(schedule)
    assert observed == [(schedule_db.users['owner'].id, schedule_db.users['owner'].id)]
    schedule_db.started.assert_awaited_once()
    schedule_db.completed.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['deactivate', 'incarnation', 'delete', 'revoke'])
async def test_schedule_runner_rejects_changed_creator_before_notification(schedule_db, change):
    from app.models.db import UserModel, UserAccessRevisionModel
    from app.services.scheduler import ScheduleRunner
    schedule = await create(schedule_db)
    if change == 'revoke':
        from app.services.project_access import access
        async with as_user(schedule_db, 'admin'):
            preview = await access.preview('own', schedule_db.users['other'].id, {})
            await access.apply_preview('own', schedule_db.users['other'].id, {}, preview['revision'])
    else:
        async with schedule_db.factory() as session:
            if change == 'deactivate':
                (await session.get(UserModel, schedule_db.users['owner'].id)).is_active = False
            elif change == 'incarnation':
                identity = await session.get(UserAccessRevisionModel, schedule_db.users['owner'].id)
                identity.incarnation = '0' * 32
            else:
                await session.execute(delete(UserModel).where(UserModel.id == schedule_db.users['owner'].id))
            await session.commit()
    with pytest.raises(HTTPException):
        await ScheduleRunner()._run_schedule(schedule)
    schedule_db.started.assert_not_awaited()
    schedule_db.execute.assert_not_awaited()
    schedule_db.completed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('project_id,allowed', [('shared', True), ('own', False)])
async def test_ownerless_legacy_schedule_has_only_shared_service_scope(schedule_db, project_id, allowed):
    from app.models.db import ScheduleModel
    from app.services.scheduler import ScheduleRunner
    async with schedule_db.factory() as session:
        schedule = ScheduleModel(project_id=project_id, test_plan_id=project_id + '-plan',
            name='Old Job', cron_expression='* * * * *', jmx_path=schedule_db.paths[project_id])
        session.add(schedule)
        await session.commit()
    if allowed:
        await ScheduleRunner()._run_schedule(schedule)
        schedule_db.execute.assert_awaited_once()
    else:
        with pytest.raises(HTTPException):
            await ScheduleRunner()._run_schedule(schedule)
        schedule_db.started.assert_not_awaited()
        schedule_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_reloads_schedule_so_stale_object_cannot_change_project(schedule_db):
    from app.services.scheduler import ScheduleRunner
    schedule = await create(schedule_db)
    schedule.project_id = 'hidden'
    schedule.jmx_path = schedule_db.paths['hidden']
    await ScheduleRunner()._run_schedule(schedule)
    assert schedule_db.execute.await_args.kwargs['project_id'] == 'own'
    assert schedule_db.execute.await_args.args[0] == schedule_db.paths['own']


@pytest.mark.asyncio
async def test_schedule_identity_storage_failure_rolls_back_new_job(schedule_db):
    from sqlalchemy.exc import OperationalError
    from app.models.db import ScheduleModel
    engine = schedule_db.factory.kw['bind']
    def fail_principal(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO schedule_principals'):
            raise OperationalError('private SQL details', {}, RuntimeError('test failure'))
    event.listen(engine.sync_engine, 'before_cursor_execute', fail_principal)
    try:
        with pytest.raises(HTTPException) as denied:
            await create(schedule_db)
        assert denied.value.status_code == 503
        assert 'SQL' not in denied.value.detail
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', fail_principal)
    async with schedule_db.factory() as session:
        assert not (await session.execute(select(ScheduleModel))).scalars().all()


@pytest.mark.asyncio
async def test_runner_rechecks_after_started_notification_before_execution_continues(schedule_db):
    from app.models.db import UserModel
    from app.services.scheduler import ScheduleRunner
    schedule = await create(schedule_db)
    external_started = []
    async def revoke_during_notification(**kwargs):
        async with schedule_db.factory() as session:
            (await session.get(UserModel, schedule_db.users['owner'].id)).is_active = False
            await session.commit()
    async def execute(path, execution_id, **kwargs):
        await kwargs['before_execute'](SimpleNamespace(id=execution_id))
        external_started.append(True)
        return SimpleNamespace(status=SimpleNamespace(value='completed'), report_dir='', log_path='', error_message=None)
    schedule_db.started.side_effect = revoke_during_notification
    schedule_db.execute.side_effect = execute
    with pytest.raises(HTTPException):
        await ScheduleRunner()._run_schedule(schedule)
    assert not external_started
    schedule_db.completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_does_not_run_job_disabled_since_dispatch(schedule_db):
    from app.models.db import ScheduleModel
    from app.services.scheduler import ScheduleRunner
    schedule = await create(schedule_db)
    async with schedule_db.factory() as session:
        (await session.get(ScheduleModel, schedule.id)).is_enabled = False
        await session.commit()
    await ScheduleRunner()._run_schedule(schedule)
    schedule_db.execute.assert_not_awaited()
    schedule_db.started.assert_not_awaited()

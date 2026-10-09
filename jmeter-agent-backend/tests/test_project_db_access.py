"""DB access boundaries against isolated persistent records and real ACL scopes."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest_asyncio.fixture
async def store_db(tmp_path, monkeypatch):
    from app.models import db
    from app.services.db_store import db_store
    from app.services import project_scope
    from app.services.security_context import current_principal
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'store.db'))
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
    users = {}
    async with factory() as session:
        for name, role in [('owner', 'operator'), ('viewer', 'viewer'), ('admin', 'admin'), ('other', 'operator')]:
            user = db.UserModel(username=name, role=role, password_hash='test-only')
            users[name] = user
            session.add(user)
        await session.flush()
        for i, project_id in enumerate(('visible-a', 'visible-b', 'hidden')):
            created = datetime(2026, 1, 1) + timedelta(days=i)
            session.add(db.ProjectModel(id=project_id, name=project_id, created_at=created))
        await session.flush()
        for i, project_id in enumerate(('visible-a', 'visible-b', 'hidden')):
            created = datetime(2026, 1, 1) + timedelta(days=i)
            session.add(db.ProjectAccessPolicyModel(project_id=project_id, mode='private',
                owner_id=users['other' if project_id == 'hidden' else 'owner'].id, version=1))
            session.add(db.TestPlanModel(id=project_id + '-plan', project_id=project_id, name='plan', created_at=created))
        await session.flush()
        for i, project_id in enumerate(('visible-a', 'visible-b', 'hidden')):
            created = datetime(2026, 1, 1) + timedelta(days=i)
            session.add(db.ExecutionModel(id=project_id + '-run', project_id=project_id,
                                         test_plan_id=project_id + '-plan', created_at=created))
            session.add(db.BaselineModel(project_id=project_id, name=project_id, execution_id=project_id + '-run'))
        session.add(db.ProjectMemberModel(project_id='visible-a', user_id=users['viewer'].id, role='operator'))
        await session.commit()
    principal_marker = current_principal.set(None)
    scope_marker = project_scope._scope.set(None)
    internal_marker = project_scope._internal.set(False)
    yield SimpleNamespace(factory=factory, store=db_store, users=users)
    project_scope._internal.reset(internal_marker)
    project_scope._scope.reset(scope_marker)
    current_principal.reset(principal_marker)
    await engine.dispose()


@asynccontextmanager
async def user_scope(db, name='owner'):
    from app.services.project_scope import bind_scope, load_user_scope
    from app.services.security_context import current_principal
    user = db.users[name]
    token = current_principal.set(SimpleNamespace(user_id=user.id, username=user.username, role=user.role))
    try:
        with bind_scope(await load_user_scope()) as scope:
            yield scope
    finally:
        current_principal.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('method,args', [('get_project', ('visible-a',)), ('list_projects', ()),
                                       ('update_project', ('visible-a', {'name': 'changed'}))])
async def test_db_business_operations_require_explicit_scope(store_db, method, args):
    with pytest.raises(HTTPException) as denied:
        await getattr(store_db.store, method)(*args)
    assert denied.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('method,key,suffix', [('list_projects', 'projects', ''),
    ('list_test_plans', 'test_plans', '-plan'), ('list_executions', 'executions', '-run')])
async def test_db_filters_before_pagination_and_counts(store_db, method, key, suffix):
    async with user_scope(store_db):
        first = await getattr(store_db.store, method)(limit=1)
        second = await getattr(store_db.store, method)(offset=1, limit=1)
    assert first['total'] == second['total'] == 2
    assert [row['id'] for row in first[key]] == ['visible-b' + suffix]
    assert [row['id'] for row in second[key]] == ['visible-a' + suffix]


@pytest.mark.asyncio
@pytest.mark.parametrize('method,args', [('get_project', ('hidden',)), ('get_test_plan', ('hidden-plan',)),
    ('get_execution', ('hidden-run',)), ('get_active_baseline', ('hidden',)),
    ('list_test_plans', ('hidden',)), ('list_executions', ('hidden',)), ('list_baselines', ('hidden',))])
async def test_db_single_or_project_filtered_reads_deny_private_outsiders(store_db, method, args):
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(store_db.store, method)(*args)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_db_baseline_list_only_returns_accessible_rows(store_db):
    async with user_scope(store_db):
        rows = await store_db.store.list_baselines()
    assert {row['project_id'] for row in rows} == {'visible-a', 'visible-b'}


@pytest.mark.asyncio
@pytest.mark.parametrize('method,args', [('update_project', ('hidden', {'name': 'changed'})),
    ('update_test_plan', ('hidden-plan', {'name': 'changed'})),
    ('update_execution', ('hidden-run', {'status': 'completed'})), ('delete_test_plan', ('hidden-plan',)),
    ('create_test_plan', ({'id': 'new', 'project_id': 'hidden', 'name': 'new'},)),
    ('create_execution', ({'id': 'new', 'project_id': 'hidden'},)),
    ('create_baseline', ({'name': 'new', 'project_id': 'hidden'},))])
async def test_db_denied_writes_have_no_persistent_side_effect(store_db, method, args):
    from app.models.db import TestPlanModel, ExecutionModel, ProjectModel
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(store_db.store, method)(*args)
    assert denied.value.status_code == 404
    async with store_db.factory() as session:
        assert (await session.get(ProjectModel, 'hidden')).name == 'hidden'
        assert (await session.get(TestPlanModel, 'hidden-plan')).name == 'plan'
        assert (await session.get(ExecutionModel, 'hidden-run')).status == 'pending'
        assert await session.get(TestPlanModel, 'new') is None
        assert await session.get(ExecutionModel, 'new') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('method,resource,updates', [
    ('update_project', 'visible-a', {'id': 'renamed'}),
    ('update_test_plan', 'visible-a-plan', {'id': 'renamed'}),
    ('update_test_plan', 'visible-a-plan', {'project_id': 'visible-b'}),
    ('update_execution', 'visible-a-run', {'id': 'renamed'}),
    ('update_execution', 'visible-a-run', {'project_id': 'visible-b'}),
    ('update_execution', 'visible-a-run', {'test_plan_id': 'visible-b-plan'}),
])
async def test_db_update_cannot_reidentify_or_move_resources(store_db, method, resource, updates):
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(store_db.store, method)(resource, updates)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('method,data', [('create_execution', {'id': 'new', 'project_id': 'visible-a', 'test_plan_id': 'visible-b-plan'}),
    ('create_baseline', {'name': 'new', 'project_id': 'visible-a', 'test_plan_id': 'visible-b-plan'}),
    ('create_baseline', {'name': 'new', 'project_id': 'visible-a', 'execution_id': 'visible-b-run'})])
async def test_db_creation_rejects_cross_project_source_records(store_db, method, data):
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(store_db.store, method)(data)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_db_viewer_cannot_write_even_when_operator_member(store_db):
    async with user_scope(store_db, 'viewer'):
        with pytest.raises(HTTPException) as denied:
            await store_db.store.update_project('visible-a', {'name': 'changed'})
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_db_authentication_crud_stays_available_without_project_scope(store_db):
    assert (await store_db.store.get_user_by_username('owner')).id == store_db.users['owner'].id
    result = await store_db.store.create_user('new-user', 'test-only', role='viewer')
    assert result['username'] == 'new-user'
    assert len(await store_db.store.list_users()) == 5


@pytest.mark.asyncio
async def test_db_maintenance_is_explicit_and_new_project_needs_no_existing_grant(store_db):
    from app.services.project_scope import internal_scope
    async with user_scope(store_db):
        created = await store_db.store.create_project({'id': 'new', 'name': 'New'})
        assert created['id'] == 'new'
    with internal_scope():
        assert (await store_db.store.get_project('hidden'))['id'] == 'hidden'
        assert (await store_db.store.list_projects())['total'] == 4
        assert (await store_db.store.update_project('hidden', {'name': 'maintained'}))['name'] == 'maintained'


@pytest.mark.asyncio
async def test_db_project_deletion_requires_manage_and_revokes_after_commit(store_db):
    from app.models.db import ProjectModel
    from app.services.project_scope import bind_scope, current_scope
    async with user_scope(store_db, 'viewer'):
        with pytest.raises(HTTPException) as denied:
            await store_db.store.delete_project('visible-a')
    assert denied.value.status_code == 404
    async with user_scope(store_db, 'owner') as original:
        pass
    async with user_scope(store_db, 'admin'):
        # Admin has manage permission without content read permission.
        assert await store_db.store.delete_project('visible-a')
    async with store_db.factory() as session:
        assert await session.get(ProjectModel, 'visible-a') is None
    with bind_scope(original), pytest.raises(HTTPException) as revoked:
        current_scope()
    assert revoked.value.status_code == 403


@pytest.mark.asyncio
async def test_db_baseline_delete_checks_its_stored_project(store_db):
    from app.models.db import BaselineModel
    async with store_db.factory() as session:
        baseline = (await session.execute(select(BaselineModel).where(BaselineModel.project_id == 'hidden'))).scalar_one()
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await store_db.store.delete_baseline(baseline.id)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('method,resource', [('delete_project', 'visible-a'), ('delete_test_plan', 'visible-a-plan')])
async def test_db_delete_cannot_cascade_into_another_projects_legacy_execution(store_db, method, resource):
    from app.models.db import ExecutionModel
    async with store_db.factory() as session:
        run = await session.get(ExecutionModel, 'hidden-run')
        run.test_plan_id = 'visible-a-plan'
        await session.commit()
    async with user_scope(store_db):
        with pytest.raises(HTTPException) as denied:
            await getattr(store_db.store, method)(resource)
    assert denied.value.status_code == 409
    async with store_db.factory() as session:
        assert await session.get(ExecutionModel, 'hidden-run') is not None


@pytest.mark.asyncio
async def test_db_missing_single_records_still_return_none_with_scope(store_db):
    async with user_scope(store_db):
        assert await store_db.store.get_project('new-project-id') is None
        assert await store_db.store.get_test_plan('new-plan-id') is None
        assert await store_db.store.get_execution('new-execution-id') is None


@pytest.mark.asyncio
async def test_db_reads_use_database_ownership_even_when_scope_index_is_wrong(store_db):
    from app.services.project_scope import AccessScope, bind_scope
    async with user_scope(store_db) as original:
        incorrect = AccessScope(original.authorization,
            {'hidden-plan': {'project_id': 'visible-a'}}, {'hidden-run': {'project_id': 'visible-a'}})
        with bind_scope(incorrect):
            for method, resource in [('get_test_plan', 'hidden-plan'), ('get_execution', 'hidden-run')]:
                with pytest.raises(HTTPException) as denied:
                    await getattr(store_db.store, method)(resource)
                assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_db_new_project_policy_is_private_after_final_integration(store_db):
    from app.models.db import ProjectAccessPolicyModel
    async with user_scope(store_db) as scope:
        await store_db.store.create_project({'id': 'new', 'name': 'New'})
    async with store_db.factory() as session:
        policy = await session.get(ProjectAccessPolicyModel, 'new')
        assert policy.mode == 'private'
        assert policy.owner_id == scope.authorization.user_id

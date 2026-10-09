"""Real HTTP/auth/database/cache integration, with only temporary local artifacts."""
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def project_http(tmp_path, monkeypatch):
    from app.models import db
    from app.models.schemas import ProjectInfo, TestPlanInfo, TestConfig, TestExecutionInfo
    from app.services import auth
    from app.services.db_store import db_store
    from app.services.storage import store
    from app.core import config
    from app.api import routes
    from app.middleware.authentication import AuthenticationMiddleware
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'http.db'))
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        return factory()
    monkeypatch.setattr(db_store, '_session', session)
    monkeypatch.setattr(db, 'get_session_factory', lambda: factory)
    monkeypatch.setattr(auth, 'verify_password', lambda plain, hashed: plain == hashed)
    monkeypatch.setattr(config.settings, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(tmp_path / 'plans'))
    monkeypatch.setattr(config.settings, 'REPORTS_DIR_DEFAULT', str(tmp_path / 'reports'))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'paths.json'))
    for collection in ('_projects', '_test_plans', '_executions'):
        monkeypatch.setattr(store, collection, {})
    users = {name: db.UserModel(username=name, role='admin' if name == 'admin' else 'operator',
                                password_hash='fixture-only') for name in ('alice', 'bob', 'reader', 'admin')}
    async with factory() as s:
        s.add_all(users.values())
        await s.flush()
        for pid, owner in [('default', None), ('P', 'alice'), ('Q', 'bob')]:
            s.add(db.ProjectModel(id=pid, name=pid))
            await s.flush()
            s.add(db.ProjectAccessPolicyModel(project_id=pid, mode='private' if owner else 'legacy_shared',
                owner_id=users[owner].id if owner else None, version=1))
            store.set_project(pid, ProjectInfo(id=pid, name=pid))
        s.add(db.ProjectMemberModel(project_id='P', user_id=users['reader'].id, role='viewer'))
        plan_dir = tmp_path / 'plans' / 'P'
        plan_dir.mkdir(parents=True)
        plan_path = plan_dir / 'p-plan.jmx'
        plan_path.write_text('<private-plan/>', encoding='utf-8')
        report_dir = tmp_path / 'reports' / 'p-run'
        report_dir.mkdir(parents=True)
        (report_dir / 'jmeter.log').write_text('private log', encoding='utf-8')
        (report_dir / 'result.jtl').write_text('private report', encoding='utf-8')
        s.add(db.TestPlanModel(id='p-plan', project_id='P', name='Private plan', jmx_path=str(plan_path)))
        await s.flush()
        s.add(db.ExecutionModel(id='p-run', project_id='P', test_plan_id='p-plan', status='completed',
            jmx_path=str(plan_path), report_dir=str(report_dir), log_path=str(report_dir / 'jmeter.log')))
        await s.commit()
        store.set_test_plan('p-plan', TestPlanInfo(id='p-plan', project_id='P', name='Private plan',
            jmx_path=str(plan_path), test_config=TestConfig(test_type='baseline'),
            created_at=datetime.now(), endpoints_count=0))
        store.set_execution('p-run', TestExecutionInfo(id='p-run', project_id='P', test_plan_id='p-plan',
            status='completed', jmx_path=str(plan_path), report_dir=str(report_dir), log_path=str(report_dir / 'jmeter.log')))
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    from app.api.project_access_routes import router as access_router
    app.include_router(access_router, prefix='/api/v1')
    from app.api.project_boundary import AuthorizedReportFiles
    app.mount('/reports', AuthorizedReportFiles(directory=str(tmp_path / 'reports')))
    app.add_middleware(AuthenticationMiddleware)
    tokens = {name: (await auth.login(name, 'fixture-only')).access_token for name in users}
    def client(name):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                headers={'Authorization': 'Bearer ' + tokens[name]})
    yield SimpleNamespace(client=client, factory=factory, users=users, root=tmp_path, store=store)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/v1/projects/P', '/api/v1/test-plans/p-plan',
    '/api/v1/test-plans/p-plan/jmx/content', '/api/v1/executions/p-run',
    '/api/v1/executions/p-run/log', '/reports/p-run/result.jtl'])
async def test_project_http_private_content_owner_reader_outsider(project_http, path):
    for user, expected in [('alice', 200), ('reader', 200), ('bob', 404), ('admin', 404)]:
        async with project_http.client(user) as client:
            response = await client.get(path)
            assert response.status_code == expected, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,payload', [
    ('put', '/api/v1/projects/P', {'name': 'tampered'}),
    ('put', '/api/v1/test-plans/p-plan/tree', {'tree': {}}),
    ('delete', '/api/v1/test-plans/p-plan', None),
    ('post', '/api/v1/executions/p-run/cancel', None),
])
async def test_project_http_member_read_does_not_allow_side_effects(project_http, method, path, payload):
    async with project_http.client('reader') as client:
        response = await client.request(method, path, json=payload)
        assert response.status_code == 403, response.text
    assert (project_http.root / 'plans' / 'P' / 'p-plan.jmx').read_text() == '<private-plan/>'
    assert project_http.store.get_project('P').name == 'P'


@pytest.mark.asyncio
async def test_project_http_lists_filter_counts_before_pagination(project_http):
    async with project_http.client('bob') as client:
        response = (await client.get('/api/v1/projects?offset=1&limit=1')).json()
        assert response['total'] == 2
        assert len(response['projects']) == 1
        assert response['projects'][0]['id'] != 'P'
        assert (await client.get('/api/v1/test-plans')).json()['total'] == 0
        assert (await client.get('/api/v1/executions')).json()['total'] == 0


@pytest.mark.asyncio
async def test_project_http_owner_transfer_acknowledges_commit_without_old_access(project_http):
    change = {'owner_id': project_http.users['bob'].id, 'members': {}}
    async with project_http.client('alice') as client:
        preview = await client.post('/api/v1/projects/P/access/preview', json=change)
        assert preview.status_code == 200
        result = await client.post('/api/v1/projects/P/access/apply',
                                   json={**change, 'expected_revision': preview.json()['revision']})
        assert result.status_code == 200, result.text
        assert (await client.get('/api/v1/projects/P')).status_code == 404
    async with project_http.client('bob') as client:
        assert (await client.get('/api/v1/projects/P')).status_code == 200


@pytest.mark.asyncio
async def test_project_http_delete_acknowledges_commit_without_stale_scope(project_http):
    async with project_http.client('alice') as client:
        result = await client.delete('/api/v1/projects/P')
        assert result.status_code == 200, result.text
        assert (await client.get('/api/v1/projects/P')).status_code == 404


@pytest.mark.asyncio
async def test_project_http_delete_preserves_foreign_registered_descendant(project_http):
    from app.models.db import ProjectModel, TestPlanModel
    foreign = project_http.root / 'plans' / 'P' / 'foreign.jmx'
    foreign.write_text('belongs to Q')
    async with project_http.factory() as session:
        session.add(TestPlanModel(id='foreign-plan', project_id='Q', name='Foreign plan', jmx_path=str(foreign)))
        await session.commit()
    async with project_http.client('alice') as client:
        result = await client.delete('/api/v1/projects/P')
        assert result.status_code == 404, result.text
    assert foreign.read_text() == 'belongs to Q'
    async with project_http.factory() as session:
        assert await session.get(ProjectModel, 'P') is not None
        assert await session.get(TestPlanModel, 'foreign-plan') is not None


@pytest.mark.asyncio
async def test_project_http_delete_allows_absent_project_directory(project_http):
    async with project_http.client('alice') as client:
        created = await client.post('/api/v1/projects', json={'name': 'Empty project'})
        assert created.status_code == 200, created.text
        project_id = created.json()['id']
        (project_http.root / 'plans' / project_id).rmdir()
        result = await client.delete('/api/v1/projects/' + project_id)
        assert result.status_code == 200, result.text
        assert (await client.get('/api/v1/projects/' + project_id)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('escape', ['%2e%2e/', '%2e%2e%5c'])
async def test_project_http_static_report_dot_segments_cannot_change_authorized_owner(project_http, escape):
    hidden = project_http.root / 'reports' / 'hidden-run'
    hidden.mkdir()
    (hidden / 'secret.txt').write_text('must never be delivered')
    async with project_http.client('alice') as client:
        response = await client.get('/reports/p-run/' + escape + 'hidden-run/secret.txt')
        assert response.status_code == 404
        assert 'must never be delivered' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,filename', [('log', 'jmeter.log'), ('html-report', 'html/index.html'),
                                            ('dashboard-data', 'html/statistics.json')])
async def test_project_http_report_readers_check_actual_file_owner(project_http, endpoint, filename):
    foreign = project_http.root / 'plans' / 'Q'
    path = foreign / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"secret": "hidden"}')
    row = project_http.store.get_execution('p-run')
    row.report_dir = str(foreign)
    row.log_path = str(path)
    async with project_http.client('alice') as client:
        response = await client.get('/api/v1/executions/p-run/' + endpoint)
        assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_project_http_new_project_is_private_and_policy_failure_never_publishes(project_http, monkeypatch):
    from app.services.project_access import access
    async with project_http.client('alice') as client:
        response = await client.post('/api/v1/projects', json={'name': 'New private'})
        assert response.status_code == 200, response.text
        pid = response.json()['id']
    async with project_http.client('bob') as client:
        assert (await client.get('/api/v1/projects/' + pid)).status_code == 404
    before = set(project_http.store.projects)
    monkeypatch.setattr(access, 'initialize_private', AsyncMock(side_effect=RuntimeError('injected policy failure')))
    async with project_http.client('alice') as client:
        with pytest.raises(RuntimeError, match='policy failure'):
            await client.post('/api/v1/projects', json={'name': 'Should roll back'})
    assert set(project_http.store.projects) == before
    from sqlalchemy import select
    from app.models.db import ProjectModel
    async with project_http.factory() as session:
        assert not (await session.execute(select(ProjectModel).where(ProjectModel.name == 'Should roll back'))).first()


@pytest.mark.asyncio
async def test_project_delivery_recheck_avoids_full_resource_scans_and_rejects_revocation(project_http, monkeypatch):
    from app.services import project_scope
    from app.services.security_context import current_principal
    from app.services.auth import UserInfo
    from fastapi import HTTPException
    alice = project_http.users['alice']
    token = current_principal.set(UserInfo(username=alice.username, role=alice.role, user_id=alice.id))
    try:
        scope = await project_scope.load_user_scope()
        with project_scope.bind_scope(scope):
            monkeypatch.setattr(project_scope, '_load_resources', AsyncMock(side_effect=AssertionError('full resource scan')))
            assert await project_scope.recheck_scope() is scope
            from app.models.db import UserAccessRevisionModel
            async with project_http.factory() as session:
                identity = await session.get(UserAccessRevisionModel, alice.id)
                identity.revision += 1
                await session.commit()
            with pytest.raises(HTTPException) as revoked:
                await project_scope.recheck_scope()
            assert revoked.value.status_code == 403
    finally:
        current_principal.reset(token)


@pytest.mark.asyncio
async def test_project_revocation_while_loading_resource_index_blocks_model_preflight(project_http, monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi import HTTPException
    from app.services import project_scope
    from app.services.security_context import current_principal
    from app.services.auth import UserInfo
    from app.services.ai.adapters.base import managed_request
    from app.models.db import UserAccessRevisionModel
    user = project_http.users['alice']
    token = current_principal.set(UserInfo(username=user.username, role=user.role, user_id=user.id))
    sdk = AsyncMock(return_value='private model result')
    class Adapter:
        @asynccontextmanager
        async def _operation(self):
            yield
        @managed_request
        async def chat(self):
            return await sdk()
    try:
        scope = await project_scope.load_user_scope()
        original_load = project_scope._load_resources
        async def load_then_revoke(authorization):
            result = await original_load(authorization)
            async with project_http.factory() as session:
                identity = await session.get(UserAccessRevisionModel, user.id)
                identity.revision += 1
                await session.commit()
            return result
        monkeypatch.setattr(project_scope, '_load_resources', load_then_revoke)
        with project_scope.bind_scope(scope), pytest.raises(HTTPException):
            await Adapter().chat()
        sdk.assert_not_awaited()
    finally:
        current_principal.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('source_kind', ['snapshot', 'unregistered-plan'])
@pytest.mark.parametrize('actor', ['alice', 'reader'])
async def test_project_execution_cannot_reclassify_private_source_as_default(project_http, source_kind, actor):
    import hashlib
    from fastapi import HTTPException
    from app.models.db import ExecutionModel
    from app.services.execution_lifecycle import ExecutionLifecycle
    from app.services.project_scope import bind_scope, load_user_scope
    from app.services.security_context import current_principal
    from app.services.auth import UserInfo
    source = (project_http.root / 'reports' / 'p-run' / 'snapshot.jmx' if source_kind == 'snapshot'
              else project_http.root / 'plans' / 'P' / 'unregistered.jmx')
    source.write_text('<private-plan/>')
    if source_kind == 'snapshot':
        async with project_http.factory() as session:
            row = await session.get(ExecutionModel, 'p-run')
            row.jmx_path = str(source)
            row.source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            await session.commit()
    user = project_http.users[actor]
    token = current_principal.set(UserInfo(username=user.username, user_id=user.id, role=user.role))
    lifecycle = ExecutionLifecycle()
    try:
        with bind_scope(await load_user_scope()):
            if actor == 'reader':
                before = set(project_http.store.test_plans)
                with pytest.raises(HTTPException) as denied:
                    await lifecycle.prepare(str(source), 'derived-run')
                assert denied.value.status_code == 403
                assert set(project_http.store.test_plans) == before
                assert not (project_http.root / 'reports' / 'derived-run').exists()
            else:
                execution = await lifecycle.prepare(str(source), 'derived-run')
                assert execution.project_id == 'P'
                assert project_http.store.get_test_plan(execution.test_plan_id).project_id == 'P'
    finally:
        await lifecycle.shutdown()
        current_principal.reset(token)

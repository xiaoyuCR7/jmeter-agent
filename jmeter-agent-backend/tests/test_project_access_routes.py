"""HTTP contract for explicit, versioned project access management."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest_asyncio.fixture
async def access_api_db(tmp_path, monkeypatch):
    from app.models import db
    from app.services.db_store import db_store
    from app.services.security_context import current_principal
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'access-api.db'))
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
            session.add(user)
            users[name] = user
        session.add_all([db.ProjectModel(id='legacy', name='Legacy'), db.ProjectModel(id='private', name='Private')])
        await session.flush()
        session.add(db.ProjectAccessPolicyModel(project_id='private', mode='private', owner_id=users['owner'].id, version=1))
        session.add(db.ProjectMemberModel(project_id='private', user_id=users['viewer'].id, role='viewer'))
        await session.commit()
    marker = current_principal.set(None)
    yield SimpleNamespace(factory=factory, users=users)
    current_principal.reset(marker)
    await engine.dispose()


@asynccontextmanager
async def client(db, name=None):
    from app.api.project_access_routes import router
    from app.services.security_context import current_principal
    principal = None if name is None else SimpleNamespace(user_id=db.users[name].id, username=name, role=db.users[name].role)
    token = current_principal.set(principal)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as connection:
            yield connection
    finally:
        current_principal.reset(token)


def test_access_management_router_exists():
    import importlib.util
    assert importlib.util.find_spec('app.api.project_access_routes') is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('name,expected,manage,read', [(None, 401, False, False), ('other', 404, False, False),
    ('viewer', 200, False, True), ('owner', 200, True, True), ('admin', 200, True, False)])
async def test_access_metadata_discloses_only_authorized_policy_fields(access_api_db, name, expected, manage, read):
    async with client(access_api_db, name) as connection:
        response = await connection.get('/api/v1/projects/private/access')
    assert response.status_code == expected
    if expected == 200:
        data = response.json()
        assert data['mode'] == 'private'
        assert data['permissions']['read'] is read
        assert data['permissions']['manage'] is manage
        assert ('owner_id' in data) is manage
        assert ('members' in data) is manage
        assert 'username' not in str(data) and 'password' not in str(data)


@pytest.mark.asyncio
async def test_legacy_metadata_uses_server_role_and_mode(access_api_db):
    async with client(access_api_db, 'viewer') as connection:
        response = await connection.get('/api/v1/projects/legacy/access')
    assert response.json() == {'project_id': 'legacy', 'mode': 'legacy_shared',
                               'permissions': {'read': True, 'write': False, 'manage': False}}


@pytest.mark.asyncio
async def test_preview_apply_requires_explicit_revision_and_rejects_replay(access_api_db):
    body = {'owner_id': access_api_db.users['owner'].id, 'members': {str(access_api_db.users['viewer'].id): 'viewer'}}
    async with client(access_api_db, 'admin') as connection:
        preview = await connection.post('/api/v1/projects/legacy/access/preview', json=body)
        assert preview.status_code == 200
        assert preview.json()['current']['mode'] == 'legacy_shared'
        assert set(preview.json()['resource_counts']) == {'test_plans', 'executions', 'baselines', 'schedules'}
        assert (await connection.post('/api/v1/projects/legacy/access/apply', json=body)).status_code == 422
        commit = {**body, 'expected_revision': preview.json()['revision']}
        assert (await connection.post('/api/v1/projects/legacy/access/apply', json=commit)).status_code == 200
        assert (await connection.post('/api/v1/projects/legacy/access/apply', json=commit)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('name,project,expected', [(None, 'legacy', 401), ('owner', 'legacy', 403),
    ('viewer', 'private', 403), ('other', 'private', 404)])
async def test_access_preview_preserves_management_authority(access_api_db, name, project, expected):
    async with client(access_api_db, name) as connection:
        response = await connection.post(f'/api/v1/projects/{project}/access/preview',
            json={'owner_id': access_api_db.users['owner'].id, 'members': {}})
    assert response.status_code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{'owner_id': 1, 'members': {'2': 'admin'}},
    {'owner_id': True, 'members': {}}, {'owner_id': 99999, 'members': {}},
    {'owner_id': 1, 'members': {}, 'mode': 'legacy_shared'}])
async def test_access_mutations_validate_ids_roles_and_unsupported_fields(access_api_db, payload):
    async with client(access_api_db, 'owner') as connection:
        response = await connection.post('/api/v1/projects/private/access/preview', json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_owner_management_does_not_enumerate_global_accounts(access_api_db, monkeypatch):
    from app.services.db_store import db_store
    async def forbidden():
        raise AssertionError('Project owners must not enumerate global accounts')
    monkeypatch.setattr(db_store, 'list_users', forbidden)
    async with client(access_api_db, 'owner') as connection:
        assert (await connection.get('/api/v1/projects/private/access')).status_code == 200

"""Project tool/import persistence order and filesystem fallback ACL contracts."""
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException


@pytest.fixture
def creation_env(tmp_path, monkeypatch):
    from app.core import config
    from app.models.schemas import ProjectInfo, ProjectEnvironment
    from app.services import project_scope, execution_lifecycle, storage
    from app.services.ai.agent.tools import project
    from app.services.db_store import db_store

    plans = tmp_path / 'plans'
    for name in ('visible', 'reader', 'hidden', 'orphan'):
        (plans / name).mkdir(parents=True)
        (plans / name / 'plan.jmx').write_text(name)
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(plans))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'no-paths.json'))
    projects = {name: ProjectInfo(id=name, name=name,
                    environment=ProjectEnvironment(base_url='https://old.invalid', headers={'old': 'value'}))
                for name in ('visible', 'reader')}
    cache = SimpleNamespace(projects=projects, test_plans={})
    cache.set_project = lambda key, value: projects.__setitem__(key, value)
    cache.get_project = projects.get
    monkeypatch.setattr(project, 'store', cache)
    monkeypatch.setattr(storage, 'store', cache)
    monkeypatch.setattr(execution_lifecycle, 'store', cache)
    access = {'visible': True, 'reader': False}
    events = []

    def require_project(project_id, *, write=False):
        if project_id not in access:
            raise HTTPException(404, 'Project resource not found')
        if write and not access[project_id]:
            raise HTTPException(403, 'Project write permission required')

    def can_project(project_id, *, write=False):
        try:
            require_project(project_id, write=write)
            return True
        except HTTPException:
            return False

    def require_path(path, *, write=False, allow_root_listing=False):
        target = Path(os.path.realpath(path))
        if target == plans and allow_root_listing and not write:
            return
        if not target.is_relative_to(plans) or target == plans:
            raise HTTPException(404, 'Project file not found')
        require_project(target.relative_to(plans).parts[0], write=write)

    def can_path(path, **kwargs):
        try:
            require_path(path, **kwargs)
            return True
        except HTTPException:
            return False

    monkeypatch.setattr(project_scope, 'require_project', require_project)
    monkeypatch.setattr(project_scope, 'can_project', can_project)
    monkeypatch.setattr(project_scope, 'require_path', require_path)
    monkeypatch.setattr(project_scope, 'can_path', can_path)
    monkeypatch.setattr(uuid, 'uuid4', lambda: uuid.UUID('12345678-1111-1111-1111-111111111111'))
    async def create_project(data):
        assert data['id'] not in projects, 'cache published before persistence'
        events.append('persist')
        return {'id': data['id']}
    create = AsyncMock(side_effect=create_project)
    update = AsyncMock(return_value={'id': 'visible'})
    monkeypatch.setattr(db_store, 'create_project', create)
    monkeypatch.setattr(db_store, 'update_project', update)
    monkeypatch.setattr(db_store, 'get_project', AsyncMock(return_value=None))
    async def refresh():
        events.append('refresh')
        access['12345678'] = True
    refresh_mock = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(project_scope, 'refresh_scope', refresh_mock)
    register = AsyncMock(return_value=SimpleNamespace(id='new-plan'))
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'register_path', register)
    return SimpleNamespace(plans=plans, projects=projects, access=access, events=events,
                           create=create, update=update, refresh=refresh_mock, register=register)


async def denied(call):
    with pytest.raises(HTTPException) as error:
        await call
    assert error.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_project_list_does_not_resurrect_filtered_hidden_or_orphan_directories(creation_env, monkeypatch):
    from app.services.ai.agent.tools import project
    inspected = []
    isdir = os.path.isdir
    def inspect(path):
        inspected.append(Path(path))
        return isdir(path)
    monkeypatch.setattr(project.os.path, 'isdir', inspect)
    rows = (await project.list_projects())['projects']
    assert {row['id'] for row in rows} == {'visible', 'reader'}
    assert creation_env.plans / 'hidden' not in inspected
    assert creation_env.plans / 'orphan' not in inspected


@pytest.mark.asyncio
async def test_project_info_denied_before_filesystem_fallback(creation_env, monkeypatch):
    from app.services.ai.agent.tools import project
    probe = Mock(return_value=True)
    monkeypatch.setattr(project.os.path, 'isdir', probe)
    await denied(project.get_project_info('hidden'))
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_project_info_filters_denied_file_aliases(creation_env, monkeypatch):
    from app.services.ai.agent.tools import project
    creation_env.projects.pop('visible')
    alias = creation_env.plans / 'visible' / 'secret.jmx'
    alias.write_text('placeholder')
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, 'realpath', lambda path: str(creation_env.plans / 'hidden' / 'plan.jmx')
                        if Path(path) == alias else realpath(path))
    result = await project.get_project_info('visible')
    assert result['jmx_files'] == ['plan.jmx']


@pytest.mark.asyncio
async def test_project_creation_failure_never_publishes_cache_or_directory(creation_env):
    from app.services.ai.agent.tools import project
    creation_env.create.side_effect = RuntimeError('policy persistence failed')
    with pytest.raises(RuntimeError, match='policy persistence failed'):
        await project.create_project('Created')
    assert '12345678' not in creation_env.projects
    assert not (creation_env.plans / '12345678').exists()


@pytest.mark.asyncio
async def test_project_creation_uses_persistence_refresh_before_directory(creation_env):
    from app.services.ai.agent.tools import project
    result = await project.create_project('Created', base_url='https://new.invalid')
    creation_env.create.assert_awaited_once()
    creation_env.refresh.assert_awaited_once()
    assert creation_env.events == ['persist', 'refresh']
    assert result['project_id'] == '12345678'
    assert (creation_env.plans / '12345678').is_dir()


@pytest.mark.asyncio
async def test_environment_update_requires_write_before_mutation(creation_env):
    from app.services.ai.agent.tools import project
    original = creation_env.projects['reader'].model_dump()
    await denied(project.update_project_env('reader', base_url='https://changed.invalid'))
    assert creation_env.projects['reader'].model_dump() == original
    creation_env.update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [False, True])
async def test_environment_persistence_failure_keeps_old_cache(creation_env, missing):
    from app.services.ai.agent.tools import project
    original = creation_env.projects['visible'].model_dump()
    if missing:
        creation_env.update.return_value = None
        expected = LookupError
    else:
        creation_env.update.side_effect = RuntimeError('database unavailable')
        expected = RuntimeError
    with pytest.raises(expected):
        await project.update_project_env('visible', headers={'new': 'secret'}, base_url='https://changed.invalid')
    assert creation_env.projects['visible'].model_dump() == original


@pytest.mark.asyncio
async def test_environment_success_publishes_only_after_db_commit(creation_env):
    from app.services.ai.agent.tools import project
    old = creation_env.projects['visible']
    async def update(project_id, payload):
        assert creation_env.projects[project_id] is old
        assert old.environment.base_url == 'https://old.invalid'
        assert payload['base_url'] == ''
        return {'id': project_id}
    creation_env.update.side_effect = update
    result = await project.update_project_env('visible', base_url='', variables={'new': 'value'})
    assert result['base_url'] == ''
    assert creation_env.projects['visible'].environment.base_url == ''
    assert old.environment.base_url == 'https://old.invalid'


SPEC = {'openapi': '3.0.0', 'info': {'title': 'Fixture', 'version': '1'},
        'servers': [{'url': 'https://example.invalid'}],
        'paths': {'/ping': {'get': {'responses': {'200': {'description': 'OK'}}}}}}


@pytest.mark.asyncio
async def test_openapi_persistence_failure_never_writes_or_publishes(creation_env, monkeypatch):
    from app.services.openapi_importer import OpenAPIImporter
    from app.services.jmeter.jmx_generator import JMXGenerator
    creation_env.create.side_effect = RuntimeError('policy persistence failed')
    save = Mock(side_effect=AssertionError('file written after failed persistence'))
    monkeypatch.setattr(JMXGenerator, 'save_jmx', save)
    result = await OpenAPIImporter().import_from_dict(SPEC)
    assert not result.success
    assert '12345678' not in creation_env.projects
    save.assert_not_called()


@pytest.mark.asyncio
async def test_openapi_uses_persistence_refresh_before_saving(creation_env, monkeypatch):
    from app.services.openapi_importer import OpenAPIImporter
    from app.services.jmeter.jmx_generator import JMXGenerator
    def save(self, content, **kwargs):
        assert creation_env.events == ['persist', 'refresh']
        assert creation_env.access[kwargs['project_id']]
        return str(creation_env.plans / kwargs['project_id'] / (kwargs['plan_id'] + '.jmx'))
    monkeypatch.setattr(JMXGenerator, 'save_jmx', save)
    result = await OpenAPIImporter().import_from_dict(SPEC)
    assert result.success, result.error
    creation_env.create.assert_awaited_once()
    creation_env.refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['project', 'path'])
async def test_openapi_checks_target_project_and_path_before_save(creation_env, monkeypatch, denial):
    from app.services import project_scope
    from app.services.openapi_importer import OpenAPIImporter
    from app.services.jmeter.jmx_generator import JMXGenerator
    async def refresh():
        creation_env.access['12345678'] = denial != 'project'
    creation_env.refresh.side_effect = refresh
    if denial == 'path':
        monkeypatch.setattr(project_scope, 'require_path', Mock(side_effect=HTTPException(404, 'Hidden target')))
    save = Mock(return_value='denied-output.jmx')
    monkeypatch.setattr(JMXGenerator, 'save_jmx', save)
    result = await OpenAPIImporter().import_from_dict(SPEC)
    assert not result.success
    save.assert_not_called()
    creation_env.register.assert_not_awaited()
    assert '12345678' not in creation_env.projects


@pytest.mark.asyncio
@pytest.mark.parametrize('entry', ['tool', 'openapi'])
async def test_creation_collision_never_updates_or_republishes_hidden_project(creation_env, entry):
    from app.models.schemas import ProjectInfo
    from app.services.ai.agent.tools import project
    from app.services.openapi_importer import OpenAPIImporter
    hidden = ProjectInfo(id='12345678', name='Hidden existing project')
    creation_env.projects['12345678'] = hidden
    creation_env.create.side_effect = HTTPException(409, 'Project already exists')
    if entry == 'tool':
        with pytest.raises(HTTPException) as error:
            await project.create_project('Replacement')
        assert error.value.status_code == 409
    else:
        assert not (await OpenAPIImporter().import_from_dict(SPEC)).success
    assert creation_env.projects['12345678'] is hidden
    creation_env.update.assert_not_awaited()
    creation_env.refresh.assert_not_awaited()
    assert not (creation_env.plans / '12345678').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('entry', ['tool', 'openapi'])
async def test_scope_refresh_failure_after_create_never_publishes_or_writes(creation_env, monkeypatch, entry):
    from app.services.ai.agent.tools import project
    from app.services.openapi_importer import OpenAPIImporter
    from app.services.jmeter.jmx_generator import JMXGenerator
    creation_env.refresh.side_effect = RuntimeError('authorization unavailable')
    save = Mock(side_effect=AssertionError('scope failure wrote JMX'))
    monkeypatch.setattr(JMXGenerator, 'save_jmx', save)
    if entry == 'tool':
        with pytest.raises(RuntimeError, match='authorization unavailable'):
            await project.create_project('Created')
    else:
        assert not (await OpenAPIImporter().import_from_dict(SPEC)).success
    assert '12345678' not in creation_env.projects
    assert not (creation_env.plans / '12345678').exists()
    save.assert_not_called()


@pytest.mark.asyncio
async def test_environment_revocation_before_cache_publish_keeps_cached_value(creation_env):
    from app.services.ai.agent.tools import project
    original = creation_env.projects['visible']
    async def update(*args):
        creation_env.access['visible'] = False
        return {'id': 'visible'}
    creation_env.update.side_effect = update
    await denied(project.update_project_env('visible', base_url='https://changed.invalid'))
    assert creation_env.projects['visible'] is original
    assert original.environment.base_url == 'https://old.invalid'

"""ACL guards run before filesystem effects; all files and policy are isolated."""
import asyncio
import io
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile


@pytest.fixture
def file_acl(tmp_path, monkeypatch):
    from app.core import config
    from app.services import project_scope
    from app.api import data_routes

    roots = {name: tmp_path / name for name in ('plans', 'reports', 'backups', 'test_data')}
    for root in roots.values():
        for project in ('visible', 'hidden', 'reader'):
            (root / project).mkdir(parents=True)
            for filename in ('plan.jmx', 'result.jtl', 'input.csv', 'jmeter.log'):
                (root / project / filename).write_text('private fixture\n', encoding='utf-8')
    monkeypatch.setattr(config.settings, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(roots['plans']))
    monkeypatch.setattr(config.settings, 'REPORTS_DIR_DEFAULT', str(roots['reports']))
    monkeypatch.setattr(config.settings, 'BACKUPS_DIR', str(roots['backups']))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'no-paths.json'))
    monkeypatch.setattr(data_routes, 'TEST_DATA_DIR', str(roots['test_data']))
    checks = []

    def require_path(path, *, write=False, allow_root_listing=False):
        resolved = Path(os.path.realpath(path))
        checks.append((resolved, write, allow_root_listing))
        if 'hidden' in resolved.parts or not any(resolved.is_relative_to(root) for root in roots.values()):
            raise HTTPException(404, 'Not found')
        if write and 'reader' in resolved.parts:
            raise HTTPException(403, 'Write access denied')
        if resolved in roots.values() and not allow_root_listing:
            raise HTTPException(404, 'Not found')

    def can_path(path, **kwargs):
        try:
            require_path(path, **kwargs)
            return True
        except HTTPException:
            return False

    def require_project(project_id, *, write=False):
        if project_id == 'hidden':
            raise HTTPException(404, 'Not found')
        if write and project_id == 'reader':
            raise HTTPException(403, 'Write access denied')

    def require_execution(execution_id, *, write=False):
        require_project(execution_id, write=write)

    monkeypatch.setattr(project_scope, 'require_path', require_path)
    monkeypatch.setattr(project_scope, 'can_path', can_path)
    monkeypatch.setattr(project_scope, 'require_project', require_project)
    monkeypatch.setattr(project_scope, 'require_execution', require_execution)
    return SimpleNamespace(**roots, checks=checks)


async def assert_denied(call):
    try:
        result = await call
    except HTTPException as error:
        assert error.status_code in (403, 404)
    else:
        assert 'error' in result or result.get('exists') is False, result


def test_safe_path_requires_project_access_inside_allowed_root(file_acl):
    from app.core.config import safe_path
    with pytest.raises(HTTPException):
        safe_path(str(file_acl.plans / 'hidden' / 'plan.jmx'))


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['read_file', 'file_exists', 'list_directory', 'delete_file', 'write_file'])
async def test_file_tools_reject_hidden_project_before_io(file_acl, monkeypatch, handler):
    from app.services.ai.agent.tools import file
    path = file_acl.plans / 'hidden' / ('plan.jmx' if handler != 'list_directory' else '')
    reader = Mock(side_effect=AssertionError('denied file opened'))
    monkeypatch.setattr(file, 'open', reader, raising=False)
    args = [str(path)] + (['replacement'] if handler == 'write_file' else [])
    await assert_denied(getattr(file, handler)(*args))
    reader.assert_not_called()
    assert (file_acl.plans / 'hidden' / 'plan.jmx').read_text() == 'private fixture\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['write_file', 'delete_file'])
async def test_file_mutations_require_write_permission(file_acl, handler):
    from app.services.ai.agent.tools import file
    path = file_acl.plans / 'reader' / 'plan.jmx'
    args = [str(path)] + (['replacement'] if handler == 'write_file' else [])
    await assert_denied(getattr(file, handler)(*args))
    assert path.read_text() == 'private fixture\n'


@pytest.mark.asyncio
async def test_directory_listing_filters_hidden_names_before_metadata(file_acl, monkeypatch):
    from app.services.ai.agent.tools import file
    original = os.path.isdir
    inspected = []
    def isdir(path):
        inspected.append(Path(path))
        return original(path)
    monkeypatch.setattr(file.os.path, 'isdir', isdir)
    result = await file.list_directory(str(file_acl.plans))
    assert {item['name'] for item in result['items']} == {'visible', 'reader'}
    assert file_acl.plans / 'hidden' not in inspected


@pytest.mark.asyncio
async def test_authorized_file_read_write_still_work(file_acl):
    from app.services.ai.agent.tools import file
    path = str(file_acl.plans / 'visible' / 'created.txt')
    assert (await file.write_file(path, 'ok'))['written']
    assert (await file.read_file(path))['content'] == 'ok'


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['parse_jmx_tree', 'get_jmx_content', 'validate_jmx', 'update_jmx', 'clone_jmx'])
async def test_jmx_tools_reject_hidden_source_before_parsing(file_acl, monkeypatch, handler):
    from app.services.ai.agent.tools import jmx
    parser = Mock(return_value={})
    monkeypatch.setattr(jmx, 'parse_jmx', parser)
    reader = Mock(side_effect=AssertionError('denied file opened'))
    monkeypatch.setattr(jmx, 'open', reader, raising=False)
    await assert_denied(getattr(jmx, handler)(str(file_acl.plans / 'hidden' / 'plan.jmx')))
    parser.assert_not_called()
    reader.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['update_jmx', 'clone_jmx'])
async def test_jmx_mutations_require_destination_write_before_source_parse(file_acl, monkeypatch, handler):
    from app.services.ai.agent.tools import jmx
    parser = Mock(return_value={})
    monkeypatch.setattr(jmx, 'parse_jmx', parser)
    await assert_denied(getattr(jmx, handler)(str(file_acl.plans / 'reader' / 'plan.jmx')))
    parser.assert_not_called()
    assert not (file_acl.plans / 'reader' / 'clone_plan.jmx.jmx').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('source_project,target_project', [('hidden', 'visible'), ('visible', 'hidden'), ('visible', 'reader')])
async def test_import_authorizes_both_sides_before_parse_or_create(file_acl, monkeypatch, source_project, target_project):
    from app.services.ai.agent.tools import jmx
    parser = Mock(return_value={})
    monkeypatch.setattr(jmx, 'parse_jmx', parser)
    await assert_denied(jmx.import_jmx(str(file_acl.plans / source_project / 'plan.jmx'), target_project, 'imported'))
    parser.assert_not_called()
    assert not (file_acl.plans / target_project / 'imported.jmx').exists()


@pytest.mark.asyncio
async def test_import_rejects_unregistered_external_source_before_parse(file_acl, monkeypatch, tmp_path):
    from app.services.ai.agent.tools import jmx
    source = tmp_path / 'external.jmx'
    source.write_text('<jmeterTestPlan/>')
    parser = Mock(return_value={})
    monkeypatch.setattr(jmx, 'parse_jmx', parser)
    await assert_denied(jmx.import_jmx(str(source), 'visible', 'imported'))
    parser.assert_not_called()


@pytest.mark.asyncio
async def test_create_jmx_denies_before_generator_save(file_acl, monkeypatch):
    from app.services.ai.agent.tools import jmx
    save = Mock(side_effect=AssertionError('denied project written'))
    monkeypatch.setattr(jmx.JMXGenerator, 'save_jmx', save)
    await assert_denied(jmx.create_jmx('name', [], 'https://example.invalid', project_id='hidden'))
    save.assert_not_called()


@pytest.mark.asyncio
async def test_plan_and_report_lists_hide_denied_entries(file_acl):
    from app.services.ai.agent.tools import jmx, jmeter
    assert {row['project_id'] for row in (await jmx.list_test_plans())['test_plans']} == {'visible', 'reader'}
    assert {row['execution_id'] for row in (await jmeter.list_report_dir())['reports']} == {'visible', 'reader'}


@pytest.mark.asyncio
async def test_html_report_path_authorized_before_parser_probe(file_acl, monkeypatch):
    from app.services.ai.agent.tools import jmeter
    parser = Mock(return_value='secret/index.html')
    monkeypatch.setattr(jmeter._report_parser, 'get_html_report_url', parser)
    await assert_denied(jmeter.get_html_report_path(str(file_acl.reports / 'hidden')))
    parser.assert_not_called()


@pytest.mark.asyncio
async def test_compare_runs_preflights_both_paths_before_parsing(file_acl, monkeypatch):
    from app.services.ai.agent.tools import resource
    from app.services.jmeter.report_parser import ReportParser
    parser = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(ReportParser, 'parse_jtl', parser)
    await assert_denied(resource.compare_runs(str(file_acl.reports / 'visible' / 'result.jtl'),
                                             str(file_acl.reports / 'hidden' / 'result.jtl')))
    parser.assert_not_called()


@pytest.mark.asyncio
async def test_drift_requires_project_read_before_history_iteration(file_acl, monkeypatch):
    from app.services.ai.agent.tools import resource
    from app.services import storage
    monkeypatch.setattr(storage, 'store', SimpleNamespace(executions={}))
    await assert_denied(resource.detect_performance_drift('hidden'))


@pytest.mark.asyncio
async def test_data_listing_filters_projects_and_denies_explicit_hidden_project(file_acl):
    from app.api import data_routes
    rows = (await data_routes.list_data_files(None))['files']
    assert {row['project_id'] for row in rows} == {'visible', 'reader'}
    await assert_denied(data_routes.list_data_files('hidden'))


@pytest.mark.asyncio
async def test_data_preview_and_delete_guard_before_open(file_acl, monkeypatch):
    from app.api import data_routes
    path = str(file_acl.test_data / 'hidden' / 'input.csv')
    reader = Mock(side_effect=AssertionError('denied file opened'))
    monkeypatch.setattr(data_routes, 'open', reader, raising=False)
    await assert_denied(data_routes.preview_csv(path, 1))
    await assert_denied(data_routes.delete_data_file(path, None))
    reader.assert_not_called()
    assert Path(path).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('project', ['hidden', 'reader'])
async def test_upload_denies_before_consuming_body_or_writing(file_acl, monkeypatch, project):
    from app.api import data_routes
    upload = UploadFile(filename='new.csv', file=io.BytesIO(b'name\nfixture\n'))
    read = AsyncMock(return_value=b'name\nfixture\n')
    monkeypatch.setattr(upload, 'read', read)
    await assert_denied(data_routes.upload_csv(upload, project, None))
    read.assert_not_awaited()
    assert not (file_acl.test_data / project / 'new.csv').exists()


@pytest.mark.asyncio
async def test_data_delete_requires_write_for_reader(file_acl):
    from app.api import data_routes
    path = file_acl.test_data / 'reader' / 'input.csv'
    await assert_denied(data_routes.delete_data_file(str(path), None))
    assert path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['import_jmx', 'clone_jmx'])
async def test_jmx_destination_cannot_escape_selected_project(file_acl, monkeypatch, handler):
    from app.services.ai.agent.tools import jmx
    from app.services import execution_lifecycle
    # Both projects are writable: the selected destination must still be honored.
    source = str(file_acl.plans / 'visible' / 'plan.jmx')
    monkeypatch.setattr(jmx, 'parse_jmx', Mock(return_value={}))
    register = AsyncMock(return_value=SimpleNamespace(id='new', project_id='visible', test_config=None, endpoints_count=0))
    monkeypatch.setattr(execution_lifecycle.execution_lifecycle, 'register_path', register)
    if handler == 'import_jmx':
        call = jmx.import_jmx(source, 'visible', '../other-project')
    else:
        call = jmx.clone_jmx(source, '../other-project')
    await assert_denied(call)
    assert not (file_acl.plans / 'other-project.jmx').exists()
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_rechecks_access_after_body_read_before_writing(file_acl, monkeypatch):
    from app.api import data_routes
    from app.services import project_scope
    upload = UploadFile(filename='after-revoke.csv', file=io.BytesIO(b'fixture'))
    chunks = iter((b'fixture', b''))
    async def read(size=-1):
        monkeypatch.setattr(project_scope, 'require_path', Mock(side_effect=HTTPException(403, 'Access changed')))
        return next(chunks)
    monkeypatch.setattr(upload, 'read', read)
    await assert_denied(data_routes.upload_csv(upload, 'visible', None))
    assert not (file_acl.test_data / 'visible' / 'after-revoke.csv').exists()


@pytest.mark.asyncio
async def test_csv_upload_rejects_oversize_without_leaving_files(file_acl, monkeypatch):
    from app.api import data_routes
    monkeypatch.setattr(data_routes.settings, 'TEST_DATA_MAX_UPLOAD_BYTES', 8, raising=False)
    monkeypatch.setattr(data_routes, 'UPLOAD_CHUNK_BYTES', 4, raising=False)
    upload = UploadFile(filename='oversize.csv', file=io.BytesIO(b'123456789'))

    with pytest.raises(HTTPException) as error:
        await data_routes.upload_csv(upload, 'visible', None)

    assert error.value.status_code == 413
    assert not list((file_acl.test_data / 'visible').glob('oversize*'))


@pytest.mark.asyncio
async def test_csv_upload_reads_body_in_bounded_chunks(file_acl, monkeypatch):
    from app.api import data_routes
    monkeypatch.setattr(data_routes.settings, 'TEST_DATA_MAX_UPLOAD_BYTES', 16, raising=False)
    monkeypatch.setattr(data_routes, 'UPLOAD_CHUNK_BYTES', 4, raising=False)
    upload = UploadFile(filename='bounded.csv', file=io.BytesIO(b'a,b\n1,2\n'))
    original_read = upload.read
    read_sizes = []

    async def recording_read(size=-1):
        read_sizes.append(size)
        return await original_read(size)

    monkeypatch.setattr(upload, 'read', recording_read)
    result = await data_routes.upload_csv(upload, 'visible', None)

    assert result['size_bytes'] == 8
    assert read_sizes and all(0 < size <= 4 for size in read_sizes)
    assert (file_acl.test_data / 'visible' / 'bounded.csv').read_bytes() == b'a,b\n1,2\n'


@pytest.mark.asyncio
async def test_csv_upload_cancellation_waits_for_copy_and_removes_output(file_acl, monkeypatch):
    from app.api import data_routes
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def delayed_copy(spool, filepath):
        started.set()
        release.wait(5)
        Path(filepath).write_bytes(b'orphan')
        finished.set()

    monkeypatch.setattr(data_routes, '_copy_upload_exclusive', delayed_copy)
    upload = UploadFile(filename='cancelled.csv', file=io.BytesIO(b'a\n1\n'))
    task = asyncio.create_task(data_routes.upload_csv(upload, 'visible', None))
    assert await asyncio.to_thread(started.wait, 2)

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()
    assert not (file_acl.test_data / 'visible' / 'cancelled.csv').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,relative', [
    ('get_html_report_path', ('html', 'index.html')),
    ('parse_dashboard_data', ('html', 'statistics.json')),
])
async def test_nested_report_link_is_authorized_before_parser(file_acl, monkeypatch, handler, relative):
    from app.services.ai.agent.tools import jmeter, resource
    from app.services.jmeter.report_parser import ReportParser
    report = file_acl.reports / 'visible'
    alias = report.joinpath(*relative)
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, 'realpath', lambda path: str(file_acl.reports / 'hidden' / 'result.jtl')
                        if Path(path) == alias else realpath(path))
    parser = Mock(side_effect=AssertionError('nested denied report parsed'))
    module = jmeter if handler == 'get_html_report_path' else resource
    monkeypatch.setattr(ReportParser, 'get_html_report_url' if module is jmeter else 'parse_dashboard_json', parser)
    await assert_denied(getattr(module, handler)(str(report)))
    parser.assert_not_called()


@pytest.mark.asyncio
async def test_file_alias_to_hidden_project_is_denied(file_acl, monkeypatch):
    from app.services.ai.agent.tools import file
    alias = file_acl.plans / 'visible' / 'alias.jmx'
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, 'realpath', lambda path: str(file_acl.plans / 'hidden' / 'plan.jmx')
                        if Path(path) == alias else realpath(path))
    await assert_denied(file.read_file(str(alias)))


@pytest.mark.asyncio
@pytest.mark.parametrize('handler', ['parse_jtl_report', 'read_test_log', 'evaluate_sla', 'parse_dashboard_data'])
async def test_report_readers_reject_hidden_paths_before_parser(file_acl, monkeypatch, handler):
    from app.services.ai.agent.tools import jmeter, resource
    from app.services.jmeter.report_parser import ReportParser
    parser = Mock(side_effect=AssertionError('denied report read'))
    for method in ('parse_jtl', 'read_log', 'parse_dashboard_json'):
        monkeypatch.setattr(ReportParser, method, parser)
    module = jmeter if handler in ('parse_jtl_report', 'read_test_log') else resource
    path = file_acl.reports / 'hidden'
    if handler != 'parse_dashboard_data':
        path /= 'jmeter.log' if handler == 'read_test_log' else 'result.jtl'
    await assert_denied(getattr(module, handler)(str(path)))
    parser.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('project,execution', [('reader', 'visible'), ('visible', 'hidden')])
async def test_selfheal_checks_plan_write_and_execution_read_before_agent(file_acl, monkeypatch, project, execution):
    import sys
    from app.services.ai.agent.tools import resource
    diagnose = AsyncMock(side_effect=AssertionError('denied selfheal agent called'))
    monkeypatch.setitem(sys.modules, 'app.services.ai.agents.selfheal_agent',
                        SimpleNamespace(selfheal=SimpleNamespace(diagnose_and_fix=diagnose)))
    await assert_denied(resource.selfheal_test(str(file_acl.plans / project / 'plan.jmx'), execution))
    diagnose.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_scope_filters_files_and_blocks_reader_write_without_db(tmp_path, monkeypatch):
    from app.core import config
    from app.services.project_scope import AccessScope, bind_scope
    from app.services.ai.agent.tools import file, jmx, jmeter
    plans, reports = tmp_path / 'plans', tmp_path / 'reports'
    for project in ('visible', 'hidden'):
        (plans / project).mkdir(parents=True)
        (plans / project / 'plan.jmx').write_text(project)
        (reports / project).mkdir(parents=True)
        (reports / project / 'result.jtl').write_text(project)
    monkeypatch.setattr(config.settings, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(config.settings, 'TEST_PLANS_DIR_DEFAULT', str(plans))
    monkeypatch.setattr(config.settings, 'REPORTS_DIR_DEFAULT', str(reports))
    monkeypatch.setattr(config, 'PATHS_CONFIG_FILE', str(tmp_path / 'no-paths.json'))
    authorization = SimpleNamespace(user_id=987001, revision=0, incarnation='file-test',
                                    project_modes={'visible': 'private', 'hidden': 'private'},
                                    allows=lambda project, action: project == 'visible' and action == 'read')
    scope = AccessScope(authorization,
        plans={project: {'project_id': project, 'jmx_path': str(plans / project / 'plan.jmx')}
               for project in ('visible', 'hidden')},
        executions={project: {'project_id': project, 'report_dir': str(reports / project)}
                    for project in ('visible', 'hidden')})
    with bind_scope(scope):
        assert (await file.read_file(str(plans / 'visible' / 'plan.jmx')))['content'] == 'visible'
        await assert_denied(file.read_file(str(plans / 'hidden' / 'plan.jmx')))
        await assert_denied(file.write_file(str(plans / 'visible' / 'plan.jmx'), 'overwrite'))
        assert [row['name'] for row in (await file.list_directory(str(plans)))['items']] == ['visible']
        assert [row['project_id'] for row in (await jmx.list_test_plans())['test_plans']] == ['visible']
        assert [row['execution_id'] for row in (await jmeter.list_report_dir())['reports']] == ['visible']

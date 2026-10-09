"""Coordinated workers refresh complete DB-authoritative business snapshots."""
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.requires_patched_sqlite


def independent_store():
    from app.services.storage import SharedStore
    value = object.__new__(SharedStore)
    value._initialized = False
    SharedStore.__init__(value)
    return value


@pytest_asyncio.fixture
async def cache_db(tmp_path, monkeypatch):
    from app.models import db
    path = tmp_path / 'cache.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    raw = sqlite3.connect(path)
    raw.execute('PRAGMA journal_mode=WAL')
    raw.commit(); raw.close()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refresh_replaces_updates_and_deletions_as_one_snapshot(cache_db):
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    first, second = independent_store(), independent_store()
    with internal_scope():
        await db_store.create_project({'id': 'p', 'name': 'old'})
        await db_store.create_test_plan({'id': 't', 'project_id': 'p', 'name': 'old'})
        await db_store.create_execution({'id': 'e', 'project_id': 'p', 'test_plan_id': 't'})
        assert await first.refresh_if_stale(force=True)
        assert await second.refresh_if_stale(force=True)
        await db_store.update_project('p', {'name': 'new'})
        await db_store.delete_test_plan('t')
        assert await first.refresh_if_stale()
    assert first.get_project('p').name == 'new'
    assert first.get_test_plan('t') is None and first.get_execution('e') is None
    assert second.get_project('p').name == 'old', 'other worker changes only at its next boundary'
    with internal_scope():
        assert await second.refresh_if_stale()
    assert second.get_project('p').name == 'new' and second.get_test_plan('t') is None


@pytest.mark.asyncio
async def test_unchanged_revision_does_not_reload_business_rows(cache_db, monkeypatch):
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    value = independent_store()
    with internal_scope():
        await db_store.create_project({'id': 'p', 'name': 'fixture'})
        assert await value.refresh_if_stale(force=True)
        monkeypatch.setattr(db_store, 'list_projects', lambda *a, **k: pytest.fail('unchanged cache reloaded'))
        assert not await value.refresh_if_stale()


@pytest.mark.asyncio
async def test_refresh_retries_when_revision_changes_during_snapshot(cache_db, monkeypatch):
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    value = independent_store()
    with internal_scope():
        await db_store.create_project({'id': 'p', 'name': 'old'})
        original = db_store.list_test_plans
        changed = False
        async def racing(*args, **kwargs):
            nonlocal changed
            result = await original(*args, **kwargs)
            if not changed:
                changed = True
                await db_store.update_project('p', {'name': 'new'})
            return result
        monkeypatch.setattr(db_store, 'list_test_plans', racing)
        assert await value.refresh_if_stale(force=True)
    assert changed and value.get_project('p').name == 'new'


@pytest.mark.asyncio
async def test_real_process_revision_invalidates_existing_cache(cache_db, tmp_path):
    from app.models import db
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    value = independent_store()
    with internal_scope():
        await db_store.create_project({'id': 'p', 'name': 'parent'})
        await value.refresh_if_stale(force=True)
    database = Path(db.get_session_factory().kw['bind'].url.database)
    helper = tmp_path / 'worker.py'
    helper.write_bytes(Path(__file__).with_name('cross_feature_worker.py').read_bytes())
    result_path, request_path = tmp_path / 'result.json', tmp_path / 'request.json'
    request_path.write_text(json.dumps({
        'mode': 'cache', 'database': str(database), 'project_id': 'p',
        'name': 'child', 'result': str(result_path),
    }), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        buffer = ctypes.create_unicode_buffer(32768)
        assert kernel.GetModuleFileNameW(kernel.GetModuleHandleW('sqlite3.dll'), buffer, len(buffer))
        environment['COORDINATION_TEST_SQLITE_DLL'] = buffer.value
    child = subprocess.run([sys.executable, '-B', str(helper), str(request_path)], env=environment,
                           capture_output=True, timeout=15)
    assert child.returncode == 0, child.stderr.decode(errors='replace')
    proof = json.loads(result_path.read_text(encoding='utf-8'))
    assert proof['pid'] != os.getpid() and proof['sqlite'] == sqlite3.sqlite_version
    assert proof['journal_mode'] == 'wal'
    assert value.get_project('p').name == 'parent'
    with internal_scope():
        assert await value.refresh_if_stale()
    assert value.get_project('p').name == 'child'


@pytest.mark.asyncio
async def test_stale_business_versions_cannot_overwrite_newer_rows(cache_db):
    from fastapi import HTTPException
    from app.services.db_store import db_store
    from app.services.project_scope import internal_scope
    with internal_scope():
        project = await db_store.create_project({'id': 'p', 'name': 'first'})
        plan = await db_store.create_test_plan({'id': 't', 'project_id': 'p', 'name': 'first'})
        execution = await db_store.create_execution({'id': 'e', 'project_id': 'p', 'test_plan_id': 't'})
        await db_store.update_project('p', {'name': 'new'}, expected_version=project['storage_version'])
        await db_store.update_test_plan('t', {'name': 'new'}, expected_version=plan['storage_version'])
        await db_store.update_execution('e', {'status': 'running'}, expected_version=execution['storage_version'])
        for method, identity, updates, version in (
            (db_store.update_project, 'p', {'description': 'stale'}, project['storage_version']),
            (db_store.update_test_plan, 't', {'description': 'stale'}, plan['storage_version']),
            (db_store.update_execution, 'e', {'status': 'failed'}, execution['storage_version']),
        ):
            with pytest.raises(HTTPException) as conflict:
                await method(identity, updates, expected_version=version)
            assert conflict.value.status_code == 409
        assert (await db_store.get_project('p'))['name'] == 'new'
        assert (await db_store.get_test_plan('t'))['name'] == 'new'
        assert (await db_store.get_execution('e'))['status'] == 'running'


@pytest.mark.asyncio
async def test_stale_cached_project_cannot_recreate_a_concurrently_deleted_row(cache_db):
    from app.models.schemas import ProjectInfo
    from app.services.db_store import db_store
    from app.services.execution_lifecycle import persist_project
    from app.services.project_scope import internal_scope
    with internal_scope():
        created = await db_store.create_project({'id': 'p', 'name': 'old'})
        stale = ProjectInfo(id='p', name='stale', storage_version=created['storage_version'])
        assert await db_store.delete_project('p')
        with pytest.raises(LookupError):
            await persist_project(stale, create=False)
        assert await db_store.get_project('p') is None


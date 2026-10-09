"""Synchronous resource guards use explicit, current authorization scopes."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def scope(tmp_path, monkeypatch, *, writable=False):
    from app.services import project_scope
    from app.services.project_scope import AccessScope
    monkeypatch.setattr(project_scope, 'settings', SimpleNamespace(
        TEST_PLANS_DIR=str(tmp_path / 'plans'), REPORTS_DIR=str(tmp_path / 'reports'),
        BACKUPS_DIR=str(tmp_path / 'backups'), BASE_DIR=str(tmp_path)))
    snapshot = SimpleNamespace(user_id=7001, global_role='operator', revision=0,
        session_key=(7001, 'operator', 0),
        project_modes={'mine': 'private', 'other': 'private', 'legacy': 'legacy_shared'},
        allows=lambda project, action='read': project in ('mine', 'legacy') and (action == 'read' or writable))
    return AccessScope(snapshot, plans={'p1': {'project_id': 'other', 'jmx_path': str(tmp_path / 'plans' / 'other' / 'x.jmx')}},
        executions={'run-other': {'project_id': 'other', 'report_dir': str(tmp_path / 'reports' / 'run-other')}})


def test_project_scope_missing_scope_never_grants_resource_access():
    from app.services.project_scope import bind_scope, require_project, require_path
    with bind_scope(None):
        for operation in (lambda: require_project('mine'), lambda: require_path('anything')):
            with pytest.raises(HTTPException):
                operation()


def test_project_scope_internal_maintenance_is_explicit():
    from app.services.project_scope import bind_scope, internal_scope, require_project
    with bind_scope(None):
        with internal_scope():
            require_project('maintenance')
        with pytest.raises(HTTPException):
            require_project('maintenance')


def test_project_scope_read_does_not_grant_write(tmp_path, monkeypatch):
    from app.services.project_scope import bind_scope, require_project
    with bind_scope(scope(tmp_path, monkeypatch)):
        require_project('mine')
        with pytest.raises(HTTPException) as denied:
            require_project('other')
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as denied:
            require_project('mine', write=True)
        assert denied.value.status_code == 403


def test_project_scope_paths_use_server_ownership_and_reject_orphans(tmp_path, monkeypatch):
    from app.services.project_scope import bind_scope, require_path, can_path
    with bind_scope(scope(tmp_path, monkeypatch, writable=True)):
        require_path(tmp_path / 'plans' / 'mine' / 'new.jmx', write=True)
        require_path(tmp_path / 'test_data' / 'mine' / 'data.csv', write=True)
        require_path(tmp_path / 'reports', allow_root_listing=True)
        for path in (tmp_path / 'plans' / 'other' / 'x.jmx',
                     tmp_path / 'reports' / 'run-other' / 'result.jtl',
                     tmp_path / 'reports' / 'unknown' / 'result.jtl',
                     tmp_path / 'backups' / 'private.jmx',
                     tmp_path / 'plans' / 'unregistered.jmx'):
            assert not can_path(path)
        assert not can_path(tmp_path / 'reports')


def test_project_scope_conflicting_knowledge_sources_do_not_fall_back_to_shared(tmp_path, monkeypatch):
    from app.services.project_scope import bind_scope, can_document
    with bind_scope(scope(tmp_path, monkeypatch)):
        assert not can_document(SimpleNamespace(source_execution_id='run-other', source_plan_id=None))
        assert not can_document(SimpleNamespace(source_execution_id=None, source_plan_id='missing'))
        assert not can_document(SimpleNamespace(source_execution_id=None, source_plan_id=None))


def test_project_scope_revocation_invalidates_copied_scope(tmp_path, monkeypatch):
    from app.services import project_scope
    from contextvars import copy_context
    monkeypatch.setattr(project_scope, '_revoked_revisions', {})
    with project_scope.bind_scope(scope(tmp_path, monkeypatch)):
        copied = copy_context()
        project_scope.note_revocation(7001, 1)
        with pytest.raises(HTTPException) as denied:
            copied.run(project_scope.require_project, 'mine')
        assert denied.value.status_code == 403


def test_project_scope_registration_cannot_override_managed_path_owner(tmp_path, monkeypatch):
    from app.services.project_scope import AccessScope, bind_scope, can_path
    original = scope(tmp_path, monkeypatch)
    rebound = tmp_path / 'plans' / 'other' / 'private.jmx'
    value = AccessScope(original.authorization, plans={'visible': {
        'project_id': 'mine', 'jmx_path': str(rebound)}})
    with bind_scope(value):
        assert not can_path(rebound)


@pytest.mark.parametrize('kind', ['project', 'execution'])
def test_project_scope_case_aliases_require_every_registered_owner(tmp_path, monkeypatch, kind):
    import os
    if os.path.normcase('P') != os.path.normcase('p'):
        pytest.skip('Case-insensitive filesystem ownership boundary')
    from app.services.project_scope import AccessScope, bind_scope, can_path
    original = scope(tmp_path, monkeypatch)
    original.authorization.project_modes = {'P': 'private', 'p': 'private'}
    original.authorization.allows = lambda project, action='read': project == 'P'
    value = AccessScope(original.authorization, executions={
        'Run': {'project_id': 'P'}, 'run': {'project_id': 'p'}})
    path = (tmp_path / 'plans' / 'P' / 'x.jmx' if kind == 'project'
            else tmp_path / 'reports' / 'Run' / 'result.jtl')
    with bind_scope(value):
        assert not can_path(path)

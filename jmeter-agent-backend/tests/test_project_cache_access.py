"""Cache views must not leak names, object references, or unauthorized mutations."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
def cache(monkeypatch):
    from app.services.storage import store
    for name in ('_projects', '_test_plans', '_executions'):
        monkeypatch.setattr(store, name, {})
    return store


def access_scope(*, write=False):
    from app.services.project_scope import AccessScope
    auth = SimpleNamespace(user_id=901, revision=0, session_key=(901, 0),
        allows=lambda pid, action='read': pid == 'mine' and (action == 'read' or write))
    return AccessScope(auth)


@pytest.mark.parametrize('collection', ['projects', 'test_plans', 'executions'])
def test_project_cache_filtered_views_and_detached_reads(cache, collection):
    from app.services.project_scope import internal_scope, bind_scope
    with internal_scope():
        rows = getattr(cache, collection)
        rows['mine'] = SimpleNamespace(id='mine', project_id='mine', nested={'secret': 'own'})
        rows['hidden'] = SimpleNamespace(id='hidden', project_id='hidden', nested={'secret': 'hidden'})
    with bind_scope(access_scope()):
        view = getattr(cache, collection)
        assert list(view) == ['mine']
        assert view.get('hidden') is None
        assert 'hidden' not in view
        assert len(view) == 1
        row = view['mine']
        row.nested['secret'] = 'changed outside cache'
        assert view['mine'].nested['secret'] == 'own'
        with pytest.raises(HTTPException):
            view['mine'] = row
        with pytest.raises(HTTPException):
            del view['mine']
    with internal_scope():
        assert getattr(cache, collection)['hidden'].nested['secret'] == 'hidden'


@pytest.mark.parametrize('collection', ['projects', 'test_plans', 'executions'])
def test_project_cache_cannot_overwrite_or_rehome_hidden_id(cache, collection):
    from app.services.project_scope import internal_scope, bind_scope
    with internal_scope():
        getattr(cache, collection)['hidden'] = SimpleNamespace(id='hidden', project_id='hidden')
    with bind_scope(access_scope(write=True)):
        with pytest.raises(HTTPException):
            getattr(cache, collection)['hidden'] = SimpleNamespace(id='hidden', project_id='mine')
    with internal_scope():
        assert getattr(cache, collection)['hidden'].project_id == 'hidden'

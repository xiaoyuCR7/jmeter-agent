"""Project ACL contracts, exclusively against a disposable SQLite database."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool


@pytest_asyncio.fixture
async def acl_db(tmp_path, monkeypatch, request):
    from app.models import db
    from app.services.db_store import db_store
    from app.services.security_context import current_principal

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'acl.db'),
                                 poolclass=AsyncAdaptedQueuePool,
                                 pool_size=getattr(request, 'param', 1), max_overflow=0)
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=DELETE')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        return factory()
    monkeypatch.setattr(db, '_engine', engine)
    monkeypatch.setattr(db, '_async_session_factory', factory)
    monkeypatch.setattr(db_store, '_session', session)
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    users = {}
    async with factory() as session:
        for name, role in [('admin', 'admin'), ('owner', 'operator'), ('member', 'operator'),
                           ('viewer', 'viewer'), ('outsider', 'operator')]:
            user = db.UserModel(username=name, role=role, password_hash='not-a-secret')
            session.add(user)
            users[name] = user
        session.add_all([db.ProjectModel(id='legacy', name='Legacy'),
                         db.ProjectModel(id='private', name='Private')])
        await session.commit()
    marker = current_principal.set(None)
    yield SimpleNamespace(factory=factory, engine=engine, users=users)
    current_principal.reset(marker)
    await engine.dispose()


@contextmanager
def principal(user):
    from app.services.security_context import current_principal
    marker = current_principal.set(SimpleNamespace(user_id=user.id, username=user.username, role=user.role))
    try:
        yield
    finally:
        current_principal.reset(marker)


def service():
    from app.services import project_access
    return project_access.access


async def make_private(db, members=None):
    access = service()
    with principal(db.users['admin']):
        preview = await access.preview('private', db.users['owner'].id, members or {})
        await access.apply_preview('private', db.users['owner'].id, members or {}, preview['revision'])


@pytest.mark.asyncio
async def test_acl_service_is_present_and_requires_authentication(acl_db):
    import importlib.util
    assert importlib.util.find_spec('app.services.project_access') is not None, 'ACL service missing'
    with pytest.raises(HTTPException) as denied:
        await service().require_project('legacy')
    assert denied.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('name,write,manage', [('viewer', False, False), ('owner', True, False), ('admin', True, True)])
async def test_legacy_shared_preserves_global_role_intersection(acl_db, name, write, manage):
    with principal(acl_db.users[name]):
        snapshot = await service().snapshot()
        assert snapshot.allows('legacy', 'read')
        assert snapshot.allows('legacy', 'write') is write
        assert snapshot.allows('legacy', 'manage') is manage
        assert snapshot.project_modes['legacy'] == 'legacy_shared'
        await service().require_project('legacy')


@pytest.mark.asyncio
async def test_private_owner_members_and_admin_content_isolation(acl_db):
    await make_private(acl_db, {acl_db.users['member'].id: 'viewer', acl_db.users['viewer'].id: 'operator'})
    for name, read, write, manage in [('owner', True, True, True), ('member', True, False, False),
                                     ('viewer', True, False, False), ('outsider', False, False, False),
                                     ('admin', False, False, True)]:
        with principal(acl_db.users[name]):
            snapshot = await service().snapshot()
            assert tuple(snapshot.allows('private', action) for action in ('read', 'write', 'manage')) == (read, write, manage)
            if read:
                await service().require_project('private')
            else:
                with pytest.raises(HTTPException) as denied:
                    await service().require_project('private')
                assert denied.value.status_code == 404
    with principal(acl_db.users['viewer']):
        with pytest.raises(HTTPException) as denied:
            await service().require_project('private', 'write')
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_current_db_role_and_active_state_override_stale_principal(acl_db):
    from app.models.db import UserModel
    await make_private(acl_db)
    with principal(acl_db.users['owner']):
        async with acl_db.factory() as session:
            owner = await session.get(UserModel, acl_db.users['owner'].id)
            owner.role = 'viewer'
            await session.commit()
        assert not (await service().snapshot()).allows('private', 'write')
        async with acl_db.factory() as session:
            owner = await session.get(UserModel, acl_db.users['owner'].id)
            owner.is_active = False
            await session.commit()
        with pytest.raises(HTTPException) as denied:
            await service().require_project('private')
        assert denied.value.status_code == 401


@pytest.mark.asyncio
async def test_owner_deletion_leaves_private_and_same_name_does_not_inherit(acl_db):
    from app.models.db import UserModel, ProjectAccessPolicyModel
    await make_private(acl_db)
    old_owner = acl_db.users['owner']
    async with acl_db.factory() as session:
        await session.execute(delete(UserModel).where(UserModel.id == old_owner.id))
        await session.commit()
        replacement = UserModel(username=old_owner.username, role='operator', password_hash='new')
        session.add(replacement)
        await session.commit()
        policy = await session.get(ProjectAccessPolicyModel, 'private')
        assert policy.mode == 'private' and policy.owner_id is None
    for user, expected in [(old_owner, 401), (replacement, 404)]:
        with principal(user), pytest.raises(HTTPException) as denied:
            await service().require_project('private')
        assert denied.value.status_code == expected


@pytest.mark.asyncio
async def test_snapshot_is_immutable_and_scoped_by_stable_identity(acl_db):
    with principal(acl_db.users['owner']):
        snapshot = await service().snapshot()
    assert snapshot.session_key[:3] == (acl_db.users['owner'].id, 'operator', 0)
    with pytest.raises(TypeError):
        snapshot.project_modes['private'] = 'private'
    with pytest.raises(TypeError):
        snapshot.projects['legacy'] = None
    with pytest.raises((AttributeError, TypeError)):
        snapshot.projects['legacy'].read = False


@pytest.mark.asyncio
async def test_init_backfills_legacy_without_changing_private_or_project_data(acl_db):
    from app.models.db import init_db, ProjectAccessPolicyModel, ProjectModel
    await make_private(acl_db)
    await init_db()
    await init_db()
    async with acl_db.factory() as session:
        legacy = await session.get(ProjectAccessPolicyModel, 'legacy')
        private = await session.get(ProjectAccessPolicyModel, 'private')
        assert (legacy.mode, legacy.owner_id) == ('legacy_shared', None)
        assert (private.mode, private.owner_id) == ('private', acl_db.users['owner'].id)
        assert (await session.get(ProjectModel, 'legacy')).name == 'Legacy'


@pytest.mark.asyncio
async def test_invalid_policy_and_unavailable_storage_fail_closed(acl_db):
    from app.models.db import ProjectAccessPolicyModel
    async with acl_db.factory() as session:
        session.add(ProjectAccessPolicyModel(project_id='private', mode='unexpected', version=1))
        await session.commit()
    with principal(acl_db.users['owner']):
        with pytest.raises(HTTPException) as denied:
            await service().require_project('private')
        assert denied.value.status_code == 503
        async with acl_db.engine.begin() as connection:
            await connection.execute(text('DROP TABLE project_access_policies'))
        with pytest.raises(HTTPException) as denied:
            await service().snapshot()
        assert denied.value.status_code == 503
        assert 'SELECT' not in denied.value.detail and 'sqlite' not in denied.value.detail


@pytest.mark.asyncio
async def test_conversion_requires_admin_and_preview_binds_actor_and_proposal(acl_db):
    access = service()
    with principal(acl_db.users['owner']), pytest.raises(HTTPException) as denied:
        await access.preview('legacy', acl_db.users['owner'].id, {})
    assert denied.value.status_code == 403
    with principal(acl_db.users['admin']):
        preview = await access.preview('legacy', acl_db.users['owner'].id, {})
        with pytest.raises(HTTPException) as denied:
            await access.apply_preview('legacy', acl_db.users['owner'].id, {acl_db.users['member'].id: 'viewer'}, preview['revision'])
        assert denied.value.status_code == 409
        await access.apply_preview('legacy', acl_db.users['owner'].id, {}, preview['revision'])
        with pytest.raises(HTTPException) as denied:
            await access.apply_preview('legacy', acl_db.users['owner'].id, {}, preview['revision'])
        assert denied.value.status_code == 409
    with principal(acl_db.users['owner']):
        preview = await access.preview('legacy', acl_db.users['owner'].id, {})
    with principal(acl_db.users['admin']), pytest.raises(HTTPException) as denied:
        await access.apply_preview('legacy', acl_db.users['owner'].id, {}, preview['revision'])
    assert denied.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('owner,members', [('missing', {}), ('owner', {'member': 'admin'}), ('owner', {'missing': 'viewer'})])
async def test_invalid_targets_rejected_before_policy_write(acl_db, owner, members):
    owner_id = acl_db.users[owner].id if owner in acl_db.users else 99999
    mapping = {(acl_db.users[name].id if name in acl_db.users else 99999): role for name, role in members.items()}
    with principal(acl_db.users['admin']), pytest.raises(HTTPException) as denied:
        await service().preview('legacy', owner_id, mapping)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_apply_is_atomic_audited_and_revokes_only_affected_users(acl_db):
    from app.models.db import ProjectAccessAuditModel
    access = service()
    before = {}
    for name, user in acl_db.users.items():
        with principal(user):
            before[name] = (await access.snapshot()).revision
    await make_private(acl_db, {acl_db.users['member'].id: 'operator'})
    for name, user in acl_db.users.items():
        with principal(user):
            revision = (await access.snapshot()).revision
            assert revision == before[name] + (name in ('admin', 'viewer', 'outsider'))
    with principal(acl_db.users['owner']):
        preview = await access.preview('private', acl_db.users['owner'].id, {acl_db.users['member'].id: 'viewer'})
        await access.apply_preview('private', acl_db.users['owner'].id, {acl_db.users['member'].id: 'viewer'}, preview['revision'])
    with principal(acl_db.users['member']):
        assert (await access.snapshot()).revision == before['member'] + 1
    async with acl_db.factory() as session:
        audits = (await session.execute(select(ProjectAccessAuditModel))).scalars().all()
        assert len(audits) == 2
        assert audits[-1].actor_id == acl_db.users['owner'].id
        assert audits[-1].before_policy['members'][str(acl_db.users['member'].id)] == 'operator'
        assert audits[-1].after_policy['members'][str(acl_db.users['member'].id)] == 'viewer'
        assert 'not-a-secret' not in repr(audits[-1].after_policy)


@pytest.mark.asyncio
async def test_new_private_helper_does_not_commit_or_revoke(acl_db):
    from app.models.db import ProjectModel, ProjectAccessPolicyModel
    access = service()
    with principal(acl_db.users['owner']):
        key = (await access.snapshot()).session_key
        async with acl_db.factory() as session:
            session.add(ProjectModel(id='new', name='New'))
            await session.flush()
            await access.initialize_private(session, 'new', acl_db.users['owner'].id)
            await session.rollback()
        async with acl_db.factory() as session:
            assert await session.get(ProjectModel, 'new') is None
            assert await session.get(ProjectAccessPolicyModel, 'new') is None
            session.add(ProjectModel(id='new', name='New'))
            await session.flush()
            await access.initialize_private(session, 'new', acl_db.users['owner'].id)
            await session.commit()
        assert (await access.snapshot()).session_key == key
        await access.require_project('new', 'manage')


@pytest.mark.asyncio
async def test_two_apply_calls_cannot_both_consume_the_same_preview(acl_db):
    access = service()
    with principal(acl_db.users['admin']):
        preview = await access.preview('legacy', acl_db.users['owner'].id, {})
        results = await asyncio.gather(*[access.apply_preview('legacy', acl_db.users['owner'].id, {}, preview['revision'])
                                        for _ in range(2)], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409


@pytest.mark.asyncio
async def test_pure_grants_keep_session_identity_but_owner_demotion_revokes(acl_db):
    access = service()
    await make_private(acl_db)
    before = {}
    for name, user in acl_db.users.items():
        with principal(user):
            before[name] = (await access.snapshot()).session_key
    with principal(acl_db.users['owner']):
        mapping = {acl_db.users['member'].id: 'operator'}
        preview = await access.preview('private', acl_db.users['owner'].id, mapping)
        await access.apply_preview('private', acl_db.users['owner'].id, mapping, preview['revision'])
    for name, user in acl_db.users.items():
        with principal(user):
            assert (await access.snapshot()).session_key == before[name]
    with principal(acl_db.users['owner']):
        mapping = {acl_db.users['owner'].id: 'operator'}
        preview = await access.preview('private', acl_db.users['member'].id, mapping)
        await access.apply_preview('private', acl_db.users['member'].id, mapping, preview['revision'])
        assert (await access.snapshot()).revision == before['owner'][2] + 1
    with principal(acl_db.users['member']):
        assert (await access.snapshot()).session_key == before['member']


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_members_policy_and_revisions(acl_db):
    from app.models.db import ProjectAccessPolicyModel, UserAccessRevisionModel, ProjectMemberModel
    await make_private(acl_db, {acl_db.users['member'].id: 'operator'})
    access = service()
    async with acl_db.engine.begin() as connection:
        await connection.execute(text("CREATE TRIGGER fail_acl_audit BEFORE INSERT ON project_access_audits "
                                      "BEGIN SELECT RAISE(ABORT, 'private sensitive test error'); END"))
    with principal(acl_db.users['owner']):
        preview = await access.preview('private', acl_db.users['owner'].id, {})
        with pytest.raises(HTTPException) as denied:
            await access.apply_preview('private', acl_db.users['owner'].id, {}, preview['revision'])
        assert denied.value.status_code in (409, 503)
        assert 'sensitive' not in denied.value.detail
    async with acl_db.factory() as session:
        assert (await session.get(ProjectAccessPolicyModel, 'private')).version == preview['current']['version']
        assert await session.get(ProjectMemberModel, ('private', acl_db.users['member'].id)) is not None
        assert (await session.get(UserAccessRevisionModel, acl_db.users['member'].id)).revision == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('acl_db', [2], indirect=True)
@pytest.mark.parametrize('existing', [False, True])
async def test_independent_connections_conflict_after_same_policy_read(acl_db, monkeypatch, existing):
    access = service()
    if existing:
        await make_private(acl_db)
    original = access._management
    reached = 0
    ready = asyncio.Event()
    async def simultaneous_read(session, project_id):
        nonlocal reached
        result = await original(session, project_id)
        reached += 1
        if reached == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return result
    with principal(acl_db.users['admin']):
        mapping = {acl_db.users['member'].id: 'operator'}
        preview = await access.preview('private', acl_db.users['owner'].id, mapping)
        monkeypatch.setattr(access, '_management', simultaneous_read)
        results = await asyncio.gather(*[access.apply_preview('private', acl_db.users['owner'].id, mapping, preview['revision'])
                                        for _ in range(2)], return_exceptions=True)
    assert reached == 2
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409


@pytest.mark.asyncio
async def test_memberships_are_unique_and_deleted_user_membership_cascades(acl_db):
    from sqlalchemy.exc import IntegrityError
    from app.models.db import UserModel, ProjectMemberModel
    member = acl_db.users['member']
    await make_private(acl_db, {member.id: 'operator'})
    async with acl_db.factory() as session:
        session.add(ProjectMemberModel(project_id='private', user_id=member.id, role='viewer'))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(delete(UserModel).where(UserModel.id == member.id))
        await session.commit()
        assert await session.get(ProjectMemberModel, ('private', member.id)) is None


@pytest.mark.asyncio
async def test_target_deactivation_invalidates_pending_preview(acl_db):
    from app.models.db import UserModel
    access = service()
    with principal(acl_db.users['admin']):
        mapping = {acl_db.users['member'].id: 'viewer'}
        preview = await access.preview('private', acl_db.users['owner'].id, mapping)
        async with acl_db.factory() as session:
            (await session.get(UserModel, acl_db.users['member'].id)).is_active = False
            await session.commit()
        with pytest.raises(HTTPException) as denied:
            await access.apply_preview('private', acl_db.users['owner'].id, mapping, preview['revision'])
        assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_deletion_revisions_commit_atomically_and_only_for_affected_users(acl_db):
    from app.models.db import ProjectModel
    access = service()
    await make_private(acl_db)
    before = {}
    for name, user in acl_db.users.items():
        with principal(user):
            before[name] = (await access.snapshot()).revision
    async with acl_db.factory() as session:
        await access.revoke_project(session, 'private')
        await session.execute(delete(ProjectModel).where(ProjectModel.id == 'private'))
        await session.rollback()
    with principal(acl_db.users['owner']):
        assert (await access.snapshot()).revision == before['owner']
    async with acl_db.factory() as session:
        await access.revoke_project(session, 'private')
        await session.execute(delete(ProjectModel).where(ProjectModel.id == 'private'))
        await session.commit()
    for name, user in acl_db.users.items():
        with principal(user):
            assert (await access.snapshot()).revision == before[name] + (name in ('owner', 'admin'))


@pytest.mark.asyncio
async def test_recreated_same_numeric_id_gets_new_snapshot_incarnation(acl_db):
    from app.models.db import UserModel
    user = acl_db.users['outsider']
    with principal(user):
        original = await service().snapshot()
        assert len(original.incarnation) >= 32
    async with acl_db.factory() as session:
        await session.execute(delete(UserModel).where(UserModel.id == user.id))
        await session.commit()
        replacement = UserModel(id=user.id, username=user.username, role=user.role, password_hash='new')
        session.add(replacement)
        await session.commit()
    with principal(replacement):
        recreated = await service().snapshot()
        assert recreated.session_key[:3] == original.session_key[:3]
        assert recreated.incarnation != original.incarnation
        assert recreated.session_key != original.session_key
        assert (await service().snapshot()).session_key == recreated.session_key


@pytest.mark.asyncio
@pytest.mark.parametrize('acl_db', [2], indirect=True)
async def test_snapshot_retries_if_revision_changes_during_policy_loading(acl_db, monkeypatch):
    access = service()
    await make_private(acl_db, {acl_db.users['member'].id: 'operator'})
    with principal(acl_db.users['member']):
        before = await access.snapshot()
    original = access._policies
    changed = False
    async def revoke_after_read(session, project_id=None):
        nonlocal changed
        result = await original(session, project_id)
        if project_id is None and not changed:
            changed = True
            with principal(acl_db.users['admin']):
                preview = await access.preview('private', acl_db.users['owner'].id, {})
                await access.apply_preview('private', acl_db.users['owner'].id, {}, preview['revision'])
        return result
    monkeypatch.setattr(access, '_policies', revoke_after_read)
    with principal(acl_db.users['member']):
        snapshot = await access.snapshot()
        assert not snapshot.allows('private'), 'Snapshot cannot pair stale grants with current revocation identity'
        assert snapshot.revision == before.revision + 1


@pytest.mark.asyncio
async def test_committed_revocation_invalidates_copied_sync_scope(acl_db):
    from app.services.project_scope import AccessScope, bind_scope, current_scope
    access = service()
    await make_private(acl_db, {acl_db.users['member'].id: 'operator'})
    with principal(acl_db.users['member']):
        snapshot = await access.snapshot()
    with bind_scope(AccessScope(snapshot)):
        with principal(acl_db.users['owner']):
            preview = await access.preview('private', acl_db.users['owner'].id, {})
            await access.apply_preview('private', acl_db.users['owner'].id, {}, preview['revision'])
        with pytest.raises(HTTPException) as denied:
            current_scope()
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_rollback_does_not_emit_revocation_notifications(acl_db, monkeypatch):
    from app.services import project_scope
    access = service()
    await make_private(acl_db, {acl_db.users['member'].id: 'operator'})
    notifications = []
    monkeypatch.setattr(project_scope, 'note_revocation', lambda *args: notifications.append(args))
    async with acl_db.engine.begin() as connection:
        await connection.execute(text("CREATE TRIGGER fail_commit_audit BEFORE INSERT ON project_access_audits "
                                      "BEGIN SELECT RAISE(ABORT, 'failure'); END"))
    with principal(acl_db.users['owner']):
        preview = await access.preview('private', acl_db.users['owner'].id, {})
        with pytest.raises(HTTPException):
            await access.apply_preview('private', acl_db.users['owner'].id, {}, preview['revision'])
    assert notifications == []


@pytest.mark.asyncio
async def test_preview_rejects_recreated_target_with_same_numeric_id(acl_db):
    from app.models.db import UserModel
    access = service()
    member = acl_db.users['member']
    mapping = {member.id: 'operator'}
    with principal(acl_db.users['admin']):
        preview = await access.preview('legacy', acl_db.users['owner'].id, mapping)
        async with acl_db.factory() as session:
            await session.execute(delete(UserModel).where(UserModel.id == member.id))
            await session.commit()
            session.add(UserModel(id=member.id, username=member.username, role='operator', password_hash='new'))
            await session.commit()
        with pytest.raises(HTTPException) as denied:
            await access.apply_preview('legacy', acl_db.users['owner'].id, mapping, preview['revision'])
        assert denied.value.status_code == 409

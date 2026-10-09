"""Bound authorization for synchronous readers; refresh at async request boundaries."""
import os
import re
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType

from fastapi import HTTPException

from app.core.config import settings
from app.services.security_context import current_principal, current_access_token

_scope = ContextVar('project_access_scope', default=None)
_internal = ContextVar('project_access_internal', default=False)
_scheduled_identity = ContextVar('project_scheduled_identity', default=None)
_revoked_revisions = {}
ACCESS_CHANGED = 'Project access changed; start a new request or session'


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _within(path, root):
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


@dataclass(frozen=True)
class AccessScope:
    authorization: object
    plans: dict = field(default_factory=dict)
    executions: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ('plans', 'executions'):
            object.__setattr__(self, name, MappingProxyType({
                key: MappingProxyType(dict(value)) for key, value in getattr(self, name).items()}))

    @property
    def session_key(self):
        return self.authorization.session_key


@contextmanager
def internal_scope():
    """Explicit maintenance capability; never use around user HTTP/MCP work."""
    token = _internal.set(True)
    try:
        yield
    finally:
        _internal.reset(token)


@contextmanager
def bind_scope(value):
    token = _scope.set(value)
    internal_token = _internal.set(False)
    schedule_token = _scheduled_identity.set(None)
    try:
        yield value
    finally:
        _scheduled_identity.reset(schedule_token)
        _internal.reset(internal_token)
        _scope.reset(token)


def note_revocation(user_id, revision, incarnation=None):
    """Invalidate copied scopes after commit, without sharing mutable snapshots."""
    key = (user_id, incarnation)
    _revoked_revisions[key] = max(revision, _revoked_revisions.get(key, 0))


def current_scope():
    if _internal.get():
        return None
    value = _scope.get()
    if value is None:
        raise HTTPException(401 if current_principal.get() is None else 503,
                            'Project authorization scope unavailable')
    auth = value.authorization
    key = (auth.user_id, getattr(auth, 'incarnation', None))
    if _revoked_revisions.get(key, 0) > auth.revision:
        raise HTTPException(403, ACCESS_CHANGED)
    return value


def require_project(project_id, *, write=False):
    value = current_scope()
    if value is None:
        return
    if not value.authorization.allows(project_id, 'read'):
        raise HTTPException(404, 'Project resource not found')
    if write and not value.authorization.allows(project_id, 'write'):
        raise HTTPException(403, 'Project write permission required')


def can_project(project_id, *, write=False):
    value = current_scope()
    return value is None or value.authorization.allows(project_id, 'write' if write else 'read')


def require_project_manage(project_id):
    value = current_scope()
    if value is not None and not value.authorization.allows(project_id, 'manage'):
        raise HTTPException(404, 'Project resource not found')


def resource_project(kind, resource_id):
    value = current_scope()
    if value is None:
        return None
    record = (value.plans if kind == 'plan' else value.executions).get(resource_id)
    if record is None:
        raise HTTPException(404, 'Project resource not found')
    return record['project_id']


def require_plan(plan_id, *, write=False):
    project = resource_project('plan', plan_id)
    if project is not None:
        require_project(project, write=write)


def require_execution(execution_id, *, write=False):
    project = resource_project('execution', execution_id)
    if project is not None:
        require_project(project, write=write)


def require_path(path, *, write=False, allow_root_listing=False):
    value = current_scope()
    if value is None:
        return
    target = _canonical(path)
    owners = set()
    for record in value.plans.values():
        if record.get('jmx_path'):
            source = _canonical(record['jmx_path'])
            if (target == source or (target.startswith(source + '.') and
                    re.fullmatch(r'backup|(?:selfheal|autotune)\.[a-f0-9]{32}\.bak', target[len(source) + 1:]))):
                owners.add(record['project_id'])
    for record in value.executions.values():
        if any(record.get(key) and target == _canonical(record[key])
               for key in ('jmx_path', 'source_jmx_path', 'log_path')):
            owners.add(record['project_id'])
        if record.get('report_dir') and _within(target, _canonical(record['report_dir'])):
            owners.add(record['project_id'])
    roots = (_canonical(settings.TEST_PLANS_DIR),
             _canonical(os.path.join(settings.BASE_DIR, 'test_data')))
    report_root = _canonical(settings.REPORTS_DIR)
    for root in roots:
        if target != root and _within(target, root):
            directory = os.path.relpath(target, root).split(os.sep)[0]
            # Flat legacy plan files rely on registration; directory ownership
            # is additional authority, never overridden by a visible alias.
            owners.update(project_id for project_id in value.authorization.project_modes
                          if os.path.normcase(project_id) == directory)
    if target != report_root and _within(target, report_root):
        directory = os.path.relpath(target, report_root).split(os.sep)[0]
        owners.update(record['project_id'] for execution_id, record in value.executions.items()
                      if os.path.normcase(execution_id) == directory)
        if not owners:
            raise HTTPException(404, 'Project file not found or not registered')
    if owners:
        for owner in owners:
            require_project(owner, write=write)
        return frozenset(owners)
    if allow_root_listing and not write and target in (*roots, report_root):
        return
    # Backups and orphan files have no trustworthy inferred project owner.
    raise HTTPException(404, 'Project file not found or not registered')


def can_path(path, *, write=False, allow_root_listing=False):
    try:
        require_path(path, write=write, allow_root_listing=allow_root_listing)
        return True
    except HTTPException:
        return False


def can_document(doc, *, write=False):
    value = current_scope()
    if value is None:
        return True
    owners = []
    for kind, source in (('execution', getattr(doc, 'source_execution_id', None)),
                         ('plan', getattr(doc, 'source_plan_id', None))):
        if source:
            records = value.executions if kind == 'execution' else value.plans
            record = records.get(source)
            if record is None:
                return False
            owners.append(record['project_id'])
    if not owners:
        from app.services.auth import Role
        return (Role.can(value.authorization.global_role, Role.OPERATOR if write else Role.VIEWER)
                and not any(mode != 'legacy_shared' for mode in value.authorization.project_modes.values()))
    return len(set(owners)) == 1 and can_project(owners[0], write=write)


async def load_user_scope():
    """Read current authorization and raw resource ownership, never filtered caches."""
    from app.services.project_access import access
    try:
        authorization = await access.snapshot()
        value = await _load_resources(authorization)
        # Resource loading yields after the permission snapshot. Do not publish
        # that snapshot if access changed while those queries were in flight.
        with bind_scope(value):
            await recheck_scope()
            from app.services.storage import refresh_business_cache
            await refresh_business_cache()
        return value
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Project authorization storage unavailable') from error


async def _load_resources(authorization):
    from sqlalchemy import select
    from app.models.db import TestPlanModel, ExecutionModel
    from app.services.db_store import db_store
    try:
        async with await db_store._session() as session:
            plans = (await session.execute(select(TestPlanModel.id, TestPlanModel.project_id,
                                                   TestPlanModel.jmx_path))).mappings().all()
            executions = (await session.execute(select(ExecutionModel.id, ExecutionModel.project_id,
                ExecutionModel.jmx_path, ExecutionModel.source_jmx_path, ExecutionModel.report_dir,
                ExecutionModel.log_path))).mappings().all()
        return AccessScope(authorization, {r['id']: dict(r) for r in plans},
                           {r['id']: dict(r) for r in executions})
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Project authorization storage unavailable') from error


async def refresh_scope():
    if _internal.get():
        return None
    old = _scope.get()
    token = current_access_token.get()
    if token:
        from fastapi.security import HTTPAuthorizationCredentials
        from app.services.auth import get_current_user
        user = await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))
        principal = current_principal.get()
        if principal is None or user.user_id != principal.user_id:
            raise HTTPException(401, 'User identity changed')
    scheduled = _scheduled_identity.get()
    new = await load_scheduled_scope(*scheduled) if scheduled is not None else await load_user_scope()
    if scheduled is not None:
        from app.services.storage import refresh_business_cache
        await refresh_business_cache()
    if old is not None and old.session_key != new.session_key:
        raise HTTPException(403, ACCESS_CHANGED)
    _scope.set(new)
    return new


async def recheck_scope():
    """Revalidate delivery without scanning all plans/executions per stream chunk.

    Every lost project permission bumps the durable user revision. Refresh full
    resource indexes at request/tool boundaries; delivery only needs the current
    active account, global role and the same permission revision/incarnation.
    """
    value = current_scope()
    if value is None:
        return None
    if value.authorization.user_id == 0:
        return await refresh_scope()  # Explicit legacy scheduler has no user row.
    principal = current_principal.get()
    token = current_access_token.get()
    if token:
        from app.services.auth import get_current_user
        from fastapi.security import HTTPAuthorizationCredentials
        principal = await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))
    auth = value.authorization
    if principal is None or (principal.user_id is not None and principal.user_id != auth.user_id):
        raise HTTPException(401, 'User identity changed')
    from sqlalchemy import select
    from app.models.db import UserModel, UserAccessRevisionModel
    from app.services.db_store import db_store
    try:
        async with await db_store._session() as session:
            row = (await session.execute(select(UserModel.role, UserModel.username, UserModel.is_active,
                UserAccessRevisionModel.revision, UserAccessRevisionModel.incarnation).join(
                UserAccessRevisionModel, UserModel.id == UserAccessRevisionModel.user_id).where(
                UserModel.id == auth.user_id))).one_or_none()
        if (row is None or not row.is_active or row.username != principal.username or
                (auth.user_id, row.role, row.revision, row.incarnation) != value.session_key):
            raise HTTPException(403, ACCESS_CHANGED)
        current_scope()  # Observe a local revocation committed during the last DB await.
        return value
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Project authorization storage unavailable') from error


async def _scheduled_user(user_id, incarnation):
    from app.models.db import UserModel, UserAccessRevisionModel
    from app.services.db_store import db_store
    from app.services.auth import UserInfo
    async with await db_store._session() as session:
        user = await session.get(UserModel, user_id)
        identity = await session.get(UserAccessRevisionModel, user_id)
        if (user is None or not user.is_active or user.role not in ('operator', 'admin')
                or identity is None or identity.incarnation != incarnation):
            raise HTTPException(403, 'Scheduled creator is no longer authorized')
        return UserInfo(username=user.username, user_id=user.id, role=user.role)


async def load_scheduled_scope(user_id, incarnation):
    """Old jobs can access only explicit shared projects; never inherit an admin."""
    if user_id is None:
        if incarnation:
            raise HTTPException(403, 'Scheduled creator no longer exists')
        import hashlib
        from app.services.project_access import access, AccessSnapshot, ProjectPermissions
        async with await access._session() as session:
            policies = await access._policies(session)
        shared = sorted(pid for pid, policy in policies.items() if policy['mode'] == 'legacy_shared')
        fingerprint = hashlib.sha256('\0'.join(shared).encode()).hexdigest()
        authorization = AccessSnapshot(0, 'operator', 0, fingerprint,
            MappingProxyType({pid: ProjectPermissions(True, True, False) for pid in shared}),
            MappingProxyType({pid: p['mode'] for pid, p in policies.items()}))
        value = await _load_resources(authorization)
        async with await access._session() as session:
            current = await access._policies(session)
        if shared != sorted(pid for pid, policy in current.items() if policy['mode'] == 'legacy_shared'):
            raise HTTPException(403, ACCESS_CHANGED)
        return value
    user = await _scheduled_user(user_id, incarnation)
    token = current_principal.set(user)
    try:
        value = await load_user_scope()
        if value.authorization.incarnation != incarnation:
            raise HTTPException(403, 'Scheduled creator identity changed')
        return value
    finally:
        current_principal.reset(token)


@asynccontextmanager
async def scheduled_scope(user_id, incarnation):
    value = await load_scheduled_scope(user_id, incarnation)
    user = await _scheduled_user(user_id, incarnation) if user_id is not None else None
    principal_token = current_principal.set(user)
    access_token = current_access_token.set('')
    try:
        with bind_scope(value):
            identity_token = _scheduled_identity.set((user_id, incarnation))
            try:
                yield value
            finally:
                _scheduled_identity.reset(identity_token)
    finally:
        current_principal.reset(principal_token)
        current_access_token.reset(access_token)

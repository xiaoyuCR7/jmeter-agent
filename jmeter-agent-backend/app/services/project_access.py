"""Durable project authorization. Importing this module enables no boundaries.

Snapshots contain identifiers and permissions only and are for internal scoped
readers. Refresh them at each protected boundary; they are not an authority cache.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
from types import MappingProxyType
from typing import Mapping

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.db import (
    ProjectModel, ProjectAccessPolicyModel, ProjectMemberModel,
    ProjectAccessAuditModel, UserAccessRevisionModel, UserModel,
    TestPlanModel, ExecutionModel, BaselineModel, ScheduleModel,
)


@dataclass(frozen=True)
class ProjectPermissions:
    read: bool = False
    write: bool = False
    manage: bool = False

    def allows(self, action: str = 'read') -> bool:
        if action not in ('read', 'write', 'manage'):
            raise ValueError('Unknown project action')
        return getattr(self, action)


@dataclass(frozen=True)
class AccessSnapshot:
    user_id: int
    global_role: str
    revision: int
    incarnation: str
    projects: Mapping[str, ProjectPermissions]
    project_modes: Mapping[str, str]

    @property
    def session_key(self) -> tuple[int, str, int, str]:
        return self.user_id, self.global_role, self.revision, self.incarnation

    def allows(self, project_id: str, action: str = 'read') -> bool:
        return self.projects.get(project_id, ProjectPermissions()).allows(action)


def _unavailable():
    return HTTPException(503, 'Project authorization unavailable')


def _permissions(user, policy: dict) -> ProjectPermissions:
    active = user.is_active and user.role in ('viewer', 'operator', 'admin')
    operator = active and user.role in ('operator', 'admin')
    owner = policy['mode'] == 'private' and policy['owner_id'] == user.id
    shared = policy['mode'] == 'legacy_shared'
    member = policy['members'].get(str(user.id))
    return ProjectPermissions(
        read=bool(active and (shared or owner or member)),
        write=bool(operator and (shared or owner or member == 'operator')),
        manage=bool(operator and (user.role == 'admin' or owner)),
    )


class ProjectAccess:
    async def _session(self):
        # Local imports avoid auth -> db_store -> project_access cycles and
        # retain the store's session injection seam for isolated callers.
        from app.services.db_store import db_store
        return await db_store._session()

    async def _user(self, session):
        from app.services.security_context import current_principal
        principal = current_principal.get()
        if principal is None:
            raise HTTPException(401, 'Authentication required')
        user_id = getattr(principal, 'user_id', None)
        if user_id is not None:
            user = await session.get(UserModel, user_id, populate_existing=True)
        else:
            # Direct legacy callers may not yet carry an ID. Materialized
            # snapshots always do, and production authentication supplies it.
            user = (await session.execute(select(UserModel).where(
                UserModel.username == principal.username,
            ).execution_options(populate_existing=True))).scalar_one_or_none()
        if (not user or not user.is_active or user.username != principal.username
                or user.role not in ('viewer', 'operator', 'admin')):
            raise HTTPException(401, 'User unavailable')
        return user

    async def _policies(self, session, project_id=None):
        projects = select(ProjectModel.id)
        policies = select(ProjectAccessPolicyModel)
        members = select(ProjectMemberModel)
        if project_id is not None:
            projects = projects.where(ProjectModel.id == project_id)
            policies = policies.where(ProjectAccessPolicyModel.project_id == project_id)
            members = members.where(ProjectMemberModel.project_id == project_id)
        result = {pid: {'mode': 'legacy_shared', 'owner_id': None, 'version': 0, 'members': {}}
                  for pid in (await session.execute(projects)).scalars()}
        for row in (await session.execute(policies)).scalars():
            if (row.mode not in ('legacy_shared', 'private') or row.version < 1
                    or (row.mode == 'legacy_shared' and row.owner_id is not None)):
                raise _unavailable()
            if row.project_id in result:
                result[row.project_id].update(mode=row.mode, owner_id=row.owner_id, version=row.version)
        for row in (await session.execute(members)).scalars():
            if row.role not in ('viewer', 'operator'):
                raise _unavailable()
            if row.project_id in result:
                result[row.project_id]['members'][str(row.user_id)] = row.role
        return result

    async def _identity(self, session, user_id):
        identity = await session.get(UserAccessRevisionModel, user_id, populate_existing=True)
        if identity is None:
            try:
                async with session.begin_nested():
                    session.add(UserAccessRevisionModel(user_id=user_id, revision=0))
                    await session.flush()
            except IntegrityError:
                # A simultaneous first snapshot may have initialized this user.
                pass
            await session.commit()
            identity = await session.get(UserAccessRevisionModel, user_id, populate_existing=True)
        if identity is None or not identity.incarnation or identity.revision < 0:
            raise _unavailable()
        return identity

    async def snapshot(self) -> AccessSnapshot:
        try:
            for _ in range(3):
                async with await self._session() as session:
                    user = await self._user(session)
                    identity = await self._identity(session, user.id)
                    before = (user.id, user.role, identity.revision, identity.incarnation)
                    policies = await self._policies(session)
                    user = await self._user(session)
                    identity = await self._identity(session, user.id)
                    after = (user.id, user.role, identity.revision, identity.incarnation)
                    # Never pair grants read before revocation with an identity
                    # read after it. Closing this session gives a fresh retry.
                    if before != after:
                        continue
                    return AccessSnapshot(*after,
                        MappingProxyType({pid: _permissions(user, policy) for pid, policy in policies.items()}),
                        MappingProxyType({pid: policy['mode'] for pid, policy in policies.items()}))
            raise _unavailable()
        except SQLAlchemyError:
            raise _unavailable() from None

    async def require_project(self, project_id: str, action: str = 'read') -> ProjectPermissions:
        if action not in ('read', 'write', 'manage'):
            raise ValueError('Unknown project action')
        try:
            async with await self._session() as session:
                user = await self._user(session)
                policy = (await self._policies(session, project_id)).get(project_id)
                if policy is None:
                    raise HTTPException(404, 'Project not found')
                permission = _permissions(user, policy)
                if not permission.allows(action):
                    raise HTTPException(403 if permission.read else 404,
                                        'Project action forbidden' if permission.read else 'Project not found')
                return permission
        except SQLAlchemyError:
            raise _unavailable() from None

    async def _management(self, session, project_id):
        user = await self._user(session)
        policy = (await self._policies(session, project_id)).get(project_id)
        if policy is None:
            raise HTTPException(404, 'Project not found')
        permission = _permissions(user, policy)
        if not permission.manage:
            raise HTTPException(403 if permission.read else 404,
                                'Project management forbidden' if permission.read else 'Project not found')
        return user, policy

    async def _proposal(self, session, owner_id, members, version):
        if (type(owner_id) is not int or not isinstance(members, dict)
                or any(type(uid) is not int or role not in ('viewer', 'operator') for uid, role in members.items())
                or owner_id in members):
            raise HTTPException(422, 'Invalid project membership')
        ids = set(members) | {owner_id}
        active = set((await session.execute(select(UserModel.id).where(
            UserModel.id.in_(ids), UserModel.is_active.is_(True),
            UserModel.role.in_(('viewer', 'operator', 'admin')),
        ))).scalars())
        if active != ids:
            raise HTTPException(422, 'Project members must be active users')
        return {'mode': 'private', 'owner_id': owner_id, 'version': version,
                'members': {str(uid): role for uid, role in sorted(members.items())}}

    async def _preview_identities(self, session, actor_id, owner_id, members):
        # Management sessions have made no policy writes yet. Materialize stable
        # account identities so a recycled numeric target ID invalidates preview.
        result = {}
        for user_id in sorted(set(members) | {actor_id, owner_id}):
            result[str(user_id)] = (await self._identity(session, user_id)).incarnation
        return result

    def _revision(self, actor_id, project_id, old, proposed, identities):
        from app.core.config import settings
        payload = json.dumps([actor_id, project_id, old, proposed, identities], sort_keys=True, separators=(',', ':'))
        return hmac.new(settings.JWT_SECRET.encode(), b'project-acl-preview-v1:' + payload.encode(), hashlib.sha256).hexdigest()

    async def preview(self, project_id, owner_id, members):
        try:
            async with await self._session() as session:
                user, old = await self._management(session, project_id)
                proposed = await self._proposal(session, owner_id, members, old['version'] + 1)
                identities = await self._preview_identities(session, user.id, owner_id, members)
                counts = {}
                for name, model in [('test_plans', TestPlanModel), ('executions', ExecutionModel),
                                    ('baselines', BaselineModel), ('schedules', ScheduleModel)]:
                    counts[name] = (await session.execute(select(func.count()).select_from(model).where(
                        model.project_id == project_id))).scalar_one()
                return {'project_id': project_id, 'current': old, 'proposed': proposed, 'resource_counts': counts,
                        'revision': self._revision(user.id, project_id, old, proposed, identities)}
        except SQLAlchemyError:
            raise _unavailable() from None

    async def _bump_revisions(self, session, user_ids):
        for user_id in sorted(user_ids):
            result = await session.execute(update(UserAccessRevisionModel).where(
                UserAccessRevisionModel.user_id == user_id,
            ).values(revision=UserAccessRevisionModel.revision + 1))
            if not result.rowcount:
                session.add(UserAccessRevisionModel(user_id=user_id, revision=1))
        await session.flush()
        if not user_ids:
            return ()
        identities = (await session.execute(select(UserAccessRevisionModel).where(
            UserAccessRevisionModel.user_id.in_(user_ids),
        ).execution_options(populate_existing=True))).scalars()
        return tuple((row.user_id, row.revision, row.incarnation) for row in identities)

    def notify_revocations(self, changes):
        """Publish only committed changes; deletion callers invoke after commit."""
        from app.services.project_scope import note_revocation
        for user_id, revision, incarnation in changes:
            note_revocation(user_id, revision, incarnation)

    async def _revoke_lost_permissions(self, session, old, proposed):
        affected = []
        for user in (await session.execute(select(UserModel).where(UserModel.is_active.is_(True)))).scalars():
            before = _permissions(user, old)
            after = _permissions(user, proposed) if proposed else ProjectPermissions()
            if any(before.allows(action) and not after.allows(action) for action in ('read', 'write', 'manage')):
                affected.append(user.id)
        return await self._bump_revisions(session, affected)

    async def apply_preview(self, project_id, owner_id, members, expected_revision):
        try:
            async with await self._session() as session:
                user, old = await self._management(session, project_id)
                proposed = await self._proposal(session, owner_id, members, old['version'] + 1)
                identities = await self._preview_identities(session, user.id, owner_id, members)
                if not isinstance(expected_revision, str) or not hmac.compare_digest(
                        expected_revision, self._revision(user.id, project_id, old, proposed, identities)):
                    raise HTTPException(409, 'Project access changed; preview again')
                if old['version'] == 0:
                    session.add(ProjectAccessPolicyModel(project_id=project_id, mode='private',
                                                         owner_id=owner_id, version=1))
                    await session.flush()
                else:
                    changed = await session.execute(update(ProjectAccessPolicyModel).where(
                        ProjectAccessPolicyModel.project_id == project_id,
                        ProjectAccessPolicyModel.version == old['version'],
                        ProjectAccessPolicyModel.mode == old['mode'],
                        ProjectAccessPolicyModel.owner_id == old['owner_id'],
                    ).values(mode='private', owner_id=owner_id, version=proposed['version']))
                    if changed.rowcount != 1:
                        raise HTTPException(409, 'Project access changed; preview again')
                await session.execute(delete(ProjectMemberModel).where(ProjectMemberModel.project_id == project_id))
                session.add_all([ProjectMemberModel(project_id=project_id, user_id=uid, role=role)
                                 for uid, role in members.items()])
                changes = await self._revoke_lost_permissions(session, old, proposed)
                session.add(ProjectAccessAuditModel(project_id=project_id, actor_id=user.id,
                                                     before_policy=old, after_policy=proposed))
                await session.commit()
                self.notify_revocations(changes)
                return {'project_id': project_id, 'policy': proposed}
        except IntegrityError:
            raise HTTPException(409, 'Project access changed; preview again') from None
        except SQLAlchemyError:
            raise _unavailable() from None

    async def initialize_private(self, session, project_id, owner_id):
        """Caller owns the project transaction. No commit, cache update or revoke."""
        user = await self._user(session)
        if user.id != owner_id or user.role not in ('operator', 'admin'):
            raise HTTPException(403, 'Project creation forbidden')
        proposed = await self._proposal(session, owner_id, {}, 1)
        session.add(ProjectAccessPolicyModel(project_id=project_id, mode='private', owner_id=owner_id, version=1))
        session.add(ProjectAccessAuditModel(project_id=project_id, actor_id=user.id,
                                           before_policy=None, after_policy=proposed))
        await session.flush()

    async def revoke_project(self, session, project_id):
        """Before deletion, in its transaction. Notify returned changes after commit."""
        old = (await self._policies(session, project_id)).get(project_id)
        if old is not None:
            return await self._revoke_lost_permissions(session, old, None)
        return ()


access = ProjectAccess()

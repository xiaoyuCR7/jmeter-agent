"""Database-backed async store — replaces in-memory SharedStore.

Business CRUD requires a bound project scope or explicit maintenance scope.
Authentication CRUD remains available before a project scope can be constructed.
"""

import logging
from typing import Optional, List, Dict, Any

from fastapi import HTTPException
from sqlalchemy import select, delete, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    get_session_factory, ProjectModel, TestPlanModel, ExecutionModel,
    EnvironmentModel, BaselineModel, UserModel, ApiTokenModel, ScheduleModel,
)
from app.core.time import utc_now_naive

logger = logging.getLogger(__name__)


async def _bump_business_revision(session):
    """Advance in the caller's commit; cache invalidation cannot lead the data."""
    from app.models.db import BusinessDataRevisionModel
    changed = await session.execute(update(BusinessDataRevisionModel).where(
        BusinessDataRevisionModel.id == 1).values(
            revision=BusinessDataRevisionModel.revision + 1))
    if changed.rowcount != 1:
        try:
            async with session.begin_nested():
                session.add(BusinessDataRevisionModel(id=1, revision=1))
                await session.flush()
        except IntegrityError:
            changed = await session.execute(update(BusinessDataRevisionModel).where(
                BusinessDataRevisionModel.id == 1).values(
                    revision=BusinessDataRevisionModel.revision + 1))
            if changed.rowcount != 1:
                raise RuntimeError('Business revision initialization raced without an authority row')


def _scope():
    # Local import: authentication imports this store while its ContextVars are
    # still being initialized.
    from app.services.project_scope import current_scope
    return current_scope()


def _require_project(project_id, *, write=False):
    from app.services.project_scope import require_project
    require_project(project_id, write=write)


def _project_conditions(column, project_id=None):
    scope = _scope()
    conditions = []
    if scope is not None:
        readable = [pid for pid, permission in scope.authorization.projects.items() if permission.read]
        conditions.append(column.in_(readable))
    if project_id is not None:
        _require_project(project_id)
        conditions.append(column == project_id)
    return conditions


def _immutable_identity(row, updates):
    for key in ('id', 'project_id'):
        if key in updates and updates[key] != getattr(row, key, None):
            raise HTTPException(422, 'Resource identity and project cannot be changed')


async def _source(session, model, resource_id, project_id):
    if resource_id is None:
        return None
    record = await session.get(model, resource_id)
    if record is None:
        raise HTTPException(404, 'Source resource not found')
    _require_project(record.project_id)
    if record.project_id != project_id:
        raise HTTPException(422, 'Source resource project mismatch')
    return record


async def _check_plan_children(session, plan_ids, project_id):
    """Old inconsistent foreign keys must not cascade into another project."""
    if not plan_ids:
        return
    for model in (ExecutionModel, BaselineModel, ScheduleModel):
        foreign = await session.execute(select(model.id).where(
            model.test_plan_id.in_(plan_ids), model.project_id != project_id,
        ).limit(1))
        if foreign.first() is not None:
            raise HTTPException(409, 'Inconsistent resource ownership requires repair')


async def _versioned_update(session, model, row, updates, expected_version, *, touch_updated=False):
    """Atomically reject an object loaded before a newer committed write."""
    expected = updates.get('storage_version', row.version) if expected_version is None else expected_version
    if type(expected) is not int or expected < 1:
        raise HTTPException(409, 'Business record version is stale')
    values = {key: val for key, val in updates.items()
              if key != 'storage_version' and hasattr(row, key) and key != 'version'}
    if touch_updated:
        values['updated_at'] = utc_now_naive()
    values['version'] = model.version + 1
    changed = await session.execute(update(model).where(
        model.id == row.id, model.version == expected).values(**values).execution_options(
            synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(409, 'Business record changed; refresh and retry')
    session.expire(row)
    await session.refresh(row)


class DBStore:
    """Async database store for projects, test plans, executions, baselines."""

    async def _session(self) -> AsyncSession:
        factory = get_session_factory()
        return factory()

    # ── Projects ──────────────────────────────────────────

    async def create_project(self, project_data: dict) -> dict:
        scope = _scope()
        if scope is not None and scope.authorization.global_role not in ('operator', 'admin'):
            raise HTTPException(403, 'Project creation requires operator permission')
        async with await self._session() as s:
            p = ProjectModel(
                id=project_data["id"],
                name=project_data["name"],
                description=project_data.get("description", ""),
                base_url=project_data.get("base_url", ""),
                environment_data=project_data.get("environment_data", {}),
                environments_data=project_data.get("environments_data"),
                created_at=project_data.get("created_at", utc_now_naive()),
            )
            s.add(p)
            await s.flush()
            from app.models.db import ProjectAccessPolicyModel
            if scope is None:
                s.add(ProjectAccessPolicyModel(project_id=p.id, mode='legacy_shared', version=1))
            else:
                from app.services.project_access import access
                await access.initialize_private(s, p.id, scope.authorization.user_id)
            await _bump_business_revision(s)
            await s.commit()
            return self._project_to_dict(p)

    async def update_project(self, project_id: str, updates: dict, *, expected_version=None) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            p = await s.get(ProjectModel, project_id)
            if not p:
                return None
            _require_project(p.id, write=True)
            _immutable_identity(p, updates)
            await _versioned_update(s, ProjectModel, p, updates, expected_version, touch_updated=True)
            await _bump_business_revision(s)
            await s.commit()
            return self._project_to_dict(p)

    async def delete_project(self, project_id: str) -> bool:
        _scope()
        async with await self._session() as s:
            p = await s.get(ProjectModel, project_id)
            if not p:
                return False
            from app.services.project_scope import require_project_manage
            from app.services.project_access import access
            require_project_manage(p.id)
            plan_ids = list((await s.execute(select(TestPlanModel.id).where(TestPlanModel.project_id == p.id))).scalars())
            await _check_plan_children(s, plan_ids, p.id)
            changes = await access.revoke_project(s, p.id)
            # Schedules and standalone executions are not ORM children of the
            # project. Remove them by their server-owned project identifier.
            await s.execute(delete(ScheduleModel).where(ScheduleModel.project_id == p.id))
            await s.execute(delete(ExecutionModel).where(ExecutionModel.project_id == p.id))
            await s.delete(p)
            await _bump_business_revision(s)
            await s.commit()
            access.notify_revocations(changes)
            return True

    async def get_project(self, project_id: str) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            p = await s.get(ProjectModel, project_id)
            if p:
                _require_project(p.id)
            return self._project_to_dict(p) if p else None

    async def list_projects(self, offset: int = 0, limit: int = 50) -> dict:
        conditions = _project_conditions(ProjectModel.id)
        async with await self._session() as s:
            stmt = select(ProjectModel).where(*conditions).offset(offset).limit(limit).order_by(ProjectModel.created_at.desc())
            result = await s.execute(stmt)
            projects = [self._project_to_dict(p) for p in result.scalars()]
            total = (await s.execute(select(func.count()).select_from(ProjectModel).where(*conditions))).scalar_one()
            return {"projects": projects, "total": total, "offset": offset, "limit": limit}

    def _project_to_dict(self, p: ProjectModel) -> dict:
        return {
            "id": p.id, "name": p.name, "description": p.description,
            "base_url": p.base_url, "environment_data": p.environment_data,
            "environments_data": p.environments_data,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "storage_version": p.version,
        }

    # ── Test Plans ────────────────────────────────────────

    async def create_test_plan(self, plan_data: dict) -> dict:
        project_id = plan_data.get('project_id', 'default')
        _require_project(project_id, write=True)
        async with await self._session() as s:
            if await s.get(ProjectModel, project_id) is None:
                raise HTTPException(404, 'Project not found')
            tp = TestPlanModel(
                id=plan_data["id"],
                project_id=plan_data.get("project_id", "default"),
                name=plan_data["name"],
                description=plan_data.get("description", ""),
                test_type=plan_data.get("test_type", "baseline"),
                jmx_path=plan_data.get("jmx_path", ""),
                jmx_content=plan_data.get("jmx_content"),
                test_config=plan_data.get("test_config", {}),
                endpoints_count=plan_data.get("endpoints_count", 0),
                created_at=plan_data.get("created_at", utc_now_naive()),
            )
            s.add(tp)
            await _bump_business_revision(s)
            await s.commit()
            return self._plan_to_dict(tp)

    async def update_test_plan(self, plan_id: str, updates: dict, *, expected_version=None) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            tp = await s.get(TestPlanModel, plan_id)
            if not tp:
                return None
            _require_project(tp.project_id, write=True)
            _immutable_identity(tp, updates)
            await _versioned_update(s, TestPlanModel, tp, updates, expected_version, touch_updated=True)
            await _bump_business_revision(s)
            await s.commit()
            return self._plan_to_dict(tp)

    async def delete_test_plan(self, plan_id: str) -> bool:
        _scope()
        async with await self._session() as s:
            tp = await s.get(TestPlanModel, plan_id)
            if not tp:
                return False
            _require_project(tp.project_id, write=True)
            await _check_plan_children(s, [tp.id], tp.project_id)
            await s.delete(tp)
            await _bump_business_revision(s)
            await s.commit()
            return True

    async def get_test_plan(self, plan_id: str) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            tp = await s.get(TestPlanModel, plan_id)
            if tp:
                _require_project(tp.project_id)
            return self._plan_to_dict(tp) if tp else None

    async def list_test_plans(self, project_id: str = None, offset: int = 0, limit: int = 50) -> dict:
        conditions = _project_conditions(TestPlanModel.project_id, project_id)
        async with await self._session() as s:
            stmt = select(TestPlanModel).where(*conditions).offset(offset).limit(limit).order_by(TestPlanModel.created_at.desc())
            result = await s.execute(stmt)
            plans = [self._plan_to_dict(p) for p in result.scalars()]
            total = (await s.execute(select(func.count()).select_from(TestPlanModel).where(*conditions))).scalar_one()
            return {"test_plans": plans, "total": total, "offset": offset, "limit": limit}

    def _plan_to_dict(self, tp: TestPlanModel) -> dict:
        return {
            "id": tp.id, "project_id": tp.project_id, "name": tp.name,
            "description": tp.description, "test_type": tp.test_type,
            "jmx_path": tp.jmx_path, "endpoints_count": tp.endpoints_count,
            "test_config": tp.test_config,
            "created_at": tp.created_at.isoformat() if tp.created_at else None,
            "updated_at": tp.updated_at.isoformat() if tp.updated_at else None,
            "storage_version": tp.version,
        }

    # ── Executions ────────────────────────────────────────

    async def create_execution(self, exec_data: dict) -> dict:
        project_id = exec_data.get('project_id', 'default')
        _require_project(project_id, write=True)
        async with await self._session() as s:
            if await s.get(ProjectModel, project_id) is None:
                raise HTTPException(404, 'Project not found')
            await _source(s, TestPlanModel, exec_data.get('test_plan_id'), project_id)
            e = ExecutionModel(
                id=exec_data["id"],
                project_id=exec_data.get("project_id", "default"),
                test_plan_id=exec_data.get("test_plan_id"),
                status=exec_data.get("status", "pending"),
                start_time=exec_data.get("start_time"),
                end_time=exec_data.get("end_time"),
                jmx_path=exec_data.get("jmx_path", ""),
                report_dir=exec_data.get("report_dir", ""),
                log_path=exec_data.get("log_path", ""),
                ai_analysis_data=exec_data.get("ai_analysis_data"),
                summary_data=exec_data.get("summary_data"),
                error_message=exec_data.get("error_message"),
                resource_snapshot_pre=exec_data.get("resource_snapshot_pre"),
                resource_snapshot_post=exec_data.get("resource_snapshot_post"),
                source_jmx_path=exec_data.get("source_jmx_path"),
                source_sha256=exec_data.get("source_sha256"),
                context_snapshot=exec_data.get("context_snapshot"),
                report_status=exec_data.get("report_status", "pending"),
                report_error=exec_data.get("report_error"),
            )
            s.add(e)
            await _bump_business_revision(s)
            await s.commit()
            return self._exec_to_dict(e)

    async def update_execution(self, exec_id: str, updates: dict, *, expected_version=None) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            e = await s.get(ExecutionModel, exec_id)
            if not e:
                return None
            _require_project(e.project_id, write=True)
            _immutable_identity(e, updates)
            await _source(s, TestPlanModel, updates.get('test_plan_id', e.test_plan_id), e.project_id)
            await _versioned_update(s, ExecutionModel, e, updates, expected_version)
            await _bump_business_revision(s)
            await s.commit()
            return self._exec_to_dict(e)

    async def get_execution(self, exec_id: str) -> Optional[dict]:
        _scope()
        async with await self._session() as s:
            e = await s.get(ExecutionModel, exec_id)
            if e:
                _require_project(e.project_id)
            return self._exec_to_dict(e) if e else None

    async def list_executions(self, project_id: str = None, offset: int = 0, limit: int = 50) -> dict:
        conditions = _project_conditions(ExecutionModel.project_id, project_id)
        async with await self._session() as s:
            stmt = select(ExecutionModel).where(*conditions).offset(offset).limit(limit).order_by(ExecutionModel.created_at.desc())
            result = await s.execute(stmt)
            execs = [self._exec_to_dict(e) for e in result.scalars()]
            total = (await s.execute(select(func.count()).select_from(ExecutionModel).where(*conditions))).scalar_one()
            return {"executions": execs, "total": total, "offset": offset, "limit": limit}

    def _exec_to_dict(self, e: ExecutionModel) -> dict:
        return {
            "id": e.id, "project_id": e.project_id, "test_plan_id": e.test_plan_id,
            "status": e.status, "start_time": e.start_time.isoformat() if e.start_time else None,
            "end_time": e.end_time.isoformat() if e.end_time else None,
            "jmx_path": e.jmx_path, "report_dir": e.report_dir, "log_path": e.log_path,
            "error_message": e.error_message,
            "resource_snapshot_pre": e.resource_snapshot_pre,
            "resource_snapshot_post": e.resource_snapshot_post,
            "ai_analysis_data": e.ai_analysis_data,
            "summary_data": e.summary_data,
            "source_jmx_path": e.source_jmx_path, "source_sha256": e.source_sha256,
            "context_snapshot": e.context_snapshot,
            "report_status": e.report_status, "report_error": e.report_error,
            "storage_version": e.version,
        }

    # ── Baselines ─────────────────────────────────────────

    async def create_baseline(self, data: dict) -> dict:
        project_id = data['project_id']
        _require_project(project_id, write=True)
        async with await self._session() as s:
            if await s.get(ProjectModel, project_id) is None:
                raise HTTPException(404, 'Project not found')
            plan = await _source(s, TestPlanModel, data.get('test_plan_id'), project_id)
            execution = await _source(s, ExecutionModel, data.get('execution_id'), project_id)
            if plan is not None and execution is not None and execution.test_plan_id != plan.id:
                raise HTTPException(422, 'Source execution and test plan mismatch')
            b = BaselineModel(
                project_id=data["project_id"],
                test_plan_id=data.get("test_plan_id"),
                name=data["name"],
                description=data.get("description", ""),
                execution_id=data.get("execution_id"),
                thresholds=data.get("thresholds", {}),
                metrics_snapshot=data.get("metrics_snapshot", {}),
                is_active=data.get("is_active", True),
            )
            s.add(b)
            await s.commit()
            return self._baseline_to_dict(b)

    async def list_baselines(self, project_id: str = None) -> list:
        conditions = _project_conditions(BaselineModel.project_id, project_id)
        async with await self._session() as s:
            stmt = select(BaselineModel).where(*conditions).order_by(BaselineModel.created_at.desc())
            result = await s.execute(stmt)
            return [self._baseline_to_dict(b) for b in result.scalars()]

    async def get_active_baseline(self, project_id: str, test_plan_id: str = None) -> Optional[dict]:
        _require_project(project_id)
        async with await self._session() as s:
            await _source(s, TestPlanModel, test_plan_id, project_id)
            stmt = select(BaselineModel).where(
                BaselineModel.project_id == project_id,
                BaselineModel.is_active == True,
            )
            if test_plan_id:
                stmt = stmt.where(BaselineModel.test_plan_id == test_plan_id)
            stmt = stmt.order_by(BaselineModel.created_at.desc()).limit(1)
            result = await s.execute(stmt)
            b = result.scalar_one_or_none()
            return self._baseline_to_dict(b) if b else None

    async def delete_baseline(self, baseline_id: int) -> bool:
        _scope()
        async with await self._session() as s:
            b = await s.get(BaselineModel, baseline_id)
            if not b:
                return False
            _require_project(b.project_id, write=True)
            await s.delete(b)
            await s.commit()
            return True

    def _baseline_to_dict(self, b: BaselineModel) -> dict:
        return {
            "id": b.id, "project_id": b.project_id, "test_plan_id": b.test_plan_id,
            "name": b.name, "description": b.description,
            "execution_id": b.execution_id,
            "thresholds": b.thresholds, "metrics_snapshot": b.metrics_snapshot,
            "is_active": b.is_active,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }

    # ── Users ─────────────────────────────────────────────

    async def get_user_by_username(self, username: str) -> Optional[UserModel]:
        async with await self._session() as s:
            stmt = select(UserModel).where(UserModel.username == username)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def create_user(self, username: str, password_hash: str, role: str = "viewer") -> dict:
        async with await self._session() as s:
            u = UserModel(username=username, password_hash=password_hash, role=role)
            s.add(u)
            await s.commit()
            return {"id": u.id, "username": u.username, "role": u.role}

    async def list_users(self) -> list[dict]:
        async with await self._session() as s:
            stmt = select(UserModel).order_by(UserModel.id)
            result = await s.execute(stmt)
            return [
                {"id": u.id, "username": u.username, "role": u.role,
                 "is_active": u.is_active, "created_at": u.created_at.isoformat() if u.created_at else None}
                for u in result.scalars()
            ]

    async def deactivate_user(self, username: str) -> bool:
        async with await self._session() as s:
            stmt = select(UserModel).where(UserModel.username == username)
            result = await s.execute(stmt)
            u = result.scalar_one_or_none()
            if not u:
                return False
            u.is_active = False
            await s.commit()
            return True

    # ── Refresh Tokens ─────────────────────────────────────

    async def store_refresh_token(self, username: str, token_hash: str, expires_at) -> None:
        """Store a refresh token hash in the ApiTokenModel table."""
        async with await self._session() as s:
            user = (await s.execute(select(UserModel).where(UserModel.username == username))).scalar_one()
            t = ApiTokenModel(
                user_id=user.id,
                name="refresh:" + token_hash,
                token_hash=token_hash,
                scopes=["refresh"],
                expires_at=expires_at,
            )
            s.add(t)
            await s.commit()

    async def get_refresh_token(self, token_hash: str) -> Optional[dict]:
        """Look up a refresh token by hash. Returns None if not found or revoked."""
        async with await self._session() as s:
            stmt = select(ApiTokenModel, UserModel.username).join(UserModel).where(
                ApiTokenModel.token_hash == token_hash,
                ApiTokenModel.name.like("refresh:%"),
                ApiTokenModel.scopes == ["refresh"],
            )
            result = await s.execute(stmt)
            row = result.one_or_none()
            if not row:
                return None
            t, username = row
            return {
                "username": username,
                "token_type": "refresh",
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "revoked": False,
            }

    async def rotate_refresh_token(self, old_hash: str, new_hash: str, expires_at) -> bool:
        """Consume once and replace in one transaction; concurrent refresh cannot replay."""
        async with await self._session() as s:
            predicate = (ApiTokenModel.token_hash == old_hash, ApiTokenModel.name.like("refresh:%"),
                         ApiTokenModel.scopes == ["refresh"],
                         ApiTokenModel.expires_at > utc_now_naive())
            token = (await s.execute(select(ApiTokenModel).where(*predicate))).scalar_one_or_none()
            if token is None:
                return False
            user_id = token.user_id
            family = token.name
            consumed = await s.execute(update(ApiTokenModel).where(*predicate).values(scopes=["refresh_consumed"]))
            if consumed.rowcount != 1:
                await s.rollback()
                return False
            s.add(ApiTokenModel(user_id=user_id, name=family, scopes=["refresh"],
                                token_hash=new_hash, expires_at=expires_at))
            await s.commit()
            return True

    async def revoke_user_sessions(self, username: str) -> None:
        async with await self._session() as s:
            users = select(UserModel.id).where(UserModel.username == username)
            await s.execute(delete(ApiTokenModel).where(ApiTokenModel.user_id.in_(users),
                                                       ApiTokenModel.name.like("refresh:%")))
            await s.commit()

    async def delete_refresh_token(self, token_hash: str) -> None:
        """Revoke the whole rotation family, even if logout carries the previous cookie."""
        async with await self._session() as s:
            family = (await s.execute(select(ApiTokenModel.name).where(
                ApiTokenModel.token_hash == token_hash,
                ApiTokenModel.name.like("refresh:%"),
            ))).scalar_one_or_none()
            if family:
                await s.execute(delete(ApiTokenModel).where(ApiTokenModel.name == family))
            await s.commit()

    async def cleanup_expired_tokens(self) -> int:
        """Delete expired tokens. Revoked sessions have already been removed."""
        async with await self._session() as s:
            result = await s.execute(delete(ApiTokenModel).where(ApiTokenModel.expires_at < utc_now_naive()))
            await s.commit()
            return result.rowcount


# Module-level singleton
db_store = DBStore()

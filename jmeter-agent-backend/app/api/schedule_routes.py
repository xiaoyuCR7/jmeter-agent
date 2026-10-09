"""Scheduled test management endpoints."""

from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.models.db import get_session_factory, ScheduleModel, SchedulePrincipalModel
from app.services.scheduler import _next_cron_time, schedule_source
from app.services.project_scope import current_scope, require_project

router = APIRouter(tags=["schedules"])


@asynccontextmanager
async def _schedule_session():
    try:
        async with get_session_factory()() as session:
            yield session
    except SQLAlchemyError:
        raise HTTPException(503, 'Schedule storage unavailable') from None


class CreateScheduleRequest(BaseModel):
    project_id: str
    name: str
    cron_expression: str = Field(..., description="minute hour dom month dow (5 fields)")
    test_plan_id: Optional[str] = None
    jmx_path: str = ""
    is_enabled: bool = True


class UpdateScheduleRequest(BaseModel):
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    test_plan_id: Optional[str] = None
    jmx_path: Optional[str] = None
    is_enabled: Optional[bool] = None


@router.post("/schedules")
async def create_schedule(request: CreateScheduleRequest):
    """Create a recurring test schedule.

    cron_expression format: minute hour dom month dow (5 fields)
    Examples:
      "0 */6 * * *"  — every 6 hours
      "30 2 * * 1"   — every Monday at 2:30 AM
      "0 9 * * 1-5"  — weekdays at 9:00 AM
    """
    require_project(request.project_id, write=True)
    scope = current_scope()
    if scope is None or not scope.authorization.user_id:
        raise HTTPException(401, 'A schedule requires an authenticated creator')
    # Validate cron expression
    try:
        next_run = _next_cron_time(request.cron_expression)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid cron expression') from None
    if next_run is None:
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    async with _schedule_session() as s:
        path = await schedule_source(s, request.project_id, request.test_plan_id, request.jmx_path)
        sch = ScheduleModel(
            project_id=request.project_id,
            test_plan_id=request.test_plan_id,
            name=request.name,
            cron_expression=request.cron_expression,
            is_enabled=request.is_enabled,
            jmx_path=path,
            next_run_at=next_run,
        )
        s.add(sch)
        await s.flush()
        s.add(SchedulePrincipalModel(schedule_id=sch.id, user_id=scope.authorization.user_id,
                                     incarnation=scope.authorization.incarnation))
        await s.commit()
        return {
            "id": sch.id, "name": sch.name,
            "cron_expression": sch.cron_expression,
            "next_run_at": sch.next_run_at.isoformat() if sch.next_run_at else None,
            "is_enabled": sch.is_enabled,
        }


@router.get("/schedules")
async def list_schedules(
    project_id: Optional[str] = Query(None),
):
    """List all scheduled tests."""
    scope = current_scope()
    if project_id:
        require_project(project_id)
    async with _schedule_session() as s:
        from sqlalchemy import select
        stmt = select(ScheduleModel).order_by(ScheduleModel.created_at.desc())
        if scope is not None:
            readable = [pid for pid, permission in scope.authorization.projects.items() if permission.read]
            stmt = stmt.where(ScheduleModel.project_id.in_(readable))
        if project_id:
            stmt = stmt.where(ScheduleModel.project_id == project_id)
        result = await s.execute(stmt)
        schedules = result.scalars().all()
        return [
            {
                "id": sch.id, "project_id": sch.project_id,
                "test_plan_id": sch.test_plan_id,
                "name": sch.name, "cron_expression": sch.cron_expression,
                "is_enabled": sch.is_enabled,
                "last_run_at": sch.last_run_at.isoformat() if sch.last_run_at else None,
                "next_run_at": sch.next_run_at.isoformat() if sch.next_run_at else None,
                "run_count": sch.run_count,
            }
            for sch in schedules
        ]


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: int, request: UpdateScheduleRequest):
    """Update an existing schedule (name, cron, test plan, jmx path, enabled state)."""
    current_scope()
    payload = request.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields to update')

    cron_changed = 'cron_expression' in payload
    if cron_changed:
        try:
            _next_cron_time(payload['cron_expression'])
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid cron expression') from None

    async with _schedule_session() as s:
        sch = await s.get(ScheduleModel, schedule_id)
        if not sch:
            raise HTTPException(status_code=404, detail="Schedule not found")
        require_project(sch.project_id, write=True)

        if 'test_plan_id' in payload or 'jmx_path' in payload:
            sch.jmx_path = await schedule_source(
                s, sch.project_id,
                payload.get('test_plan_id', sch.test_plan_id),
                payload.get('jmx_path', sch.jmx_path),
            )
            sch.test_plan_id = payload.get('test_plan_id', sch.test_plan_id)
        if 'name' in payload:
            sch.name = payload['name']
        if 'cron_expression' in payload:
            sch.cron_expression = payload['cron_expression']
        if 'is_enabled' in payload:
            sch.is_enabled = payload['is_enabled']

        enabled = payload.get('is_enabled', sch.is_enabled)
        if enabled and (cron_changed or not sch.next_run_at):
            sch.next_run_at = _next_cron_time(sch.cron_expression)
        elif not enabled:
            sch.next_run_at = None

        sch.updated_at = datetime.utcnow()
        await s.commit()
        return {
            "id": sch.id, "name": sch.name,
            "cron_expression": sch.cron_expression,
            "is_enabled": sch.is_enabled,
            "next_run_at": sch.next_run_at.isoformat() if sch.next_run_at else None,
        }


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int):
    """Delete a scheduled test."""
    current_scope()
    async with _schedule_session() as s:
        sch = await s.get(ScheduleModel, schedule_id)
        if not sch:
            raise HTTPException(status_code=404, detail="Schedule not found")
        require_project(sch.project_id, write=True)
        await s.delete(sch)
        await s.commit()
    return {"message": "Schedule deleted"}


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int):
    """Enable or disable a scheduled test."""
    current_scope()
    async with _schedule_session() as s:
        sch = await s.get(ScheduleModel, schedule_id)
        if not sch:
            raise HTTPException(status_code=404, detail="Schedule not found")
        require_project(sch.project_id, write=True)
        sch.is_enabled = not sch.is_enabled
        if sch.is_enabled and not sch.next_run_at:
            sch.next_run_at = _next_cron_time(sch.cron_expression)
        await s.commit()
        return {"id": sch.id, "is_enabled": sch.is_enabled, "next_run_at": sch.next_run_at.isoformat() if sch.next_run_at else None}

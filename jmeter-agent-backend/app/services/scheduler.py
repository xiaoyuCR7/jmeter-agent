"""Background scheduler for recurring performance tests.

Uses a lightweight asyncio loop to check due schedules every 60 seconds.
Stores schedule configs in the database.
"""

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import select

from app.models.db import get_session_factory, ScheduleModel, SchedulePrincipalModel, ProjectModel, TestPlanModel
from app.core.time import utc_now_naive
from app.services.coordination import ConfigurationMismatch, CoordinationError

logger = logging.getLogger(__name__)


async def schedule_source(session, project_id, test_plan_id, jmx_path):
    """Resolve the real file and require both its ownership and declared project."""
    from app.core.config import settings
    from app.services.project_scope import current_scope, require_project, require_path
    require_project(project_id, write=True)
    if await session.get(ProjectModel, project_id) is None:
        raise HTTPException(404, 'Project not found')
    plan = await session.get(TestPlanModel, test_plan_id) if test_plan_id else None
    if test_plan_id:
        if plan is None:
            raise HTTPException(404, 'Test plan not found')
        require_project(plan.project_id)
        if plan.project_id != project_id:
            raise HTTPException(422, 'Schedule source project mismatch')
    source = jmx_path or (plan.jmx_path if plan else '')
    try:
        path = Path(source).expanduser().resolve(strict=True)
        if not source or not path.is_file() or path.suffix.lower() != '.jmx':
            raise ValueError('Invalid source')
    except (OSError, ValueError):
        raise HTTPException(400, 'Schedule requires an existing JMX file') from None
    require_path(str(path))
    canonical = os.path.normcase(str(path))
    if plan is not None:
        if canonical != os.path.normcase(str(Path(plan.jmx_path).expanduser().resolve())):
            raise HTTPException(422, 'Schedule source does not match its test plan')
    else:
        scope = current_scope()
        owners = set()
        if scope is not None:
            for record in (*scope.plans.values(), *scope.executions.values()):
                if any(record.get(key) and os.path.normcase(str(Path(record[key]).expanduser().resolve())) == canonical
                       for key in ('jmx_path', 'source_jmx_path')):
                    owners.add(record['project_id'])
        if owners:
            if owners != {project_id}:
                raise HTTPException(422, 'Schedule source project mismatch')
        elif not path.is_relative_to(Path(settings.TEST_PLANS_DIR).resolve() / project_id):
            raise HTTPException(422, 'Schedule source must belong to its project')
    return str(path)


def _parse_cron_fields(expr: str) -> dict:
    """Parse a standard 5-field cron expression into field ranges.

    Returns dict with minute, hour, dom, month, dow — each as set of valid values.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression: {expr} (need 5 fields)")

    names = ["minute", "hour", "dom", "month", "dow"]
    parsed = {"dom_wildcard": "*" in fields[2], "dow_wildcard": "*" in fields[4]}
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]

    for i, (name, (lo, hi)) in enumerate(zip(names, ranges)):
        field = fields[i]
        values = set()
        for part in field.split(","):
            match = re.fullmatch(r"(\*|[0-9]+(?:-[0-9]+)?)(?:/([0-9]+))?", part)
            if not match:
                raise ValueError(f"Invalid cron {name} field: {field}")
            base, step_text = match.groups()
            step = int(step_text) if step_text else 1
            if step <= 0:
                raise ValueError("Cron step must be positive")
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                start, end = map(int, base.split("-"))
            else:
                start = int(base)
                end = hi if step_text else start
            if not lo <= start <= end <= hi:
                raise ValueError(f"Cron {name} must be within {lo}-{hi}")
            values.update(range(start, end + 1, step))
        parsed[name] = {value % 7 for value in values} if name == "dow" else values

    return parsed


def _cron_day_matches(fields: dict, dt: datetime) -> bool:
    dom = dt.day in fields["dom"]
    dow = (dt.weekday() + 1) % 7 in fields["dow"]
    # Traditional cron: restricted DOM and DOW are alternatives; a wildcard
    # day field leaves the other field in control (including wildcard steps).
    return (dom and dow) if fields["dom_wildcard"] or fields["dow_wildcard"] else (dom or dow)


def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Check if a cron expression matches the given datetime."""
    try:
        fields = _parse_cron_fields(cron_expr)
        return (
            dt.minute in fields["minute"]
            and dt.hour in fields["hour"]
            and dt.month in fields["month"]
            and _cron_day_matches(fields, dt)
        )
    except ValueError:
        return False


def _next_cron_time(cron_expr: str, after: datetime = None) -> Optional[datetime]:
    """Find the next minute, parsing once and checking at most eight years of days.

    Eight years cover a leap-day gap across a non-leap century. The runner
    supplies UTC datetimes; no new local-time/DST policy is introduced here.
    """
    fields = _parse_cron_fields(cron_expr)
    earliest = (after or utc_now_naive()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = earliest.replace(hour=0, minute=0)
    hours, minutes = sorted(fields["hour"]), sorted(fields["minute"])
    for _ in range(366 * 8):
        if day.month in fields["month"] and _cron_day_matches(fields, day):
            for hour in hours:
                if day.date() == earliest.date() and hour < earliest.hour:
                    continue
                for minute in minutes:
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= earliest:
                        return candidate
        if day.year == 9999 and day.month == 12 and day.day == 31:
            break
        day += timedelta(days=1)
    return None


class ScheduleRunner:
    """Background task that checks and executes scheduled tests."""

    def __init__(self, coordinator=None, *, completion_verifier=None):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._jobs: dict[int, asyncio.Task] = {}
        # Explicit injection only. Startup/deployment remains single worker until
        # every entrypoint, execution owner and shutdown path joins coordination.
        from app.services.schedule_dispatch import ScheduleDispatchStore
        self._dispatches = ScheduleDispatchStore(coordinator) if coordinator is not None else None
        # Optional synchronous attestation (execution_id, result) -> exactly True
        # only after actual work, including descendants/containers, is confirmed
        # stopped. Business terminal status or root-process exit is not proof.
        # No production completion/containment verifier is provided in this slice.
        self._completion_verifier = completion_verifier

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("ScheduleRunner started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        jobs = list(self._jobs.values())
        for task in jobs:
            task.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._jobs.clear()
        logger.info("ScheduleRunner stopped")

    async def _loop(self):
        """Main loop: check schedules every 60 seconds."""
        # Wait 5s for the rest of the app to initialize
        await asyncio.sleep(5)

        while self._running:
            try:
                await self._check_and_run()
            except Exception as e:
                logger.error(f"ScheduleRunner error: {e}")
            await asyncio.sleep(60)

    async def _check_and_run(self):
        """Dispatch due schedules; each job has at most one owned task."""
        now = utc_now_naive()
        factory = get_session_factory()
        if self._dispatches is not None:
            engine = factory.kw.get('bind')
            url = getattr(engine, 'url', None)
            if (url is None or url.get_backend_name() != 'sqlite' or not url.database
                    or Path(url.database).resolve() != self._dispatches.coordinator.database_path):
                raise ConfigurationMismatch('Schedules and coordination require the same authoritative SQLite DB')

        async with factory() as s:
            result = await s.execute(
                select(ScheduleModel).where(
                    ScheduleModel.is_enabled == True,
                    ScheduleModel.next_run_at <= now,
                )
            )
            due = result.scalars().all()

        for schedule in due:
            if schedule.id in self._jobs:
                continue
            # Advance before launching. Missed ticks are coalesced, not replayed.
            next_run = _next_cron_time(schedule.cron_expression, now)
            if self._dispatches is not None:
                dispatch = self._dispatches.claim(schedule, now, next_run)
                if dispatch is None:
                    continue
                coroutine = self._run_schedule(schedule, dispatch=dispatch)
                try:
                    task = asyncio.create_task(coroutine)
                except BaseException:
                    coroutine.close()
                    self._dispatches.finish(dispatch)
                    raise
                self._jobs[schedule.id] = task
                task.add_done_callback(lambda done, job_id=schedule.id, owned=dispatch:
                                       self._dispatch_done(job_id, done, owned))
                continue
            async with factory() as s3:
                sch = await s3.get(ScheduleModel, schedule.id)
                if sch:
                    sch.last_run_at = now
                    sch.next_run_at = next_run
                    sch.run_count = (sch.run_count or 0) + 1
                    await s3.commit()
            task = asyncio.create_task(self._run_schedule(schedule))
            self._jobs[schedule.id] = task
            task.add_done_callback(lambda done, job_id=schedule.id: self._job_done(job_id, done))

    def _job_done(self, job_id: int, task: asyncio.Task):
        if self._jobs.get(job_id) is task:
            self._jobs.pop(job_id)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Scheduled job %s failed: %s", job_id, task.exception())

    def _dispatch_done(self, job_id, task, dispatch):
        try:
            # Runs even when a task is cancelled before its coroutine starts.
            self._dispatches.finish(dispatch, cancelled=task.cancelled())
        except Exception:
            # Storage/ownership failures leave claims occupied; no local fallback.
            logger.exception('Could not settle schedule dispatch %s', dispatch.execution_id)
        finally:
            self._job_done(job_id, task)

    async def _run_schedule(self, schedule, *, dispatch=None):
        from app.services.execution_lifecycle import execution_lifecycle
        from app.services.notifications import notify_test_started, notify_test_completed
        from app.services.project_scope import scheduled_scope, refresh_scope, require_project

        async with get_session_factory()() as session:
            # Queued ORM objects are not authoritative after a toggle/delete.
            schedule = await session.get(ScheduleModel, schedule.id)
            if schedule is None or not schedule.is_enabled:
                return
            principal = await session.get(SchedulePrincipalModel, schedule.id)
            user_id = principal.user_id if principal else None
            incarnation = principal.incarnation if principal else None
        if principal is not None and user_id is None:
            raise HTTPException(403, 'Scheduled creator no longer exists')
        async with scheduled_scope(user_id, incarnation):
            async with get_session_factory()() as session:
                jmx_path = await schedule_source(session, schedule.project_id,
                                                 schedule.test_plan_id, schedule.jmx_path)
            logger.info('Running scheduled test id=%s', schedule.id)
            exec_id = dispatch.execution_id if dispatch else f"sched_{schedule.id}_{uuid.uuid4().hex[:12]}"
            async def started(execution):
                # Lifecycle has persisted execution ownership before this hook.
                await refresh_scope()
                require_project(schedule.project_id, write=True)
                await notify_test_started(execution_id=exec_id, jmx_path=jmx_path)
                # A webhook can yield long enough for access to be revoked.
                await refresh_scope()
                require_project(schedule.project_id, write=True)
            try:
                await refresh_scope()
                if dispatch is not None:
                    self._dispatches.mark_started(dispatch)
                result = await execution_lifecycle.execute(
                    jmx_path, exec_id, test_plan_id=schedule.test_plan_id,
                    project_id=schedule.project_id, before_execute=started)
                if dispatch is not None:
                    # The business result remains untouched when containment
                    # proof is missing. Only an explicit attestation may release.
                    if self._completion_verifier is not None:
                        try:
                            dispatch.work_completed = self._completion_verifier(exec_id, result) is True
                        except Exception:
                            logger.exception('Actual completion could not be verified for %s', exec_id)
                await refresh_scope()
                require_project(schedule.project_id)
                await notify_test_completed(
                    execution_id=exec_id, status=result.status.value,
                    report_dir=result.report_dir or '', log_path=result.log_path or '',
                    error=result.error_message)
            except (HTTPException, CoordinationError):
                raise
            except Exception as exc:
                await refresh_scope()
                require_project(schedule.project_id)
                await notify_test_completed(execution_id=exec_id, status='failed', error=str(exc))


# Module-level singleton
runner = ScheduleRunner()

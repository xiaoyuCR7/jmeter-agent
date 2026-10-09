"""Opt-in same-host schedule admission; never startup wiring or a replay queue.

The coordinator and schedule ledger MUST use the same authoritative SQLite DB.
This narrow adapter reuses the coordinator's bounded transaction and exact-claim
validation so the enabled/due CAS and permanent batch insert commit together.
No SQL transaction remains open during execution, notifications or awaits.
"""
from dataclasses import dataclass
import uuid

from app.core.time import utc_now_naive
from app.services.coordination import Busy, ClaimHandle, StaleClaim


def _timestamp(value):
    # Match SQLAlchemy's SQLite DateTime representation exactly, including .000000.
    return value.isoformat(' ', timespec='microseconds') if value is not None else None


@dataclass
class ScheduleDispatch:
    execution_id: str
    handle: ClaimHandle
    effects_possible: bool = False
    work_completed: bool = False
    settled: bool = False


class _ScheduleChanged(Exception):
    pass


class ScheduleDispatchStore:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def claim(self, schedule, now, next_run):
        """Return the newly committed batch only; busy/stale/duplicate skip it."""
        if schedule.next_run_at is None or schedule.next_run_at > now:
            return None
        execution_id = f'sched_{uuid.uuid4().hex}'
        try:
            admission = self.coordinator.claim(execution_id, [f'schedule:{schedule.id}'])
        except Busy:
            return None
        if not admission.acquired:
            return None
        dispatch = ScheduleDispatch(execution_id, admission.handle)
        committed = False
        try:
            with self.coordinator._transaction() as connection:
                self.coordinator._validate_claim(connection, dispatch.handle)
                inserted = connection.execute(
                    'INSERT INTO schedule_dispatches (execution_id,schedule_id,planned_at,'
                    'owner_generation,operation_id,claim_id,state,created_at,updated_at) '
                    'VALUES (?,?,?,?,?,?,\'committed\',?,?) '
                    'ON CONFLICT(schedule_id,planned_at) DO NOTHING',
                    (execution_id, schedule.id, _timestamp(schedule.next_run_at),
                     dispatch.handle.owner_generation, dispatch.handle.operation_id,
                     dispatch.handle.claim_id, _timestamp(now), _timestamp(now)))
                if inserted.rowcount != 1:
                    raise _ScheduleChanged()
                changed = connection.execute(
                    'UPDATE schedules SET last_run_at=?,next_run_at=?,'
                    'run_count=COALESCE(run_count,0)+1,updated_at=? '
                    'WHERE id=? AND is_enabled=1 AND next_run_at=? AND cron_expression=?',
                    (_timestamp(now), _timestamp(next_run), _timestamp(now), schedule.id,
                     _timestamp(schedule.next_run_at), schedule.cron_expression))
                if changed.rowcount != 1:
                    raise _ScheduleChanged()
            committed = True
            return dispatch
        except _ScheduleChanged:
            return None
        finally:
            # Nothing external has been called yet, even if commit had an
            # ambiguous storage result. A persisted batch still cannot replay.
            if not committed:
                self.coordinator.release(dispatch.handle, work_completed=True)

    def _state(self, dispatch, state):
        with self.coordinator._transaction() as connection:
            self.coordinator._validate_claim(connection, dispatch.handle)
            changed = connection.execute(
                'UPDATE schedule_dispatches SET state=?,updated_at=? '
                'WHERE execution_id=? AND owner_generation=? AND operation_id=? AND claim_id=? '
                "AND state IN ('committed','running')",
                (state, _timestamp(utc_now_naive()), dispatch.execution_id,
                 dispatch.handle.owner_generation, dispatch.handle.operation_id, dispatch.handle.claim_id))
            if changed.rowcount != 1:
                raise StaleClaim('Schedule dispatch is no longer current')

    def mark_started(self, dispatch):
        """Must commit before lifecycle preparation, notifications or execution."""
        # Set first: a storage error cannot prove mark_side_effects did not commit.
        dispatch.effects_possible = True
        self.coordinator.mark_side_effects(dispatch.handle)
        self._state(dispatch, 'running')

    def finish(self, dispatch, *, cancelled=False):
        """Async wrapper cancellation is never proof of actual process completion."""
        if dispatch.settled:
            return
        uncertain = dispatch.effects_possible and (cancelled or not dispatch.work_completed)
        state = 'unknown' if uncertain else 'completed' if dispatch.work_completed else 'abandoned'
        self._state(dispatch, state)
        if uncertain:
            self.coordinator.quarantine(dispatch.handle)
        else:
            self.coordinator.release(dispatch.handle, work_completed=True)
        dispatch.settled = True

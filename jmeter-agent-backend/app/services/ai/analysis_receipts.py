"""Owner-scoped durable receipts. Uncertain requests are never reclaimed/replayed."""
import asyncio
import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update, text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.models import db
from app.services.auth import Role
from app.services.security_context import current_principal
from app.services import project_scope
from app.services.coordination import PROTOCOL_VERSION
from app.services.process_identity import ProcessIdentity, current_identity, coordination_domain, probe, Liveness

logger = logging.getLogger(__name__)
MAX_RESULT_BYTES = 4 * 1024 * 1024
_current_receipt = ContextVar('analysis_receipt', default=None)


class ReceiptError(RuntimeError):
    def __init__(self, message='Analysis receipt storage unavailable; do not resubmit with a new key automatically',
                 status_code=503):
        super().__init__(message)
        self.status_code = status_code


@asynccontextmanager
async def _session():
    try:
        async with db.get_session_factory()() as session:
            yield session
    except ReceiptError:
        raise
    except Exception as error:
        logger.error('Analysis receipt storage failed (%s)', type(error).__name__)
        raise ReceiptError() from error


@dataclass(frozen=True)
class ReceiptHandle:
    owner_id: int
    request_id: str
    generation: str


def result_payload(result):
    """Only final report fields; never persist task, context, events or traceback."""
    return dict(agent_type='analysis', success=result.success, output=result.output,
                plan_steps=[dict(step_id=s.step_id, description=s.description, success=s.success) for s in result.plan],
                iterations=result.iterations, error=result.error, duration_seconds=result.duration_seconds,
                **({'analysis': result.analysis} if result.analysis is not None else {}))


def metadata(receipt):
    return {key: value for key, value in receipt.items() if key != 'result'}


def _require_execution(execution_id, *, write=False):
    try:
        project_scope.require_execution(execution_id, write=write)
    except HTTPException as error:
        raise ReceiptError(str(error.detail), error.status_code) from error


async def _authorize_execution(execution_id, *, write=False):
    try:
        await project_scope.refresh_scope()
    except HTTPException as error:
        raise ReceiptError(str(error.detail), error.status_code) from error
    _require_execution(execution_id, write=write)


class AnalysisReceipts:
    def __init__(self, coordinator=None):
        self._coordinator_override = coordinator
        self.generation = uuid.uuid4().hex
        self.accepting = True
        self._active = {}

    @property
    def coordinator(self):
        if self._coordinator_override is not None:
            return self._coordinator_override
        from app.services.ai.operation_runtime import agent_operations
        return agent_operations.coordinator

    async def _current_process_owner(self, session, *, accepting=False):
        """Validate in the caller's async transaction; never nest a sync writer."""
        coordinator = self.coordinator
        if coordinator is None:
            raise ReceiptError('Receipt ownership coordination is unavailable')
        bind = session.get_bind()
        if bind.dialect.name != 'sqlite' or not bind.url.database \
                or Path(bind.url.database).resolve() != coordinator.database_path:
            raise ReceiptError('Receipt and coordination must use the same application database')
        actual = current_identity()
        expected = coordinator.owner
        if (actual.pid, actual.creation_time) != (expected.pid, expected.creation_time) \
                or coordination_domain() != coordinator.domain:
            raise ReceiptError('Receipt belongs to a different process identity')
        protocol = await session.get(db.CoordinationProtocolModel, 1)
        capacities = json.dumps(coordinator.capacities, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        if protocol is None or protocol.protocol != PROTOCOL_VERSION or coordinator.protocol != PROTOCOL_VERSION \
                or protocol.domain != coordinator.domain or protocol.capacities_json != capacities:
            raise ReceiptError('Receipt coordination protocol or domain is inconsistent')
        owner = await session.get(db.ProcessOwnerModel, expected.generation)
        states = ('active',) if accepting else ('active', 'draining')
        if owner is None or (owner.pid, owner.creation_time, owner.protocol) != (
            expected.pid, expected.creation_time, coordinator.protocol
        ) or owner.state not in states:
            raise ReceiptError('Receipt process owner is not current')
        return owner

    async def _bound_operation(self, session, binding, *, states, handle=None):
        if binding.operation_id is None or binding.claim_id is None:
            if binding.operation_id is not None or binding.claim_id is not None:
                raise ReceiptError('Receipt operation binding is incomplete')
            if handle is not None:
                raise ReceiptError('Receipt has not bound this operation')
            return None
        operation = await session.get(db.CoordinationOperationModel, binding.operation_id)
        if operation is None or (operation.owner_generation, operation.claim_id) != (
            binding.process_generation, binding.claim_id
        ) or operation.state not in states:
            raise ReceiptError('Receipt operation binding is no longer current')
        try:
            resources = json.loads(operation.resources_json)
            if not isinstance(resources, list) or not resources or len(resources) > 1024:
                raise ValueError('Malformed operation resources')
            resources = tuple((key, fence) for key, fence in resources)
            if any(not isinstance(key, str) or not key or type(fence) is not int or not 0 < fence < 2 ** 63
                   for key, fence in resources) or len({key for key, _ in resources}) != len(resources):
                raise ValueError('Malformed operation fences')
        except (TypeError, ValueError) as error:
            raise ReceiptError('Receipt operation resources are invalid') from error
        if handle is not None and (
            handle.owner_generation != binding.process_generation or handle.operation_id != binding.operation_id
            or handle.claim_id != binding.claim_id or tuple((token.key, token.fence) for token in handle.resources) != resources
        ):
            raise ReceiptError('Receipt operation claim does not match')
        # A finished operation may have released its resources before the final
        # receipt commit. Its immutable operation ID/claim still identify it.
        if operation.state in ('active', 'draining'):
            for key, fence in resources:
                lease = await session.get(db.ResourceLeaseModel, key)
                if lease is None or (lease.owner_generation, lease.operation_id, lease.claim_id, lease.fence, lease.state) != (
                    binding.process_generation, binding.operation_id, binding.claim_id, fence, operation.state
                ):
                    raise ReceiptError('Receipt resource ownership is stale')
        return operation

    async def _writable_owner(self, session, row, handle, *, cleanup=False, final=False):
        if handle.generation != self.generation:
            raise ReceiptError('Receipt service generation is not current')
        binding = await session.get(db.AnalysisReceiptOwnerModel, row.id)
        if binding is None:
            if self.coordinator is not None:
                raise ReceiptError('Legacy receipt has no coordinated owner')
            return None
        owner = await self._current_process_owner(session)
        if binding.process_generation != owner.generation:
            raise ReceiptError('Receipt belongs to a different worker')
        states = ('active', 'draining')
        if final or cleanup:
            states += ('released',)
        if cleanup:
            states += ('quarantined',)
        await self._bound_operation(session, binding, states=states)
        return binding

    async def bind_operation(self, handle, ownership):
        """Bind once after Agent admission and before Memory/model/tool work."""
        if await self._owner(mutate=True) != handle.owner_id:
            raise ReceiptError('Analysis receipt not found', 404)
        async with _session() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            row = (await session.execute(select(db.AnalysisReceiptModel).where(
                db.AnalysisReceiptModel.owner_id == handle.owner_id,
                db.AnalysisReceiptModel.request_id == handle.request_id,
                db.AnalysisReceiptModel.generation == handle.generation,
                db.AnalysisReceiptModel.state == 'running'))).scalar_one_or_none()
            if row is None:
                raise ReceiptError('Analysis receipt is no longer writable')
            await _authorize_execution(row.execution_id, write=True)
            binding = await self._writable_owner(session, row, handle)
            if binding is None:
                if ownership is not None:
                    raise ReceiptError('Cannot bind an uncoordinated receipt to an operation')
                return
            if ownership is None or ownership.runtime.coordinator is None:
                raise ReceiptError('A coordinated operation is required')
            claim = ownership.handle
            if claim.owner_generation != binding.process_generation:
                raise ReceiptError('Operation belongs to a different worker')
            if binding.operation_id is not None and (binding.operation_id, binding.claim_id) != (
                claim.operation_id, claim.claim_id
            ):
                raise ReceiptError('Receipt is already bound to another operation')
            # Populate only in-memory first; failed validation rolls back without
            # persisting a partial operation binding.
            binding.operation_id, binding.claim_id = claim.operation_id, claim.claim_id
            await self._bound_operation(session, binding, states=('active', 'draining'), handle=claim)
            await session.commit()

    async def _running_state(self, session, row):
        binding = await session.get(db.AnalysisReceiptOwnerModel, row.id)
        if binding is None:
            handle = ReceiptHandle(row.owner_id, row.request_id, row.generation)
            owner_task = self._active.get(handle)
            return 'running' if owner_task is not None and not owner_task.done() else 'unknown'
        try:
            await self._current_process_owner(session)
            owner = await session.get(db.ProcessOwnerModel, binding.process_generation)
            if owner is None or owner.protocol != PROTOCOL_VERSION or owner.state not in ('active', 'draining'):
                return 'unknown'
            await self._bound_operation(session, binding, states=('active', 'draining'))
            return 'running' if probe(ProcessIdentity(owner.pid, owner.creation_time)) is Liveness.ALIVE else 'unknown'
        except (ReceiptError, ValueError, TypeError):
            return 'unknown'

    def start(self):
        if self._active:
            raise RuntimeError('Cannot restart receipt tracking with active requests')
        self.generation = uuid.uuid4().hex
        self.accepting = True

    def _track_owner(self, handle):
        task = asyncio.current_task()
        if self._active.get(handle) is task:
            return
        self._active[handle] = task

        def completed(owner):
            # A stream runner may have taken over from the claiming request.
            # Only forget this task; the durable reservation must remain intact.
            if self._active.get(handle) is owner:
                self._active.pop(handle, None)

        task.add_done_callback(completed)

    async def _owner(self, mutate=False):
        principal = current_principal.get()
        if principal is None or principal.user_id is None:
            raise ReceiptError('Authentication required', 401)
        if mutate and not Role.can(principal.role, Role.OPERATOR):
            raise ReceiptError('Operator role required', 403)
        async with _session() as session:
            owner = (await session.execute(select(db.UserModel).where(
                db.UserModel.id == principal.user_id, db.UserModel.username == principal.username,
                db.UserModel.is_active.is_(True)))).scalar_one_or_none()
            if owner is None:
                raise ReceiptError('Active user required', 401)
            if mutate and not Role.can(owner.role, Role.OPERATOR):
                raise ReceiptError('Operator role required', 403)
            owner_id = owner.id
        # The first authorization snapshot may initialize UserAccessRevision
        # using its own connection. Materialize it before taking our write lock.
        # Each protected writer STILL refreshes and authorizes inside its own
        # transaction, so revocation between these boundaries is not accepted.
        if mutate:
            try:
                await project_scope.refresh_scope()
            except HTTPException as error:
                raise ReceiptError(str(error.detail), error.status_code) from error
        return owner_id

    async def _view(self, row, session):
        _require_execution(row.execution_id)
        state = row.state
        if state == 'running':
            state = await self._running_state(session, row)
        return dict(request_id=row.request_id, execution_id=row.execution_id, state=state, phase=row.phase,
                    partial_effects_possible=row.partial_effects_possible, archive=row.archive_data,
                    result_available=row.result_data is not None, result=row.result_data,
                    created_at=row.created_at.isoformat() + 'Z', updated_at=row.updated_at.isoformat() + 'Z')

    async def get(self, request_id):
        owner = await self._owner()
        async with _session() as session:
            row = (await session.execute(select(db.AnalysisReceiptModel).where(
                db.AnalysisReceiptModel.owner_id == owner,
                db.AnalysisReceiptModel.request_id == request_id))).scalar_one_or_none()
            if row is None:
                raise ReceiptError('Analysis receipt not found', 404)
            await _authorize_execution(row.execution_id)
            return await self._view(row, session)

    async def claim(self, request):
        owner = await self._owner(mutate=True)
        fields = request.model_dump(exclude={'request_id', 'stream'})
        fingerprint = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        handle = ReceiptHandle(owner, request.request_id, self.generation)
        async with _session() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            query = select(db.AnalysisReceiptModel).where(db.AnalysisReceiptModel.owner_id == owner,
                                                        db.AnalysisReceiptModel.request_id == request.request_id)
            row = (await session.execute(query)).scalar_one_or_none()
            await _authorize_execution(row.execution_id if row is not None else request.execution_id,
                                       write=row is None)
            if row is None:
                if not self.accepting:
                    raise ReceiptError('Analysis service is stopping', 503)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                row = db.AnalysisReceiptModel(owner_id=owner, request_id=request.request_id,
                    execution_id=request.execution_id, fingerprint=fingerprint, generation=self.generation,
                    state='running', phase='accepted', partial_effects_possible=False, created_at=now, updated_at=now)
                session.add(row)
                try:
                    if self.coordinator is not None:
                        process_owner = await self._current_process_owner(session, accepting=True)
                        await session.flush()
                        session.add(db.AnalysisReceiptOwnerModel(receipt_id=row.id,
                            process_generation=process_owner.generation))
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    row = (await session.execute(query)).scalar_one_or_none()
                    if row is None:
                        raise ReceiptError()
                else:
                    if not self.accepting:
                        # A stop raced the commit. The reserved key stays unknown.
                        raise ReceiptError('Analysis service stopped during admission', 503)
                    self._track_owner(handle)
                    return handle, await self._view(row, session)
            if row.fingerprint != fingerprint:
                raise ReceiptError('request_id already belongs to different analysis parameters', 409)
            return None, await self._view(row, session)

    async def _update(self, handle, *, authorize=True, **values):
        if not authorize and (set(values) != {'state'} or values['state'] not in ('unknown', 'cancelled', 'failed')):
            raise ReceiptError('Maintenance may only finalize receipt state', 403)
        if authorize and await self._owner(mutate=True) != handle.owner_id:
            raise ReceiptError('Analysis receipt not found', 404)
        async with _session() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            row = (await session.execute(select(db.AnalysisReceiptModel).where(
                db.AnalysisReceiptModel.owner_id == handle.owner_id,
                db.AnalysisReceiptModel.request_id == handle.request_id,
                db.AnalysisReceiptModel.generation == handle.generation))).scalar_one_or_none()
            if row is None:
                raise ReceiptError('Analysis receipt is no longer writable')
            await self._writable_owner(session, row, handle, cleanup=not authorize,
                                       final='state' in values and 'result_data' in values)
            if authorize:
                await _authorize_execution(row.execution_id, write=True)
            changed = await session.execute(update(db.AnalysisReceiptModel).where(
                db.AnalysisReceiptModel.owner_id == handle.owner_id,
                db.AnalysisReceiptModel.request_id == handle.request_id,
                db.AnalysisReceiptModel.generation == handle.generation,
                db.AnalysisReceiptModel.state == 'running').values(
                    **values, updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            if changed.rowcount != 1:
                raise ReceiptError('Analysis receipt is no longer writable', 503)
            await session.commit()
            if not authorize:
                return None  # Maintenance finalization must not load/report user content.
            row = (await session.execute(select(db.AnalysisReceiptModel).where(
                db.AnalysisReceiptModel.owner_id == handle.owner_id,
                db.AnalysisReceiptModel.request_id == handle.request_id))).scalar_one()
            return await self._view(row, session)

    async def checkpoint(self, handle, phase, archive=None):
        values = dict(phase=phase)
        if phase in ('model', 'postprocessing', 'archive', 'evaluation'):
            values['partial_effects_possible'] = True
        if archive is not None:
            values['archive_data'] = {key: archive[key] for key in (
                'fact_archived', 'hypotheses_archived', 'conclusion_archived', 'raw_report_archived') if key in archive}
        await self._update(handle, **values)

    async def finish(self, handle, result):
        payload = result_payload(result)
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > MAX_RESULT_BYTES:
                raise ValueError('Result exceeds receipt size limit')
        except (TypeError, ValueError, OverflowError) as error:
            raise ReceiptError('Analysis result could not be persisted; earlier effects may have completed') from error
        state = 'succeeded' if result.success else 'cancelled' if result.error == 'Cancelled by user' else 'failed'
        return await self._update(handle, state=state, result_data=json.loads(encoded))

    async def abandon(self, handle, state='unknown'):
        if state not in ('unknown', 'cancelled', 'failed'):
            raise ValueError('Abandon can only record unknown, cancelled or failed state')
        try:
            await self._update(handle, authorize=False, state=state)
        finally:
            self._active.pop(handle, None)

    async def run(self, handle, operation):
        if handle not in self._active or not self.accepting:
            raise ReceiptError('Analysis request is not active', 503)
        self._track_owner(handle)
        token = _current_receipt.set((self, handle))
        try:
            result = await operation()
            receipt = await self.finish(handle, result)
            result.receipt = metadata(receipt)
            return result
        except BaseException as error:
            state = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'unknown'
            try:
                await self.abandon(handle, state)
            except ReceiptError:
                pass  # Keep the reservation; read-side reports an unknown outcome.
            raise
        finally:
            _current_receipt.reset(token)
            self._active.pop(handle, None)

    async def shutdown(self):
        self.accepting = False
        owners = {task for task in self._active.values() if task is not asyncio.current_task() and not task.done()}
        for task in owners:
            task.cancel()
        if owners:
            await asyncio.gather(*owners, return_exceptions=True)
        for handle in list(self._active):
            try:
                await self.abandon(handle, 'cancelled')
            except ReceiptError:
                pass


analysis_receipts = AnalysisReceipts()


async def bind_current_operation(ownership):
    bound = _current_receipt.get()
    if bound is not None:
        service, handle = bound
        await service.bind_operation(handle, ownership)


async def checkpoint(phase, archive=None):
    if phase in ('model', 'archive', 'evaluation'):
        from app.services.ai.operation_runtime import current_operation
        operation = current_operation()
        if operation is not None:
            operation.before_effect()
    bound = _current_receipt.get()
    if bound is not None:
        service, handle = bound
        await service.checkpoint(handle, phase, archive)

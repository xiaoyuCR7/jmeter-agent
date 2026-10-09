"""Optional durable Agent admission; deployment wiring is a separate gate.

The default instance preserves single-worker operation. Once explicitly supplied
a Coordinator, storage failures never fall back to local admission. Actual thread
futures retain the whole claim even if their asyncio waiter/loop has disappeared.
"""
import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from app.services.coordination import CoordinationError, StaleClaim, OperationConflict

logger = logging.getLogger(__name__)
_operation = ContextVar('coordinated_agent_operation', default=None)


class AgentOperationRuntime:
    def __init__(self, coordinator=None):
        self.coordinator = coordinator
        self._operations = set()
        self._lock = threading.RLock()
        self.accepting = True

    @property
    def enabled(self):
        return self.coordinator is not None

    @property
    def active_count(self):
        with self._lock:
            return len(self._operations)

    def acquire(self, session_id, *, analysis=False):
        capacity = {'agent': 1, **({'analysis': 1} if analysis else {})}
        return self._acquire(session_id, capacity)

    def acquire_session_maintenance(self, session_id):
        return self._acquire(session_id, {})

    def _acquire(self, session_id, capacity):
        if not self.enabled:
            return None
        with self._lock:
            if not self.accepting:
                raise CoordinationError('Agent admission is stopping')
            admitted = self.coordinator.claim(uuid.uuid4().hex,
                resources=['session:' + session_id] if session_id else [], capacity=capacity)
            if not admitted.acquired:
                raise OperationConflict('Existing operation cannot authorize new work')
            operation = AgentOperation(self, admitted.handle, session_id)
            self._operations.add(operation)
            return operation

    def stop_admission(self):
        with self._lock:
            self.accepting = False

    def _finished(self, operation):
        with self._lock:
            self._operations.discard(operation)


class AgentOperation:
    def __init__(self, runtime, handle, session_id):
        self.runtime = runtime
        self.handle = handle
        self.session_id = session_id
        self._lock = threading.RLock()
        self._pending = set()
        self.closed = False
        self.has_effects = False
        self.unresolved = False
        self._released = False

    @contextmanager
    def activate(self):
        token = _operation.set(self)
        try:
            yield self
        finally:
            _operation.reset(token)

    def assert_active(self):
        with self._lock:
            if self.closed:
                raise StaleClaim('Operation is already closing or closed')
            self.runtime.coordinator.assert_owned(self.handle)

    def before_effect(self):
        with self._lock:
            if self.closed:
                raise StaleClaim('Operation is already closing or closed')
            # Commit before the caller enters its SDK, Tool or archive writer.
            self.runtime.coordinator.mark_side_effects(self.handle)
            self.has_effects = True

    def track_future(self, future):
        with self._lock:
            if self.closed:
                raise StaleClaim('Cannot submit work into a closed operation')
            self._pending.add(future)
            future.add_done_callback(self._work_finished)

    def _work_finished(self, future):
        with self._lock:
            self._pending.discard(future)
            self._finish_if_drained()

    def close(self, *, uncertain=False):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self.unresolved = self.unresolved or uncertain
            try:
                if self.unresolved:
                    self.runtime.coordinator.quarantine(self.handle)
                elif self._pending:
                    self.runtime.coordinator.mark_draining(self.handle)
            except CoordinationError:
                logger.error('Could not persist operation drain state; ownership retained')
                self.unresolved = True
            self._finish_if_drained()

    def _finish_if_drained(self):
        if not self.closed or self._pending or self._released or self.unresolved:
            return
        try:
            self.runtime.coordinator.release(self.handle, work_completed=True)
        except CoordinationError:
            # Do not remove the local record or claim that a failed CAS freed it.
            logger.error('Could not release completed operation; ownership retained')
            return
        self._released = True
        self.runtime._finished(self)


def current_operation():
    return _operation.get()


def require_session_owner(session_id):
    if not agent_operations.enabled:
        return
    operation = current_operation()
    if operation is None or operation.session_id != session_id:
        raise StaleClaim('Conversation history requires its current session claim')
    operation.assert_active()


@contextmanager
def effect_boundary():
    operation = current_operation()
    if operation is not None:
        operation.before_effect()
    try:
        yield
    except BaseException:
        if operation is not None:
            # The external boundary did not return a known result. Preserve the
            # claim for resolution rather than equating coroutine exit to stop.
            operation.unresolved = True
        raise


agent_operations = AgentOperationRuntime()

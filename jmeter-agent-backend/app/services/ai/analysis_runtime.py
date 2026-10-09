"""Process-local admission; cancelled waiters do not release running thread work."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import partial
from typing import Optional

from app.core.config import settings


_current_lease: ContextVar[Optional['AnalysisLease']] = ContextVar('analysis_lease', default=None)


class AnalysisRuntime:
    def __init__(self, limit: Optional[int] = None):
        self._configured_limit = limit
        self._limit = None
        self._loop = None
        self._executor = None
        self._leases: set[AnalysisLease] = set()
        self._idle = None
        self._accepting = True  # Standalone callers may initialize lazily.

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def active_count(self) -> int:
        return len(self._leases)

    @property
    def draining_count(self) -> int:
        return sum(lease.closed for lease in self._leases)

    def start(self) -> None:
        if self._leases:
            raise RuntimeError('Cannot restart analysis resources while work remains')
        limit = self._configured_limit
        if limit is None:
            limit = settings.ANALYSIS_MAX_CONCURRENT
        if limit is None:
            limit = settings.AGENT_MAX_CONCURRENT
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError('Analysis concurrency must be a positive integer')
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor = None
        self._limit = limit
        self._loop = asyncio.get_running_loop()
        self._idle = asyncio.Event()
        self._idle.set()
        self._accepting = True

    def try_acquire(self) -> Optional['AnalysisLease']:
        if not self._accepting:
            return None
        if self._loop is not asyncio.get_running_loop():
            # No cross-loop ownership transfer while active or draining.
            self.start()
        if len(self._leases) >= self._limit:
            return None
        lease = AnalysisLease(self, asyncio.current_task())
        self._leases.add(lease)
        self._idle.clear()
        return lease

    def _release(self, lease: 'AnalysisLease') -> None:
        self._leases.discard(lease)
        if not self._leases:
            self._idle.set()

    async def shutdown(self) -> None:
        self._accepting = False
        if self._executor is not None:
            # This cancels queued futures, never claims running functions stopped.
            self._executor.shutdown(wait=False, cancel_futures=True)
        owners = set()
        for lease in list(self._leases):
            owner = lease.owner
            if owner is not None and not owner.done() and owner is not asyncio.current_task():
                owners.add(owner)
                owner.cancel()
            else:
                lease.close()
        if owners:
            await asyncio.gather(*owners, return_exceptions=True)
        for lease in list(self._leases):
            if lease.owner is not None and lease.owner.done():
                lease.close()
        if self._idle is not None:
            await self._idle.wait()


class AnalysisLease:
    def __init__(self, runtime: AnalysisRuntime, owner: asyncio.Task):
        self.runtime = runtime
        self.owner = owner
        self.closed = False
        self._pending: set[Future] = set()

    @contextmanager
    def activate(self):
        token = _current_lease.set(self)
        try:
            yield self
        finally:
            _current_lease.reset(token)

    async def run_sync(self, function, *args, **kwargs):
        if self.closed or not self.runtime.accepting:
            raise asyncio.CancelledError()
        for future in list(self._pending):
            if future.done():
                self._work_done(future)
        if self._pending:
            raise RuntimeError('Analysis already has outstanding synchronous work')
        from app.services.ai.operation_runtime import current_operation
        operation = current_operation()
        if operation is not None:
            operation.assert_active()
        if self.runtime._executor is None:
            self.runtime._executor = ThreadPoolExecutor(
                max_workers=self.runtime._limit, thread_name_prefix='analysis-read')
        future = self.runtime._executor.submit(copy_context().run, partial(function, *args, **kwargs))
        self._pending.add(future)
        if operation is not None:
            operation.track_future(future)
        loop = self.runtime._loop

        def finished(actual: Future):
            # Future.cancel() fails once running; only its real completion frees work.
            if not loop.is_closed():
                loop.call_soon_threadsafe(self._work_done, actual)

        future.add_done_callback(finished)
        return await asyncio.wrap_future(future)

    def _work_done(self, future: Future) -> None:
        self._pending.discard(future)
        if self.closed and not self._pending:
            self.runtime._release(self)

    def close(self) -> None:
        self.closed = True
        self.owner = None
        for future in list(self._pending):
            future.cancel()  # Only pending work can be cancelled.
            if future.done():
                self._work_done(future)
        if not self._pending:
            self.runtime._release(self)


analysis_runtime = AnalysisRuntime()


async def run_analysis_sync(function, *args, **kwargs):
    """Keep unregistered/legacy reads on their original asyncio.to_thread path."""
    lease = _current_lease.get()
    if lease is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    return await lease.run_sync(function, *args, **kwargs)

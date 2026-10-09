"""Admission and physical thread ownership; no model/JMeter/network activity."""
import asyncio
import contextvars
import threading

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def runtime():
    from app.services.ai.analysis_runtime import AnalysisRuntime
    resource = AnalysisRuntime(limit=1)
    resource.start()
    yield resource
    await resource.shutdown()


@pytest.mark.asyncio
async def test_analysis_admission_capacity_and_release_are_exact(runtime):
    lease = runtime.try_acquire()
    assert lease is not None and runtime.active_count == 1
    assert runtime.try_acquire() is None
    lease.close()
    lease.close()
    assert runtime.active_count == 0
    replacement = runtime.try_acquire()
    assert replacement is not None
    replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit, fallback, expected', [(None, 2, 2), (1, 3, 1)])
async def test_analysis_admission_configured_limit_and_fallback(monkeypatch, explicit, fallback, expected):
    from app.core.config import settings
    from app.services.ai.analysis_runtime import AnalysisRuntime
    monkeypatch.setattr(settings, 'ANALYSIS_MAX_CONCURRENT', explicit, raising=False)
    monkeypatch.setattr(settings, 'AGENT_MAX_CONCURRENT', fallback)
    resource = AnalysisRuntime()
    resource.start()
    leases = [resource.try_acquire() for _ in range(expected)]
    try:
        assert all(leases) and resource.try_acquire() is None
    finally:
        for lease in leases:
            lease.close()
        await resource.shutdown()


@pytest.mark.parametrize('invalid', [0, -1, True, False, 1.5, 'wrong', '2.5', ''])
def test_analysis_admission_setting_rejects_invalid_values(invalid):
    from app.core.config import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ANALYSIS_MAX_CONCURRENT=invalid)


@pytest.mark.asyncio
async def test_analysis_admission_invalid_fallback_fails_before_work(monkeypatch):
    from app.core.config import settings
    from app.services.ai.analysis_runtime import AnalysisRuntime
    monkeypatch.setattr(settings, 'ANALYSIS_MAX_CONCURRENT', None, raising=False)
    monkeypatch.setattr(settings, 'AGENT_MAX_CONCURRENT', 0)
    with pytest.raises(ValueError):
        AnalysisRuntime().start()


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_analysis_admission_cancelled_thread_keeps_slot_until_physical_finish(runtime, fails):
    from app.services.ai.analysis_runtime import run_analysis_sync
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert release.wait(10), 'test thread was not released'
        if fails:
            raise OSError('injected read failure')
        return 'read result'
    lease = runtime.try_acquire()
    async def run():
        with lease.activate():
            return await run_analysis_sync(read)
    waiter = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        waiter.cancel()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        lease.close()
        lease.close()
        assert runtime.active_count == 1 and runtime.draining_count == 1
        assert runtime.try_acquire() is None
    finally:
        release.set()
        lease.close()
        await asyncio.gather(waiter, return_exceptions=True)
    async with asyncio.timeout(5):
        while runtime.active_count:
            await asyncio.sleep(0)
    assert runtime.draining_count == 0
    fresh = runtime.try_acquire()
    assert fresh is not None
    fresh.close()


@pytest.mark.asyncio
async def test_analysis_admission_preserves_context_and_exception(runtime):
    from app.services.ai.analysis_runtime import run_analysis_sync
    marker = contextvars.ContextVar('fixture-marker', default='absent')
    token = marker.set('present')
    lease = runtime.try_acquire()
    try:
        with lease.activate():
            assert await run_analysis_sync(marker.get) == 'present'
            with pytest.raises(OSError, match='fixture'):
                await run_analysis_sync(lambda: (_ for _ in ()).throw(OSError('fixture')))
    finally:
        marker.reset(token)
        lease.close()
    await asyncio.sleep(0)
    assert runtime.active_count == 0


@pytest.mark.asyncio
async def test_analysis_admission_rejects_parallel_submissions_in_one_lease(runtime):
    from app.services.ai.analysis_runtime import run_analysis_sync
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        return release.wait(10)
    lease = runtime.try_acquire()
    try:
        with lease.activate():
            first = asyncio.create_task(run_analysis_sync(read))
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(RuntimeError, match='outstanding'):
                await run_analysis_sync(lambda: 'must not submit')
            release.set()
            assert await first
    finally:
        release.set()
        lease.close()


@pytest.mark.asyncio
async def test_analysis_admission_unbound_helper_preserves_legacy_behavior(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.ai.analysis_runtime import run_analysis_sync
    execute = AsyncMock(return_value='legacy')
    monkeypatch.setattr(asyncio, 'to_thread', execute)
    assert await run_analysis_sync(str, 7) == 'legacy'
    execute.assert_awaited_once_with(str, 7)


@pytest.mark.asyncio
async def test_analysis_admission_cancelled_queued_work_never_runs():
    from concurrent.futures import ThreadPoolExecutor
    from app.services.ai.analysis_runtime import AnalysisRuntime, run_analysis_sync
    resource = AnalysisRuntime(limit=2)
    resource.start()
    # Deliberately occupy the only test worker to exercise a not-yet-started future.
    resource._executor = ThreadPoolExecutor(max_workers=1)
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        return release.wait(10)
    first, second = resource.try_acquire(), resource.try_acquire()
    calls = []
    pending = None
    try:
        with first.activate():
            running = asyncio.create_task(run_analysis_sync(read))
        assert await asyncio.to_thread(entered.wait, 5)
        with second.activate():
            pending = asyncio.create_task(run_analysis_sync(lambda: calls.append('unexpected')))
        await asyncio.sleep(0)
        assert resource.try_acquire() is None
        assert len(second._pending) == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        second.close()
        assert resource.active_count == 1 and not calls
        fresh = resource.try_acquire()
        assert fresh is not None
        fresh.close()
    finally:
        release.set()
        await asyncio.gather(running, *([pending] if pending else []), return_exceptions=True)
        first.close()
        second.close()
        await resource.shutdown()
    assert not calls


@pytest.mark.asyncio
async def test_analysis_admission_submit_failure_and_stale_context_do_not_leak(runtime, monkeypatch):
    from unittest.mock import Mock
    from app.services.ai.analysis_runtime import run_analysis_sync
    executor = Mock()
    executor.submit.side_effect = RuntimeError('fixture submit failure')
    monkeypatch.setattr(runtime, '_executor', executor)
    lease = runtime.try_acquire()
    with lease.activate():
        with pytest.raises(RuntimeError, match='fixture submit'):
            await run_analysis_sync(str, 7)
        lease.close()
        with pytest.raises(asyncio.CancelledError):
            await run_analysis_sync(str, 8)
    assert runtime.active_count == 0
    executor.submit.assert_called_once()


@pytest.mark.asyncio
async def test_analysis_admission_shutdown_waits_for_actual_work_and_rejects_new_calls(runtime):
    from app.services.ai.analysis_runtime import run_analysis_sync
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert release.wait(10)
    async def run():
        lease = runtime.try_acquire()
        try:
            with lease.activate():
                await run_analysis_sync(read)
        finally:
            lease.close()
    owner = asyncio.create_task(run())
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(runtime.shutdown())
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert not closing.done() and runtime.try_acquire() is None
        assert runtime.active_count == 1
        with pytest.raises(RuntimeError):
            runtime.start()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
        if closing:
            await asyncio.wait_for(closing, 5)
    assert runtime.active_count == 0 and runtime.try_acquire() is None
    runtime.start()
    fresh = runtime.try_acquire()
    assert fresh is not None
    fresh.close()

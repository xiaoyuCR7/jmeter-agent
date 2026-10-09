"""ConcurrencyGuard — semaphore-based concurrent execution limiter.

Prevents resource exhaustion by limiting the number of simultaneous:
- JMeter test executions
- Agent runs
- LLM API calls

Usage:
    from app.middleware.concurrency_guard import concurrency_guard

    async with concurrency_guard.jmeter():
        await executor.execute(...)

    async with concurrency_guard.agent():
        await agent.run(...)

    async with concurrency_guard.llm():
        await adapter.chat(...)
"""

import asyncio
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GuardStats:
    active: int = 0
    waiting: int = 0
    total_completed: int = 0
    total_rejected: int = 0
    max_concurrent_reached: int = 0
    avg_wait_ms: float = 0.0
    _wait_times: list[float] = field(default_factory=list)

    def record_wait(self, wait_ms: float):
        self._wait_times.append(wait_ms)
        self.avg_wait_ms = sum(self._wait_times[-100:]) / min(len(self._wait_times), 100)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "waiting": self.waiting,
            "total_completed": self.total_completed,
            "total_rejected": self.total_rejected,
            "max_concurrent_reached": self.max_concurrent_reached,
            "avg_wait_ms": round(self.avg_wait_ms, 1),
        }


class Guard:
    """Semaphore guard for a single resource type."""

    def __init__(self, name: str, max_concurrent: int, timeout: float = 300.0):
        self.name = name
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._timeout = timeout
        self.stats = GuardStats()
        self._lock: Optional[asyncio.Lock] = None

    @property
    def available(self) -> bool:
        return not self._semaphore.locked()

    async def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns False if timed out."""
        async with await self._get_lock():
            self.stats.waiting += 1

        start = time.monotonic()
        try:
            acquired = False
            # Use a background task to wait for the semaphore with a timeout
            async with asyncio.timeout(self._timeout):
                await self._semaphore.acquire()
                acquired = True
        except asyncio.TimeoutError:
            async with await self._get_lock():
                self.stats.waiting -= 1
                self.stats.total_rejected += 1
            logger.warning(
                f"[{self.name}] Timed out waiting for slot "
                f"({self._timeout}s). {self.stats.active} active."
            )
            return False

        wait_ms = (time.monotonic() - start) * 1000
        async with await self._get_lock():
            self.stats.waiting -= 1
            self.stats.active += 1
            self.stats.max_concurrent_reached = max(
                self.stats.max_concurrent_reached, self.stats.active
            )
            self.stats.record_wait(wait_ms)

        if wait_ms > 1000:
            logger.info(f"[{self.name}] Slot acquired after {wait_ms:.0f}ms wait.")
        return True

    async def release(self):
        async with await self._get_lock():
            self.stats.active -= 1
            self.stats.total_completed += 1
        self._semaphore.release()

    @asynccontextmanager
    async def __call__(self):
        """Context manager: async with guard(): ..."""
        ok = await self.acquire()
        if not ok:
            raise ConcurrencyLimitError(
                f"[{self.name}] Concurrency limit ({self._max}) reached. "
                f"Try again later or increase the limit."
            )
        try:
            yield
        finally:
            await self.release()

    async def __aenter__(self):
        """Allow 'async with guard:' without calling guard()."""
        ok = await self.acquire()
        if not ok:
            raise ConcurrencyLimitError(
                f"[{self.name}] Concurrency limit ({self._max}) reached. "
                f"Try again later or increase the limit."
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
        return False


class ConcurrencyLimitError(Exception):
    """Raised when a concurrency guard denies access."""
    pass


class ConcurrencyGuard:
    """Manages multiple resource guards: jmeter, agent, llm."""

    def __init__(self):
        self._jmeter = Guard(
            "jmeter",
            max_concurrent=settings.RESOURCE_MAX_CONCURRENT_TESTS,
            timeout=settings.RESOURCE_MAX_WAIT_SECONDS,
        )
        self._agent = Guard(
            "agent",
            max_concurrent=settings.AGENT_MAX_CONCURRENT,
            timeout=120.0,
        )
        self._llm = Guard(
            "llm",
            max_concurrent=10,
            timeout=60.0,
        )

    def jmeter(self):
        return self._jmeter

    def agent(self):
        return self._agent

    def llm(self):
        return self._llm

    def get_all_stats(self) -> dict:
        return {
            "jmeter": self._jmeter.stats.to_dict(),
            "agent": self._agent.stats.to_dict(),
            "llm": self._llm.stats.to_dict(),
        }


# Module singleton
concurrency_guard = ConcurrencyGuard()


def create_concurrency_guard() -> ConcurrencyGuard:
    return concurrency_guard

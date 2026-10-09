"""ResourceMonitor — real-time CPU and memory usage monitoring.

Used by TestScheduler and agent tools to ensure performance tests
don't run when the host machine is already under high load, which
would skew test results.
"""

import time
import asyncio
import threading
import math
from dataclasses import dataclass, field
from typing import Optional, Callable

import psutil

from app.core.config import settings


@dataclass
class ResourceSnapshot:
    """A point-in-time resource usage snapshot."""

    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    disk_percent: float
    timestamp: float = field(default_factory=time.time)

    @property
    def is_healthy(self) -> bool:
        """Return True if all metrics are within safe thresholds."""
        return (
            self.cpu_percent <= settings.RESOURCE_CPU_THRESHOLD
            and self.memory_percent <= settings.RESOURCE_MEMORY_THRESHOLD
        )

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "memory_used_gb": round(self.memory_used_gb, 2),
            "memory_total_gb": round(self.memory_total_gb, 2),
            "disk_percent": round(self.disk_percent, 1),
            "timestamp": self.timestamp,
            "is_healthy": self.is_healthy,
        }


class ResourceMonitor:
    """Monitors CPU and memory usage of the host machine.

    Usage:
        monitor = ResourceMonitor()
        snapshot = monitor.snapshot()
        if snapshot.is_healthy:
            # safe to run test
        else:
            await monitor.wait_until_safe()

        # Or continuous monitoring in background:
        await monitor.start_monitoring(interval=5.0)
        # ... run tests ...
        await monitor.stop_monitoring()
    """

    def __init__(
        self,
        cpu_threshold: Optional[float] = None,
        memory_threshold: Optional[float] = None,
    ):
        self.cpu_threshold = cpu_threshold or settings.RESOURCE_CPU_THRESHOLD
        self.memory_threshold = memory_threshold or settings.RESOURCE_MEMORY_THRESHOLD

        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._latest_snapshot: Optional[ResourceSnapshot] = None
        self._history: list[ResourceSnapshot] = []
        self._max_history = 120  # keep 10 min at 5s intervals
        self._warn_callback: Optional[Callable[[ResourceSnapshot], None]] = None
        self._lock = threading.Lock()

    # ── snapshot ──────────────────────────────────────────────

    @staticmethod
    def snapshot() -> ResourceSnapshot:
        """Take a one-off resource usage snapshot."""
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(settings.BASE_DIR)
        return ResourceSnapshot(
            cpu_percent=cpu,
            memory_percent=mem.percent,
            memory_used_gb=mem.used / (1024**3),
            memory_total_gb=mem.total / (1024**3),
            disk_percent=disk.percent,
        )

    @property
    def latest(self) -> Optional[ResourceSnapshot]:
        return self._latest_snapshot

    def get_history(self, last_n: int = 20) -> list[ResourceSnapshot]:
        """Return recent snapshots for trend analysis."""
        return self._history[-last_n:] if self._history else []

    def get_average(self, last_n: int = 6) -> ResourceSnapshot:
        """Return an averaged snapshot over the last N readings.

        Uses a short window (default 6 = ~30s at 5s interval) to avoid
        averaging away recent spikes.
        """
        recent = self._history[-last_n:] if self._history else []
        if not recent:
            return self.snapshot()
        n = len(recent)
        return ResourceSnapshot(
            cpu_percent=sum(s.cpu_percent for s in recent) / n,
            memory_percent=sum(s.memory_percent for s in recent) / n,
            memory_used_gb=sum(s.memory_used_gb for s in recent) / n,
            memory_total_gb=recent[-1].memory_total_gb,
            disk_percent=sum(s.disk_percent for s in recent) / n,
        )

    # ── health checks ────────────────────────────────────────

    def is_safe(self, use_average: bool = True) -> bool:
        """Check if the system is healthy enough to start a test.

        Args:
            use_average: If True, use a rolling average to avoid reacting
                         to momentary spikes. If False, use latest snapshot.
        """
        if use_average and len(self._history) >= 3:
            snap = self.get_average(6)
        elif self._latest_snapshot:
            snap = self._latest_snapshot
        else:
            snap = self.snapshot()
        return snap.is_healthy

    async def wait_until_safe(
        self,
        timeout: float = 300.0,
        check_interval: float = 5.0,
    ) -> bool:
        """Block until resources are within safe thresholds.

        Returns True if safe, False if timed out.

        Args:
            timeout: Maximum seconds to wait.
            check_interval: Seconds between checks.
        """
        if not math.isfinite(timeout) or not math.isfinite(check_interval) or timeout <= 0 or check_interval <= 0:
            raise ValueError('Timeout and check interval must be finite and positive')
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            snap = await asyncio.to_thread(self.snapshot)
            self._latest_snapshot = snap
            if snap.is_healthy:
                return True
            await asyncio.sleep(check_interval)
        return False

    def diagnose(self, snapshot: Optional[ResourceSnapshot] = None) -> str:
        """Return a human-readable diagnosis of current resource state."""
        snap = snapshot or self._latest_snapshot or self.snapshot()
        issues = []
        if snap.cpu_percent > self.cpu_threshold:
            issues.append(
                f"CPU {snap.cpu_percent:.1f}% > threshold {self.cpu_threshold}%"
            )
        if snap.memory_percent > self.memory_threshold:
            issues.append(
                f"Memory {snap.memory_percent:.1f}% > threshold {self.memory_threshold}%"
            )
        if not issues:
            return f"System healthy: CPU {snap.cpu_percent:.1f}%, Memory {snap.memory_percent:.1f}%"
        return "Resource contention: " + "; ".join(issues)

    # ── background monitoring ─────────────────────────────────

    async def start_monitoring(
        self,
        interval: float = 5.0,
        warn_callback: Optional[Callable[[ResourceSnapshot], None]] = None,
    ):
        """Start background resource monitoring.

        Args:
            interval: Seconds between samples.
            warn_callback: Called when a snapshot exceeds thresholds.
        """
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Monitoring interval must be finite and positive')
        if self._monitoring:
            return
        self._monitoring = True
        self._warn_callback = warn_callback
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval))

    async def stop_monitoring(self):
        """Stop background monitoring."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def _monitor_loop(self, interval: float):
        """Background loop that samples resources."""
        while self._monitoring:
            try:
                snap = await asyncio.to_thread(self.snapshot)
                self._latest_snapshot = snap
                self._history.append(snap)
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

                if not snap.is_healthy and self._warn_callback:
                    self._warn_callback(snap)
            except Exception:
                pass  # never let monitoring crash the app
            await asyncio.sleep(interval)


# ── module-level singleton ────────────────────────────────────

_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Return the global ResourceMonitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = ResourceMonitor()
    return _monitor

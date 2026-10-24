"""WebSocket manager — pub/sub for real-time test progress events.

Includes automatic heartbeat/ping-pong to keep connections alive through proxies.
Also includes background cleanup task to recycle dead/long-idle connections.
"""

import asyncio
import json
import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 60  # seconds
CLEANUP_INTERVAL = 120  # seconds - cleanup idle channels


class WSManager:
    """Manages WebSocket connections grouped by execution_id channel.

    Usage:
        manager = WSManager()

        # In WebSocket endpoint:
        await manager.connect(execution_id, websocket)

        # During test execution:
        await manager.broadcast(execution_id, {"event": "progress", "pct": 50})
        await manager.broadcast(execution_id, {"event": "log", "line": "..."})
        await manager.broadcast(execution_id, {"event": "done", "status": "completed"})
    """

    def __init__(self):
        self._channels: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_tasks: Dict[int, asyncio.Task] = {}
        self._last_pong: Dict[int, float] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def _heartbeat(self, ws: WebSocket, execution_id: str):
        """Send periodic ping frames to keep connection alive."""
        ws_id = id(ws)
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                # Check if connection is still valid
                async with self._lock:
                    if execution_id not in self._channels or ws not in self._channels[execution_id]:
                        break
                    # Check if we received pong in time
                    last_pong = self._last_pong.get(ws_id, 0)
                    timed_out = (asyncio.get_running_loop().time() - last_pong) > HEARTBEAT_TIMEOUT
                if timed_out:
                    logger.warning(f"WS heartbeat timeout for execution={execution_id}")
                    await self.disconnect(execution_id, ws)
                    return
                # Send ping
                await ws.send_text(json.dumps({"type": "ping", "ts": asyncio.get_event_loop().time()}))
            except Exception:
                await self.disconnect(execution_id, ws)
                return

    def _register_pong(self, ws: WebSocket):
        """Record pong response from client."""
        self._last_pong[id(ws)] = asyncio.get_event_loop().time()

    async def connect(self, execution_id: str, ws: WebSocket):
        """Accept a WebSocket connection and register to a channel."""
        await ws.accept()
        ws_id = id(ws)
        async with self._lock:
            if execution_id not in self._channels:
                self._channels[execution_id] = set()
            self._channels[execution_id].add(ws)
            self._last_pong[ws_id] = asyncio.get_event_loop().time()
        # Start heartbeat task
        self._heartbeat_tasks[ws_id] = asyncio.create_task(self._heartbeat(ws, execution_id))
        logger.debug(f"WS connected: execution={execution_id}, clients={len(self._channels.get(execution_id, set()))}")

    async def disconnect(self, execution_id: str, ws: WebSocket):
        """Remove a disconnected client."""
        ws_id = id(ws)
        async with self._lock:
            if execution_id in self._channels:
                self._channels[execution_id].discard(ws)
                if not self._channels[execution_id]:
                    del self._channels[execution_id]
            # Cleanup heartbeat task
            task = self._heartbeat_tasks.pop(ws_id, None)
            if task and task is not asyncio.current_task():
                task.cancel()
            self._last_pong.pop(ws_id, None)
    async def broadcast(self, execution_id: str, data: dict):
        """Send a JSON message to all clients watching an execution."""
        async with self._lock:
            channel = list(self._channels.get(execution_id, set()))
        message = json.dumps(data, ensure_ascii=False, default=str)
        for ws in channel:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(execution_id, ws)

    async def broadcast_log(self, execution_id: str, line: str):
        """Send a log line to subscribers."""
        await self.broadcast(execution_id, {"type": "log", "line": line, "ts": None})

    async def broadcast_progress(self, execution_id: str, pct: int, message: str = ""):
        """Send a progress update."""
        await self.broadcast(execution_id, {"type": "progress", "pct": pct, "message": message})

    async def broadcast_status(self, execution_id: str, status: str, error: str = None):
        """Send a status change event."""
        await self.broadcast(execution_id, {
            "type": "status",
            "status": status,
            "error": error,
        })

    def get_channel_count(self) -> dict:
        """Return active channel counts (for debugging)."""
        return {k: len(v) for k, v in self._channels.items()}

    async def _cleanup_idle(self):
        """Background task to clean up idle channels and stale connections."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                async with self._lock:
                    for execution_id in list(self._channels.keys()):
                        channel = self._channels[execution_id]
                        if not channel:
                            del self._channels[execution_id]
                            logger.debug(f"Cleaned up empty channel: {execution_id}")
                        else:
                            stale = []
                            for ws in channel:
                                ws_id = id(ws)
                                last_pong = self._last_pong.get(ws_id, 0)
                                if (asyncio.get_event_loop().time() - last_pong) > HEARTBEAT_TIMEOUT * 2:
                                    stale.append(ws)
                            for ws in stale:
                                channel.discard(ws)
                                task = self._heartbeat_tasks.pop(id(ws), None)
                                if task:
                                    task.cancel()
                                self._last_pong.pop(id(ws), None)
                            if not channel:
                                del self._channels[execution_id]
                                logger.debug(f"Cleaned up stale channel: {execution_id}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")

    def start(self):
        """Start background cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._shutdown_event.clear()
            self._cleanup_task = asyncio.create_task(self._cleanup_idle())
            logger.debug("WS manager cleanup task started")

    async def stop(self):
        """Stop background cleanup task and clean up all resources."""
        self._shutdown_event.set()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        async with self._lock:
            heartbeat_tasks = list(self._heartbeat_tasks.values())
            for task in heartbeat_tasks:
                task.cancel()
            self._heartbeat_tasks.clear()
            self._channels.clear()
            self._last_pong.clear()
        if heartbeat_tasks:
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
        logger.debug("WS manager stopped and cleaned up")


# Module-level singleton
ws_manager = WSManager()

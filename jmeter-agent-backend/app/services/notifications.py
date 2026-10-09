"""Notifications — webhook dispatch + MCP SSE event push.

Centralized notification hub for test lifecycle events.
Called from REST API runner and scheduled runner so both paths
trigger webhooks and real-time SSE notifications consistently.

Webhook payload format (POST JSON):
{
  "event": "test.completed" | "test.started" | "test.failed",
  "execution_id": "...",
  "status": "completed" | "failed" | "cancelled",
  "timestamp": "2024-...",
  "summary": { ... optional metrics ... }
}
"""

import json
import asyncio
import logging
from typing import Optional

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.time import utc_now_naive

logger = logging.getLogger(__name__)

# ── In-memory SSE subscriber registry ─────────────────────────
# Each entry captures the subscriber's token and stable identity, never a producer scope.
_sse_subscribers: list[dict] = []


def register_sse_subscriber() -> "asyncio.Queue":
    """Register a new SSE subscriber. Returns an async queue for events."""
    from app.services.auth import UserInfo
    from app.services.security_context import current_principal, current_access_token
    principal, token = current_principal.get(), current_access_token.get()
    if not token or not isinstance(principal, UserInfo) or principal.user_id is None:
        raise HTTPException(401, 'Subscriber identity required')
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _sse_subscribers.append({"queue": q, "created_at": utc_now_naive().timestamp(),
                             "principal": principal.model_copy(deep=True), "token": token})
    # Purge stale subscribers (>1 hour old)
    _purge_stale()
    return q


def unregister_sse_subscriber(q: "asyncio.Queue"):
    """Remove an SSE subscriber."""
    global _sse_subscribers
    _sse_subscribers = [s for s in _sse_subscribers if s["queue"] is not q]


def _purge_stale():
    """Remove subscribers older than 1 hour."""
    global _sse_subscribers
    cutoff = utc_now_naive().timestamp() - 3600
    _sse_subscribers = [
        s for s in _sse_subscribers
        if s.get("created_at", 0) > cutoff
    ]


async def _push_sse_event(event_type: str, data: dict):
    """Push an event to all connected SSE subscribers."""
    execution_id = data.get('execution_id')
    if not isinstance(execution_id, str) or not execution_id:
        return
    from app.services.ws_manager import authorize_subscriber
    payload = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": utc_now_naive().isoformat(),
    }, ensure_ascii=False, default=str)
    dead = []
    for sub in list(_sse_subscribers):
        try:
            await authorize_subscriber(sub.get('principal'), sub.get('token'), execution_id)
            sub["queue"].put_nowait(payload)
        except HTTPException as error:
            if error.status_code == 401:
                dead.append(sub['queue'])
            # A project denial filters this event; other projects may be readable.
        except Exception:
            dead.append(sub["queue"])
    for q in dead:
        unregister_sse_subscriber(q)


# ── Webhook dispatch ──────────────────────────────────────────

async def _fire_webhook(event: str, execution_id: str, status: str,
                        summary: Optional[dict] = None, error: Optional[str] = None):
    """POST webhook notification if WEBHOOK_URL is configured."""
    if not settings.WEBHOOK_URL:
        return

    should_fire = (
        (event == "test.completed" and settings.WEBHOOK_ON_COMPLETE)
        or (event == "test.failed" and settings.WEBHOOK_ON_FAILURE)
        or (event == "test.started")
    )
    if not should_fire:
        return

    # Global webhook configuration is not an authorization grant for private
    # projects. Resolve persisted ownership even for trusted background callers.
    try:
        from sqlalchemy import select
        from app.models.db import ExecutionModel, ProjectModel, ProjectAccessPolicyModel
        from app.services.db_store import db_store
        async with await db_store._session() as session:
            row = (await session.execute(select(ProjectAccessPolicyModel.mode).select_from(ExecutionModel)
                .join(ProjectModel, ProjectModel.id == ExecutionModel.project_id)
                .outerjoin(ProjectAccessPolicyModel, ProjectAccessPolicyModel.project_id == ProjectModel.id)
                .where(ExecutionModel.id == execution_id))).one_or_none()
        if row is None or row[0] not in (None, 'legacy_shared'):
            return
    except Exception:
        logger.warning('Webhook authorization unavailable; event not sent')
        return

    payload = {
        "event": event,
        "execution_id": execution_id,
        "status": status,
        "timestamp": utc_now_naive().isoformat(),
    }
    if summary:
        payload["summary"] = summary
    if error:
        payload["error"] = error

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                settings.WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json",
                         "X-JMeter-Agent-Event": event},
            )
            if resp.status_code >= 400:
                logger.warning(
                    f"Webhook POST returned {resp.status_code}: {resp.text[:200]}"
                )
            else:
                logger.info(f"Webhook fired: {event} for {execution_id}")
    except Exception as e:
        logger.error(f"Webhook dispatch failed for {execution_id}: {e}")


# ── Public API ────────────────────────────────────────────────

async def notify_test_started(execution_id: str, jmx_path: str = "",
                              test_plan_name: str = ""):
    """Notify that a test has started."""
    data = {
        "execution_id": execution_id,
        "jmx_path": jmx_path,
        "test_plan_name": test_plan_name,
        "status": "running",
    }
    await _push_sse_event("test.started", data)
    await _fire_webhook("test.started", execution_id, "running")


async def notify_test_completed(execution_id: str, status: str,
                                summary: Optional[dict] = None,
                                error: Optional[str] = None,
                                report_dir: str = "",
                                log_path: str = ""):
    """Notify that a test has completed (success or failure)."""
    event = "test.completed" if status == "completed" else "test.failed"
    data = {
        "execution_id": execution_id,
        "status": status,
        "report_dir": report_dir,
        "log_path": log_path,
    }
    if summary:
        data["summary"] = {
            "total_requests": summary.get("total_requests"),
            "avg_response_time": summary.get("avg_response_time"),
            "p99_response_time": summary.get("p99_response_time") or (
                summary.get("percentiles", {}).get("p99") if isinstance(summary.get("percentiles"), dict) else None
            ),
            "error_rate": summary.get("error_rate"),
            "throughput": summary.get("throughput"),
        }
    if error:
        data["error"] = error

    await _push_sse_event(event, data)
    await _fire_webhook(event, execution_id, status, summary, error)


async def notify_test_cancelled(execution_id: str):
    """Notify that a test was cancelled."""
    data = {"execution_id": execution_id, "status": "cancelled"}
    await _push_sse_event("test.cancelled", data)
    await _fire_webhook("test.failed", execution_id, "cancelled",
                        error="Test cancelled by user")

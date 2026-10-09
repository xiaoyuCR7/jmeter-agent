"""MCP 2024-11-05 HTTP+SSE transport, not Streamable HTTP.

GET /mcp/sse advertises an owner-bound POST URI. POST acknowledges with 202;
JSON-RPC responses are delivered exclusively as that connection's SSE messages.
"""

import json
import asyncio
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
import anyio
from app.services.authenticated_stream import AuthenticatedStreamingResponse as StreamingResponse

from app.services.mcp_server import mcp_server, MCPTransportError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])


class _MCPStreamingResponse(StreamingResponse):
    """Own the session even if ASGI send fails while the generator is suspended."""
    def __init__(self, content, server, session, **kwargs):
        self.server = server
        self.session = session
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                # The shared response closes its iterator. This additionally
                # owns sessions when headers fail before iteration ever starts.
                await self.server.close_session(self.session)


async def init_mcp_server():
    """Register all tools, resources, and prompts — called from main.py lifespan."""
    mcp_server.register_all_agent_tools()
    mcp_server.register_all_resources()
    mcp_server.register_all_prompts()
    logger.info(f"MCP Server initialized: {len(mcp_server._tools)} tools, "
                f"{len(mcp_server._resources)} resources, "
                f"{len(mcp_server._prompts)} prompts")


@router.get("/mcp/status")
async def mcp_status():
    """Get MCP server status — tools, resources, prompts count."""
    return {
        "server_name": mcp_server.name,
        "server_version": mcp_server.version,
        "protocol_version": "2024-11-05",
        "transport": "sse",
        "tools_count": len(mcp_server._tools),
        "resources_count": len(mcp_server._resources),
        "prompts_count": len(mcp_server._prompts),
        "tools": [
            {"name": t.name, "description": t.description[:100]}
            for t in mcp_server._tools.values()
        ],
        "resources": [
            {"uri": r.uri, "name": r.name, "description": r.description}
            for r in mcp_server._resources.values()
        ],
        "prompts": [
            {"name": p.name, "description": p.description}
            for p in mcp_server._prompts.values()
        ],
        "endpoints": {
            "messages": "/api/v1/mcp/messages",
            "sse": "/api/v1/mcp/sse",
            "status": "/api/v1/mcp/status",
        },
    }


@router.post("/mcp/messages")
async def mcp_messages(request: Request):
    """Accept one session message. Missing sessions are not a custom HTTP transport."""
    principal = _principal(request)
    owner = principal.username
    session_id = request.query_params.get("session_id")
    if not session_id:
        raise HTTPException(400, "Connect to /api/v1/mcp/sse first")
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413, "MCP message exceeds 1 MiB")
        try:
            session = mcp_server.get_session(session_id, owner)
        except MCPTransportError as local_error:
            from app.services.mcp_mailbox import mcp_mailbox
            if local_error.status_code != 404 or not mcp_mailbox.enabled:
                raise
            await mcp_mailbox.submit(session_id, principal, bytes(body))
        else:
            mcp_server.submit(session, bytes(body))
    except MCPTransportError as error:
        raise HTTPException(error.status_code, error.detail) from error
    return Response(status_code=202)


def _principal(request: Request):
    user = getattr(request.state, "auth_user", None)
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


def _owner(request: Request) -> str:
    return _principal(request).username


@router.get("/mcp/sse")
async def mcp_sse(request: Request):
    """Each stream owns its queue and pending tasks; disconnect revokes its POST URI."""
    principal = _principal(request)
    owner = principal.username
    server = mcp_server
    try:
        # Admission must happen before response headers, so limits return HTTP 429.
        session = server.create_session(owner)
        from app.services.mcp_mailbox import mcp_mailbox
        if mcp_mailbox.enabled:
            try:
                await mcp_mailbox.register(session, principal)
            except MCPTransportError as error:
                await server.close_session(session)
                raise HTTPException(error.status_code, error.detail) from error
    except MCPTransportError as error:
        raise HTTPException(error.status_code, error.detail) from error

    async def event_stream():
        try:
            root = request.scope.get("root_path", "").rstrip("/")
            endpoint = f"{root}/api/v1/mcp/messages?session_id={session.id}"
            yield f"event: endpoint\ndata: {endpoint}\n\n"
            last_heartbeat = asyncio.get_running_loop().time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    if session.mailbox is not None:
                        await session.mailbox.drain(session)
                    timeout = .25 if session.mailbox is not None else 15.0
                    response = await asyncio.wait_for(session.queue.get(), timeout=timeout)
                    yield f"event: message\ndata: {json.dumps(response, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if asyncio.get_running_loop().time() - last_heartbeat >= 15:
                        last_heartbeat = asyncio.get_running_loop().time()
                        yield ": heartbeat\n\n"
        finally:
            # Starlette cancels streams on disconnect. Shield task cleanup from
            # its AnyIO cancellation scope, then wait for tool finally blocks.
            with anyio.CancelScope(shield=True):
                await server.close_session(session)

    return _MCPStreamingResponse(event_stream(), server, session, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

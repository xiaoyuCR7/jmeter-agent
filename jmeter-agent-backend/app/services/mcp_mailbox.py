"""Encrypted, short-lived routing for legacy MCP POST-to-SSE delivery."""
import base64
import hashlib
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select, text, update

from app.models import db


class MCPMailbox:
    # The SSE owner renews this. A failed close therefore cannot leave a live
    # looking route for an hour after its local session disappeared.
    SESSION_TTL = 45.0
    MAX_MESSAGES = 64

    def __init__(self, coordinator=None):
        self._coordinator_override = coordinator

    @property
    def coordinator(self):
        if self._coordinator_override is not None:
            return self._coordinator_override
        from app.services.ai.operation_runtime import agent_operations
        return agent_operations.coordinator

    @property
    def enabled(self):
        return self.coordinator is not None

    @staticmethod
    def _fernet():
        from app.core.config import settings
        key = hashlib.sha256(b'jmeter-agent/mcp-mailbox/v1\0' + settings.JWT_SECRET.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    async def _identity(self, owner):
        if owner is None or owner.user_id is None:
            return None
        from app.services.project_access import access
        async with db.get_session_factory()() as session:
            user = await session.get(db.UserModel, owner.user_id)
            if user is None or not user.is_active or user.username != owner.username or user.role != owner.role:
                return None
            identity = await access._identity(session, user.id)
            await session.commit()
            return user.id, identity.incarnation

    async def register(self, mcp_session, owner):
        from app.services.mcp_server import MCPTransportError
        coordinator = self.coordinator
        identity = await self._identity(owner)
        if coordinator is None or identity is None:
            raise MCPTransportError(503, 'MCP routing authority is unavailable')
        coordinator.heartbeat()
        now = time.time()
        async with db.get_session_factory()() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            await session.execute(delete(db.MCPMessageModel).where(
                db.MCPMessageModel.session_id.in_(select(db.MCPSessionRouteModel.id).where(
                    db.MCPSessionRouteModel.expires_at <= now))))
            await session.execute(delete(db.MCPSessionRouteModel).where(
                db.MCPSessionRouteModel.expires_at <= now))
            global_count = (await session.execute(select(func.count()).select_from(
                db.MCPSessionRouteModel).where(db.MCPSessionRouteModel.state == 'active'))).scalar_one()
            owner_count = (await session.execute(select(func.count()).select_from(
                db.MCPSessionRouteModel).where(db.MCPSessionRouteModel.state == 'active',
                    db.MCPSessionRouteModel.owner_id == identity[0],
                    db.MCPSessionRouteModel.owner_incarnation == identity[1]))).scalar_one()
            if global_count >= mcp_session.server.MAX_SESSIONS or owner_count >= mcp_session.server.MAX_OWNER_SESSIONS:
                raise MCPTransportError(429, 'Too many MCP sessions')
            session.add(db.MCPSessionRouteModel(
                id=mcp_session.id, owner_id=identity[0], owner_incarnation=identity[1],
                process_generation=coordinator.owner.generation, state='active',
                expires_at=now + self.SESSION_TTL, created_at=now))
            await session.commit()
        mcp_session.mailbox = self
        mcp_session.route_identity = identity
        mcp_session.route_generation = coordinator.owner.generation

    async def submit(self, session_id, owner, raw_body):
        from app.services.mcp_server import MCPTransportError
        from app.services.process_identity import ProcessIdentity, Liveness, probe
        if not isinstance(raw_body, bytes) or len(raw_body) > 1024 * 1024:
            raise MCPTransportError(413, 'MCP message exceeds 1 MiB')
        identity = await self._identity(owner)
        if identity is None or self.coordinator is None:
            raise MCPTransportError(404, 'MCP session not found')
        now = time.time()
        async with db.get_session_factory()() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            route = await session.get(db.MCPSessionRouteModel, session_id)
            if route is None or (route.owner_id, route.owner_incarnation) != identity:
                raise MCPTransportError(404, 'MCP session not found')
            if route.state != 'active' or route.expires_at <= now:
                raise MCPTransportError(409, 'MCP stream owner was lost; reconnect')
            process = await session.get(db.ProcessOwnerModel, route.process_generation)
            if process is None or process.state not in ('active', 'draining'):
                route.state = 'closed'; await session.commit()
                raise MCPTransportError(409, 'MCP stream owner was lost; reconnect')
            liveness = probe(ProcessIdentity(process.pid, process.creation_time))
            if liveness is Liveness.DEAD:
                route.state = 'closed'; await session.execute(delete(db.MCPMessageModel).where(
                    db.MCPMessageModel.session_id == route.id)); await session.commit()
                raise MCPTransportError(409, 'MCP stream owner was lost; reconnect')
            if liveness is not Liveness.ALIVE:
                raise MCPTransportError(503, 'MCP stream owner cannot be verified')
            pending = (await session.execute(select(func.count()).select_from(db.MCPMessageModel).where(
                db.MCPMessageModel.session_id == route.id))).scalar_one()
            if pending >= self.MAX_MESSAGES:
                raise MCPTransportError(429, 'MCP session mailbox is full')
            encrypted = self._fernet().encrypt(raw_body).decode('ascii')
            session.add(db.MCPMessageModel(session_id=route.id, encrypted_payload=encrypted,
                                           fingerprint=hashlib.sha256(raw_body).hexdigest(), created_at=now))
            await session.commit()
        return True

    async def drain(self, mcp_session, *, limit=16):
        from app.services.mcp_server import MCPTransportError
        coordinator = self.coordinator
        if coordinator is None or mcp_session.closed or mcp_session.route_generation != coordinator.owner.generation:
            return 0
        coordinator.heartbeat()
        delivered = 0
        now = time.time()
        async with db.get_session_factory()() as session:
            route = await session.get(db.MCPSessionRouteModel, mcp_session.id)
            if route is None or route.state != 'active' or route.process_generation != coordinator.owner.generation:
                raise MCPTransportError(409, 'MCP stream owner was lost; reconnect')
            if route.expires_at <= now + self.SESSION_TTL / 2:
                route.expires_at = now + self.SESSION_TTL
                await session.commit()
            rows = (await session.execute(select(db.MCPMessageModel).where(
                db.MCPMessageModel.session_id == mcp_session.id).order_by(
                    db.MCPMessageModel.id).limit(limit))).scalars().all()
            for row in rows:
                try:
                    payload = self._fernet().decrypt(row.encrypted_payload.encode('ascii'))
                except (InvalidToken, ValueError):
                    route.state = 'closed'
                    await session.execute(delete(db.MCPMessageModel).where(
                        db.MCPMessageModel.session_id == mcp_session.id))
                    await session.commit()
                    raise MCPTransportError(409, 'MCP mailbox integrity check failed')
                if hashlib.sha256(payload).hexdigest() != row.fingerprint:
                    route.state = 'closed'; await session.commit()
                    raise MCPTransportError(409, 'MCP mailbox integrity check failed')
                try:
                    mcp_session.server.submit(mcp_session, payload)
                except MCPTransportError as error:
                    if error.status_code == 429:
                        break
                    await session.delete(row); await session.commit()
                    raise
                await session.delete(row)
                await session.commit()
                delivered += 1
        return delivered

    async def close(self, mcp_session):
        async with db.get_session_factory()() as session:
            await session.execute(delete(db.MCPMessageModel).where(
                db.MCPMessageModel.session_id == mcp_session.id))
            await session.execute(delete(db.MCPSessionRouteModel).where(
                db.MCPSessionRouteModel.id == mcp_session.id,
                db.MCPSessionRouteModel.process_generation == mcp_session.route_generation))
            await session.commit()


mcp_mailbox = MCPMailbox()

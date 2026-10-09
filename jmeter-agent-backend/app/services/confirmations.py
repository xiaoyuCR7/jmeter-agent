"""Short-lived, one-shot decisions for exact tool invocations.

The default path remains in-process. An explicitly configured Coordinator uses
the authoritative application database so another worker can display/decide a
request, while only the original exact claim may consume the decision.
"""
import asyncio
import base64
import copy
import hashlib
import json
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, text, update

from app.models import db
from app.services.auth import Role


class ConfirmationManager:
    def __init__(self, timeout_seconds=120.0, max_pending=128, *, coordinator=None, poll_seconds=.1):
        self.timeout_seconds = timeout_seconds
        self.max_pending = max_pending
        self.poll_seconds = max(.01, min(float(poll_seconds), 1.0))
        self._coordinator_override = coordinator
        self._pending = {}

    @property
    def coordinator(self):
        if self._coordinator_override is not None:
            return self._coordinator_override
        from app.services.ai.operation_runtime import agent_operations
        return agent_operations.coordinator

    @staticmethod
    def _fernet():
        from app.core.config import settings
        key = hashlib.sha256(b'jmeter-agent/tool-confirmation/v1\0' + settings.JWT_SECRET.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    @staticmethod
    def _canonical(tool_name, arguments):
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 100:
            raise ValueError('Invalid confirmation tool name')
        encoded = json.dumps({'tool': tool_name, 'arguments': arguments}, sort_keys=True,
                             ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 64 * 1024:
            raise ValueError('Confirmation parameters exceed the persistence limit')
        return encoded

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

    async def request(self, owner, tool_name: str, arguments: dict) -> bool:
        if not owner or not Role.can(owner.role, Role.OPERATOR):
            return False
        if self.coordinator is None:
            return await self._request_local(owner, tool_name, arguments)
        try:
            encoded = self._canonical(tool_name, copy.deepcopy(arguments))
        except (TypeError, ValueError):
            return False
        identity = await self._identity(owner)
        if identity is None:
            return False
        from app.services.ai.operation_runtime import current_operation
        operation = current_operation()
        if operation is None or operation.runtime.coordinator is not self.coordinator:
            return False
        try:
            operation.assert_active()
        except Exception:
            return False
        handle = operation.handle
        now = time.time()
        request_id = uuid.uuid4().hex
        fingerprint = hashlib.sha256(encoded).hexdigest()
        encrypted = self._fernet().encrypt(encoded).decode('ascii')
        async with db.get_session_factory()() as session:
            # Serialize the global capacity check with the insert. No network,
            # user wait, or tool work occurs while this write lock is held.
            await session.execute(text('BEGIN IMMEDIATE'))
            await session.execute(update(db.ToolConfirmationModel).where(
                db.ToolConfirmationModel.state == 'pending',
                db.ToolConfirmationModel.expires_at <= now).values(
                    state='abandoned', approved=False, encrypted_arguments='', updated_at=now))
            pending = (await session.execute(select(func.count()).select_from(db.ToolConfirmationModel).where(
                db.ToolConfirmationModel.state == 'pending', db.ToolConfirmationModel.expires_at > now))).scalar_one()
            if pending >= self.max_pending:
                return False
            session.add(db.ToolConfirmationModel(
                id=request_id, owner_id=identity[0], owner_incarnation=identity[1], tool_name=tool_name,
                fingerprint=fingerprint, encrypted_arguments=encrypted,
                process_generation=handle.owner_generation, operation_id=handle.operation_id,
                claim_id=handle.claim_id, state='pending', approved=None,
                expires_at=now + self.timeout_seconds, created_at=now, updated_at=now))
            await session.commit()
        consumed = False
        try:
            deadline = now + self.timeout_seconds
            while True:
                current = time.time()
                if current >= deadline:
                    return False
                try:
                    operation.assert_active()
                except Exception:
                    return False
                async with db.get_session_factory()() as session:
                    row = await session.get(db.ToolConfirmationModel, request_id)
                    if row is None or (row.owner_id, row.owner_incarnation, row.process_generation,
                                       row.operation_id, row.claim_id, row.fingerprint) != (
                            identity[0], identity[1], handle.owner_generation, handle.operation_id,
                            handle.claim_id, fingerprint):
                        return False
                    if row.state in ('approved', 'denied'):
                        changed = await session.execute(update(db.ToolConfirmationModel).where(
                            db.ToolConfirmationModel.id == request_id,
                            db.ToolConfirmationModel.state == row.state).values(
                                state='consumed', encrypted_arguments='', updated_at=current))
                        if changed.rowcount == 1:
                            await session.commit()
                            consumed = True
                            return row.approved is True
                    elif row.state != 'pending':
                        return False
                await asyncio.sleep(min(self.poll_seconds, max(0, deadline - time.time())))
        finally:
            if not consumed:
                cleanup = asyncio.create_task(self._abandon(request_id))
                cancelled = None
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError as error:
                        cancelled = error
                if cancelled is not None:
                    if not cleanup.cancelled():
                        cleanup.exception()
                    raise cancelled
                cleanup.result()

    @staticmethod
    async def _abandon(request_id):
        async with db.get_session_factory()() as session:
            await session.execute(update(db.ToolConfirmationModel).where(
                db.ToolConfirmationModel.id == request_id,
                db.ToolConfirmationModel.state.in_(('pending', 'approved', 'denied'))).values(
                    state='abandoned', approved=False, encrypted_arguments='', updated_at=time.time()))
            await session.commit()

    async def _request_local(self, owner, tool_name, arguments):
        if len(self._pending) >= self.max_pending:
            return False
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = {
            'id': request_id, 'owner': owner.username, 'tool': tool_name,
            'arguments': copy.deepcopy(arguments), 'expires_at': time.time() + self.timeout_seconds,
            'future': future,
        }
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)

    async def list_pending(self, owner) -> list[dict]:
        if self.coordinator is None:
            username = owner if isinstance(owner, str) else owner.username
            return [copy.deepcopy({k: v for k, v in item.items() if k != 'future'})
                    for item in self._pending.values()
                    if item['owner'] == username and not item['future'].done()
                    and item['expires_at'] > time.time()]
        identity = await self._identity(owner)
        if identity is None:
            return []
        now = time.time()
        async with db.get_session_factory()() as session:
            rows = (await session.execute(select(db.ToolConfirmationModel).where(
                db.ToolConfirmationModel.owner_id == identity[0],
                db.ToolConfirmationModel.owner_incarnation == identity[1],
                db.ToolConfirmationModel.state == 'pending',
                db.ToolConfirmationModel.expires_at > now).order_by(
                    db.ToolConfirmationModel.created_at))).scalars().all()
        result = []
        for row in rows:
            try:
                payload = json.loads(self._fernet().decrypt(row.encrypted_arguments.encode('ascii')).decode())
                encoded = self._canonical(payload['tool'], payload['arguments'])
                if payload['tool'] != row.tool_name or hashlib.sha256(encoded).hexdigest() != row.fingerprint:
                    continue
            except (InvalidToken, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            result.append({'id': row.id, 'owner': owner.username, 'tool': row.tool_name,
                           'arguments': payload['arguments'], 'expires_at': row.expires_at})
        return result

    async def decide(self, request_id: str, owner, approved: bool) -> None:
        if self.coordinator is None:
            username = owner if isinstance(owner, str) else owner.username
            item = self._pending.get(request_id)
            if item is None or item['future'].done() or item['expires_at'] <= time.time():
                raise KeyError('Confirmation expired or already consumed')
            if item['owner'] != username:
                raise PermissionError('Confirmation belongs to another user')
            item['future'].set_result(approved)
            return
        identity = await self._identity(owner)
        now = time.time()
        async with db.get_session_factory()() as session:
            row = await session.get(db.ToolConfirmationModel, request_id)
            if row is None:
                raise KeyError('Confirmation expired or already consumed')
            if identity is None or (row.owner_id, row.owner_incarnation) != identity:
                raise PermissionError('Confirmation belongs to another user incarnation')
            if row.state != 'pending' or row.expires_at <= now:
                if row.state == 'pending':
                    row.state, row.approved, row.encrypted_arguments, row.updated_at = 'abandoned', False, '', now
                    await session.commit()
                raise KeyError('Confirmation expired or already consumed')
            changed = await session.execute(update(db.ToolConfirmationModel).where(
                db.ToolConfirmationModel.id == request_id,
                db.ToolConfirmationModel.state == 'pending').values(
                    state='approved' if approved else 'denied', approved=bool(approved),
                    encrypted_arguments='', updated_at=now))
            if changed.rowcount != 1:
                raise KeyError('Confirmation expired or already consumed')
            await session.commit()


confirmations = ConfirmationManager()

"""Short-lived encrypted fan-out between local WebSocket worker processes."""
import base64
import hashlib
import json
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, text, update

from app.models import db


class WSEventBus:
    SUBSCRIPTION_TTL = 45.0
    EVENT_TTL = 60.0
    MAX_EVENT_BYTES = 16 * 1024
    MAX_PENDING_PER_WORKER = 256

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
        key = hashlib.sha256(b'jmeter-agent/ws-event/v1\0' + settings.JWT_SECRET.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    @classmethod
    def _payload(cls, data):
        if not isinstance(data, dict):
            raise ValueError('WebSocket event must be an object')
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False, default=str).encode()
        if len(encoded) > cls.MAX_EVENT_BYTES:
            raise ValueError('WebSocket event exceeds the cross-worker limit')
        return encoded

    async def subscribe(self, execution_id):
        coordinator = self.coordinator
        if coordinator is None:
            return
        coordinator.heartbeat()
        now = time.time()
        generation = coordinator.owner.generation
        async with db.get_session_factory()() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            row = await session.get(db.WSWorkerSubscriptionModel, (execution_id, generation))
            if row is None:
                session.add(db.WSWorkerSubscriptionModel(
                    execution_id=execution_id, process_generation=generation,
                    subscriber_count=1, expires_at=now + self.SUBSCRIPTION_TTL))
            else:
                row.subscriber_count += 1
                row.expires_at = now + self.SUBSCRIPTION_TTL
            await session.commit()

    async def unsubscribe(self, execution_id):
        coordinator = self.coordinator
        if coordinator is None:
            return
        generation = coordinator.owner.generation
        async with db.get_session_factory()() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            row = await session.get(db.WSWorkerSubscriptionModel, (execution_id, generation))
            if row is not None:
                if row.subscriber_count <= 1:
                    await session.delete(row)
                else:
                    row.subscriber_count -= 1
                    row.expires_at = time.time() + self.SUBSCRIPTION_TTL
            await session.commit()

    async def touch(self, execution_ids=None):
        coordinator = self.coordinator
        if coordinator is None:
            return
        coordinator.heartbeat()
        expires_at = time.time() + self.SUBSCRIPTION_TTL
        async with db.get_session_factory()() as session:
            generation = coordinator.owner.generation
            if execution_ids is None:
                await session.execute(update(db.WSWorkerSubscriptionModel).where(
                    db.WSWorkerSubscriptionModel.process_generation == generation).values(
                        expires_at=expires_at))
            else:
                execution_ids = set(execution_ids)
                if any(not isinstance(value, str) or not value or len(value) > 50
                       for value in execution_ids):
                    raise ValueError('Invalid WebSocket execution subscription')
                await session.execute(text('BEGIN IMMEDIATE'))
                existing = {row.execution_id: row for row in (await session.execute(select(
                    db.WSWorkerSubscriptionModel).where(
                        db.WSWorkerSubscriptionModel.process_generation == generation,
                        db.WSWorkerSubscriptionModel.execution_id.in_(execution_ids)))).scalars()}
                for execution_id in execution_ids:
                    row = existing.get(execution_id)
                    if row is None:
                        session.add(db.WSWorkerSubscriptionModel(
                            execution_id=execution_id, process_generation=generation,
                            subscriber_count=1, expires_at=expires_at))
                    else:
                        row.subscriber_count = 1
                        row.expires_at = expires_at
            await session.commit()

    async def publish(self, execution_id, data):
        coordinator = self.coordinator
        if coordinator is None:
            return 0
        coordinator.heartbeat()
        payload = self._payload(data)
        encrypted = self._fernet().encrypt(payload).decode('ascii')
        fingerprint = hashlib.sha256(payload).hexdigest()
        now = time.time()
        async with db.get_session_factory()() as session:
            await session.execute(text('BEGIN IMMEDIATE'))
            await session.execute(delete(db.WSDeliveryModel).where(db.WSDeliveryModel.expires_at <= now))
            await session.execute(delete(db.WSWorkerSubscriptionModel).where(
                db.WSWorkerSubscriptionModel.expires_at <= now))
            targets = list((await session.execute(select(
                db.WSWorkerSubscriptionModel.process_generation).where(
                    db.WSWorkerSubscriptionModel.execution_id == execution_id,
                    db.WSWorkerSubscriptionModel.subscriber_count > 0,
                    db.WSWorkerSubscriptionModel.process_generation != coordinator.owner.generation))).scalars())
            for target in targets:
                pending = list((await session.execute(select(db.WSDeliveryModel.id).where(
                    db.WSDeliveryModel.target_generation == target).order_by(
                        db.WSDeliveryModel.id))).scalars())
                overflow = len(pending) - self.MAX_PENDING_PER_WORKER + 1
                if overflow > 0:
                    await session.execute(delete(db.WSDeliveryModel).where(
                        db.WSDeliveryModel.id.in_(pending[:overflow])))
                session.add(db.WSDeliveryModel(
                    execution_id=execution_id, target_generation=target,
                    encrypted_payload=encrypted, fingerprint=fingerprint,
                    expires_at=now + self.EVENT_TTL, created_at=now))
            await session.commit()
        return len(targets)

    async def drain(self, deliver, *, limit=100):
        coordinator = self.coordinator
        if coordinator is None:
            return 0
        coordinator.heartbeat()
        generation = coordinator.owner.generation
        now = time.time()
        delivered = 0
        async with db.get_session_factory()() as session:
            rows = (await session.execute(select(db.WSDeliveryModel).where(
                db.WSDeliveryModel.target_generation == generation,
                db.WSDeliveryModel.expires_at > now).order_by(db.WSDeliveryModel.id).limit(limit))).scalars().all()
            for row in rows:
                try:
                    payload = self._fernet().decrypt(row.encrypted_payload.encode('ascii'))
                    if hashlib.sha256(payload).hexdigest() != row.fingerprint:
                        raise InvalidToken
                    data = json.loads(payload)
                except (InvalidToken, UnicodeError, json.JSONDecodeError):
                    await session.delete(row); await session.commit()
                    continue
                result = deliver(row.execution_id, data)
                if hasattr(result, '__await__'):
                    await result
                await session.delete(row); await session.commit()
                delivered += 1
        return delivered

    async def close(self):
        coordinator = self.coordinator
        if coordinator is None:
            return
        generation = coordinator.owner.generation
        async with db.get_session_factory()() as session:
            await session.execute(delete(db.WSWorkerSubscriptionModel).where(
                db.WSWorkerSubscriptionModel.process_generation == generation))
            await session.execute(delete(db.WSDeliveryModel).where(
                db.WSDeliveryModel.target_generation == generation))
            await session.commit()


ws_event_bus = WSEventBus()

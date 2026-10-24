"""Short-lived, one-shot user decisions for exact agent tool invocations.

Tools marked ``require_confirmation`` wait here until the user approves or
denies them in the UI, or until the request times out (treated as denied).
"""
import asyncio
import copy
import time
import uuid


class ConfirmationManager:
    def __init__(self, timeout_seconds=120.0, max_pending=128):
        self.timeout_seconds = timeout_seconds
        self.max_pending = max_pending
        self._pending = {}

    async def request(self, tool_name: str, arguments: dict) -> bool:
        if len(self._pending) >= self.max_pending:
            return False
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = {
            'id': request_id, 'tool': tool_name, 'arguments': copy.deepcopy(arguments),
            'expires_at': time.time() + self.timeout_seconds, 'future': future,
        }
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)

    def list_pending(self) -> list[dict]:
        return [copy.deepcopy({k: v for k, v in item.items() if k != 'future'})
                for item in self._pending.values()
                if not item['future'].done() and item['expires_at'] > time.time()]

    def decide(self, request_id: str, approved: bool) -> None:
        item = self._pending.get(request_id)
        if item is None or item['future'].done() or item['expires_at'] <= time.time():
            raise KeyError('Confirmation expired or already consumed')
        item['future'].set_result(approved)


confirmations = ConfirmationManager()

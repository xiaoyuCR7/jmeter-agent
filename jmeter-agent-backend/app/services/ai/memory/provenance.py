"""Knowledge scope comes from an execution snapshot, never model-supplied metadata."""
import hashlib
import json


def execution_scope(execution_id: str) -> str:
    from app.services.storage import store
    execution = store.get_execution(execution_id) if execution_id else None
    return snapshot_scope(execution.context_snapshot) if execution else ''


def snapshot_scope(snapshot) -> str:
    if not snapshot or not snapshot.get('project_id') or not snapshot.get('environment_fingerprint'):
        return ''  # Legacy records remain unscoped; do not guess their ownership.
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


async def load_execution_scope(execution_id: str) -> str:
    from app.services.db_store import db_store
    record = await db_store.get_execution(execution_id) if execution_id else None
    return snapshot_scope(record.get('context_snapshot')) if record else ''

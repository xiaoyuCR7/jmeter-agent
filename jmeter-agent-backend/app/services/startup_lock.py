"""Short cross-platform file lock for additive SQLite startup migrations."""
import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path


class _LockFile:
    def __init__(self, target: Path):
        self.path = Path(str(target) + '.startup.lock').resolve()
        self.file = None

    def try_acquire(self):
        from app.services.coordination import _require_local_disk
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _require_local_disk(self.path)
        handle = self.path.open('a+b')
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return False
        self.file = handle
        return True

    def release(self):
        handle, self.file = self.file, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def startup_file_lock(database_path: Path, *, timeout=30.0):
    """Acquire one local migration owner; OS releases the lock after process death."""
    if not 0 < timeout <= 300:
        raise ValueError('Startup lock timeout must be between 0 and 300 seconds')
    lock = _LockFile(database_path)
    deadline = time.monotonic() + timeout
    while not lock.try_acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError('Timed out waiting for the database startup owner')
        time.sleep(.05)
    try:
        yield
    finally:
        lock.release()


@asynccontextmanager
async def startup_migration_lock(engine, *, timeout=30.0):
    """Async startup wrapper; non-SQLite databases use their own migration tooling."""
    if not engine.url.drivername.startswith('sqlite') or not engine.url.database \
            or engine.url.database == ':memory:':
        yield
        return
    lock = _LockFile(Path(engine.url.database))
    deadline = time.monotonic() + timeout
    while True:
        attempt = asyncio.create_task(asyncio.to_thread(lock.try_acquire))
        try:
            acquired = await asyncio.shield(attempt)
        except asyncio.CancelledError:
            # shield keeps the short nonblocking OS call alive; drain it and
            # release if it won the race before propagating cancellation.
            while not attempt.done():
                try:
                    await asyncio.shield(attempt)
                except asyncio.CancelledError:
                    pass
            if not attempt.cancelled() and attempt.exception() is None and attempt.result():
                await asyncio.to_thread(lock.release)
            raise
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Timed out waiting for the database startup owner')
        await asyncio.sleep(.05)
    try:
        yield
    finally:
        await asyncio.to_thread(lock.release)

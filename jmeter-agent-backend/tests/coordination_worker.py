"""Bounded inert child for real-process coordination tests; no application startup."""
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == 'win32' and os.environ.get('COORDINATION_TEST_SQLITE_DLL'):
    import ctypes
    _sqlite_dll = ctypes.WinDLL(os.environ['COORDINATION_TEST_SQLITE_DLL'])
if os.environ.get('JMETER_AGENT_TEST_PYSQLITE3') == '1':
    import pysqlite3 as sqlite3
else:
    import sqlite3
if os.environ.get('COORDINATION_TEST_SQLITE_VERSION'):
    assert sqlite3.sqlite_version == os.environ['COORDINATION_TEST_SQLITE_VERSION']

from app.services.coordination import Busy, Coordinator, StaleClaim


request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
coordinator = Coordinator(request['database'], capacities=request['capacities'])
coordinator.register()
Path(request['response']).with_suffix('.ready').touch()
deadline = time.monotonic() + 30
while request['gate'] and not Path(request['gate']).exists():
    if time.monotonic() > deadline:
        raise SystemExit('gate timeout')
    time.sleep(.01)
result = dict(pid=os.getpid(), creation_time=coordinator.owner.creation_time,
              generation=coordinator.owner.generation, sqlite=sqlite3.sqlite_version)
with sqlite3.connect(request['database']) as connection:
    result['journal_mode'] = connection.execute('PRAGMA journal_mode').fetchone()[0]


def publish(data, suffix=None):
    path = Path(request['response'])
    if suffix:
        path = path.with_suffix(suffix)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.replace(path)


def wait_for(path):
    while not Path(path).exists():
        if time.monotonic() > deadline:
            raise RuntimeError('bounded runtime worker gate timeout')
        time.sleep(.01)


if request['runtime_role']:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services.ai.operation_runtime import AgentOperationRuntime
    runtime = AgentOperationRuntime(coordinator)
    if request['runtime_role'] == 'holder':
        entered = Event()
        def work():
            entered.set()
            wait_for(request['release_gate'])
        operation = runtime.acquire(request['resources'][0], analysis=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            operation.assert_active()
            future = executor.submit(work)
            operation.track_future(future)
            assert entered.wait(5)
            operation.close()
            publish(dict(result, status='draining', future_done=future.done(), active_count=runtime.active_count))
            future.result(timeout=max(.1, deadline - time.monotonic()))
        # Executor shutdown has joined the thread and its done callbacks.
        publish(dict(result, status='released', future_done=future.done(), active_count=runtime.active_count), '.released.json')
    else:
        try:
            operation = runtime.acquire(request['resources'][0], analysis=True)
        except Busy:
            publish(dict(result, status='busy'))
        else:
            publish(dict(result, status='unexpected-acquisition'))
            operation.close()
        wait_for(request['retry_gate'])
        operation = runtime.acquire(request['resources'][0], analysis=True)
        operation.close()
        publish(dict(result, status='acquired', active_count=runtime.active_count), '.retry.json')
    while time.monotonic() < deadline:
        time.sleep(.05)
    raise SystemExit(0)


try:
    admission = coordinator.claim(request['operation'], request['resources'],
                                  capacity={'analysis': request['slots']})
    result['status'] = 'acquired' if admission.acquired else 'duplicate'
    result['has_handle'] = admission.handle is not None
    if request['effects']:
        coordinator.mark_side_effects(admission.handle)
    if request['stale']:
        old = admission.handle
        coordinator.release(old, work_completed=True)
        new = coordinator.claim(request['operation'] + ':next', request['resources'],
                                capacity={'analysis': request['slots']}).handle
        result['new_fences'] = [token.fence for token in new.resources]
        try:
            coordinator.release(old, work_completed=True)
        except StaleClaim:
            result['old_claim_rejected'] = True
except Busy:
    result['status'] = 'busy'
publish(result)
# Deliberately never heartbeat; parent owns cleanup and no external work is done.
while time.monotonic() < deadline:
    time.sleep(.05)

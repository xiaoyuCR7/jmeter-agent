"""Startup schema work has one cross-platform local-disk owner."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_real_process_startup_lock_is_exclusive_and_released(tmp_path):
    target = tmp_path / 'database.sqlite'
    helper = tmp_path / 'worker.py'
    helper.write_bytes(Path(__file__).with_name('startup_lock_worker.py').read_bytes())
    release = tmp_path / 'release'
    first_entered, second_entered = tmp_path / 'first-entered', tmp_path / 'second-entered'
    first_result, second_result = tmp_path / 'first-result', tmp_path / 'second-result'
    first_request, second_request = tmp_path / 'first.json', tmp_path / 'second.json'
    first_request.write_text(json.dumps({'mode': 'hold', 'target': str(target), 'entered': str(first_entered),
        'release': str(release), 'result': str(first_result)}), encoding='utf-8')
    second_request.write_text(json.dumps({'mode': 'wait', 'target': str(target), 'entered': str(second_entered),
        'release': str(release), 'result': str(second_result)}), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    first = subprocess.Popen([sys.executable, '-B', str(helper), str(first_request)], env=environment)
    deadline = time.monotonic() + 5
    while not first_entered.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert first_entered.exists()
    second = subprocess.Popen([sys.executable, '-B', str(helper), str(second_request)], env=environment)
    time.sleep(.15)
    assert not second_entered.exists()
    released_at = time.monotonic()
    release.write_text('release', encoding='utf-8')
    assert first.wait(5) == second.wait(5) == 0
    assert float(second_result.read_text(encoding='utf-8')) >= released_at


@pytest.mark.asyncio
async def test_init_db_uses_startup_lock(monkeypatch):
    from app.models import db
    entered = False
    class Probe:
        async def __aenter__(self):
            nonlocal entered
            entered = True
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr('app.services.startup_lock.startup_migration_lock', lambda engine: Probe())
    await db.init_db()
    assert entered

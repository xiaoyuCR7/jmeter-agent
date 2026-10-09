"""Same-host primitives, isolated SQLite files, never application startup."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_patched_sqlite


def test_process_identity_primitive_exists():
    assert importlib.util.find_spec('app.services.process_identity') is not None


def test_coordination_primitive_exists():
    assert importlib.util.find_spec('app.services.coordination') is not None


def test_current_identity_is_raw_and_alive():
    from app.services.process_identity import current_identity, probe, Liveness
    owner = current_identity()
    assert owner.pid == os.getpid()
    assert isinstance(owner.creation_time, str) and owner.creation_time
    assert probe(owner) is Liveness.ALIVE
    assert probe(replace(owner, creation_time=owner.creation_time + '0')) is Liveness.DEAD


def test_probe_errors_are_unknown(monkeypatch):
    from app.services import process_identity as identity
    owner = identity.current_identity()
    def denied(pid):
        raise PermissionError('denied')
    monkeypatch.setattr(identity, '_inspect', denied)

    assert identity.probe(owner) is identity.Liveness.UNKNOWN


@pytest.mark.parametrize('raw', ['', 'not-a-creation-time', '-1', '0', str(2 ** 64)])
def test_malformed_creation_time_is_unknown_not_pid_reuse(raw):
    from app.services.process_identity import current_identity, probe, Liveness
    assert probe(replace(current_identity(), creation_time=raw)) is Liveness.UNKNOWN


@pytest.mark.parametrize('ticks', ['00123', str(2 ** 64)])
def test_linux_noncanonical_or_overflow_ticks_are_unknown(monkeypatch, ticks):
    from app.services import process_identity as identity
    boot = '11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(identity.sys, 'platform', 'linux')
    monkeypatch.setattr(identity, '_inspect', lambda pid: (identity.Liveness.ALIVE, boot + ':123'))
    owner = identity.ProcessIdentity(42, boot + ':' + ticks)
    assert identity.probe(owner) is identity.Liveness.UNKNOWN


def test_domain_namespace_is_stable_and_nonempty():
    from app.services import process_identity as identity
    assert hasattr(identity, 'coordination_domain')
    assert identity.coordination_domain() == identity.coordination_domain()
    assert len(identity.coordination_domain()) == 64


@pytest.fixture
def database(tmp_path):
    from sqlalchemy import create_engine
    from app.models.db import Base
    path = tmp_path / 'coordination.db'
    engine = create_engine('sqlite:///' + str(path))
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    return path


@pytest.fixture
def coordinator(database):
    from app.services.coordination import Coordinator
    result = Coordinator(database, capacities={'analysis': 2})
    result.register()
    return result


def test_atomic_resource_and_capacity_admission(coordinator):
    from app.services.coordination import Busy
    first = coordinator.claim('one', ['session:a'], capacity={'analysis': 2})
    assert first.acquired and len(first.handle.resources) == 3
    with pytest.raises(Busy):
        coordinator.claim('two', ['session:b'], capacity={'analysis': 1})
    # The denied admission did not partially take session:b.
    second = coordinator.claim('three', ['session:b'])
    assert second.acquired
    coordinator.release(first.handle, work_completed=True)
    assert coordinator.claim('two', ['session:c'], capacity={'analysis': 2}).acquired


def test_operation_id_is_idempotent_never_replayed(coordinator):
    from app.services.coordination import OperationConflict
    first = coordinator.claim('same', ['a'], capacity={'analysis': 1})
    duplicate = coordinator.claim('same', ['a'], capacity={'analysis': 1})
    assert not duplicate.acquired and duplicate.handle == first.handle
    with pytest.raises(OperationConflict):
        coordinator.claim('same', ['different'])
    coordinator.release(first.handle, work_completed=True)
    duplicate = coordinator.claim('same', ['a'], capacity={'analysis': 1})
    assert not duplicate.acquired and duplicate.state == 'released'
    assert duplicate.handle is None


def test_old_claim_and_fence_cannot_mutate_or_release(coordinator):
    from app.services.coordination import StaleClaim
    old = coordinator.claim('first', ['a']).handle
    coordinator.release(old, work_completed=True)
    new = coordinator.claim('second', ['a']).handle
    assert old.claim_id != new.claim_id
    assert new.resources[0].fence == old.resources[0].fence + 1
    for action in [lambda: coordinator.release(old, work_completed=True),
                   lambda: coordinator.mark_side_effects(old),
                   lambda: coordinator.mark_draining(old),
                   lambda: coordinator.quarantine(old),
                   lambda: coordinator.mark_side_effects(replace(new, resources=old.resources))]:
        with pytest.raises(StaleClaim):
            action()
    coordinator.mark_side_effects(new)


def test_assert_owned_checks_exact_claim_before_submission(coordinator):
    from app.services.coordination import StaleClaim
    handle = coordinator.claim('one', ['a']).handle
    assert hasattr(coordinator, 'assert_owned')
    coordinator.assert_owned(handle)
    coordinator.release(handle, work_completed=True)
    with pytest.raises(StaleClaim):
        coordinator.assert_owned(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native handles')
@pytest.mark.parametrize('wait_result,expected', [(258, 'alive'), (0, 'dead'), (0xFFFFFFFF, 'unknown')])
def test_windows_creation_and_wait_use_same_handle(monkeypatch, wait_result, expected):
    import ctypes
    from app.services import process_identity as identity
    calls = []
    handle = 123456789
    class Call:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)
    def opened(access, inherit, pid):
        calls.append(('open', pid))
        assert access == 0x101000
        return handle
    def times(received, creation, *other):
        calls.append(('times', received))
        creation._obj.dwHighDateTime = 0xFFFFFFFF
        creation._obj.dwLowDateTime = 0xFFFFFFFE
        return True
    def waited(received, timeout):
        calls.append(('wait', received))
        assert timeout == 0
        return wait_result
    class Kernel:
        OpenProcess = Call(opened)
        GetProcessTimes = Call(times)
        WaitForSingleObject = Call(waited)
        CloseHandle = Call(lambda received: calls.append(('close', received)))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: Kernel())
    state, raw = identity._inspect_windows(42)
    assert state.value == expected
    assert calls == [('open', 42), ('times', handle), ('wait', handle), ('close', handle)]
    if expected != 'unknown':
        assert raw == str(0xFFFFFFFFFFFFFFFE)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native errors')
@pytest.mark.parametrize('error,expected', [(5, 'unknown'), (6, 'unknown'), (87, 'dead')])
def test_windows_open_errors_are_conservative(monkeypatch, error, expected):
    import ctypes
    from app.services import process_identity as identity
    class Call:
        def __call__(self, *args):
            return 0
    class Kernel:
        OpenProcess = Call()
        GetProcessTimes = Call()
        WaitForSingleObject = Call()
        CloseHandle = Call()
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: Kernel())
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: error)
    assert identity._inspect_windows(42)[0].value == expected


def test_draining_keeps_capacity_until_actual_work_completion(coordinator):
    from app.services.coordination import Busy, OwnerInactive, CoordinationError
    handle = coordinator.claim('a', ['a'], capacity={'analysis': 2}).handle
    coordinator.mark_draining(handle)
    with pytest.raises(CoordinationError):
        coordinator.release(handle, work_completed=False)
    with pytest.raises(Busy):
        coordinator.claim('b', ['b'], capacity={'analysis': 1})
    coordinator.stop_admission()
    coordinator.heartbeat()
    with pytest.raises(OwnerInactive):
        coordinator.claim('c', ['c'])
    coordinator.release(handle, work_completed=True)
    coordinator.close()
    with pytest.raises(OwnerInactive):
        coordinator.register()
    with pytest.raises(OwnerInactive):
        coordinator.heartbeat()


def test_quarantine_is_not_released_by_normal_owner(coordinator):
    from app.services.coordination import Busy, StaleClaim
    handle = coordinator.claim('unknown', ['a']).handle
    coordinator.mark_side_effects(handle)
    coordinator.quarantine(handle)
    with pytest.raises(StaleClaim):
        coordinator.release(handle, work_completed=True)
    with pytest.raises(Busy):
        coordinator.claim('later', ['a'])


def test_protocol_and_capacity_configuration_must_match(coordinator, database):
    from app.services.coordination import Coordinator, ProtocolMismatch, ConfigurationMismatch
    with pytest.raises(ProtocolMismatch):
        Coordinator(database, capacities={'analysis': 2}, protocol=999).register()
    with pytest.raises(ConfigurationMismatch):
        Coordinator(database, capacities={'analysis': 3}).register()


def test_other_host_or_pid_namespace_rejected_before_probe(coordinator, database, monkeypatch):
    from app.services import coordination
    coordinator.claim('existing', ['a'])
    assert hasattr(coordination, 'coordination_domain')
    monkeypatch.setattr(coordination, 'coordination_domain', lambda: 'other-pid-namespace')
    foreign = coordination.Coordinator(database, capacities={'analysis': 2})
    monkeypatch.setattr(coordination, 'probe', lambda owner: pytest.fail('must not probe a foreign PID namespace'))
    with pytest.raises(coordination.ConfigurationMismatch):
        foreign.register()


def test_missing_protocol_cannot_rebind_existing_owner_records(coordinator, database):
    from app.services.coordination import Coordinator, StorageUnavailable
    coordinator.claim('existing', ['a'])
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM coordination_protocol')
    with pytest.raises(StorageUnavailable):
        Coordinator(database, capacities={'analysis': 2}).register()


def test_invalid_lease_state_is_not_reclaimed_even_if_owner_dead(coordinator, database, monkeypatch):
    from app.services import coordination
    from app.services.process_identity import Liveness
    other = coordination.Coordinator(database, capacities={'analysis': 2})
    other.register()
    other.claim('existing', ['a'])
    with sqlite3.connect(database) as db:
        db.execute("UPDATE resource_leases SET state='unrecognized'")
    monkeypatch.setattr(coordination, 'probe', lambda owner: Liveness.DEAD)
    with pytest.raises(coordination.StorageUnavailable):
        coordinator.claim('unsafe', ['a'])


def test_released_lease_must_agree_with_active_effectful_operation(coordinator, database):
    from app.services.coordination import StorageUnavailable
    handle = coordinator.claim('existing', ['a']).handle
    coordinator.mark_side_effects(handle)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE resource_leases SET state='released'")
    with pytest.raises(StorageUnavailable):
        coordinator.claim('unsafe', ['a'])


@pytest.mark.parametrize('column,value', [('state', 'closed'), ('state', 'invalid'), ('protocol', 999)])
def test_reclaim_validates_owner_state_and_protocol(coordinator, database, monkeypatch, column, value):
    from app.services import coordination
    from app.services.process_identity import Liveness
    other = coordination.Coordinator(database, capacities={'analysis': 2})
    other.register()
    other.claim('existing', ['a'])
    with sqlite3.connect(database) as db:
        db.execute(f'UPDATE process_owners SET {column}=? WHERE generation=?', (value, other.owner.generation))
    monkeypatch.setattr(coordination, 'probe', lambda owner: Liveness.DEAD)
    with pytest.raises(coordination.StorageUnavailable):
        coordinator.claim('unsafe', ['a'])


@pytest.mark.parametrize('filesystem', ['nfs', 'cifs', 'overlay', 'unknownfs'])
def test_linux_remote_or_unverified_filesystem_is_refused(monkeypatch, filesystem):
    from app.services import coordination
    from pathlib import PurePosixPath
    monkeypatch.setattr(coordination.sys, 'platform', 'linux')
    monkeypatch.setattr(coordination.Path, 'read_text', lambda self: f'1 0 0:1 / / rw - {filesystem} server rw\n')
    with pytest.raises(coordination.ConfigurationMismatch):
        coordination._require_local_disk(PurePosixPath('/data/coordination.db'))


def test_linux_local_filesystem_uses_longest_mountpoint(monkeypatch):
    from app.services import coordination
    from pathlib import PurePosixPath
    monkeypatch.setattr(coordination.sys, 'platform', 'linux')
    monkeypatch.setattr(coordination.Path, 'read_text', lambda self:
        '1 0 0:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /data rw - nfs server rw\n')
    coordination._require_local_disk(PurePosixPath('/safe/db'))
    with pytest.raises(coordination.ConfigurationMismatch):
        coordination._require_local_disk(PurePosixPath('/data/coordination.db'))


@pytest.mark.parametrize('filesystems', [('ext4', 'cifs'), ('cifs', 'ext4')])
def test_linux_stacked_deepest_mountpoints_are_refused(monkeypatch, filesystems):
    from app.services import coordination
    from pathlib import PurePosixPath
    monkeypatch.setattr(coordination.sys, 'platform', 'linux')
    monkeypatch.setattr(coordination.Path, 'read_text', lambda self:
        '1 0 0:1 / / rw - ext4 /dev/root rw\n'
        f'2 1 0:2 / /data rw - {filesystems[0]} source rw\n'
        f'3 2 0:3 / /data rw - {filesystems[1]} source rw\n')
    with pytest.raises(coordination.ConfigurationMismatch):
        coordination._require_local_disk(PurePosixPath('/data/coordination.db'))


def test_linux_proc_view_must_match_current_pid_namespace(monkeypatch):
    from app.services import process_identity as identity
    monkeypatch.setattr(identity.sys, 'platform', 'linux')
    monkeypatch.setattr(identity.Path, 'read_text', lambda self: 'machine')
    monkeypatch.setattr(identity.os, 'readlink', lambda path: 'pid:[1]' if '/self/' in path else 'pid:[2]')
    with pytest.raises(RuntimeError):
        identity.coordination_domain()


@pytest.mark.parametrize('kill_result,expected', [('alive', 'unknown'), ('denied', 'unknown'), ('missing', 'dead')])
def test_linux_hidden_proc_pid_is_not_assumed_dead(monkeypatch, kill_result, expected):
    from app.services import process_identity as identity
    def read(path):
        if str(path).endswith('boot_id'):
            return '11111111-1111-1111-1111-111111111111'
        raise FileNotFoundError('hidden by proc mount options')
    def kill(pid, signal):
        assert pid == 42 and signal == 0
        if kill_result == 'denied':
            raise PermissionError('denied')
        if kill_result == 'missing':
            raise ProcessLookupError('no process')
    monkeypatch.setattr(identity.Path, 'read_text', read)
    monkeypatch.setattr(identity.os, 'kill', kill)
    assert identity._inspect_linux(42)[0].value == expected


def test_hardlinked_database_alias_is_refused_before_sql(database):
    from app.services.coordination import Coordinator, ConfigurationMismatch
    alias = database.with_name('alias.db')
    os.link(database, alias)
    for path in (database, alias):
        with pytest.raises(ConfigurationMismatch):
            Coordinator(path, capacities={'analysis': 2}).register()


def test_unpatched_sqlite_runtime_is_refused_before_registration(database, monkeypatch):
    from app.services import coordination
    monkeypatch.setattr(coordination.sqlite3, 'sqlite_version_info', (3, 50, 4))
    with pytest.raises(coordination.ConfigurationMismatch):
        coordination.Coordinator(database, capacities={'analysis': 2}).register()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows drive classification')
def test_mapped_remote_drive_is_refused(database, monkeypatch):
    import ctypes
    from app.services import coordination
    original = ctypes.WinDLL
    class FakeCall:
        def __call__(self, path):
            return 4  # DRIVE_REMOTE, including mapped drive letters.
    class FakeKernel:
        GetDriveTypeW = FakeCall()
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: FakeKernel() if name == 'kernel32' else original(name, **kwargs))
    with pytest.raises(coordination.ConfigurationMismatch):
        coordination.Coordinator(database, capacities={'analysis': 2})


def test_partial_insert_failure_rolls_back_whole_admission(coordinator, database):
    from app.services.coordination import StorageUnavailable
    with sqlite3.connect(database) as db:
        db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON resource_leases "
                   "WHEN NEW.resource_key='resource:b' BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
    with pytest.raises(StorageUnavailable):
        coordinator.claim('fault', ['a', 'b'], capacity={'analysis': 1})
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM resource_leases').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM coordination_operations').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_second')
    assert coordinator.claim('fault', ['a', 'b'], capacity={'analysis': 1}).acquired


def test_mutation_fault_rolls_back_and_preserves_lease(coordinator, database):
    from app.services.coordination import StorageUnavailable, Busy
    handle = coordinator.claim('fault', ['a', 'b']).handle
    with sqlite3.connect(database) as db:
        db.execute("CREATE TRIGGER fail_release BEFORE UPDATE ON resource_leases "
                   "WHEN NEW.resource_key='resource:b' BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
    with pytest.raises(StorageUnavailable):
        coordinator.release(handle, work_completed=True)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT DISTINCT state FROM resource_leases').fetchall() == [('active',)]
        assert db.execute('SELECT state FROM coordination_operations').fetchone() == ('active',)
        db.execute('DROP TRIGGER fail_release')
    with pytest.raises(Busy):
        coordinator.claim('cannot', ['a'])
    coordinator.release(handle, work_completed=True)


def test_additive_schema_rerun_preserves_business_rows_and_leases(coordinator, database):
    from sqlalchemy import create_engine
    from app.models.db import Base
    handle = coordinator.claim('existing', ['a']).handle
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO projects(id,name,description) VALUES ('old','Old project','unchanged')")
    engine = create_engine('sqlite:///' + str(database))
    try:
        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name,description FROM projects WHERE id='old'").fetchone() == ('Old project', 'unchanged')
        assert db.execute('SELECT claim_id,fence,state FROM resource_leases').fetchone() == (handle.claim_id, 1, 'active')
    coordinator.assert_owned(handle)


def test_handle_from_other_process_identity_is_rejected(coordinator, monkeypatch):
    from app.services import coordination
    from app.services.process_identity import ProcessIdentity
    handle = coordinator.claim('mine', ['a']).handle
    monkeypatch.setattr(coordination, 'current_identity', lambda: ProcessIdentity(coordinator.owner.pid + 1, coordinator.owner.creation_time))
    with pytest.raises(coordination.OwnerInactive):
        coordinator.release(handle, work_completed=True)


def test_storage_failure_and_lock_are_not_busy_or_local_fallback(coordinator, database, tmp_path):
    from app.services.coordination import Coordinator, StorageUnavailable
    missing = Coordinator(tmp_path / 'missing' / 'db', capacities={'analysis': 2})
    with pytest.raises(StorageUnavailable):
        missing.register()
    with sqlite3.connect(database) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(StorageUnavailable):
            coordinator.claim('locked', ['a'])
        blocker.rollback()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM coordination_operations WHERE operation_id='locked'").fetchone()[0] == 0
    assert coordinator.claim('locked', ['a']).acquired


def test_unknown_probe_and_old_heartbeat_do_not_reclaim(coordinator, database, monkeypatch):
    from app.services import coordination
    from app.services.process_identity import Liveness
    other = coordination.Coordinator(database, capacities={'analysis': 2})
    other.register()
    handle = other.claim('one', ['a']).handle
    with sqlite3.connect(database) as db:
        db.execute('UPDATE process_owners SET heartbeat_at=0 WHERE generation=?', (other.owner.generation,))
    with pytest.raises(coordination.Busy):
        coordinator.claim('two', ['a'])
    monkeypatch.setattr(coordination, 'probe', lambda owner: Liveness.UNKNOWN)
    with pytest.raises(coordination.Busy):
        coordinator.claim('two', ['a'])
    # Probe failure never makes a resource reusable.
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT claim_id FROM resource_leases WHERE resource_key=?', (handle.resources[0].key,)).fetchone()[0] == handle.claim_id


def test_foreign_operation_replay_does_not_grant_handle(coordinator, database):
    from app.services.coordination import Coordinator
    owner = Coordinator(database, capacities={'analysis': 2})
    owner.register()
    owner.claim('same', ['a'])
    duplicate = coordinator.claim('same', ['a'])
    assert not duplicate.acquired and duplicate.handle is None


@pytest.fixture
def workers(database, tmp_path):
    helper = tmp_path / 'coordination_worker.py'
    helper.write_bytes(Path(__file__).with_name('coordination_worker.py').read_bytes())
    children = []
    def start(operation, *, effects=False, gate=None, resources=None, slots=1, stale=False,
              runtime_role=None, release_gate=None, retry_gate=None, capacities=None):
        name = uuid.uuid4().hex
        request = tmp_path / (name + '-request.json')
        response = tmp_path / (name + '-response.json')
        request.write_text(json.dumps(dict(database=str(database), operation=operation,
            effects=effects, gate=str(gate) if gate else None, response=str(response),
            resources=resources or ['session:shared'], slots=slots, stale=stale,
            runtime_role=runtime_role, release_gate=str(release_gate) if release_gate else None,
            retry_gate=str(retry_gate) if retry_gate else None,
            capacities=capacities or {'analysis': 2})), encoding='utf-8')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        if sys.platform == 'win32':
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            kernel.GetModuleFileNameW.restype = ctypes.c_uint
            buffer = ctypes.create_unicode_buffer(32768)
            assert kernel.GetModuleFileNameW(kernel.GetModuleHandleW('sqlite3.dll'), buffer, len(buffer))
            env['COORDINATION_TEST_SQLITE_DLL'] = buffer.value
            env['COORDINATION_TEST_SQLITE_VERSION'] = sqlite3.sqlite_version
        # Bootstrap path comes from the verification runner, never project argv.
        bootstrap = os.environ.get('COORDINATION_TEST_BOOTSTRAP')
        args = [sys.executable, '-B'] + ([bootstrap] if bootstrap else []) + [str(helper), str(request)]
        process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(process)
        return process, response
    yield start
    for process in children:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)


def _response(process, path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f'worker exited {process.returncode}: {stdout!r} {stderr!r}')
        time.sleep(.02)
    pytest.fail('bounded worker response timeout')


def _ready(process, path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.with_suffix('.ready').exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f'worker startup failed {process.returncode}: {stdout!r} {stderr!r}')
        time.sleep(.01)
    pytest.fail('bounded worker readiness timeout')


@pytest.mark.parametrize('slots,second_resources', [(1, ['session:shared']), (2, ['session:other'])])
def test_two_os_processes_compete_atomically(workers, coordinator, tmp_path, slots, second_resources):
    gate = tmp_path / 'gate'
    first, path1 = workers('child-one', gate=gate, slots=slots)
    second, path2 = workers('child-two', gate=gate, slots=slots, resources=second_resources)
    _ready(first, path1)
    _ready(second, path2)
    gate.touch()
    one, two = _response(first, path1), _response(second, path2)
    assert one['pid'] != two['pid'] and os.getpid() not in (one['pid'], two['pid'])
    assert sorted([one['status'], two['status']]) == ['acquired', 'busy']
    assert one['sqlite'] == two['sqlite'] == sqlite3.sqlite_version
    assert one['journal_mode'] == two['journal_mode'] == 'wal'
    print('two-process-contention', json.dumps([one, two]))


def test_two_os_processes_operation_id_only_admits_once(workers, coordinator, tmp_path):
    gate = tmp_path / 'gate'
    first, path1 = workers('same-operation', gate=gate)
    second, path2 = workers('same-operation', gate=gate)
    _ready(first, path1)
    _ready(second, path2)
    gate.touch()
    one, two = _response(first, path1), _response(second, path2)
    assert one['pid'] != two['pid']
    assert sorted([one['status'], two['status']]) == ['acquired', 'duplicate']
    duplicate = one if one['status'] == 'duplicate' else two
    assert not duplicate['has_handle']
    print('two-process-idempotency', json.dumps([one, two]))


def test_real_worker_old_claim_is_rejected_after_reacquisition(workers, coordinator):
    process, path = workers('stale-worker', stale=True)
    info = _response(process, path)
    assert info['old_claim_rejected']
    assert info['new_fences'] == [2, 2]
    print('real-process-stale-claim', json.dumps(info))


@pytest.mark.parametrize('capacity,second_session', [(2, 'conversation'), (1, 'other-conversation')])
def test_two_os_process_runtime_keeps_session_and_capacity_until_actual_thread_done(workers, database, tmp_path,
                                                                                 capacity, second_session):
    capacities = {'agent': capacity, 'analysis': capacity}
    release, retry = tmp_path / 'release-thread', tmp_path / 'retry-admission'
    first, path1 = workers('runtime-holder', runtime_role='holder', release_gate=release,
                           resources=['conversation'], capacities=capacities)
    one = _response(first, path1)
    assert one['status'] == 'draining' and not one['future_done'] and one['active_count'] == 1
    second, path2 = workers('runtime-contender', runtime_role='contender', retry_gate=retry,
                            resources=[second_session], capacities=capacities)
    two = _response(second, path2)
    assert two['status'] == 'busy' and one['pid'] != two['pid']
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT state FROM resource_leases').fetchall() == [('draining',)] * 3
    release.touch()
    drained = _response(first, path1.with_suffix('.released.json'))
    assert drained['future_done'] and drained['active_count'] == 0
    assert first.poll() is None  # The worker survived; only actual thread completion released the claim.
    retry.touch()
    admitted = _response(second, path2.with_suffix('.retry.json'))
    assert admitted['status'] == 'acquired'
    assert one['sqlite'] == two['sqlite'] == admitted['sqlite'] == sqlite3.sqlite_version
    print('two-process-actual-thread-drain', json.dumps([one, two, drained, admitted]))


def test_real_alive_without_heartbeat_and_confirmed_death_reclamation(workers, coordinator, database):
    from app.services.coordination import Busy
    from app.services.process_identity import ProcessIdentity, probe, Liveness
    process, path = workers('dead-safe', slots=2)
    info = _response(process, path)
    assert info['status'] == 'acquired'
    identity = ProcessIdentity(info['pid'], info['creation_time'])
    with sqlite3.connect(database) as db:
        db.execute('UPDATE process_owners SET heartbeat_at=0 WHERE generation=?', (info['generation'],))
    assert probe(identity) is Liveness.ALIVE
    with pytest.raises(Busy):
        coordinator.claim('while-alive', ['session:shared'], capacity={'analysis': 2})
    process.terminate()
    process.wait(timeout=15)
    assert probe(identity) is Liveness.DEAD
    admitted = coordinator.claim('after-death', ['session:shared'], capacity={'analysis': 2})
    assert admitted.acquired
    assert all(resource.fence == 2 for resource in admitted.handle.resources)
    duplicate = coordinator.claim('dead-safe', ['session:shared'], capacity={'analysis': 2})
    assert not duplicate.acquired and duplicate.state == 'abandoned'
    print('real-death-reclamation', json.dumps(info))


def test_dead_owner_with_possible_external_effects_stays_quarantined(workers, coordinator, database):
    from app.services.coordination import Busy
    process, path = workers('dead-effects', effects=True, slots=2)
    info = _response(process, path)
    process.terminate()
    process.wait(timeout=15)
    with pytest.raises(Busy):
        coordinator.claim('unsafe-retry', ['session:shared'], capacity={'analysis': 2})
    with sqlite3.connect(database) as db:
        states = db.execute('SELECT state FROM resource_leases WHERE owner_generation=?', (info['generation'],)).fetchall()
        assert states and all(row == ('quarantined',) for row in states)
    print('real-death-quarantine', json.dumps(info))

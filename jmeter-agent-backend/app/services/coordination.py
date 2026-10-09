"""Persistent ownership primitives for ONE host and ONE local application DB.

Caller creates the additive ORM schema before register(). No import-time writes,
background threads, work execution, replay, TTL takeover, or local fallback.
Each call uses one bounded, short BEGIN IMMEDIATE transaction. Callers must never
run model/network/thread work within a database transaction. They retain actual
Future/process tracking and release only after that work is confirmed finished.
"""
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.process_identity import ProcessIdentity, Liveness, current_identity, probe, coordination_domain

PROTOCOL_VERSION = 1


class CoordinationError(RuntimeError):
    pass


class Busy(CoordinationError):
    """Healthy coordination storage, but required resources are occupied."""


class StorageUnavailable(CoordinationError):
    """No admission is safe; caller MUST NOT fall back to a local semaphore."""


class ProtocolMismatch(CoordinationError):
    pass


class ConfigurationMismatch(CoordinationError):
    pass


class OwnerInactive(CoordinationError):
    pass


class StaleClaim(CoordinationError):
    pass


class OperationConflict(CoordinationError):
    pass


@dataclass(frozen=True)
class Owner(ProcessIdentity):
    generation: str
    protocol: int


@dataclass(frozen=True)
class ResourceToken:
    key: str
    fence: int


@dataclass(frozen=True)
class ClaimHandle:
    owner_generation: str
    operation_id: str
    claim_id: str
    resources: tuple[ResourceToken, ...]


@dataclass(frozen=True)
class Admission:
    # Only acquired=True authorizes new work. Duplicate operation IDs never do.
    acquired: bool
    handle: ClaimHandle | None
    state: str


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _require_local_disk(path):
    if str(path).startswith('\\\\'):
        raise ConfigurationMismatch('Coordination requires a local disk, not a network path')
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        if kernel.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED only; mapped remote drives are 4.
            raise ConfigurationMismatch('Coordination requires a verified fixed local drive')
    elif sys.platform.startswith('linux'):
        try:
            matches = []
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                fields, filesystem = line.split(' - ', 1)
                mountpoint = fields.split()[4]
                for escaped, decoded in [('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')]:
                    mountpoint = mountpoint.replace(escaped, decoded)
                mount = PurePosixPath(mountpoint)
                if PurePosixPath(path).is_relative_to(mount):
                    matches.append((len(mount.parts), filesystem.split()[0]))
            # Conservative: overlay/FUSE/tmpfs and unknown filesystems require
            # explicit deployment work, not an assumption about their backing.
            local = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs', 'jfs', 'reiserfs', 'nilfs2'}
            depth = max((depth for depth, _ in matches), default=-1)
            deepest = [filesystem for candidate_depth, filesystem in matches if candidate_depth == depth]
            # Stacked mounts at the same path have no safely inferred ordering
            # here. Refuse ambiguity even when one candidate looks local.
            if len(deepest) != 1 or deepest[0] not in local:
                raise ConfigurationMismatch('Coordination requires a verified local persistent filesystem')
        except ConfigurationMismatch:
            raise
        except Exception as error:
            raise ConfigurationMismatch('Cannot verify coordination filesystem') from error
    else:
        raise ConfigurationMismatch('Unsupported coordination filesystem platform')


def _require_patched_sqlite():
    version = sqlite3.sqlite_version_info
    # https://sqlite.org/wal.html#walresetbug: fixed releases and maintained
    # backports. This verifies this binding only, not arbitrary other runtimes.
    if not (version >= (3, 51, 3) or version[:2] == (3, 50) and version[2] >= 7
            or version[:2] == (3, 44) and version[2] >= 6):
        raise ConfigurationMismatch('Coordination requires SQLite with the WAL-reset fix')


class Coordinator:
    def __init__(self, database_path, *, capacities, protocol=PROTOCOL_VERSION, timeout=.25):
        self.database_path = Path(database_path).resolve()
        _require_local_disk(self.database_path)
        if not 0 < timeout <= 5:
            raise ValueError('Coordination timeout must be positive and at most 5 seconds')
        if not isinstance(capacities, dict) or any(
            not isinstance(key, str) or not key or len(key) > 100
            or type(value) is not int or not 1 <= value <= 1024
            for key, value in capacities.items()
        ) or sum(capacities.values()) > 1024:
            raise ValueError('Invalid fixed capacity configuration')
        self.capacities = dict(capacities)
        self.protocol = protocol
        self.timeout = timeout
        try:
            self.domain = coordination_domain()
        except Exception as error:
            raise ConfigurationMismatch('Cannot establish the local coordination domain') from error
        identity = current_identity()
        self.owner = Owner(identity.pid, identity.creation_time, uuid.uuid4().hex, protocol)

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            _require_local_disk(self.database_path)
            try:
                if self.database_path.stat().st_nlink != 1:
                    raise ConfigurationMismatch('Hardlinked database paths are not supported')
            except OSError as error:
                raise StorageUnavailable('Cannot verify coordination database path') from error
            # mode=rw prevents typos or a disappeared DB from silently creating
            # a second authority. Migration/initialization is caller-owned.
            connection = sqlite3.connect(self.database_path.as_uri() + '?mode=rw',
                                         uri=True, timeout=self.timeout, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            raise StorageUnavailable('Coordination storage unavailable; new work refused') from error
        finally:
            if connection is not None:
                # Closing also rolls back a failed transaction, including any
                # partially reserved resource set. Never retain a write lock.
                connection.close()

    def _current_process(self):
        try:
            identity = current_identity()
        except Exception as error:
            raise OwnerInactive('Cannot verify current process identity') from error
        if (identity.pid, identity.creation_time) != (self.owner.pid, self.owner.creation_time):
            raise OwnerInactive('Owner belongs to a different OS process')
        try:
            domain = coordination_domain()
        except Exception as error:
            raise ConfigurationMismatch('Cannot verify current coordination domain') from error
        if domain != self.domain:
            raise ConfigurationMismatch('The process namespace changed')

    def _protocol(self, connection):
        row = connection.execute('SELECT * FROM coordination_protocol WHERE id=1').fetchone()
        if self.protocol != PROTOCOL_VERSION or row is None or row['protocol'] != self.protocol:
            raise ProtocolMismatch('All workers must use the same supported coordination protocol')
        if row['capacities_json'] != _json(self.capacities):
            raise ConfigurationMismatch('All workers must use the same fixed capacity configuration')
        if row['domain'] != self.domain:
            raise ConfigurationMismatch('Database belongs to a different host or process namespace')

    def _registered(self, connection, *, accepting=False):
        self._current_process()
        _require_patched_sqlite()
        self._protocol(connection)
        row = connection.execute('SELECT * FROM process_owners WHERE generation=?',
                                 (self.owner.generation,)).fetchone()
        if row is None or (row['pid'], row['creation_time'], row['protocol']) != (
            self.owner.pid, self.owner.creation_time, self.protocol
        ) or row['state'] not in (('active',) if accepting else ('active', 'draining')):
            raise OwnerInactive('Owner is unregistered, stopped, or no longer current')
        return row

    def register(self):
        """One immutable generation; registration never reanimates a retired owner."""
        self._current_process()
        _require_patched_sqlite()
        if self.protocol != PROTOCOL_VERSION:
            raise ProtocolMismatch('Unsupported coordination protocol')
        with self._transaction() as connection:
            if connection.execute('SELECT 1 FROM coordination_protocol WHERE id=1').fetchone() is None:
                # A lost handshake cannot silently rebind existing PID records
                # to a different host/namespace/configuration.
                occupied = any(connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                               for table in ('process_owners', 'coordination_operations', 'resource_leases'))
                if occupied:
                    raise StorageUnavailable('Protocol identity is missing from an existing coordination store')
            connection.execute('INSERT OR IGNORE INTO coordination_protocol VALUES (1, ?, ?, ?)',
                               (self.protocol, _json(self.capacities), self.domain))
            self._protocol(connection)
            existing = connection.execute('SELECT * FROM process_owners WHERE generation=?',
                                          (self.owner.generation,)).fetchone()
            if existing is not None:
                self._registered(connection, accepting=True)
            else:
                connection.execute('INSERT INTO process_owners VALUES (?, ?, ?, ?, ?, ?)',
                                   (self.owner.generation, self.owner.pid, self.owner.creation_time,
                                    self.protocol, time.time(), 'active'))
        return self.owner

    def heartbeat(self):
        with self._transaction() as connection:
            self._registered(connection)
            connection.execute('UPDATE process_owners SET heartbeat_at=? WHERE generation=?',
                               (time.time(), self.owner.generation))

    def stop_admission(self):
        with self._transaction() as connection:
            self._registered(connection)
            connection.execute("UPDATE process_owners SET state='draining' WHERE generation=?",
                               (self.owner.generation,))

    def close(self):
        """Retire only an empty generation. Does not infer work completion."""
        with self._transaction() as connection:
            self._registered(connection)
            active = connection.execute("SELECT 1 FROM resource_leases WHERE owner_generation=? "
                                        "AND state!='released' LIMIT 1", (self.owner.generation,)).fetchone()
            if active:
                raise Busy('Owner still has active or quarantined resources')
            connection.execute("UPDATE process_owners SET state='closed' WHERE generation=?",
                               (self.owner.generation,))

    def _reclaim_dead(self, connection, key):
        lease = connection.execute('SELECT * FROM resource_leases WHERE resource_key=?', (key,)).fetchone()
        if lease is None:
            return True
        if lease['state'] not in ('active', 'draining', 'released', 'quarantined'):
            raise StorageUnavailable('Resource lease state is unrecognized')
        owner = connection.execute('SELECT * FROM process_owners WHERE generation=?',
                                   (lease['owner_generation'],)).fetchone()
        operation = connection.execute('SELECT * FROM coordination_operations WHERE operation_id=?',
                                       (lease['operation_id'],)).fetchone()
        if owner is None or operation is None:
            raise StorageUnavailable('Coordination ownership records are incomplete')
        expected_states = ('released', 'abandoned') if lease['state'] == 'released' else (lease['state'],)
        if (operation['owner_generation'], operation['claim_id']) != (lease['owner_generation'], lease['claim_id']) \
                or operation['state'] not in expected_states \
                or ResourceToken(key, lease['fence']) not in self._handle(operation).resources:
            raise StorageUnavailable('Coordination ownership records are inconsistent')
        if owner['protocol'] != self.protocol or owner['state'] not in ('active', 'draining', 'dead', 'closed') \
                or owner['state'] == 'closed' and lease['state'] != 'released':
            raise StorageUnavailable('Stored owner protocol or state is inconsistent')
        if lease['state'] == 'released':
            if operation['state'] == 'abandoned' and operation['sideeffects_possible']:
                raise StorageUnavailable('Abandoned operation has unresolved external effects')
            return True
        if lease['state'] == 'quarantined':
            return False
        if probe(ProcessIdentity(owner['pid'], owner['creation_time'])) is not Liveness.DEAD:
            return False
        connection.execute("UPDATE process_owners SET state='dead' WHERE generation=?",
                           (owner['generation'],))
        unknown = operation['sideeffects_possible']
        state = 'quarantined' if unknown else 'released'
        connection.execute('UPDATE resource_leases SET state=? WHERE owner_generation=? AND claim_id=? '
                           "AND state IN ('active', 'draining')",
                           (state, owner['generation'], operation['claim_id']))
        connection.execute('UPDATE coordination_operations SET state=? WHERE operation_id=? AND claim_id=?',
                           ('quarantined' if unknown else 'abandoned', operation['operation_id'], operation['claim_id']))
        return not unknown

    @staticmethod
    def _handle(row):
        try:
            items = json.loads(row['resources_json'])
            if not isinstance(items, list) or not items or len(items) > 1024:
                raise ValueError('Invalid stored resource set')
            tokens = tuple(ResourceToken(key, fence) for key, fence in items)
            if any(not isinstance(token.key, str) or not token.key or type(token.fence) is not int
                   or not 0 < token.fence < 2 ** 63 for token in tokens) or len({t.key for t in tokens}) != len(tokens):
                raise ValueError('Invalid stored fence')
            return ClaimHandle(row['owner_generation'], row['operation_id'], row['claim_id'], tokens)
        except (ValueError, TypeError) as error:
            raise StorageUnavailable('Stored claim is malformed') from error

    def claim(self, operation_id, resources=(), *, capacity=None):
        """Atomically reserve the whole resource set plus fixed capacity slots.

        Operation IDs are durable tombstones, including after confirmed death.
        Duplicate calls never authorize another execution; callers must inspect
        acquired, not merely the existence of a handle.
        """
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200:
            raise ValueError('A bounded operation ID is required')
        if isinstance(resources, str):
            raise ValueError('Resources must be a collection, not a string')
        resources = list(resources)
        if any(not isinstance(key, str) or not key or len(key) > 512 for key in resources):
            raise ValueError('Resource keys must be bounded nonempty strings')
        resources = sorted(set(resources))
        capacity = dict(capacity or {})
        if any(pool not in self.capacities or type(count) is not int or not 1 <= count <= self.capacities[pool]
               for pool, count in capacity.items()):
            raise ValueError('Unknown capacity pool or invalid slot count')
        if not resources and not capacity or len(resources) + sum(capacity.values()) > 1024:
            raise ValueError('Admission must request between 1 and 1024 resources')
        fingerprint = hashlib.sha256(_json([resources, capacity]).encode()).hexdigest()
        result = None
        with self._transaction() as connection:
            self._registered(connection, accepting=True)
            existing = connection.execute('SELECT * FROM coordination_operations WHERE operation_id=?',
                                          (operation_id,)).fetchone()
            if existing is not None:
                if existing['fingerprint'] != fingerprint:
                    raise OperationConflict('Operation ID already has different resource requirements')
                handle = self._handle(existing) if existing['owner_generation'] == self.owner.generation \
                    and existing['state'] in ('active', 'draining') else None
                return Admission(False, handle, existing['state'])
            keys = ['resource:' + key for key in resources]
            available = all([self._reclaim_dead(connection, key) for key in keys])
            for pool, count in sorted(capacity.items()):
                free = []
                for slot in range(self.capacities[pool]):
                    key = 'capacity:' + _json([pool, slot])
                    if self._reclaim_dead(connection, key):
                        free.append(key)
                if len(free) < count:
                    available = False
                keys.extend(free[:count])
            if available:
                claim_id = uuid.uuid4().hex
                tokens = []
                for key in keys:
                    row = connection.execute('SELECT fence FROM resource_leases WHERE resource_key=?', (key,)).fetchone()
                    tokens.append(ResourceToken(key, row['fence'] + 1 if row else 1))
                serialized = _json([[token.key, token.fence] for token in tokens])
                connection.execute('INSERT INTO coordination_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                                   (operation_id, fingerprint, self.owner.generation, claim_id, serialized, 'active', 0))
                for token in tokens:
                    connection.execute('INSERT INTO resource_leases VALUES (?, ?, ?, ?, ?, ?) '
                                       'ON CONFLICT(resource_key) DO UPDATE SET owner_generation=excluded.owner_generation, '
                                       'operation_id=excluded.operation_id, claim_id=excluded.claim_id, '
                                       'fence=excluded.fence, state=excluded.state',
                                       (token.key, self.owner.generation, operation_id, claim_id, token.fence, 'active'))
                result = Admission(True, ClaimHandle(self.owner.generation, operation_id, claim_id, tuple(tokens)), 'active')
        # Commit death/quarantine observations even when admission was busy.
        if result is None:
            raise Busy('Required resources or capacity remain occupied')
        return result

    def _validate_claim(self, connection, handle):
        self._registered(connection)
        if not isinstance(handle, ClaimHandle) or handle.owner_generation != self.owner.generation:
            raise StaleClaim('Claim belongs to a different owner')
        operation = connection.execute('SELECT * FROM coordination_operations WHERE operation_id=?',
                                       (handle.operation_id,)).fetchone()
        if operation is None or operation['state'] not in ('active', 'draining') \
                or self._handle(operation) != handle:
            raise StaleClaim('Claim is no longer current')
        for token in handle.resources:
            row = connection.execute('SELECT * FROM resource_leases WHERE resource_key=?', (token.key,)).fetchone()
            if row is None or (row['owner_generation'], row['operation_id'], row['claim_id'], row['fence'], row['state']) != (
                handle.owner_generation, handle.operation_id, handle.claim_id, token.fence, operation['state']
            ):
                raise StaleClaim('Resource fence is no longer current')
        return operation

    def mark_side_effects(self, handle):
        """MUST commit before any model, child process, tool, or external write."""
        with self._transaction() as connection:
            self._validate_claim(connection, handle)
            connection.execute('UPDATE coordination_operations SET sideeffects_possible=1 '
                               'WHERE operation_id=? AND claim_id=?', (handle.operation_id, handle.claim_id))

    def assert_owned(self, handle):
        """Validate before local preparation; does not mark external side effects."""
        with self._transaction() as connection:
            self._validate_claim(connection, handle)

    def _change_state(self, handle, state):
        with self._transaction() as connection:
            self._validate_claim(connection, handle)
            connection.execute('UPDATE coordination_operations SET state=? WHERE operation_id=? AND claim_id=?',
                               (state, handle.operation_id, handle.claim_id))
            for token in handle.resources:
                changed = connection.execute('UPDATE resource_leases SET state=? WHERE resource_key=? '
                                             'AND owner_generation=? AND claim_id=? AND fence=?',
                                             (state, token.key, handle.owner_generation, handle.claim_id, token.fence))
                if changed.rowcount != 1:
                    raise StaleClaim('Resource ownership changed')

    def mark_draining(self, handle):
        self._change_state(handle, 'draining')

    def quarantine(self, handle):
        """Unknown external outcome: retain resources pending explicit resolution."""
        self._change_state(handle, 'quarantined')

    def release(self, handle, *, work_completed):
        """Caller attests ACTUAL work ended, not just asyncio wrapper cancellation."""
        if work_completed is not True:
            raise CoordinationError('Release requires confirmed actual work completion')
        self._change_state(handle, 'released')

"""Conservative OS identity probes; no heartbeat or floating-point timestamps.

Windows creation time and exit state are read through the SAME process handle.
An unavailable probe is UNKNOWN, never evidence that an owner has died.
"""
import ctypes
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Liveness(Enum):
    ALIVE = 'alive'
    DEAD = 'dead'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_time: str


def coordination_domain():
    """Bind PID observations to the same machine and process namespace.

    Namespace changes require explicit stopped-worker maintenance, never an
    implicit domain reset. Raw process identity separately distinguishes boots.
    """
    if sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            machine, _ = winreg.QueryValueEx(key, 'MachineGuid')
        value = 'windows:' + machine + ':host-pid-namespace'
    elif sys.platform.startswith('linux'):
        machine = Path('/etc/machine-id').read_text().strip()
        namespace = os.readlink('/proc/self/ns/pid')
        # Prove /proc belongs to our own PID namespace using only our own entry:
        # a foreign procfs numbers this process differently. Reading init's
        # (/proc/1) namespace needs ptrace access that a non-root service lacks.
        pid = os.getpid()
        if (not namespace or os.readlink('/proc/self') != str(pid)
                or os.readlink(f'/proc/{pid}/ns/pid') != namespace):
            raise RuntimeError('The proc filesystem does not expose the current PID namespace')
        value = ':'.join(['linux', machine, namespace])
    else:
        raise RuntimeError('Unsupported process namespace')
    if not machine:
        raise RuntimeError('Cannot establish machine identity')
    return hashlib.sha256(value.encode()).hexdigest()


def _inspect_windows(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        # ERROR_INVALID_PARAMETER for a positive PID means no such process.
        # Access denied and all other errors remain ambiguous.
        state = Liveness.DEAD if ctypes.get_last_error() == 87 else Liveness.UNKNOWN
        return state, None
    try:
        creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return Liveness.UNKNOWN, None
        raw = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        waited = kernel.WaitForSingleObject(handle, 0)
        if waited == 0:  # WAIT_OBJECT_0: signaled process handle
            return Liveness.DEAD, raw
        if waited == 258:  # WAIT_TIMEOUT: still executing
            return Liveness.ALIVE, raw
        return Liveness.UNKNOWN, None
    finally:
        kernel.CloseHandle(handle)


def _inspect_linux(pid):
    # /proc starttime is an integer clock-tick count; boot ID prevents reuse
    # across reboot. The state and starttime come from one stat snapshot.
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except FileNotFoundError:
        # hidepid mounts can hide a live PID even in this process namespace.
        # Signal zero checks existence without delivering a signal. Success or
        # denial cannot supply the missing raw identity, so remains UNKNOWN.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return Liveness.DEAD, None
        except OSError:
            pass
        return Liveness.UNKNOWN, None
    fields = stat[stat.rindex(')') + 2:].split()
    raw = boot + ':' + fields[19]
    return (Liveness.DEAD if fields[0] in ('Z', 'X') else Liveness.ALIVE), raw


def _inspect(pid):
    if type(pid) is not int or pid <= 0 or pid > 0xFFFFFFFF:
        return Liveness.UNKNOWN, None
    if sys.platform == 'win32':
        return _inspect_windows(pid)
    if sys.platform.startswith('linux'):
        return _inspect_linux(pid)
    return Liveness.UNKNOWN, None


def current_identity():
    """Fail closed if this process cannot establish its own exact OS identity."""
    state, creation = _inspect(os.getpid())
    if state is not Liveness.ALIVE or not creation:
        raise RuntimeError('Cannot establish current OS process identity')
    return ProcessIdentity(os.getpid(), creation)


def probe(identity):
    try:
        raw = identity.creation_time
        if not isinstance(raw, str):
            return Liveness.UNKNOWN
        if sys.platform == 'win32':
            if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2 ** 64 or str(int(raw)) != raw:
                return Liveness.UNKNOWN
        elif sys.platform.startswith('linux'):
            boot, ticks = raw.split(':')
            if str(uuid.UUID(boot)) != boot or not ticks.isascii() or not ticks.isdecimal() \
                    or not 0 <= int(ticks) < 2 ** 64 or str(int(ticks)) != ticks:
                return Liveness.UNKNOWN
        else:
            return Liveness.UNKNOWN
        state, creation = _inspect(identity.pid)
        if state is Liveness.UNKNOWN:
            return state
        if state is Liveness.DEAD or creation != identity.creation_time:
            return Liveness.DEAD
        return Liveness.ALIVE
    except Exception:
        return Liveness.UNKNOWN

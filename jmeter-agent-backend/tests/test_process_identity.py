"""Linux coordination domain must be provable by an unprivileged service account."""
import pytest

from app.services import process_identity

OWN_NAMESPACE = 'pid:[4026531836]'


def _simulate_linux(monkeypatch, *, procfs_self, links):
    monkeypatch.setattr(process_identity.sys, 'platform', 'linux')
    monkeypatch.setattr(process_identity.os, 'getpid', lambda: 4242)

    def readlink(path):
        path = str(path).replace('\\', '/')
        if path == '/proc/1/ns/pid':
            # Non-root users cannot inspect init's namespace (ptrace access check).
            raise PermissionError(13, 'Permission denied', path)
        if path == '/proc/self':
            return procfs_self
        return links[path]

    original_read_text = process_identity.Path.read_text
    def read_text(self, *args, **kwargs):
        if self.as_posix() == '/etc/machine-id':
            return 'fixture-machine\n'
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(process_identity.os, 'readlink', readlink)
    monkeypatch.setattr(process_identity.Path, 'read_text', read_text)


def test_unprivileged_linux_process_establishes_its_domain(monkeypatch):
    _simulate_linux(monkeypatch, procfs_self='4242', links={
        '/proc/self/ns/pid': OWN_NAMESPACE, '/proc/4242/ns/pid': OWN_NAMESPACE})
    first = process_identity.coordination_domain()
    assert len(first) == 64 and first == process_identity.coordination_domain()


def test_procfs_from_another_pid_namespace_is_still_refused(monkeypatch):
    # /proc mounted from an outer namespace numbers this process differently.
    _simulate_linux(monkeypatch, procfs_self='98765', links={
        '/proc/self/ns/pid': OWN_NAMESPACE, '/proc/4242/ns/pid': 'pid:[4026532001]'})
    with pytest.raises(RuntimeError):
        process_identity.coordination_domain()

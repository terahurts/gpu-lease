"""An exclusive lock the operating system releases when the holder dies.

Everything else in this package rests on this: a lease is a kernel object, not
a file containing a PID, so a crashed proxy (or a killed terminal) cannot leave
the GPU locked until someone notices.

Windows : named mutex (CreateMutexW / WaitForSingleObject). Win32 mutexes are
          thread-affine, so every wait and release is funnelled through one
          agent thread that keeps the handle.
POSIX   : flock(LOCK_EX|LOCK_NB) on a file under the temp directory, released
          by the kernel when the process exits.

One object may be used from many threads: an in-process gate queues them, and
only one thread at a time ever reaches the OS lock, which matters because a
Win32 mutex (and flock on a shared fd) treats a second acquisition by the same
process as success rather than as contention.
"""

from __future__ import annotations

import os
import queue
import threading
import time

__all__ = ["ExclusiveLock", "LockError"]

WAIT_OBJECT_0 = 0x0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102

if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    _k32.ReleaseMutex.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _INFINITE = 0xFFFFFFFF


class LockError(RuntimeError):
    """The lock could not be created or the agent thread failed."""


class _MutexAgent(threading.Thread):
    """Keeps one mutex handle and does all waiting/releasing on one thread.

    A Win32 mutex belongs to the thread that waited on it; ReleaseMutex from
    another thread fails with ERROR_NOT_OWNER. Leases are granted on request
    threads but released by watcher threads (an image job releases long after
    /prompt returned), so ownership is pinned to this thread.
    """

    def __init__(self, name):
        super().__init__(daemon=True, name="gpu-lease-mutex")
        self.name = name
        self.jobs = queue.Queue()
        self.handle = None
        self.start()

    def _call(self, op, timeout=None):
        box, done = {}, threading.Event()
        self.jobs.put((op, timeout, box, done))
        budget = None if timeout is None else max(30.0, timeout + 30)
        if not done.wait(budget):
            raise LockError("mutex agent did not answer")
        if "err" in box:
            raise box["err"]
        return box.get("result")

    def acquire(self, timeout):
        return self._call("acquire", timeout)

    def release(self):
        return self._call("release")

    def run(self):
        while True:
            op, timeout, box, done = self.jobs.get()
            try:
                if op == "acquire":
                    if self.handle is None:
                        handle = _k32.CreateMutexW(None, False, self.name)
                        if not handle:
                            raise LockError(
                                "CreateMutexW(%r) failed: %s"
                                % (self.name, ctypes.get_last_error()))
                        self.handle = handle
                    wait = _INFINITE if timeout is None else int(timeout * 1000)
                    rc = _k32.WaitForSingleObject(self.handle, wait)
                    if rc in (WAIT_OBJECT_0, WAIT_ABANDONED):
                        box["result"] = True
                    elif rc == WAIT_TIMEOUT:
                        _k32.CloseHandle(self.handle)
                        self.handle = None
                        box["result"] = False
                    else:
                        raise LockError("WaitForSingleObject returned 0x%x" % rc)
                else:
                    if self.handle is not None:
                        _k32.ReleaseMutex(self.handle)
                        _k32.CloseHandle(self.handle)
                        self.handle = None
                    box["result"] = True
            except BaseException as exc:  # noqa: BLE001 - hand it to the caller
                box["err"] = exc
            finally:
                done.set()


class ExclusiveLock:
    """One named lock, shared across threads and processes.

    ``acquire(timeout)``: ``None`` waits forever, ``0`` tries once. Returns True
    when the lock is held. ``release()`` is idempotent.

    The name is what other processes see. On Windows, prefix ``Global\\`` to
    cross logon sessions.
    """

    def __init__(self, name: str = "gpu-lease"):
        self.name = name
        self._gate = threading.Condition()
        self._busy = False          # a thread of ours is waiting for or holding
        self._owned = False
        self._agent = None
        self._fd = None
        if os.name == "nt":
            self._agent = _MutexAgent(name)
        else:
            self._path = _posix_path(name)

    # -- os layer ----------------------------------------------------------
    def _os_acquire(self, timeout):
        if os.name == "nt":
            return bool(self._agent.acquire(timeout))
        import fcntl

        if self._fd is None:
            self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o666)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                time.sleep(0.001 if timeout == 0 else 0.05)

    def _os_release(self):
        if os.name == "nt":
            self._agent.release()
            return
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)

    # -- api ---------------------------------------------------------------
    def acquire(self, timeout=None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._gate:
            while self._busy:                 # another thread of ours is inside
                if deadline is None:
                    self._gate.wait(0.5)
                    continue
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._gate.wait(min(left, 0.5))
            self._busy = True
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            got = self._os_acquire(left)
        except BaseException:
            self._finish(False)
            raise
        if got:
            with self._gate:
                self._owned = True
            return True
        self._finish(False)
        return False

    def release(self) -> None:
        with self._gate:
            if not self._owned:
                return
            self._os_release()
            self._owned = False
            self._busy = False
            self._gate.notify_all()

    def _finish(self, owned):
        with self._gate:
            self._owned = owned
            self._busy = False
            self._gate.notify_all()

    @property
    def held(self) -> bool:
        return self._owned

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<ExclusiveLock %r held=%s>" % (self.name, self._owned)


def _posix_path(name: str) -> str:
    import tempfile

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return os.path.join(tempfile.gettempdir(), "gpu-lease-%s.lock" % safe)

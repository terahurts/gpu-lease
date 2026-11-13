"""One lease, many claimants: FIFO, refcounted, priorities, two policies.

The queue lives in-process so it can be fair and observable; the truth is the
OS lock underneath it, so a second proxy process pointed at the same lock name
also takes turns instead of loading a model on top of yours.
"""

from __future__ import annotations

import threading
import time

from .lock import ExclusiveLock

__all__ = ["Lease", "LeaseManager", "LeaseTimeout"]


class LeaseTimeout(RuntimeError):
    """Nobody gave way in time; answer busy instead of queueing forever."""


class Lease:
    """One turn on the GPU. Refcounted, because two replies may stream at once."""

    def __init__(self, manager, owner, waited):
        self._manager = manager
        self.owner = owner
        self.waited = waited
        self.since = time.time()
        self._done = False

    def release(self):
        if self._done:
            return
        self._done = True
        self._manager.release(self.owner)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.release()


class LeaseManager:
    """Turns requests into turns.

    ``mode="queue"``  wait up to ``timeout`` seconds, fair by arrival.
    ``mode="reject"`` never wait: whoever holds the GPU makes you fail at once.
    ``priority``      lower runs first; equal priorities stay FIFO. Use it to
                      let an interactive chat jump a queued batch render.
    """

    def __init__(self, name="gpu-lease", timeout=1800.0, mode="queue", lock=None):
        if mode not in ("queue", "reject"):
            raise ValueError("mode must be 'queue' or 'reject', not %r" % (mode,))
        self.name = name
        self.timeout = timeout
        self.mode = mode
        self.cond = threading.Condition()
        self.owner = None
        self.count = 0
        self.since = None
        self._next_ticket = 0
        self._queue = []          # [(priority, ticket)], earliest first
        self.lock = lock or ExclusiveLock(name)

    # -- state -------------------------------------------------------------
    def status(self):
        with self.cond:
            return {
                "lock": self.name,
                "mode": self.mode,
                "owner": self.owner,
                "count": self.count,
                "waiting": len(self._queue),
                "since": self.since,
                "held_for": None if self.since is None else round(time.time() - self.since, 1),
            }

    def held_by(self):
        with self.cond:
            return self.owner

    def _front(self):
        return min(self._queue) if self._queue else None

    def _at_front(self, key):
        return self._front() is None or self._front() == key

    # -- the turn ----------------------------------------------------------
    def acquire(self, owner, priority=0, timeout=None, on_wait=None,
                before_run=None):
        """Block until ``owner`` may use the GPU, then run ``before_run``.

        ``before_run(lease)`` is where eviction and model loading happen, and
        it runs with the lock already held, so no other side can grab the card
        between "freed" and "loading".
        """
        timeout = self.timeout if timeout is None else timeout
        if self.mode == "reject":
            timeout = 0.0
        with self.cond:
            key = (priority, self._next_ticket)
            self._next_ticket += 1
            self._queue.append(key)
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        lease = None
        try:
            while lease is None:
                with self.cond:
                    at_front = self._at_front(key)
                    if at_front and self.owner == owner:   # same side: join it
                        self.count += 1
                        self._queue.remove(key)
                        lease = Lease(self, owner, time.monotonic() - started)
                        break
                    left = deadline - time.monotonic()
                if at_front:
                    # 1 ms when refusing at once, short otherwise so the queue
                    # is re-checked rather than slept through
                    attempt = 0.001 if left <= 0 else min(left, 2.0)
                    if self.lock.acquire(attempt):
                        with self.cond:
                            if self.owner is None and self._at_front(key):
                                self.owner, self.count = owner, 1
                                self.since = time.time()
                                self._queue.remove(key)
                                self.cond.notify_all()
                                lease = Lease(self, owner,
                                              time.monotonic() - started)
                                break
                        self.lock.release()
                    if time.monotonic() >= deadline:
                        raise LeaseTimeout(
                            "GPU busy (%s) and the queue timeout is 0"
                            % (self.owner or "another process"))
                    continue
                if left <= 0:
                    raise LeaseTimeout(
                        "waited %.0fs for the GPU; %s still holds it"
                        % (time.monotonic() - started, self.owner or "a loader"))
                with self.cond:
                    self.cond.wait(min(left, 0.5))
                if on_wait:
                    on_wait(time.monotonic() - started)
        except BaseException:
            with self.cond:
                if key in self._queue:
                    self._queue.remove(key)
                self.cond.notify_all()
            raise
        if on_wait:
            on_wait(lease.waited)
        if before_run is not None:
            try:
                before_run(lease)
            except BaseException:
                lease.release()
                raise
        return lease

    def release(self, owner):
        with self.cond:
            if self.owner != owner or self.count <= 0:
                return
            self.count -= 1
            if self.count > 0:
                self.cond.notify_all()
                return
            self.owner, self.since = None, None
            self.cond.notify_all()
        self.lock.release()

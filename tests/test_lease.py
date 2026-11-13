import threading
import time
import unittest
import uuid

from gpu_lease.lease import LeaseManager, LeaseTimeout


def name():
    return "gpu-lease-test-%s" % uuid.uuid4().hex[:8]


class TestLeaseManager(unittest.TestCase):
    def make(self, **kw):
        kw.setdefault("timeout", 5.0)
        return LeaseManager(name(), **kw)

    def test_one_holder_at_a_time(self):
        manager = self.make()
        inside, overlap = [], []

        def worker(tag):
            with manager.acquire(tag):
                inside.append(tag)
                if len(inside) > 1:
                    overlap.append(list(inside))
                time.sleep(0.05)
                inside.remove(tag)

        threads = [threading.Thread(target=worker, args=("t%d" % i,))
                   for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(overlap, [])

    def test_arrival_order_is_fair(self):
        manager = self.make()
        order = []

        with manager.acquire("first"):
            threads = []
            for tag in ("a", "b", "c"):
                thread = threading.Thread(
                    target=lambda t: (with_lease(manager, t, order)),
                    args=(tag,))
                thread.start()
                threads.append(thread)
                time.sleep(0.05)          # fix arrival order
            time.sleep(0.1)
        for thread in threads:
            thread.join(10)
        self.assertEqual(order, ["a", "b", "c"])

    def test_same_owner_joins_instead_of_queueing(self):
        manager = self.make()
        with manager.acquire("llm"):
            with manager.acquire("llm"):
                self.assertEqual(manager.status()["count"], 2)
            self.assertEqual(manager.status()["count"], 1)
        self.assertIsNone(manager.status()["owner"])

    def test_reject_mode_fails_at_once(self):
        manager = self.make(mode="reject")
        manager.timeout = 30
        with manager.acquire("llm"):
            started = time.monotonic()
            with self.assertRaises(LeaseTimeout):
                manager.acquire("comfy")
            self.assertLess(time.monotonic() - started, 1.0)

    def test_queue_timeout(self):
        manager = self.make(timeout=0.4)
        with manager.acquire("llm"):
            started = time.monotonic()
            with self.assertRaises(LeaseTimeout):
                manager.acquire("comfy")
            self.assertGreaterEqual(time.monotonic() - started, 0.35)

    def test_priority_jumps_the_queue(self):
        manager = self.make()
        order = []
        with manager.acquire("holder"):
            backlog = threading.Thread(
                target=lambda: with_lease(manager, "batch", order, priority=10))
            backlog.start()
            time.sleep(0.05)
            chat = threading.Thread(
                target=lambda: with_lease(manager, "chat", order, priority=0))
            chat.start()
            time.sleep(0.05)
        backlog.join(10)
        chat.join(10)
        self.assertEqual(order, ["chat", "batch"])

    def test_before_run_runs_with_the_lock_held_and_can_abort(self):
        manager = self.make()
        seen = []

        def prepare(lease):
            seen.append((lease.owner, manager.status()["owner"]))

        with manager.acquire("llm", before_run=prepare):
            pass
        self.assertEqual(seen, [("llm", "llm")])

        def explode(_lease):
            raise RuntimeError("model failed to load")

        with self.assertRaises(RuntimeError):
            manager.acquire("llm", before_run=explode)
        self.assertIsNone(manager.status()["owner"])   # no leaked lease


def with_lease(manager, tag, order, priority=0):
    with manager.acquire(tag, priority=priority):
        order.append(tag)
        time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()

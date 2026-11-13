import os
import subprocess
import threading
import sys
import time
import unittest
import uuid

from gpu_lease.lock import ExclusiveLock, LockError

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


class TestExclusiveLock(unittest.TestCase):
    def setUp(self):
        self.name = "gpu-lease-test-%s" % uuid.uuid4().hex[:8]

    def test_acquire_release_round_trip(self):
        lock = ExclusiveLock(self.name)
        self.assertTrue(lock.acquire(0))
        self.assertTrue(lock.held)
        lock.release()
        self.assertFalse(lock.held)
        lock.release()                      # idempotent
        self.assertTrue(lock.acquire(0))

    def test_second_local_acquire_waits_for_the_first(self):
        """Two threads, one object: the second must not slip through the OS
        lock (a Win32 mutex and a shared flock both look "already ours")."""
        lock = ExclusiveLock(self.name)
        self.assertTrue(lock.acquire(0))
        self.assertFalse(lock.acquire(0))       # same object: no barging in
        acquired = []

        def contender():
            acquired.append(lock.acquire(1.0))

        thread = threading.Thread(target=contender)
        thread.start()
        time.sleep(0.1)
        lock.release()
        thread.join(5)
        self.assertEqual(acquired, [True])
        self.assertTrue(lock.held)              # the waiter now owns it
        lock.release()

    def test_second_process_waits_for_us(self):
        """The kernel, not a PID file, is what makes this safe."""
        script = (
            "import sys, time\n"
            "from gpu_lease.lock import ExclusiveLock\n"
            "lock = ExclusiveLock(sys.argv[1])\n"
            "assert lock.acquire(None)\n"
            "print('held', flush=True)\n"
            "time.sleep(1.5)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.Popen([sys.executable, "-c", script, self.name],
                                 stdout=subprocess.PIPE, text=True, env=env)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            lock = ExclusiveLock(self.name)
            started = time.monotonic()
            self.assertFalse(lock.acquire(0.3))
            self.assertGreaterEqual(time.monotonic() - started, 0.25)
            self.assertEqual(child.wait(timeout=10), 0)
            self.assertTrue(lock.acquire(2.0))
            lock.release()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_lock_survives_its_holder_crashing(self):
        """A killed process must not leave the GPU locked for the day."""
        script = (
            "import sys, time\n"
            "from gpu_lease.lock import ExclusiveLock\n"
            "lock = ExclusiveLock(sys.argv[1])\n"
            "assert lock.acquire(None)\n"
            "print('held', flush=True)\n"
            "time.sleep(60)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.Popen([sys.executable, "-c", script, self.name],
                                 stdout=subprocess.PIPE, text=True, env=env)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            lock = ExclusiveLock(self.name)
            self.assertFalse(lock.acquire(0.2))
            child.kill()
            child.wait(timeout=10)
            self.assertTrue(lock.acquire(5.0))
            lock.release()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

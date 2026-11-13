"""gpu-lease: several VRAM-hungry servers, one GPU, no OOM races.

Point your clients at this instead of at the servers, and the servers take
turns. Nothing on either side needs to know the other exists.
"""

from __future__ import annotations

import threading

from .backend import KINDS, Backend, Hook, Watch, build
from .config import Config, ConfigError, LeaseConfig, LogConfig, from_text, load
from .lease import Lease, LeaseManager, LeaseTimeout
from .lock import ExclusiveLock, LockError
from .proxy import Engine

__version__ = "0.2.0"

__all__ = [
    "lease", "Engine", "Config", "ConfigError", "LeaseConfig", "LogConfig",
    "load", "from_text", "LeaseManager", "Lease", "LeaseTimeout",
    "ExclusiveLock", "LockError", "Backend", "Hook", "Watch", "build", "KINDS",
    "__version__",
]

_managers = {}
_guard = threading.Lock()


def lease(owner, name="gpu-lease", timeout=1800.0, mode="queue"):
    """Take a turn in-process, sharing the lock with a running proxy.

        with gpu_lease.lease("llm", timeout=600):
            requests.post(...)

    Same fairness as the proxy (arrival order, refcounted per owner), so a
    script and a browser-driven session never load a model at the same time.
    """
    with _guard:
        manager = _managers.get(name)
        if manager is None:
            manager = _managers[name] = LeaseManager(name, timeout=timeout,
                                                     mode=mode)
    return manager.acquire(owner)

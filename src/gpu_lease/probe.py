"""Small probes: JSON over HTTP, and how full the card is."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request

__all__ = ["http_json", "vram", "wait_vram_below", "HttpResult"]

HttpResult = "tuple[int|None, object]"


def http_json(url, body=None, method=None, timeout=60.0, headers=None):
    """(status, decoded body). Transport failures return (None, {...}).

    A backend that is not up yet is not an error for a gate: it is a reason to
    wait, so this never raises for connection problems.
    """
    data = None
    head = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        head.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, (json.loads(raw) if raw.strip() else {})
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw[:400]}
    except (urllib.error.URLError, OSError, ValueError):
        return None, {}


def vram():
    """(used_mib, total_mib) from nvidia-smi, or (None, None) if unavailable."""
    if not shutil.which("nvidia-smi"):
        return None, None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        used, total = (int(v) for v in out[0].split(","))
        return used, total
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None


def wait_vram_below(ceiling_mib, timeout=60.0, poll=0.25):
    """Block until used VRAM <= ceiling. Windows frees lazily, so this matters.

    Returns the last reading. Raises TimeoutError when something outside the
    gate keeps the card full, which is worth failing loudly rather than
    loading into an OOM.
    """
    deadline = time.monotonic() + timeout
    used = None
    while True:
        used, _total = vram()
        if used is None or used <= ceiling_mib:
            return used
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "VRAM still %s MiB after %.0fs, wanted <= %d MiB - something "
                "outside gpu-lease is holding the card" % (used, timeout, ceiling_mib))
        time.sleep(poll)

"""Read a TOML file into backends, a lease policy and a logging level."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "gpu-lease needs Python 3.11+ or the 'tomli' package on 3.9/3.10"
        ) from exc

from .backend import Backend, Hook, Watch, build

__all__ = ["Config", "LeaseConfig", "LogConfig", "load", "from_text", "EXAMPLE"]

BOUND_KEYS = {"name", "kind", "listen", "upstream", "priority", "token",
              "health", "expect_mib", "passthrough_log", "gated_last",
              "gated_suffix", "gated_paths", "hold"}
TABLES = {"evict": "evict", "prepare": "prepare", "watch": "watch"}
SCALAR_TABLES = {"idle"}   # parsed by hand: not a Hook/Watch


class ConfigError(ValueError):
    pass


@dataclass
class LeaseConfig:
    name: str = "gpu-lease"
    queue_timeout: float = 1800.0
    mode: str = "queue"            # queue | reject
    early_headers_after: float = 20.0
    keepalive: float = 10.0
    grace: float = 180.0           # backend boot grace, seconds
    vram_ceiling: int = 0          # MiB; 0 disables the post-eviction check
    heartbeat_status: bool = True  # expose GET /__lease/status on every port


@dataclass
class LogConfig:
    level: str = "info"            # quiet | info | debug
    file: str = ""


@dataclass
class Config:
    lease: LeaseConfig = field(default_factory=LeaseConfig)
    logging: LogConfig = field(default_factory=LogConfig)
    backends: list = field(default_factory=list)

    def by_name(self, name):
        for backend in self.backends:
            if backend.name == name:
                return backend
        raise KeyError(name)

    def names(self):
        return [b.name for b in self.backends]


def load(path):
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError("no config file at %s" % path)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("%s is not valid TOML: %s" % (path, exc))
    return from_text(doc, source=os.path.basename(path))


def from_text(doc, source="<config>"):
    if not isinstance(doc, dict):
        raise ConfigError("%s: the document must be a table" % source)
    lease = _lease(doc.get("lease") or {}, source)
    logging = _logging(doc.get("logging") or {}, source)
    rows = doc.get("backend") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        raise ConfigError("%s: at least one [[backend]] is required" % source)
    backends = [_backend(row, source, i) for i, row in enumerate(rows, 1)]
    seen_names, seen_listen = set(), set()
    for backend in backends:
        if backend.name in seen_names:
            raise ConfigError("%s: duplicate backend name %r"
                              % (source, backend.name))
        seen_names.add(backend.name)
        if backend.listen in seen_listen:
            raise ConfigError("%s: %s and another backend listen on %s:%d"
                              % (source, backend.name, *backend.listen))
        seen_listen.add(backend.listen)
    return Config(lease=lease, logging=logging, backends=backends)


def _lease(row, source):
    known = set(LeaseConfig.__dataclass_fields__)
    _reject_unknown(row, known, source, "lease")
    cfg = LeaseConfig(**{k: v for k, v in row.items() if k in known})
    if cfg.mode not in ("queue", "reject"):
        raise ConfigError("%s: lease.mode must be queue or reject, not %r"
                          % (source, cfg.mode))
    if cfg.queue_timeout < 0:
        raise ConfigError("%s: lease.queue_timeout must be >= 0" % source)
    return cfg


def _logging(row, source):
    known = set(LogConfig.__dataclass_fields__)
    _reject_unknown(row, known, source, "logging")
    cfg = LogConfig(**{k: v for k, v in row.items() if k in known})
    if cfg.level not in ("quiet", "info", "debug"):
        raise ConfigError("%s: logging.level must be quiet, info or debug"
                          % source)
    return cfg


def _backend(row, source, index):
    label = "%s: backend #%d" % (source, index)
    name = row.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError("%s needs a name" % label)
    if "listen" not in row:
        raise ConfigError("%s (%s) needs a listen address" % (label, name))
    if "upstream" not in row:
        raise ConfigError("%s (%s) needs an upstream URL" % (label, name))
    kind = row.get("kind", "generic")
    listen = _addr(row["listen"], "%s (%s).listen" % (label, name))
    upstream = str(row["upstream"]).rstrip("/")
    if not upstream.startswith(("http://", "https://")):
        raise ConfigError("%s (%s).upstream must be an http(s) URL"
                          % (label, name))
    overrides = {k: v for k, v in row.items()
                 if k in BOUND_KEYS - {"name", "kind", "listen", "upstream"}}
    for table, field in TABLES.items():
        if table in row:
            value = row[table]
            if not isinstance(value, dict):
                raise ConfigError("%s (%s).%s must be a table"
                                  % (label, name, table))
            overrides[field] = value
    idle = row.get("idle")
    if idle is not None:
        if not isinstance(idle, dict):
            raise ConfigError("%s (%s).idle must be a table" % (label, name))
        extra = set(idle) - {"free_after"}
        if extra:
            raise ConfigError("%s (%s).idle has unknown keys: %s"
                              % (label, name, ", ".join(sorted(extra))))
        try:
            overrides["idle_free_after"] = int(idle.get("free_after", 0))
        except (TypeError, ValueError):
            raise ConfigError("%s (%s).idle.free_after must be a whole "
                              "number of seconds" % (label, name))
    unknown = set(row) - BOUND_KEYS - set(TABLES) - SCALAR_TABLES
    if unknown:
        raise ConfigError("%s (%s) has unknown keys: %s"
                          % (label, name, ", ".join(sorted(unknown))))
    try:
        return build(name, listen, upstream, kind, **overrides)
    except TypeError as exc:
        raise ConfigError("%s (%s): %s" % (label, name, exc))
    except (ValueError, KeyError) as exc:
        raise ConfigError("%s (%s): %s" % (label, name, exc))


def _addr(value, label):
    if isinstance(value, int):
        return ("127.0.0.1", value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return ("127.0.0.1", int(text))
        if text.startswith("["):            # [::1]:8080
            host, _, port = text.rpartition("]:")
            return (host.lstrip("["), int(port))
        host, sep, port = text.rpartition(":")
        if not sep or not port.isdigit():
            raise ConfigError("%s must be host:port, not %r" % (label, value))
        return (host or "127.0.0.1", int(port))
    raise ConfigError("%s must be host:port or a port number" % label)


def _reject_unknown(row, known, source, where):
    unknown = {k for k in row if k not in known}
    if unknown:
        raise ConfigError("%s: unknown key(s) in [%s]: %s"
                          % (source, where, ", ".join(sorted(unknown))))


def _hook(kind, **kw):
    return Hook(kind=kind, **kw)


__all__ += ["Hook", "Watch", "Backend", "_hook"]

EXAMPLE = """\
# gpu-lease - one GPU, two servers, no more OOM races.
# Client URLs stay put: the gate takes over the ports the clients already know.

[lease]
name = "gpu-lease"        # lock name; Global\\\\gpu-lease to cross logon sessions
queue_timeout = 1800      # seconds a turn may wait; 0 = refuse immediately
mode = "queue"            # queue | reject
early_headers_after = 20  # start the response + keepalives this early
keepalive = 10
grace = 180               # wait this long for a backend that is still booting
vram_ceiling = 0          # MiB to wait for after eviction (0 = off)

[logging]
level = "info"            # quiet | info | debug

[[backend]]
name = "llm"
kind = "llama"            # llama.cpp router: unload/load barrier + model status
listen = "127.0.0.1:6772"
upstream = "http://127.0.0.1:6872"

[[backend]]
name = "comfy"
kind = "comfyui"          # POST /free, then hold until /history knows the job
listen = "127.0.0.1:8188"
upstream = "http://127.0.0.1:8198"
watch = { timeout = 2700 }
"""

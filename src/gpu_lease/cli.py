"""gpu-lease command line: run, status, lock, doctor, init."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import __version__
from .backend import llama_models, llama_resident
from .config import EXAMPLE, ConfigError, load
from .lease import LeaseManager, LeaseTimeout
from .probe import http_json, vram
from .proxy import STATUS_PATH, Engine

EXIT_OK, EXIT_ERROR, EXIT_BUSY, EXIT_NOT_RUNNING, EXIT_TIMEOUT = 0, 1, 2, 3, 4


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return handler(args)
    except ConfigError as exc:
        print("gpu-lease: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_OK


def _parser():
    parser = argparse.ArgumentParser(
        prog="gpu-lease",
        description="Queue VRAM-hungry servers behind one GPU lease, as URLs.")
    parser.add_argument("--version", action="version",
                        version="gpu-lease %s" % __version__)
    subs = parser.add_subparsers(dest="command")

    run = subs.add_parser("run", help="serve the gate")
    run.add_argument("-c", "--config", default="gpu-lease.toml")
    run.add_argument("--host", help="override every listen address (e.g. 0.0.0.0)")
    run.add_argument("--level", choices=("quiet", "info", "debug"),
                    help="override logging.level")
    run.set_defaults(handler=cmd_run)

    status = subs.add_parser("status", help="who holds the GPU, and what is resident")
    status.add_argument("-c", "--config", default="gpu-lease.toml")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    lock = subs.add_parser("lock", help="run a command while holding the GPU")
    lock.add_argument("--name", default="gpu-lease")
    lock.add_argument("--owner", default="cli")
    lock.add_argument("--timeout", type=float, default=1800.0)
    lock.add_argument("cmd", nargs=argparse.REMAINDER)
    lock.set_defaults(handler=cmd_lock)

    doctor = subs.add_parser("doctor", help="check config, ports and backends")
    doctor.add_argument("-c", "--config", default="gpu-lease.toml")
    doctor.set_defaults(handler=cmd_doctor)

    init = subs.add_parser("init", help="write a starter config")
    init.add_argument("-o", "--output", default="gpu-lease.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=cmd_init)
    return parser


# ------------------------------------------------------------------ commands
def cmd_run(args):
    cfg = load(args.config)
    if args.level:
        cfg.logging.level = args.level
    engine = Engine(cfg, host=args.host, level=args.level)
    return engine.serve()


def cmd_status(args):
    cfg = load(args.config)
    doc, answered = None, None
    for backend in cfg.backends:
        url = "http://%s:%d%s" % (backend.listen[0] or "127.0.0.1",
                                  backend.listen[1], STATUS_PATH)
        code, body = http_json(url, timeout=5)
        if code == 200 and isinstance(body, dict):
            doc, answered = body, backend
            break
    if doc is None:
        print("gpu-lease: not running (no answer on %s)"
              % ", ".join("%s:%d" % b.listen for b in cfg.backends))
        return EXIT_NOT_RUNNING
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
        return EXIT_OK
    _print_status(cfg, doc)
    return EXIT_OK


def _print_status(cfg, doc):
    print("gpu-lease  %s  [%s, queue timeout %ss]"
          % (doc.get("lock") or cfg.lease.name,
             doc.get("mode") or cfg.lease.mode,
             int(cfg.lease.queue_timeout)))
    holder = doc.get("owner")
    if holder:
        print("  holder   %s (%d turn(s), %.1fs, %d waiting)"
              % (holder, doc.get("count", 1), doc.get("held_for") or 0.0,
                 doc.get("waiting", 0)))
    else:
        print("  holder   nobody (GPU free)")
    vram_doc = doc.get("vram") or {}
    if vram_doc.get("used_mib") is not None:
        print("  vram     %s / %s MiB"
              % (f"{vram_doc['used_mib']:,}", f"{vram_doc['total_mib']:,}"))
    for backend in (doc.get("backends") or []):
        print("  %-7s  http://%s  ->  %s  %s"
              % (backend["name"], backend["listen"], backend["upstream"],
                 _health(backend)))
    for backend in cfg.backends:
        if backend.kind == "llama":
            resident = llama_resident(backend.upstream)
            models = llama_models(backend.upstream)
            if models:
                print("  %-7s  %d preset(s), resident: %s"
                      % (backend.name, len(models),
                         ", ".join(resident) or "none"))


def _health(backend_doc):
    code, doc = http_json(backend_doc["upstream"] + "/", timeout=3)
    if code is None:
        return "not answering"
    return "ok"


def cmd_lock(args):
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("gpu-lease lock: give me a command after --", file=sys.stderr)
        return EXIT_ERROR
    manager = LeaseManager(args.name, timeout=args.timeout)
    try:
        with manager.acquire(args.owner, timeout=args.timeout):
            print("gpu-lease: holding the GPU for %s" % " ".join(cmd), flush=True)
            return subprocess.call(cmd)
    except LeaseTimeout as exc:
        print("gpu-lease: %s" % exc, file=sys.stderr)
        return EXIT_TIMEOUT


def cmd_doctor(args):
    problems = []
    cfg = load(args.config)
    print("config      %s" % os.path.abspath(args.config))
    print("lock        %r (%s)" % (cfg.lease.name,
                                   "mode " + cfg.lease.mode))
    used, total = vram()
    print("vram        %s" % ("not detectable (nvidia-smi missing)"
                              if used is None else "%s / %s MiB used"
                              % (f"{used:,}", f"{total:,}")))
    holder = _status_holder(cfg)
    print("gate        %s" % ("running, holder: %s" % (holder or "nobody")
                              if holder is not False else "not running"))
    for backend in cfg.backends:
        host, port = backend.listen
        free = _port_free(host or "127.0.0.1", port)
        code, _doc = http_json(backend.upstream + backend.health, timeout=5)
        upstream = ("unreachable" if code is None else "answers %s" % code)
        if not free:
            problems.append("%s: %s:%d is already in use (is the real server "
                            "still on that port?)" % (backend.name, host, port))
        if code is None:
            problems.append("%s: %s is not answering %s%s"
                            % (backend.name, backend.upstream, backend.upstream,
                               backend.health))
        print("  %-7s %-6s listen %-16s %-9s upstream %s"
              % (backend.name, backend.kind, "%s:%d" % (host, port),
                 "free" if free else "BUSY", upstream))
        if backend.gates("POST", "/probe/chat/completions"):
            print("          gated: POST %s"
                  % ", ".join(["/.../" + s for s in backend.gated_last] or
                              list(backend.gated_paths) or ["(none)"]))
    if not problems:
        print("no problems found")
        return EXIT_OK
    print("\n%d problem(s):" % len(problems))
    for line in problems:
        print("  - %s" % line)
    return EXIT_ERROR


def _status_holder(cfg):
    for backend in cfg.backends:
        url = "http://%s:%d%s" % (backend.listen[0] or "127.0.0.1",
                                  backend.listen[1], STATUS_PATH)
        code, body = http_json(url, timeout=3)
        if code == 200 and isinstance(body, dict):
            return body.get("owner") or "nobody"
    return False


def _port_free(host, port):
    import socket

    with socket.socket() as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def cmd_init(args):
    if os.path.exists(args.output) and not args.force:
        print("gpu-lease: %s exists (use --force to overwrite)" % args.output,
              file=sys.stderr)
        return EXIT_ERROR
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(EXAMPLE)
    print("wrote %s" % os.path.abspath(args.output))
    return EXIT_OK

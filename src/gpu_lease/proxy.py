"""The relay: a port the client already knows, forwarding to the real server.

A turn is:

    gated request  ->  take the lease  ->  evict the others  ->  prepare self
                   ->  forward and stream back  ->  release

Requests that cannot allocate VRAM are forwarded straight through, so client
polling, downloads and progress websockets never queue behind a generation.
"""

from __future__ import annotations

import http.client
import json
import select
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .backend import evict, job_id_from, prepare, watch_done
from .lease import LeaseManager, LeaseTimeout
from .probe import http_json, vram, wait_vram_below

__all__ = ["Engine", "Relay", "Handler"]

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
}
STATUS_PATH = "/__lease/status"


class Engine:
    """Owns the lease, the backends and the turn bookkeeping."""

    def __init__(self, cfg, host=None, level=None, out=None):
        self.cfg = cfg
        self.level = level or cfg.logging.level
        self.out = out
        if host:
            for backend in cfg.backends:
                backend.listen = (host, backend.listen[1])
        self.leases = LeaseManager(cfg.lease.name,
                                   timeout=cfg.lease.queue_timeout,
                                   mode=cfg.lease.mode)
        self.relays = []
        self._lock = threading.Lock()

    # -- logging -----------------------------------------------------------
    def log(self, message, level="info"):
        order = {"quiet": 0, "info": 1, "debug": 2}
        if order.get(level, 1) > order.get(self.level, 1):
            return
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        stream = self.out
        if stream is None:
            import sys
            stream = sys.stdout
        with self._lock:
            print(line, file=stream, flush=True)

    # -- the interesting part ---------------------------------------------
    def on_acquired(self, backend, request_body):
        """Runs with the lock held: nobody can take the card mid-way."""
        for other in self.cfg.backends:
            if other is backend or other.evict_hook is None:
                continue
            try:
                evict(other, log=lambda m: self.log(m))
            except Exception as exc:  # noqa: BLE001 - keep the turn going
                self.log("could not evict %s: %s" % (other.name, exc))
        try:
            prepare(backend, request_body, log=lambda m: self.log(m))
        except Exception as exc:
            raise RuntimeError("could not prepare %s: %s" % (backend.name, exc))
        if self.cfg.lease.vram_ceiling:
            try:
                used = wait_vram_below(self.cfg.lease.vram_ceiling, timeout=90)
                self.log("vram %s MiB after eviction" % used, "debug")
            except TimeoutError as exc:
                raise RuntimeError(str(exc))

    def watch_job(self, backend, job_id, lease):
        """Hold the lease past /prompt until the backend finishes that job."""
        started = time.monotonic()
        self.log("%s job %s holds the card" % (backend.name, str(job_id)[:8]))
        give_up = _queue_probe(backend) if backend.kind == "comfyui" else None
        done = False
        try:
            done = watch_done(backend.watch, backend.upstream, str(job_id),
                              give_up_seen=give_up,
                              log=lambda m: self.log(m, "debug"),
                              headers=_auth(backend))
        finally:
            lease.release()
            self.log("%s job %s %s after %.1fs"
                     % (backend.name, str(job_id)[:8],
                        "done" if done else "given up on",
                        time.monotonic() - started))

    def status(self):
        doc = self.leases.status()
        used, total = vram()
        doc["vram"] = {"used_mib": used, "total_mib": total}
        doc["backends"] = [
            {"name": b.name, "kind": b.kind, "listen": "%s:%d" % b.listen,
             "upstream": b.upstream, "priority": b.priority}
            for b in self.cfg.backends
        ]
        return doc

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Bind and serve in background threads (also handy when embedded)."""
        for backend in self.cfg.backends:
            self.relays.append(Relay(backend, self))
        for relay in self.relays:
            threading.Thread(target=relay.serve_forever, daemon=True,
                             name="relay-%s" % relay.backend.name).start()
        return self

    def serve(self):
        try:
            self.start()
        except OSError as exc:
            print("gpu-lease: cannot listen (%s)" % exc, file=_stderr())
            print("  another gpu-lease is probably running on that port",
                  file=_stderr())
            self.close()
            return 2
        self.log("gpu-lease: one lease, %d front door(s)" % len(self.relays))
        for relay in self.relays:
            self.log("  %-6s %s  ->  %s"
                     % (relay.backend.name,
                        "http://%s:%d" % relay.backend.listen,
                        relay.backend.upstream))
        if self.cfg.lease.name:
            self.log("  lock %r, mode %s, queue timeout %ss"
                     % (self.cfg.lease.name, self.cfg.lease.mode,
                        int(self.cfg.lease.queue_timeout)))
        if any(b.token for b in self.cfg.backends):
            self.log("  bearer tokens required where configured")
        self.log("  ctrl-c to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            self.log("stopping")
        finally:
            self.close()
        return 0

    def close(self):
        for relay in self.relays:
            try:
                relay.shutdown()
                relay.server_close()
            except Exception:  # noqa: BLE001 - shutting down anyway
                pass
        self.relays = []


def _auth(backend):
    return {"Authorization": "Bearer %s" % backend.token} if backend.token else {}


def _stderr():
    import sys
    return sys.stderr


def _queue_probe(backend):
    """True while ComfyUI still lists the job (running or pending)."""

    def seen(job_id):
        code, doc = http_json(backend.upstream + "/queue", timeout=10,
                              headers=_auth(backend))
        if code != 200 or not isinstance(doc, dict):
            return True            # can't tell: keep holding
        rows = (doc.get("queue_running") or []) + (doc.get("queue_pending") or [])
        ids = {str(row[1]) for row in rows
               if isinstance(row, list) and len(row) > 1}
        return str(job_id) in ids

    return seen


class Beat(threading.Thread):
    """Keepalive bytes while the turn waits, so client deadlines never fire.

    SSE ignores a line that does not start with "data:", and JSON tolerates
    leading whitespace: both are legal filler *inside* a body we already
    started, which is safer than letting a 40 s queue look like a hang.
    """

    def __init__(self, handler, interval):
        super().__init__(daemon=True, name="keepalive")
        self.handler = handler
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.interval):
            self.handler.pad()

    def stop(self):
        self._stop.set()
        self.join(2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _early = False
    _want_sse = False

    def log_message(self, fmt, *args):  # noqa: A003 - silence the default log
        pass

    # -- plumbing ----------------------------------------------------------
    def read_body(self):
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            data = bytearray()
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()
                    break
                data += self.rfile.read(size)
                self.rfile.readline()
            return bytes(data)
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def send_json(self, status, doc, extra=None):
        blob = json.dumps(doc).encode()
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(blob)
        self.wfile.flush()
        self.close_connection = True

    def flip_early(self):
        if self._early:
            return
        self._early = True
        self.send_response_only(200)
        self.send_header("Content-Type",
                         "text/event-stream" if self._want_sse else "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.pad()

    def on_wait(self, waited):
        if waited >= self.engine.cfg.lease.early_headers_after:
            self.flip_early()

    def pad(self):
        if not self._early:
            return
        try:
            self.wfile.write(b": gpu-lease waiting\n\n" if self._want_sse else b" ")
            self.wfile.flush()
        except OSError:
            pass

    # -- verbs -------------------------------------------------------------
    def do_GET(self):
        self.handle_one()

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET

    # -- one request -------------------------------------------------------
    def handle_one(self):
        self._early = False
        self._want_sse = False
        try:
            backend = self.server.backend
            if backend.token and not self.token_ok(backend.token):
                return self.send_json(401, {"error": "missing or bad token"})
            path = urllib.parse.urlsplit(self.path).path
            if path == STATUS_PATH and self.engine.cfg.lease.heartbeat_status:
                return self.send_json(200, self.engine.status())
            if (self.headers.get("Upgrade") or "").lower() == "websocket":
                return self.tunnel()
            body = (self.read_body()
                    if self.command in ("POST", "PUT", "PATCH") else b"")
            if not backend.gates(self.command, path):
                if self.command == "POST" and backend.passthrough_log:
                    self.engine.log("pass-through POST %s %s (cannot allocate VRAM)"
                                    % (backend.name, path), "debug")
                return self.forward(body)
            self.turn(backend, body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    @property
    def engine(self):
        return self.server.engine

    def token_ok(self, want):
        got = (self.headers.get("Authorization") or "").strip()
        return got in ("Bearer %s" % want, want)

    def turn(self, backend, body):
        doc = {}
        if body:
            try:
                doc = json.loads(body) or {}
            except ValueError:
                doc = {}
        if not isinstance(doc, dict):
            doc = {}
        self._want_sse = doc.get("stream") is True
        beat = Beat(self, self.engine.cfg.lease.keepalive)
        beat.start()
        try:
            lease = self.engine.leases.acquire(
                backend.name,
                priority=backend.priority,
                on_wait=self.on_wait,
                before_run=lambda _lease: self.engine.on_acquired(backend, doc))
        except LeaseTimeout as exc:
            beat.stop()
            self.engine.log("%s refused: %s" % (backend.name, exc))
            return self.fail(429, str(exc), {"Retry-After": "1"})
        except Exception as exc:  # noqa: BLE001 - eviction/prepare failed
            beat.stop()
            self.engine.log("%s could not start: %s" % (backend.name, exc))
            return self.fail(503, str(exc))
        beat.stop()
        if lease.waited >= 0.5:
            self.engine.log("%s waited %.1fs for the card"
                            % (backend.name, lease.waited))
        if backend.hold == "watch":
            return self.job_turn(backend, lease, body)
        started = time.monotonic()
        try:
            self.forward(body)
        finally:
            lease.release()
            self.engine.log("%s turn done after %.1fs"
                            % (backend.name, time.monotonic() - started))

    def job_turn(self, backend, lease, body):
        """The work starts after the response, so the lease outlives it."""
        try:
            payload = self.forward(body, capture=True)
        except Exception:
            lease.release()
            raise
        job_id = None
        try:
            job_id = job_id_from(backend, json.loads(payload or b"{}"))
        except (ValueError, TypeError):
            pass
        if not job_id:
            lease.release()
            return
        threading.Thread(target=self.engine.watch_job,
                         args=(backend, job_id, lease),
                         daemon=True, name="watch-%s" % backend.name).start()

    def fail(self, status, message, extra=None):
        doc = {"error": {"message": message, "type": "gpu_busy"}}
        if not self._early:
            return self.send_json(status, doc, extra)
        try:  # headers are out; the error has to travel inside the body
            if self._want_sse:
                self.wfile.write(("data: %s\n\n" % json.dumps(doc)).encode())
            else:
                self.wfile.write(json.dumps(doc).encode())
            self.wfile.flush()
        except OSError:
            pass

    def connect(self):
        """Reach the real backend, or give it the boot grace first.

        Every process on a desktop starts at once, so a client can call us
        seconds before the backend's socket exists. Hold the request (with
        keepalives) instead of returning a 502 mid-session.
        """
        backend = self.server.backend
        host, port, _base = backend.upstream_parts()
        waited_from = time.monotonic()
        while True:
            conn = http.client.HTTPConnection(host, port, timeout=3600)
            try:
                conn.connect()
                return conn
            except OSError:
                conn.close()
                if time.monotonic() - waited_from >= self.engine.cfg.lease.grace:
                    return None
                self.on_wait(time.monotonic() - waited_from)
                self.pad()
                time.sleep(1.0)

    def forward(self, body, capture=False):
        backend = self.server.backend
        conn = self.connect()
        if conn is None:
            return self.fail(503, "%s is not listening at %s"
                             % (backend.name, backend.upstream))
        try:
            headers = {}
            for key, value in self.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low in ("host", "content-length",
                                                "accept-encoding"):
                    continue
                headers[key] = value
            host, port, _base = backend.upstream_parts()
            headers["Host"] = "%s:%d" % (host, port)
            if body:
                headers["Content-Length"] = str(len(body))
            conn.request(self.command, self.path, body=body or None,
                         headers=headers)
            resp = conn.getresponse()
            if not self._early:
                self.send_response_only(resp.status, resp.reason)
                for key, value in resp.getheaders():
                    low = key.lower()
                    if low in HOP_BY_HOP or low == "content-length":
                        continue
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
            kept = []
            if self.command != "HEAD" and resp.status not in (204, 304):
                while True:
                    piece = resp.read1(65536)
                    if not piece:
                        break
                    if capture:
                        kept.append(piece)
                    else:
                        self.wfile.write(piece)
                        self.wfile.flush()
            data = b"".join(kept)
            if capture:
                self.wfile.write(data)
                self.wfile.flush()
                self.close_connection = True
            return data
        finally:
            conn.close()

    def tunnel(self):
        """Raw byte relay, so progress websockets work and frames stay opaque."""
        backend = self.server.backend
        host, port, _base = backend.upstream_parts()
        up = None
        waited_from = time.monotonic()
        while up is None:
            try:
                up = socket.create_connection((host, port), timeout=3600)
            except OSError:
                if time.monotonic() - waited_from >= self.engine.cfg.lease.grace:
                    return self.send_json(502, {"error": "upstream unreachable"})
                self.on_wait(time.monotonic() - waited_from)
                self.pad()
                time.sleep(1.0)
        head = "%s %s HTTP/1.1\r\n" % (self.command, self.path)
        for key, value in self.headers.items():
            head += "%s: %s\r\n" % (key, value)
        head = head.replace("\r\nHost: ", "\r\nX-Forwarded-Host: ")
        head += "Host: %s:%d\r\n\r\n" % (host, port)
        up.sendall(head.encode("latin-1"))
        socks = {self.connection: up, up: self.connection}
        self.close_connection = True
        try:
            while True:
                ready, _, _ = select.select(list(socks), [], [], 3600)
                if not ready:
                    break
                for src in ready:
                    chunk = src.recv(65536)
                    if not chunk:
                        return
                    socks[src].sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                up.close()
            except OSError:
                pass


class Relay(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second gate silently steal a live
    # listener's port and split the client's traffic between them.
    allow_reuse_address = False

    def __init__(self, backend, engine):
        self.backend = backend
        self.engine = engine
        super().__init__(backend.listen, Handler)

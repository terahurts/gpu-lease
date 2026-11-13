"""A tiny HTTP server that records requests and plays canned responses."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Mock:
    """routes: {path-prefix: fn(path, body) -> (status, doc, delay)}"""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self._lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: A003
                pass

            def _respond(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = raw
                with mock._lock:
                    mock.calls.append((self.command, self.path, body,
                                       time.monotonic()))
                fn = None
                for prefix, candidate in mock.routes.items():
                    if self.path == prefix or self.path.startswith(prefix):
                        fn = candidate
                        break
                result = fn(self.path, body) if fn else (200, {"ok": True})
                status, doc = result[0], result[1]
                delay = result[2] if len(result) > 2 else 0
                if delay:
                    time.sleep(delay)
                blob = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
                ctype = ("text/event-stream" if isinstance(doc, bytes)
                         else "application/json")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(blob)
                self.close_connection = True

            do_GET = do_POST = do_PUT = _respond

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self, prefix):
        with self._lock:
            return sum(1 for call in self.calls if call[1].startswith(prefix))

    def paths(self):
        with self._lock:
            return [call[1] for call in self.calls]

    def when(self, prefix):
        """Monotonic timestamp of the first matching request, or None."""
        with self._lock:
            hits = [call[3] for call in self.calls if call[1].startswith(prefix)]
        return hits[0] if hits else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def llama_mock(load_seconds=0.0, chat_seconds=0.0, models=("m1", "m2")):
    """Router-ish: /models status, load that is not instant, chat that streams.

    Every preset is listed from the start, exactly like llama.cpp's router does
    for a --models-preset file, so "not listed" really does mean "unknown".
    """
    mock = Mock()
    state = {name: "unloaded" for name in models}
    lock = threading.Lock()

    def models(_path, _body):
        with lock:
            rows = [{"id": key, "status": {"value": value}}
                    for key, value in state.items()]
        return 200, {"object": "list", "data": rows}

    def load(_path, body):
        name = (body or {}).get("model")

        def finish():
            time.sleep(load_seconds)
            with lock:
                state[name] = "loaded"

        with lock:
            state[name] = "loading"
        threading.Thread(target=finish, daemon=True).start()
        return 200, {"status": "ok"}

    def unload(_path, body):
        with lock:
            state[(body or {}).get("model")] = "unloaded"
        return 200, {}

    def chat(_path, _body):
        return (200, b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                     b'data: [DONE]\n\n', chat_seconds)

    mock.routes["/models/load"] = load
    mock.routes["/models/unload"] = unload
    mock.routes["/models"] = models
    mock.routes["/v1/chat/completions"] = chat
    return mock, state


def comfy_mock(job_seconds=0.6):
    """POST /prompt returns an id; /history only knows it once the job is done."""
    mock = Mock()
    jobs = {}

    def prompt(_path, _body):
        jobs["p1"] = time.monotonic()
        return 200, {"prompt_id": "p1", "number": 1}

    def history(path, _body):
        job = path.rsplit("/", 1)[-1]
        started = jobs.get(job)
        if started is not None and time.monotonic() - started >= job_seconds:
            return 200, {job: {"status": {"completed": True}}}
        return 200, {}

    def queue(_path, _body):
        return 200, {"queue_running": [], "queue_pending": []}

    mock.routes["/prompt"] = prompt
    mock.routes["/history/"] = history
    mock.routes["/queue"] = queue
    mock.routes["/free"] = lambda _p, _b: (200, {})
    return mock, jobs


def ollama_mock(models=("m1",)):
    """Ollama: /api/ps is the residency list, keep_alive:0 unloads, and the
    list lags the unload answer by exactly one call (measured behaviour)."""
    mock = Mock()
    resident = set(models)
    pending = set()

    def ps(_path, _body):
        for name in list(pending):
            resident.discard(name)
            pending.discard(name)
        return 200, {"models": [{"name": n, "size_vram": 100}
                                for n in sorted(resident)]}

    def generate(_path, body):
        name = (body or {}).get("model")
        if name and name.startswith("embed:"):
            return 400, {"error": '"%s" does not support generate' % name}
        if (body or {}).get("keep_alive") == 0:
            pending.add(name)
        return 200, {"model": name, "response": "", "done": True,
                     "done_reason": "unload"}

    def embed(_path, body):
        name = (body or {}).get("model")
        if (body or {}).get("keep_alive") == 0:
            pending.add(name)
        return 200, {"embeddings": [[]]}

    mock.routes["/api/ps"] = ps
    mock.routes["/api/generate"] = generate
    mock.routes["/api/embed"] = embed
    return mock, resident


def lmstudio_mock(loaded=("openai/gpt-oss-20b",), idle=("qwen2.5-7b",)):
    """LM Studio: /api/v0/models reports loaded vs not-loaded."""
    mock = Mock()
    state = {name: "loaded" for name in loaded}
    state.update({name: "not-loaded" for name in idle})

    def models(_path, _body):
        return 200, {"data": [{"id": n, "instance_id": n, "state": s}
                              for n, s in state.items()]}

    def unload(_path, body):
        name = (body or {}).get("instance_id")
        if name in state:
            state[name] = "not-loaded"
        return 200, {"instance_id": name}

    mock.routes["/api/v0/models"] = models
    mock.routes["/api/v1/models/unload"] = unload
    return mock, state


def koboldcpp_mock(model="kcpp-model", admin_route="/api/admin/unload"):
    """KoboldCpp: /api/v1/model reports the current model; admin_route=None
    makes every admin candidate answer 404, like a build without --admin."""
    mock = Mock()
    state = {"model": model}

    def unload(_path, _body):
        state["model"] = ""
        return 200, {"ok": True}

    def missing(_path, _body):
        return 404, {"detail": "not found"}

    def current(_path, _body):
        if not state["model"]:
            return 503, {"detail": "No model loaded"}
        return 200, {"result": state["model"]}

    for path in ("/api/admin/unload", "/api/v1/admin/unload",
                 "/api/extra/admin/unload"):
        mock.routes[path] = unload if path == admin_route else missing
    mock.routes["/api/v1/model"] = current
    return mock, state


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

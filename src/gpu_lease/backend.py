"""What a backend is, and the three things that make one well behaved.

  gated   which requests can allocate VRAM; everything else passes through
  evict   how to give the card back (called on the *others* when you take it)
  prepare how to be genuinely ready before your request is forwarded

Kinds are presets for those three; every field can be overridden in the config
file, so an unknown server is a few lines rather than a fork.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field, replace

from .probe import http_json

__all__ = ["Backend", "Watch", "Hook", "KINDS", "build", "evict", "prepare",
           "watch_done"]

# Paths whose final segment runs a model. Matched case-insensitively on the
# last path segment, so /v1/chat/completions and /v1/embeddings are covered,
# and so are llama.cpp's native singular forms (/embedding, /rerank, /infill).
RUN_SEGMENTS = ("completions", "embeddings", "embedding", "embed", "rerank",
                "reranking", "infill", "responses")

KINDS = {
    # llama.cpp --models-preset (router). /models/load answers 200 immediately
    # and loads for 6-40 s, so "loaded" must be polled for, not assumed.
    "llama": {
        "gated_last": RUN_SEGMENTS,
        "gated_suffix": ("/chat/completions", "/responses"),
        "hold": "response",
        "evict": {"kind": "llama"},
        "prepare": {"kind": "llama", "body_from": "body.model"},
        "health": "/models",
    },
    # ComfyUI: POST /prompt returns a prompt_id and the work starts after the
    # response, so the turn is held until /history knows that id.
    "comfyui": {
        "gated_paths": ("/prompt", "/api/prompt"),
        "hold": "watch",
        "watch": {"path": "/history/{id}", "id_from": "prompt_id",
                  "done_when": "key", "timeout": 2700, "poll": 1.0,
                  "give_up_polls": 10},
        "evict": {"kind": "comfyui"},
        "health": "/queue",
    },
    # Any OpenAI-compatible server with nothing to free remotely (vLLM in
    # eager mode, text-generation-webui, ...). It only takes turns.
    "openai": {
        "gated_last": RUN_SEGMENTS,
        "hold": "response",
        "health": "/v1/models",
    },
    # Ollama keeps a model resident (keep_alive, 5 min default) and has no load
    # call: the model loads inside the request, with the lease held. Measured
    # 2026-11-13; /api/ps lags the unload response by one call, so poll.
    "ollama": {
        "gated_last": RUN_SEGMENTS + ("generate", "chat"),
        "gated_paths": ("/api/embed", "/api/embeddings", "/api/generate",
                        "/api/chat"),
        "hold": "response",
        "evict": {"kind": "ollama"},
        "health": "/api/ps",
    },
    # LM Studio: JIT loading, idle TTL (60 min default), Auto-Evict on model
    # switch, explicit unload. Preset built from the vendor docs - UNTESTED,
    # see docs/backends.md.
    "lmstudio": {
        "gated_last": RUN_SEGMENTS,
        "gated_suffix": ("/chat/completions", "/responses"),
        "hold": "response",
        "evict": {"kind": "lmstudio"},
        "health": "/api/v0/models",
    },
    # KoboldCpp: one model per process, OpenAI-compatible plus native routes.
    # Unloading requires --admin; the admin route is probed. UNTESTED, see
    # docs/backends.md.
    "koboldcpp": {
        "gated_last": RUN_SEGMENTS + ("generate",),
        "gated_paths": ("/api/v1/generate", "/api/extra/generate/stream",
                        "/v1/chat/completions", "/v1/completions"),
        "hold": "response",
        "evict": {"kind": "koboldcpp"},
        "health": "/api/v1/model",
    },
    "generic": {"hold": "response"},
}


@dataclass
class Hook:
    """One HTTP call, optionally derived from the client's request body."""

    kind: str = "http"            # http | llama | comfyui | ollama | ...
                     # ... | lmstudio | koboldcpp
    method: str = "POST"
    path: str = ""
    body: dict = field(default_factory=dict)
    body_from: str = ""           # "body.model" -> take that value from the request
    expect: tuple = (200,)
    timeout: float = 60.0
    settle: float = 0.0


@dataclass
class Watch:
    """Hold the turn past the response until the backend reports completion."""

    path: str = "/history/{id}"
    id_from: str = "prompt_id"
    done_when: str = "key"        # key | status_completed | nonempty
    timeout: float = 2700.0
    poll: float = 1.0
    give_up_polls: int = 10       # cancelled jobs vanish from the queue


@dataclass
class Backend:
    name: str
    listen: tuple
    upstream: str
    kind: str = "generic"
    priority: int = 0
    gated_last: tuple = ()
    gated_suffix: tuple = ()
    gated_paths: tuple = ()
    hold: str = "response"
    watch: Watch = None
    evict_hook: Hook = None
    prepare_hook: Hook = None
    health: str = ""
    token: str = ""
    expect_mib: int = 0
    passthrough_log: bool = True
    idle_free_after: int = 0   # seconds of no turns before evicting; 0 = off

    @property
    def host(self):
        return self.listen[0]

    @property
    def port(self):
        return self.listen[1]

    def upstream_parts(self):
        parts = urllib.parse.urlsplit(self.upstream)
        default = 443 if (parts.scheme or "http") == "https" else 80
        return (parts.hostname or "127.0.0.1", parts.port or default,
                "%s://%s:%d" % (parts.scheme or "http",
                                parts.hostname or "127.0.0.1",
                                parts.port or default))

    def gates(self, method, path):
        if method != "POST":
            return False
        clean = path.rstrip("/") or "/"
        if clean in self.gated_paths:
            return True
        last = clean.rsplit("/", 1)[-1].lower()
        if last in self.gated_last:
            return True
        return any(clean.endswith(suffix) for suffix in self.gated_suffix)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Backend %s %s:%d -> %s>" % (self.kind, self.host, self.port,
                                             self.upstream)


def build(name, listen, upstream, kind="generic", **overrides) -> Backend:
    """Assemble a Backend from a kind preset plus per-field overrides."""
    preset = dict(KINDS.get(kind) or KINDS["generic"])
    for key, value in overrides.items():
        if value is not None:
            preset[key] = value
    hook_keys = {"evict": "evict_hook", "prepare": "prepare_hook"}
    kwargs = {}
    for key, value in preset.items():
        if key in hook_keys:
            kwargs[hook_keys[key]] = _as_hook(value)
        elif key == "watch":
            kwargs["watch"] = _as_watch(value)
        elif key in ("gated_last", "gated_suffix", "gated_paths"):
            kwargs[key] = tuple(value or ())
        else:
            kwargs[key] = value
    kwargs.setdefault("gated_last", ())
    kwargs.setdefault("gated_suffix", ())
    kwargs.setdefault("gated_paths", ())
    if not isinstance(listen, tuple):
        raise TypeError("listen must be (host, port)")
    if kwargs.get("hold") == "watch" and kwargs.get("watch") is None:
        kwargs["watch"] = Watch()
    return Backend(name=name, listen=listen, upstream=upstream.rstrip("/"),
                   kind=kind, **kwargs)


def _as_hook(value):
    if value is None or isinstance(value, Hook):
        return value
    return Hook(**value)


def _as_watch(value):
    if value is None or isinstance(value, Watch):
        return value
    return Watch(**value)


# --------------------------------------------------------------------- hooks
def evict(backend, log=None):
    """Make this backend hand back its VRAM. Returns a short description."""
    hook = backend.evict_hook
    if hook is None:
        return "nothing to free"
    started = time.monotonic()
    if hook.kind == "llama":
        names = llama_resident(backend.upstream)
        for model in names:
            http_json(backend.upstream + "/models/unload", {"model": model},
                      timeout=60, headers=_auth(backend))
        deadline = time.monotonic() + hook.timeout
        while time.monotonic() < deadline and llama_resident(backend.upstream):
            time.sleep(0.25)
        text = "unloaded %s" % (", ".join(names) or "nothing")
    elif hook.kind == "comfyui":
        code, doc = http_json(backend.upstream + "/free",
                              {"unload_models": True, "free_memory": True},
                              timeout=hook.timeout, headers=_auth(backend))
        if code is None:
            raise RuntimeError("%s is not answering at %s"
                               % (backend.name, backend.upstream))
        if code not in hook.expect:
            raise RuntimeError("%s /free answered %s: %s"
                               % (backend.name, code, json.dumps(doc)[:200]))
        time.sleep(hook.settle or 0.5)
        text = "cached models freed"
    elif hook.kind == "ollama":
        names = ollama_resident(backend.upstream)
        for model in names:
            ollama_unload(backend.upstream, model)
        deadline = time.monotonic() + hook.timeout
        while time.monotonic() < deadline and ollama_resident(backend.upstream):
            time.sleep(0.25)
        left = ollama_resident(backend.upstream)
        if left:
            raise RuntimeError(
                "%s still has %s resident and Ollama will not let go: start "
                "Ollama with OLLAMA_KEEP_ALIVE=0, or run `ollama stop %s`"
                % (backend.name, ", ".join(left), left[0]))
        text = "unloaded %s" % (", ".join(names) or "nothing")
    elif hook.kind == "lmstudio":
        instances = lmstudio_loaded(backend.upstream)
        for instance in instances:
            lmstudio_unload(backend.upstream, instance)
        deadline = time.monotonic() + hook.timeout
        while (time.monotonic() < deadline
               and lmstudio_loaded(backend.upstream)):
            time.sleep(0.25)
        text = "unloaded %s" % (", ".join(instances) or "nothing")
    elif hook.kind == "koboldcpp":
        model = koboldcpp_model(backend.upstream)
        if not model:
            text = "nothing loaded"
        else:
            route = koboldcpp_unload(backend.upstream)
            if route is None:
                raise RuntimeError(
                    "%s (%s) will not unload: KoboldCpp needs --admin for the "
                    "admin endpoint, or start it with --adminunloadtimeout 60 "
                    "so it evicts itself" % (backend.name, model))
            deadline = time.monotonic() + hook.timeout
            while (time.monotonic() < deadline
                   and koboldcpp_model(backend.upstream)):
                time.sleep(0.25)
            text = "unloaded %s via %s" % (model, route)
    else:
        code, doc = http_json(backend.upstream + hook.path, hook.body or None,
                              method=hook.method, timeout=hook.timeout,
                              headers=_auth(backend))
        if code is not None and code not in hook.expect:
            raise RuntimeError("%s evict hook answered %s: %s"
                               % (backend.name, code, json.dumps(doc)[:200]))
        time.sleep(hook.settle)
        text = "%s %s -> %s" % (hook.method, hook.path, code)
    if log:
        log("evicted %s: %s (%.1fs)" % (backend.name, text,
                                        time.monotonic() - started))
    return text


def prepare(backend, request_body, log=None):
    """Ensure the backend is ready for this request, with the lock held.

    For llama.cpp that means the named preset is *loaded*, not "loading".
    """
    hook = backend.prepare_hook
    if hook is None:
        return
    model = None
    if hook.body_from.startswith("body."):
        model = (request_body or {}).get(hook.body_from.split(".", 1)[1])
    if hook.kind == "llama":
        if not model or model not in llama_models(backend.upstream):
            return          # "local", a file path, or not a preset: let it try
        llama_load(backend.upstream, model, timeout=hook.timeout, log=log)
    elif hook.kind == "http":
        body = dict(hook.body or {})
        if model:
            body["model"] = model
        code, doc = http_json(backend.upstream + hook.path, body,
                              method=hook.method, timeout=hook.timeout,
                              headers=_auth(backend))
        if code is not None and code not in hook.expect:
            raise RuntimeError("%s prepare hook answered %s: %s"
                               % (backend.name, code, json.dumps(doc)[:200]))


def watch_done(watch: Watch, upstream: str, job_id: str, give_up_seen=None,
               log=None, headers=None):
    """Poll until the backend reports ``job_id`` finished. True when it did."""
    deadline = time.monotonic() + watch.timeout
    missing = 0
    while time.monotonic() < deadline:
        url = upstream + watch.path.replace("{id}", urllib.parse.quote(job_id))
        code, doc = http_json(url, timeout=15, headers=headers)
        if code == 200 and isinstance(doc, dict):
            if watch.done_when == "key" and job_id in doc:
                return True
            if watch.done_when == "nonempty" and doc:
                return True
            if watch.done_when == "status_completed":
                row = doc.get(job_id) or doc.get("status") or {}
                status = row.get("status") if isinstance(row, dict) else None
                if isinstance(status, dict) and status.get("completed"):
                    return True
                if status == "completed":
                    return True
        if give_up_seen is not None:
            missing = missing + 1 if not give_up_seen(job_id) else 0
            if watch.give_up_polls and missing >= watch.give_up_polls:
                return False       # cancelled or rejected: stop holding
        time.sleep(watch.poll)
    return False


def job_id_from(backend, payload):
    if not isinstance(payload, dict) or backend.watch is None:
        return None
    value = payload.get(backend.watch.id_from)
    return value if isinstance(value, (str, int)) and str(value) else None


# llama.cpp router helpers -------------------------------------------------
RESIDENT = ("loaded", "loading", "sleeping")


def llama_models(upstream):
    """{id: status}. ``sleeping`` (--sleep-idle-seconds) still occupies the
    single --models-max slot even though the weights left the card, so it has
    to be unloaded before another preset can load."""
    code, doc = http_json(upstream + "/models", timeout=15)
    if code != 200 or not isinstance(doc, dict):
        return {}
    return {row.get("id"): (row.get("status") or {}).get("value")
            for row in doc.get("data") or [] if isinstance(row, dict)}


def llama_resident(upstream):
    return [mid for mid, st in llama_models(upstream).items() if st in RESIDENT]


def llama_load(upstream, model, timeout=300.0, log=None):
    """Do not return before the preset is actually loaded.

    POST /models/load returns 200 in ~0.1 s and then loads for 6-40 s; trusting
    the 200 walks straight into an OOM. Two other cases are handled here:
    "model is already running" (a sleeping preset still holds the slot) and
    "model limit reached" (--models-max 1 while another load lands).
    """
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while time.monotonic() < deadline:
        models = llama_models(upstream)
        state = models.get(model)
        if state is None:
            raise RuntimeError("llama.cpp does not know the model %r "
                               "(see GET %s/models)" % (model, upstream))
        if state == "loaded":
            if log and time.monotonic() - started > 1.0:
                log("loaded %s in %.1fs" % (model, time.monotonic() - started))
            return
        if state in ("sleeping", "loading"):
            http_json(upstream + "/models/unload", {"model": model}, timeout=60)
            time.sleep(0.5)
            continue
        others = [m for m in llama_resident(upstream) if m != model]
        if others:
            for other in others:
                http_json(upstream + "/models/unload", {"model": other}, timeout=60)
            time.sleep(0.5)
            continue
        code, doc = http_json(upstream + "/models/load", {"model": model}, timeout=60)
        msg = json.dumps(doc)[:200]
        if code is None:
            raise RuntimeError("llama.cpp is not answering at %s" % upstream)
        if code == 200:
            if log:
                log("loading %s ..." % model)
            while time.monotonic() < deadline:
                if llama_models(upstream).get(model) == "loaded":
                    if log:
                        log("loaded %s in %.1fs"
                            % (model, time.monotonic() - started))
                    return
                time.sleep(0.5)
            break
        if "already running" in msg or "limit reached" in msg:
            http_json(upstream + "/models/unload", {"model": model}, timeout=60)
            time.sleep(1.0)
            continue
        raise RuntimeError("loading %s failed (%s): %s" % (model, code, msg))
    raise RuntimeError("timed out waiting for %s to load" % model)


# ------------------------------------------------- other servers' residency
# Ollama, LM Studio and KoboldCpp have no "load this preset" call, so only
# eviction needs code. Their unload recipes were read from vendor docs; see
# docs/backends.md for what is measured and what is not.

def ollama_resident(upstream):
    """Names of the models currently holding memory (GET /api/ps)."""
    code, doc = http_json(upstream + "/api/ps", timeout=15)
    if code != 200 or not isinstance(doc, dict):
        return []
    names = []
    for row in doc.get("models") or []:
        if isinstance(row, dict):
            name = row.get("name") or row.get("model")
            if name:
                names.append(name)
    return names


def ollama_unload(upstream, model):
    """keep_alive:0 is the only unload Ollama offers. /api/generate answers
    "does not support generate" for embedding models, so the embed routes are
    tried as a fallback. Returns the route that worked, or None.

    The embedding fallback is UNTESTED: no embedding model was resident when
    the unload calls were measured (docs/backends.md)."""
    for path, body in (("/api/generate", {"model": model, "keep_alive": 0}),
                       ("/api/embed", {"model": model, "input": [],
                                       "keep_alive": 0}),
                       ("/api/embeddings", {"model": model, "prompt": "",
                                            "keep_alive": 0})):
        code, _doc = http_json(upstream + path, body, timeout=60)
        if code == 200:
            return path
    return None


def lmstudio_loaded(upstream):
    """Instance ids LM Studio reports as loaded, via the v0 API (v1 fallback).

    UNTESTED: written from lmstudio.ai/docs/developer on 2026-11-13."""
    for path in ("/api/v0/models", "/api/v1/models"):
        code, doc = http_json(upstream + path, timeout=15)
        if code != 200 or not isinstance(doc, dict):
            continue
        rows = doc.get("data") or doc.get("models") or []
        if not isinstance(rows, list):
            continue
        loaded = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            state = str(row.get("state") or row.get("status") or "").lower()
            ident = (row.get("instance_id") or row.get("id")
                     or row.get("model_key"))
            if ident and state in ("loaded", "idle", "running"):
                loaded.append(ident)
        return loaded
    return []


def lmstudio_unload(upstream, instance):
    code, _doc = http_json(upstream + "/api/v1/models/unload",
                           {"instance_id": instance}, timeout=60)
    return code == 200


KOBOLD_UNLOAD_ROUTES = ("/api/admin/unload", "/api/v1/admin/unload",
                        "/api/extra/admin/unload")


def koboldcpp_model(upstream):
    """Current model name, or "" when nothing is loaded.

    GET /api/v1/model answers {"result": "<name>"}; an unloaded KoboldCpp stops
    answering it, which is what the evict poll keys on."""
    code, doc = http_json(upstream + "/api/v1/model", timeout=10)
    if code == 200 and isinstance(doc, dict):
        return str(doc.get("result") or "")
    return ""


def koboldcpp_unload(upstream):
    """First admin route that accepts an unload, or None.

    UNTESTED: KoboldCpp exposes this only with --admin and the wiki does not
    document the route, so the candidates are probed in order."""
    for path in KOBOLD_UNLOAD_ROUTES:
        code, _doc = http_json(upstream + path, {}, timeout=60)
        if code in (200, 202):
            return path
    return None


def _auth(backend):
    return {"Authorization": "Bearer %s" % backend.token} if backend.token else {}


def with_overrides(backend, **fields):
    return replace(backend, **fields)

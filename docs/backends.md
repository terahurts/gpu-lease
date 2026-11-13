# Backends: what's supported, and the API facts behind each

Engineering notes, not marketing. Every claim is tagged with where it came from:

- **[built-in]** — implemented as a `kind` in `src/gpu_lease/backend.py`, covered by tests
- **[measured]** — probed against a real server, with the date and the numbers
- **[docs]** — taken from the vendor's documentation on the date shown, *not* tested
- **[open]** — unknown; needs someone with the program installed

The point of this file is that the next person (or the next session) doesn't have
to rediscover which HTTP call frees VRAM. The whole project rests on three
questions per program: *which paths need the card*, *how do I make it let go*,
and *how do I make it load before I let the request through*.

---

## Summary

| program | default port | queue | evict | load | status |
|---|---|---|---|---|---|
| llama.cpp `llama-server` | 8080 | yes | `POST /models/unload` | `POST /models/load` + poll | **[built-in]** `kind = "llama"` |
| ComfyUI | 8188 | yes | `POST /free` | implicit on request | **[built-in]** `kind = "comfyui"` |
| Ollama | 11434 | yes | `POST /api/generate {keep_alive:0}` per model from `/api/ps` | implicit on request | **[built-in, untested]** `kind = "ollama"` |
| LM Studio | 1234 | yes | `POST /api/v1/models/unload {instance_id}` | `POST /api/v1/models/load` | **[built-in, untested]** `kind = "lmstudio"` |
| KoboldCpp | 5001 | yes | admin route (probed); `--adminunloadtimeout` is the config-only route | implicit on request | **[built-in, untested]** `kind = "koboldcpp"` |
| anything OpenAI-compatible | — | yes | none | implicit | **[built-in]** `kind = "openai"` |
| anything else | — | yes | one fixed request (`[backend.evict]`) | one fixed request (`[backend.prepare]`) | **[built-in]** `kind = "generic"` |

**[built-in, untested]** means the code exists, the call sequence is covered by
tests against mock servers, and nobody has pointed it at the real program yet.
llama.cpp and ComfyUI are the only kinds with a *measured* eviction.

## Tested and untested

**llama.cpp and ComfyUI are the supported, tested combinations.** Ollama, LM
Studio and KoboldCpp have `kind` presets and behave the same way in the queue,
but they were written from vendor documentation: they *should* work, and that is
not the same as *does*. YMMV, YOLO. If one misbehaves, the fix is almost always
in the eviction call, and the notes below say which call is unverified.

`kind = "openai"` remains the zero-code option for any OpenAI-compatible server:
it gates `/v1/chat/completions`, `/v1/completions`, `/v1/responses` and
`/v1/embeddings`-style paths and holds the lease for the whole response. What it
does *not* do is free the other program's VRAM, so an evict hook (or
self-eviction by the program) is still required for the collision to be
impossible.

---

## llama.cpp — [built-in]

Router mode (`--models-preset`, `--models-max 1`) on 6772 in the reference
setup. Endpoints used by `kind = "llama"`:

- `GET /models` — every preset plus its state: `loaded` / `sleeping` /
  `unloaded`. **A `sleeping` preset still occupies the one model slot**, so it
  must be unloaded before a different preset can load.
- `POST /models/unload {model}` — **[measured 2026-11-13]** returns in ~1.0 s and
  the VRAM is actually gone.
- `POST /models/load {model}` — **[measured 2026-11-13]** returns 200 in ~0.1 s
  and then loads for 6–40 s **asynchronously**. This is the single most important
  detail in the project: a 200 is not "ready". `llama_load()` polls `/models`
  until the state is `loaded`, and only then does the request go upstream.
- A second `/models/load` while one is resident answers `400 "model is already
  running"`; a load that would exceed `--models-max` answers `400 "limit
  reached"`. `llama_load()` handles both by unloading first.

`--sleep-idle-seconds N` makes llama.cpp dump VRAM after N idle seconds, leaving
the preset `sleeping`. It is a safety net, not a substitute for the evict hook:
5 s costs a reload on every pause (measured: 11.3 GB freed, 4–26 s to reload).

---

## ComfyUI — [built-in]

- `POST /prompt` — **[measured]** returns `prompt_id` in ~0 s; the render happens
  *after* the response, so the turn must be held by polling
  `GET /history/{prompt_id}` until that key appears (`hold = "watch"`). A
  512×512 render is ~35–40 s.
- `POST /free {"unload_models": true, "free_memory": true}` — **[measured]**
  answers in 0.0 s; the VRAM disappears (14.5 GB → 2.6 GB).
- `GET /queue` — used as the health check, and to learn when `/free` is safe:
  `/free` waits for the queue to drain, so the evict call is capped at 8 s
  (an abandoned `queue_running` entry after an interrupt otherwise cost a chat
  turn ~25 s).
- ComfyUI never frees VRAM on its own; there is no idle timer. This is why the
  gate exists.

---

## Ollama — [measured 2026-11-13, this machine, Windows]

Probed with `nomic-embed-text` (274 MB) so the numbers are cheap and repeatable.

| call | result |
|---|---|
| `GET /api/ps` | `{"models": []}` when nothing is loaded; each loaded model carries `size_vram` |
| `POST /api/embed {model, input}` | 200 in 6.31 s cold; model resident at 323 MB VRAM |
| `POST /api/generate {model, keep_alive: 0}` | **200 in 0.01 s, body `"done_reason": "unload"`**; VRAM 2991 → 2436 MiB |
| `GET /api/ps` immediately after that 200 | still lists the model — **the list lags the response**, so poll |
| `POST /api/generate {model}` with no prompt | `400 "\"nomic-embed-text\" does not support generate"` — there is no generic "load" call |
| `GET /v1/models` | 200, all 10 installed models, OpenAI-compatible |

Consequences for a `kind = "ollama"`:

1. **Evict** = `GET /api/ps`, then `POST /api/generate {"model": m, "keep_alive": 0}`
   for each `m`, then poll `/api/ps` until empty. Same shape as the llama evict,
   hence ~30 lines.
2. **No prepare hook.** Loading happens inside the request, while the lease is
   held — which is better than llama.cpp's out-of-band 6–40 s load. Don't add
   one; `/api/generate` with an empty prompt is model-type dependent (the 400
   above).
3. **`/api/generate` cannot unload an embedding model?** — **[open]** The 400 came
   from a *generate* call on an embedding model. Whether
   `POST /api/generate {model: <embedding model>, keep_alive: 0}` also 400s, or
   unloads anyway, is untested. If it 400s, the kind needs
   `POST /api/embed {model, keep_alive: 0}` as a fallback for
   `embeddinggemma`-class models.
4. Environment (config-only alternative, no code change):
   `OLLAMA_KEEP_ALIVE=0` (unload after every request — measured mechanism, but
   pays a 6.3 s+ load per turn), `OLLAMA_MAX_LOADED_MODELS=1`,
   `OLLAMA_NUM_PARALLEL=1`, and `OLLAMA_HOST=127.0.0.1:11534` to move the server
   one port up so the gate can own 11434.

---

## LM Studio — [docs 2026-11-13]

Not installed here; everything below is from `lmstudio.ai/docs/developer`.
Server on 1234 by default, JIT loading (first request loads the model).

- OpenAI compatibility: `POST /v1/chat/completions`, `/v1/completions`,
  `/v1/embeddings`, `GET /v1/models` — so `kind = "openai"` queues it today.
- REST v0 (LM Studio ≥ 0.3.6): `GET /api/v0/models` and `/api/v0/models/{id}`
  return **`loaded` vs `not-loaded` state**, max context and quantization — the
  equivalent of llama.cpp's `/models` state, which is what a resident check needs.
  Also `POST /api/v0/chat/completions`, `/api/v0/completions`, `/api/v0/embeddings`
  with richer stats (TTFT, tok/s).
- REST v1: `POST /api/v1/models/load`, `POST /api/v1/models/unload` with body
  `{"instance_id": "openai/gpt-oss-20b"}`. `GET /api/v1/models` **[open]** — the
  docs' endpoint list shows unload/load but the enumeration route for instance
  IDs isn't in the page I read; assume it exists or derive IDs from
  `/api/v0/models`.
- Idle TTL: `ttl` field in a request payload (seconds), default **60 minutes** for
  JIT-loaded models; `lms load <model> --ttl 3600` for CLI loads.
- **Auto-Evict** (default enabled, Developer → Server Settings): unloads
  previously JIT-loaded models before loading a new one. This makes model
  *switching* safe but does **not** free VRAM when ComfyUI wants the card — a
  gate evict hook is still needed.
- A `kind = "lmstudio"` would need: gated paths as `openai`, evict =
  enumerate loaded → `POST /api/v1/models/unload {instance_id}` → poll until
  `/api/v0/models` shows none loaded, no prepare hook. **[open]** whether
  `instance_id` is stable/predictable enough to use without a list call.

---

## KoboldCpp — [docs 2026-11-13]

Not installed here; from the `LostRuins/koboldcpp` wiki (API page and FAQ). One
model per process, launched from a GUI/CLI, default port 5001.

- OpenAI compatibility: `/v1/completions`, `/v1/chat/completions`, and
  `/v1/embeddings` when started with `--embeddingsmodel` — so `kind = "openai"`
  queues it today.
- Native: `POST /api/v1/generate` (the main one), `GET /api/v1/model` (current
  model name — usable as a resident check), `/api/v1/config/max_length`,
  `/api/v1/config/max_context_length`.
- Streaming: `/api/extra/generate/stream` (SSE) and the polled
  `/api/extra/generate/check` (1 Hz, what KoboldAI Lite uses). Also
  `/api/extra/abort`, `/api/extra/perf`, `/api/extra/version`,
  `/api/extra/tokencount`, `/api/extra/true_max_context_length`.
- Unload: **`--admin`** enables admin mode ("unload and reload different
  configurations or models"), with `--admindir` (directory of `.kcpps` configs)
  and optional `--adminpassword`. **[open]** the exact admin route and body —
  the wiki doesn't spell it out; read it from a running build (or the source)
  before writing the kind.
- **`--adminunloadtimeout N`** auto-unloads the current model after N idle
  seconds — the config-only route, the KoboldCpp analogue of
  `--sleep-idle-seconds`.
- **Router mode** (`--routermode`) is a reverse-proxy router for hotswapping
  models *within* one process, and current builds hotswap from the OpenAI-
  compatible API too ("more like llama-swap"). If a user already runs router
  mode, gpu-lease only needs to queue, not to load.
- A `kind = "koboldcpp"` would need: gated paths `/v1/chat/completions`,
  `/v1/completions`, `/api/v1/generate`; hold = response (generate is
  synchronous, and streaming is SSE); evict = the admin unload call once known;
  health = `/api/v1/model`.

---

## Adding a kind — the checklist

Five edits, all mechanical once the three questions are answered:

1. `KINDS` in `src/gpu_lease/backend.py` — gated paths, `hold`, `evict`,
   `prepare`, `health`. A preset only *adds* keys; per-field overrides still win.
2. `evict()` / `prepare()` in the same file — a new `hook.kind` branch, with the
   helper pair `<name>_resident()` / `<name>_unload()` beside `llama_resident()`
   and `llama_unload()`. The llama pair is the template: list what's resident,
   unload each, poll until gone, log the move.
3. `tests/mocks.py` — a route configurable mock (the existing `llama_mock` /
   `comfy_mock` show the shape) plus a test in `tests/test_backend.py` asserting
   evict issues the right calls and that prepare doesn't return too early.
4. `examples/` — one `[[backend]]` block in a config, and the program-specific
   instruction for moving its port (`--port`, `OLLAMA_HOST`, …).
5. Docs — a row in the summary table above and in `docs/config-explained.md`,
   plus the sentence a non-technical reader needs: *"move the program's port
   from X to Y, or set this variable"*.

Tests must stay hermetic: no kind may require a real server or a pulled model to
pass CI. Measure against the real thing once, record the numbers here, then test
against the mock.

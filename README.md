*This project is entirely vibe-coded by DeepSeek V4.1 Flash. It was originally created because my own GPU's VRAM was getting maxed-out when an AI chat app sent a request to a local model via Llama.CPP at the same time that I was using ComfyUI, causing my PC to grind to an almost-standstill.*

*I'm not a coder and I have no idea how it works or if it will work successfully for you.*

---

# gpu-lease

**One GPU, several VRAM-hungry servers, no OOM races.**

`gpu-lease` is a transparent HTTP gate. Point your clients at it instead of at
your servers, and the servers take turns: whoever wants the GPU waits until the
other one has given it back, instead of both loading a model at the same time.

```
client  ──► 127.0.0.1:6772 ──► gpu-lease ──► llama-server    127.0.0.1:6872
client  ──► 127.0.0.1:8188 ──►    (one lease)  ──► ComfyUI        127.0.0.1:8198
```

Written for llama.cpp + ComfyUI on Windows 11, but nothing in it is specific to
either: backends are entries in a TOML file with three hooks (gate, evict,
prepare), and there are presets for llama.cpp, ComfyUI, any OpenAI-compatible
server, and a generic kind for everything else.

> **Tested with llama.cpp and ComfyUI. Nothing else has been tested.**
> Presets for Ollama, LM Studio and KoboldCpp exist, written from their API
> documentation and covered by tests against mock servers, so they *should*
> work — but nobody has run those three end to end yet. YMMV, YOLO, and please
> report what happens if it eats your afternoon.


> ### New here? Start with the plain-language guide
>
> **[docs/start-here.md](docs/start-here.md)** — what this is, in one page, no
> jargon. Then **[docs/install.md](docs/install.md)** for every click and
> keystroke of the Windows setup, with "you should see" checkpoints.
>
> | guide | for |
> |---|---|
> | [start-here.md](docs/start-here.md) | the idea in one page |
> | [install.md](docs/install.md) | step-by-step setup, from zero |
> | [config-explained.md](docs/config-explained.md) | every setting, in plain English |
> | [day-to-day.md](docs/day-to-day.md) | the morning routine and reading the log |
> | [troubleshooting.md](docs/troubleshooting.md) | symptom → cause → fix, plus how to undo |
> | [faq.md](docs/faq.md) | safety, speed, two GPUs, other programs |
> | [glossary.md](docs/glossary.md) | port, lease, VRAM, endpoint, … |
> | [backends.md](docs/backends.md) | which programs work, and what frees their VRAM |
>
> Everything below is the technical reference for people who want to understand
> or change the machinery.

```
$ gpu-lease status
gpu-lease  gpu-lease  [queue, queue timeout 1800s]
  holder   comfy (1 turn(s), 38.4s, 1 waiting)
  vram     11,084 / 16,376 MiB
  llm      http://127.0.0.1:6772  ->  http://127.0.0.1:6872  ok
  comfy    http://127.0.0.1:8188  ->  http://127.0.0.1:8198  ok
  llm      8 preset(s), resident: Qwen3-Embedding-0.6B
```

## Why

Two local servers on one card is a scheduling problem nobody wants to solve by
hand. Loading a 12B chat model takes 6–40 s and ~11 GB; ComfyUI parks 7–12 GB
after a render and only releases it when asked. When both load at once you get a
CUDA OOM at best, and at worst silent Windows WDDM spill: the model "loads",
then runs three times slower than it should with no error anywhere.

Neither server can see the other, and most clients let you type only a URL. So
the queue belongs in front of the URLs.

## How a turn works

```
gated request ──► take the lease ──► evict the others ──► prepare self
              ──► forward, stream back ──► release
```

1. **Only some requests are gated.** `POST /v1/chat/completions`, `…/embeddings`,
   `…/rerank`, ComfyUI's `/prompt` — anything that can allocate VRAM. Polling,
   downloads, uploads, progress websockets and `/v1/models` pass straight
   through, so a client that polls `/history` every second never queues.
2. **Taking the lease evicts the others.** llama.cpp gets `POST /models/unload`
   per resident preset; ComfyUI gets `POST /free`. Eviction measured at ~1 s,
   which is why the design is "hand it back between turns" rather than "share
   it".
3. **Preparing is a barrier, not a request.** llama.cpp's `/models/load` answers
   `200` in 0.1 s and then loads for up to 40 s. The lease is only handed over
   when `/models` reports the preset as `loaded` — and a `sleeping` preset
   (`--sleep-idle-seconds`) still occupies the single `--models-max` slot, so it
   is unloaded first.
4. **Some turns outlive their response.** ComfyUI's `/prompt` returns a
   `prompt_id` and renders afterwards, so the lease is held until `/history`
   knows that id. It gives up after N polls so a cancelled job cannot wedge the
   gate.
5. **Waiting looks like thinking, not like a hang.** A queued turn whose wait
   passes `early_headers_after` sends its headers plus keepalive bytes (SSE
   comments, or whitespace for JSON — both legal inside a body) so no client
   deadline fires while the other side renders.

The lock is a kernel object (Windows named mutex, `flock` on POSIX), not a PID
file, so a crashed gate or a killed terminal releases the GPU immediately.

## Install

```bash
pip install -e .            # from this directory
gpu-lease --version
```

Python 3.9+; no runtime dependencies (`tomli` on 3.9/3.10). Or run it without
installing: `python -m gpu_lease run -c gpu-lease.toml` with `src` on
`PYTHONPATH`.

## Quickstart

```bash
gpu-lease init -o gpu-lease.toml      # starter config with the llama+comfy case
gpu-lease doctor -c gpu-lease.toml    # ports, upstreams, VRAM
gpu-lease run -c gpu-lease.toml
```

Then move the real servers one port up and leave the clients alone:

| server | its own port | what clients use |
|---|---|---|
| llama-server | `--port 6872` | `http://127.0.0.1:6772/v1` |
| ComfyUI | `--port 8198` | `http://127.0.0.1:8188` |

That direction matters: the gate takes the *familiar* port, so no config field
anywhere — chat base URL, an embedding endpoint URL, a stray script — can
accidentally bypass it.

## Configuration

```toml
[lease]
name = "gpu-lease"        # OS lock name; Global\... to cross logon sessions
queue_timeout = 1800      # seconds one turn may wait; 0 = refuse at once
mode = "queue"            # queue | reject
early_headers_after = 20  # start the response + keepalives after this long
keepalive = 10            # seconds between keepalive bytes
grace = 180               # wait for a backend that is still booting
vram_ceiling = 0          # MiB to require after eviction (0 = off)

[logging]
level = "info"            # quiet | info | debug
file = ""                 # reserved

[[backend]]
name = "llm"
kind = "llama"
listen = "127.0.0.1:6772"
upstream = "http://127.0.0.1:6872"
priority = 0              # lower runs first among waiting turns
token = ""                # optional: require Authorization: Bearer <token>
```

| backend key | meaning |
|---|---|
| `kind` | `llama`, `comfyui`, `openai`, `generic` — preset for the hooks below |
| `listen` | `host:port`, `:port`, or a bare port |
| `upstream` | the real server's base URL |
| `priority` | tie-break for the queue (lower wins); equal priorities are FIFO |
| `gated_paths` | exact paths that need the lease |
| `gated_last` | last path segment that needs it (`completions`, `embedding`, …) |
| `gated_suffix` | extra path suffixes, e.g. `/chat/completions` |
| `hold` | `response` (release after the last byte) or `watch` |
| `health` | path `gpu-lease doctor` probes |
| `token` | per-backend bearer token |
| `passthrough_log` | log ungated POSTs in `debug` (the canary for bypasses) |

Hooks, all optional and overridable per backend:

```toml
[backend.evict]        # how to make it hand VRAM back (called on the others)
kind = "llama"         # llama | comfyui | http
# kind = "http"
# method = "POST"
# path = "/api/unload"
# expect = [200, 404]

[backend.prepare]      # how to be ready before its own request is forwarded
kind = "llama"         # llama | http
body_from = "body.model"

[backend.watch]        # hold the turn past the response
path = "/history/{id}"
id_from = "prompt_id"
done_when = "key"      # key | nonempty | status_completed
timeout = 2700
poll = 1.0
give_up_polls = 10
```

## Recipes

**llama.cpp + ComfyUI** (the original): `examples/llama-comfyui.toml`. Give chat
`priority = 0` and ComfyUI `priority = 10` and an interactive reply jumps a
queued render.

**Two chat models, refuse instead of queue**: `examples/minimal.toml`, with
`mode = "reject"` — a busy GPU becomes `429` + `Retry-After: 1`, which most
clients already retry.

**A server with no preset** (SD.Next, Whisper, a homebrew trainer):
`examples/custom-hooks.toml` — declare `gated_paths`, one `evict` call and one
`watch` path.

**Reachable from another device**: `gpu-lease run --host 0.0.0.0` and set a
`token` per backend; clients send `Authorization: Bearer <token>`.

**Inside a Python script**, same lock, no HTTP hop:

```python
import gpu_lease, requests

with gpu_lease.lease("llm", timeout=600):
    requests.post("http://127.0.0.1:6872/v1/chat/completions", json=...)
```

**From a shell**, hold the GPU for any command:

```bash
gpu-lease lock --timeout 600 -- python train_lora.py
```

## CLI

| command | does |
|---|---|
| `gpu-lease run -c cfg` | serve the gate |
| `gpu-lease status -c cfg [--json]` | holder, waiters, VRAM, backends, resident models |
| `gpu-lease doctor -c cfg` | config sanity, listen ports free, upstreams answering |
| `gpu-lease lock -- cmd…` | run a command holding the lease |
| `gpu-lease init -o cfg` | write a starter config |

Every listener also answers `GET /__lease/status` with the same JSON, so a
dashboard can poll whichever port the client uses.

## Field notes

Measured on the machine this was extracted from — an RTX 4080 16 GB, Windows 11,
llama.cpp b12182 in router mode (`--models-preset`, `--models-max 1`) and
ComfyUI:

| action | cost |
|---|---|
| chat turn, 12B Q6_K, model already resident | ~0.4 s first byte |
| same with the preset unloaded | 6–40 s, all of it inside the turn |
| `POST /models/unload` (14.5 GB back) | 1.0 s |
| ComfyUI `POST /free` | ~0.0 s to answer, 0.5 s to settle |
| a queued turn behind a 512×512 render | 35–41 s, then served normally |

Two things that bit us and are now handled in code:

- `sleeping` (`--sleep-idle-seconds`) frees the VRAM but keeps the model slot,
  so the next preset's load answers `400 model is already running`.
- ComfyUI leaves stale `queue_running` entries after an interrupt, so waiting
  for the queue to drain needs a cap; the watch gives up after `give_up_polls`.

## Limits

- **Only clients that go through the gate are scheduled.** A browser tab on the
  real port is still a bypass; that is why the recommended layout gives the gate
  the familiar port and moves the server.
- One lease per GPU. Two cards → two configurations with different `name`s and
  different ports (the tool is not cluster-aware).
- A model that fits in *neither* arrangement is still a model that does not fit;
  `mode = "reject"` just makes the failure fast and legible.
- `vram_ceiling` uses `nvidia-smi`; on AMD/Apple it is skipped rather than
  guessed.

## Troubleshooting

| symptom | cause |
|---|---|
| `cannot listen (WinError 10048)` | another gate is running on that port |
| client gets `429` | `mode = "reject"`, or `queue_timeout` elapsed |
| first request after boot hangs up to `grace` seconds | the backend's socket does not exist yet; it is being waited for, not lost |
| a request returns `503 … is not listening` | `grace` expired: the backend never started |
| `pass-through POST /v1/xxx` in the log and it did allocate VRAM | add that path to `gated_paths`/`gated_last` |
| GPU stays at the previous holder's VRAM | `evict` hook failed; check `debug` logs and the backend's own unload endpoint |

## Design notes

- **Kernel lock, not a PID file.** `WAIT_ABANDONED` is treated as success: the
  previous holder died mid-turn, and the card is ours.
- **One mutex owner thread.** Win32 mutexes are thread-affine, and leases are
  released by watcher threads long after the request that took them returned.
- **Refcounted per owner.** Two concurrent replies from the same backend share a
  turn instead of queueing behind each other.
- **Early response, then keepalives.** A 40 s queue must not look like a hang to
  a client with a header timeout; SSE comments (`: gpu-lease waiting`) and JSON
  whitespace are both legal filler.
- **Ungated POSTs are logged.** If some path starts allocating VRAM, the log
  says so instead of leaving you to wonder why the card is full.

## License

MIT.

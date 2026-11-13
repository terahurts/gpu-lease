# The settings file, line by line

Open `gpu-lease.toml` in Notepad. Everything is plain text. Lines starting with
`#` are notes to yourself — the program ignores them.

The file has three kinds of section:

- `[lease]` — how the queue behaves (one per file)
- `[logging]` — how chatty the window is
- `[[backend]]` — one block per program being managed (two double brackets
  means "there can be several of these")

**Rules that keep you out of trouble:**

- Keep every value in quotes unless it's a bare number.
- One setting per line.
- Don't use tabs; spaces are fine.
- If the doorman refuses to start and names a line number, look there first —
  its complaint is literal ("unknown setting 'timesout'").

---

## `[lease]` — how the queue behaves

| setting | default | what it does, plainly | change it when |
|---|---|---|---|
| `name` | `"gpu-lease"` | The name of the "ticket" the programs share. Two copies of the doorman with **different** names don't see each other. | only if you run two cards, or a second copy for a second set of programs |
| `queue_timeout` | `1800` | How long one request may wait for its turn, in seconds (1800 = 30 min). | your image jobs are slower than 30 minutes |
| `mode` | `"queue"` | `"queue"` = wait in line. `"reject"` = don't wait, refuse immediately with "busy, try again". | you'd rather see an error than a pause (some apps retry automatically) |
| `early_headers_after` | `20` | After this many seconds of waiting, start the reply with "still here, waiting" so the app doesn't give up on a silent connection. | an app times out anyway (try `5`) |
| `keepalive` | `10` | Seconds between those "still here" nudges. | rarely |
| `grace` | `180` | When a program isn't listening yet (still booting), how long to hold requests instead of failing them. | you start everything at once and ComfyUI is slow (try `240`) |
| `vram_ceiling` | `0` | `0` = off. Otherwise, before granting a turn, wait until at least this much graphics memory is free. | you want a hard guarantee rather than trust (e.g. `1000`) |

Notes worth knowing:

- `queue_timeout` and `grace` are different waits. `grace` is "the program isn't
  there yet"; `queue_timeout` is "the program is there but busy".
- In `reject` mode, apps that retry automatically (many do) get the same
  experience as `queue` mode but faster failures.
- `early_headers_after` exists because some apps hang up if a reply's first byte
  takes more than ~30 s. Nobody sees the nudge; it's invisible to you and to the
  app.

## `[logging]` — the window's chatter

| setting | default | meaning |
|---|---|---|
| `level` | `"info"` | `quiet` = only problems. `info` = one line per turn (recommended). `debug` = everything, including every ungated request. |

`debug` is the mode to use when *"why didn't it queue that?"* — it prints
`pass-through POST /v1/something (no lease)` for requests that skip the queue,
which is how you find a setting that bypasses it.

## `[[backend]]` — one block per program

Duplicate this block for each program. The `name` is just a label for the log.

| setting | what it means | example |
|---|---|---|
| `name` | label only | `"llm"` |
| `kind` | what sort of program: `llama`, `comfyui`, `ollama`, `lmstudio`, `koboldcpp`, `openai`, `generic` | `"comfyui"` |
| `listen` | the address the doorman answers on (the one your apps use) | `"127.0.0.1:8188"` |
| `upstream` | where the real program now listens | `"http://127.0.0.1:8198"` |
| `priority` | who goes first among *waiting* requests. Lower wins; equal = arrival order | `0` for chat, `10` for images |
| `token` | optional password for this door only | `""` (off) |
| `health` | a harmless address the health check pokes | set by `kind` |

The `kind` values, in plain terms:

| kind | use for | it knows how to |
|---|---|---|
| `llama` | llama.cpp's `llama-server` | ask it to unload models, wait for a model to finish loading |
| `comfyui` | ComfyUI | free its memory before a turn, keep holding the card until a render truly finishes |
| `ollama` | Ollama | ask `/api/ps` what is loaded, unload each with `keep_alive: 0` |
| `lmstudio` | LM Studio | unload every loaded instance because another program wants the card |
| `koboldcpp` | KoboldCpp | unload through the admin endpoint (needs `--admin`) |
| `openai` | any program speaking the OpenAI API (vLLM, oobabooga…) | nothing special — it just queues |
| `generic` | anything else | nothing special — you can teach it with the tables below |

> Only `llama` and `comfyui` have been tested against the real programs.
> `ollama`, `lmstudio` and `koboldcpp` are built from their documentation and
> *should* work — see [backends.md](backends.md#tested-and-untested) if one
> doesn't. YMMV.

### What gets queued, and what doesn't

Only requests that can allocate graphics memory are queued. Everything else
passes straight through, so polling and progress bars never wait.

For `kind = "llama"` the queued requests are the ones that run a model:
`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/embedding`,
`/embed`, `/v1/rerank`, `/reranking`, `/v1/infill`, `/v1/responses`.
For `kind = "comfyui"` it's `/prompt` only — uploading an image or checking
history never waits.

You can change this per backend:

```toml
[[backend]]
name = "llm"
kind = "llama"
listen = "127.0.0.1:6772"
upstream = "http://127.0.0.1:6872"
gated_paths = ["/v1/chat/completions"]   # only these exact paths
# gated_last  = ["completions"]          # or: any path whose last word is this
# gated_suffix = ["/chat/completions"]   # or: any path ending with this
```

`gated_paths` is the safest of the three: list exactly what your app calls.

### The three hooks, if your program isn't llama.cpp or ComfyUI

Everything the doorman does *to* a program is one of three small actions. The
`kind` fills them in; you can override any of them.

```toml
  [backend.evict]     # "give the memory back" — called on the OTHER program
  kind = "http"
  method = "POST"
  path = "/api/unload-checkpoint"
  expect = [200, 404]
```

```toml
  [backend.prepare]   # "get ready before my own request arrives"
  kind = "http"                    # or "llama" for llama.cpp
  method = "POST"
  path = "/api/preload"
  body_from = "body.model"         # copy the model name out of the request
```

```toml
  [backend.watch]     # "I answered, but I'm still working"
  path = "/history/{id}"           # {id} is filled from the reply
  id_from = "prompt_id"
  done_when = "key"                # key | nonempty | status_completed
  timeout = 2700
  poll = 1.0
```

A `watch` is for programs that reply "started!" and then work for a minute —
image generators, mostly. Without it the doorman would think the turn ended
instantly and let the other program in while a render was still running.

---

## Two worked examples

**Chat and images, chat gets priority.** This is the default file:
`examples/llama-comfyui.toml`.

**Two chat models that must never share the card** (for example a big writing
model and a small embedding model that don't fit together): two `[[backend]]`
blocks, different `listen`/`upstream` pairs, `queue_timeout = 60` so nobody
waits long for a five-second turn.

**A program nobody has written a preset for:** `examples/custom-hooks.toml`
shows one with `gated_paths`, an `evict` call and a `watch`.

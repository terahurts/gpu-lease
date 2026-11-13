# Changelog

## 0.3.0 - 2026-11-13

- `[backend.idle] free_after = <seconds>`: run a backend's evict hook once
  nothing has used it for that long, so an idle ComfyUI stops holding VRAM and
  cached memory. Only runs when the card is free and nobody is queued, takes the
  lease so it cannot race a turn, fires once per idle period, and re-arms when
  the backend is used again. With `[backend.evict]` it applies to any backend,
  llama.cpp included.
- 55 tests (6 new: parsed config and five idle-free cases).

## 0.2.0 - 2026-11-13

- `kind` presets for Ollama, LM Studio and KoboldCpp. Ollama unloads with
  `keep_alive: 0` per resident model (with an embed-route fallback), LM Studio
  through `/api/v1/models/unload`, KoboldCpp through its admin endpoint. **All
  three are untested against the real programs** - llama.cpp and ComfyUI remain
  the tested combinations. YMMV.
- `docs/backends.md`: per-program API notes - what frees VRAM, what loads a
  model - with numbers measured against llama.cpp, ComfyUI and Ollama, and
  docs-only notes for LM Studio and KoboldCpp.
- Docs assume the folder is at `C:\gpu-lease` instead of the author's home
  directory; the README opens with the project's origin note.
- 49 tests (8 new: the three eviction sequences, against mock servers).

## 0.1.0 - 2026-11-13

First standalone release, extracted from a working two-server setup
(llama.cpp router + ComfyUI on one 16 GB card, Windows 11).

- One cross-process lease (`Windows` named mutex / POSIX `flock`) shared by any
  number of HTTP front doors; abandoned locks are recoverable.
- Backend kinds: `llama` (unload/sleep handling, load barrier on `/models`),
  `comfyui` (`/free` eviction, hold until `/history`), `openai`, `generic`.
- Declarative `evict` / `prepare` / `watch` hooks per backend, all overridable.
- Turn policies: `queue` (FIFO with priorities) or `reject` (`429` +
  `Retry-After`), per-turn timeouts.
- Streaming relays for SSE and chunked bodies, pass-through for everything that
  cannot allocate VRAM, websocket tunnelling, optional per-backend bearer token.
- Early-response keepalives so a long queue never trips a client's header
  timeout, and a boot `grace` window so starting everything at once is safe.
- `run`, `status`, `doctor`, `lock`, `init` commands; `GET /__lease/status` on
  every listener; `gpu_lease.lease()` for in-process turns.
- 41 tests, including two-process lock contention, unfairness regressions and a
  full proxy integration suite against mock backends.
- Plain-language documentation for non-technical users: `docs/start-here.md`,
  `install.md`, `config-explained.md`, `day-to-day.md`, `troubleshooting.md`,
  `faq.md`, `glossary.md`, with a link checker (`scripts/check_links.py`) so the
  guides cannot rot silently.

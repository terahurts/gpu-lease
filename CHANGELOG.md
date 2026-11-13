# Changelog

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
- `docs/backends.md`: per-program API notes - what frees VRAM, what loads a
  model - with numbers measured against llama.cpp, ComfyUI and Ollama, and
  docs-only notes for LM Studio and KoboldCpp.
- Plain-language documentation for non-technical users: `docs/start-here.md`,
  `install.md`, `config-explained.md`, `day-to-day.md`, `troubleshooting.md`,
  `faq.md`, `glossary.md`, with a link checker (`scripts/check_links.py`) so the
  guides cannot rot silently.

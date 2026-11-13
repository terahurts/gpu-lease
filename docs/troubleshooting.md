# When something looks wrong

Find your symptom in the words you'd actually use, then follow the fix. Each one
is written as **what you see → why → what to do**.

---

## "My chat app can't connect at all"

**Why:** the doorman isn't running, or it is running but your app is pointed
somewhere else.

1. Look at the doorman's window. Is it still open, with the four `[time]` lines
   at the top? If it's closed, start it again — your app talks to the doorman
   now, so with the doorman gone there is nothing at the old address.
2. `gpu-lease status -c gpu-lease.toml`. `not running` confirms it.
3. If it *is* running, check the app's URL one more time: `http://127.0.0.1:6772/v1`
   — with the `/v1` on the end for a chat connection, without it for ComfyUI.

## "The doorman won't start: `cannot listen … WinError 10048`"

**Why:** something already owns one of the `listen` ports. Usually a second copy
of the doorman, or the real program that never moved.

1. `gpu-lease doctor -c gpu-lease.toml` — it prints `BUSY` next to the address
   that's taken.
2. If it's an old doorman window you forgot about, close it.
3. If it's the real program still on the old port, you missed step 6 of
   [install.md](install.md#step-6--move-the-two-programs-one-port-up): it needs
   `--port` moved to the `upstream` number.

## "Everything was fine and then a request sat there for a minute"

**Why:** it was waiting its turn. That's the feature.

Look in the log: `llm waited 41.3s for the card` — the other program had the
card for 41 seconds. If you want to know *what* had it: `gpu-lease status` shows
the holder and how long it's been holding.

If the pause is longer than you're willing to wait, lower `queue_timeout` — the
request then fails with a clear "busy" message instead of waiting.

## "An app shows an error: 429 / 'too many requests'"

**Why:** `mode = "reject"` in the settings, or a wait longer than
`queue_timeout`.

- If you didn't choose `reject`: change `mode = "queue"`.
- Many apps retry by themselves, so 429s often fix themselves and you never
  notice. If yours doesn't, use `queue` mode.

## "An app shows 503 / 'service unavailable' / 'not listening'"

**Why:** the real program behind that door is not running (or never started).
The doorman waited `grace` seconds and then said so instead of hanging.

1. Is ComfyUI / llama.cpp actually running? Look at its own window.
2. `gpu-lease doctor` — the line will say `upstream unreachable`.
3. Check the program's port matches the `upstream` line in the settings file.

## "It works in curl but not in my app" / "the app says the URL is invalid"

**Why:** your app may be adding or expecting a `/v1` that isn't there (or the
reverse), or it has a second URL field with an old address in it.

1. In apps with two address boxes — a "base URL" and an "embedding endpoint" — an
   override in the second box bypasses the doorman. **Empty it** and the app
   falls back to the first box.
2. Chat connection: ends with `/v1`. Image connection: no `/v1`.

## "Nothing is queuing — both programs still load at once"

**Why:** one of the two apps is talking to the real program directly, not to the
doorman.

1. Start the doorman with `--level debug` and use the app. A request that skips
   the queue prints `pass-through POST /v1/… (no lease)`.
2. If a request that clearly loads a model appears as pass-through, that path
   isn't in the doorman's list. Add it to `gated_paths` for that backend (see
   [config-explained.md](config-explained.md#what-gets-queued-and-what-doesnt)).
3. If nothing at all appears, the app is pointed at the real program's port.
   Compare the URL in the app with `listen` in the settings file.

## "The graphics card still shows 12 GB used when nothing is running"

**Why:** a program is holding memory with no job in flight — usually ComfyUI
after a render, or llama.cpp in `sleeping` state.

That's expected between turns; the doorman only frees memory when the *other*
side needs the card. If you want it empty now:

```powershell
curl -X POST http://127.0.0.1:6872/models/unload -H "Content-Type: application/json" -d "{\"model\":\"your-model-name\"}"
curl -X POST http://127.0.0.1:8198/free -H "Content-Type: application/json" -d "{\"unload_models\":true,\"free_memory\":true}"
```

(Use the `upstream` ports — the real programs — for these.)

## "A model loads twice / out of memory anyway"

**Why:** something is still pointed at the real port, or a second doorman is
running with a different `name` so the two don't coordinate.

1. `gpu-lease status` — is there more than one doorman window open?
2. Were both copies started from the same settings file? Two files with
   different `name` values would run independently by design.

## "ComfyUI takes 25 seconds to start a render after I hit stop"

**Why:** ComfyUI leaves a stale "still running" entry in its queue after an
interrupt, and the doorman waits for the queue to drain (up to 8 seconds) and for
the job to be reported done. This is deliberate — the alternative is rendering
into a card that another program is using.

Wait it out, or restart ComfyUI if it persists.

## "The window shows a long list of `pass-through` lines"

**Why:** `--level debug`. Nothing is wrong — debug prints one line per request,
including the harmless ones. Switch back with `--level info`.

## "Something is running but I can't see a window"

The doorman is a window; closing it stops it. But two other things can look
"stuck":

- **A queued request with keepalives** — the app thinks it's talking to a quiet
  server. Check `gpu-lease status`: `1 waiting` and a holder that's been there
  a long time means the holder is stuck. Restart the *holder's* program.
- **llama.cpp loading a model** — 10–40 s, no output. Its own window says
  `loading model`. Normal.

---

## How to undo everything

Five minutes, no leftovers. Do it in this order:

1. **Stop the doorman** (Ctrl+C in its window).
2. **In your apps:** point the chat URL and image URL at the *real* ports —
   the `upstream` numbers from your settings file (`http://127.0.0.1:6872/v1`,
   `http://127.0.0.1:8198`).
3. **Put the two programs back on their old ports:** remove `--port 8198` from
   the ComfyUI `.bat`, and change llama.cpp's `--port` back to 6772.
4. Restart both programs and your apps. Everything is exactly as it was before.
5. Optional: `python -m pip uninstall gpu-lease`, and delete the folder.

Nothing was ever written into the two programs, your app's database, or your
Windows settings, so that's the complete list.

## When you need to ask for help

Copy these four things into your message — they answer 90% of questions before
they're asked:

1. The doorman's window contents (the last ~30 lines).
2. The output of `gpu-lease doctor -c gpu-lease.toml`.
3. The output of `gpu-lease status -c gpu-lease.toml`.
4. Your `gpu-lease.toml`, with the model names left in — they're not secret.
